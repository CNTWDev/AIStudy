"""题库：AI 现场生成的题目、讲解、背景、阅读短文全部沉淀下来，以后先用库里的。

- 题目（items + item_kps）：一道题挂在一个或几个知识点上；同一概念在别的教材里的知识点（语言相同）也能用。
  每道题记下语言、年级、出题目的、生成记录（模型 / 提示词版本）和做题统计（做了几次、对了几次、平均用时）。
  内容相同的题只存一份（qhash 去重）。
- 其它内容（contents）：teach 分步讲解、context 背景和用处、passage 阅读短文。和孩子个人有关的部分不存，用的时候再拼。
- 有问题的题：孩子 / 家长可以标记；两个不同的人标记过就暂停使用（review），管理员在后台恢复或下架。
"""
import hashlib
import json
import re
import uuid

from . import db, llm
from .catalog import catalog

FLAG_REASONS = {"wrong": "答案好像不对", "unclear": "题目有错或看不懂", "offtopic": "和这个知识点没关系"}
FLAGS_TO_REVIEW = 2  # 几个不同的人标记后暂停使用


def _norm(s) -> str:
    if isinstance(s, (list, dict)):
        s = json.dumps(s, ensure_ascii=False, sort_keys=True)
    return re.sub(r"[\s　]+", "", str(s or "")).lower().strip("。.!！?？")


def fingerprint(*parts) -> str:
    return hashlib.sha1("\x1f".join(_norm(p) for p in parts).encode()).hexdigest()


def item_hash(it: dict) -> str:
    return fingerprint(it.get("type"), it.get("q"), it.get("options") or "", it.get("answer", it.get("model", "")))


def kp_lang(kp_id: str) -> str:
    """题目语言：教材包声明的出题语言（见 catalog.Pack.item_lang）。"""
    pid = catalog.kp_pack.get(kp_id)
    return catalog.packs[pid].item_lang if pid else ""


def gen_meta(task: str, **extra) -> dict:
    return {**llm.model_of(task), "prompt_v": llm.PROMPT_VERSION.get(task, 1), **extra}


# ------------------------------------------------------------------ 题目

def row_to_item(r) -> dict:
    d = db.jload(r["data"], {})
    d.update(id=r["id"], kp_id=r["kp_id"], kp_ids=db.jload(r["kp_ids"], []), type=r["type"], difficulty=r["difficulty"],
             source=r["source"])
    return d


def link(item_id: str, kp_id: str, role="main"):
    if kp_id:
        db.run("INSERT INTO item_kps(item_id, kp_id, role) VALUES(?,?,?) ON CONFLICT(item_id, kp_id) DO NOTHING",
               item_id, kp_id, role)


def save_items(kp_id: str, items: list[dict], source="ai", purpose="", grade="", meta=None) -> list[dict]:
    """存题。内容和库里已有的题一样就不重复存，直接把那道题也挂到这个知识点上。"""
    out, lang, now = [], kp_lang(kp_id), db.now()
    for it in items:
        h = item_hash(it)
        old = db.one("SELECT * FROM items WHERE qhash=? ORDER BY created_at LIMIT 1", h)
        if old:
            link(old["id"], kp_id, "main" if old["kp_id"] == kp_id else "also")
            if old["status"] == "active":
                out.append(row_to_item(old))
            continue
        iid = "AI-" + uuid.uuid4().hex[:10]
        data = {k: v for k, v in it.items() if k not in ("type", "difficulty", "id", "kp_ids")}
        try:
            diff = min(3, max(1, int(it.get("difficulty", 2))))
        except (TypeError, ValueError):
            diff = 2
        also = [k for k in (it.get("kp_ids") or []) if k != kp_id and catalog.kp(k)]
        db.run("INSERT INTO items(id,kp_id,kp_ids,type,difficulty,data,source,created_at,lang,grade,purpose,gen_meta,qhash,updated_at) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", iid, kp_id, db.jdump([kp_id] + also), it["type"], diff, db.jdump(data),
               source, now, lang, grade, purpose, db.jdump(meta or {}), h, now)
        link(iid, kp_id)
        for k in also:
            link(iid, k, "also")
        out.append(row_to_item(db.one("SELECT * FROM items WHERE id=?", iid)))
    return out


def candidates(user_id: int, kp_id: str) -> list:
    """这个知识点能用的题：自己的，加上同一概念在别的教材里、语言相同的知识点的题。
    孩子自己标记过有问题的题不再出现；别的孩子的试卷原题不出现。"""
    lang = kp_lang(kp_id)
    kps = [kp_id] + [k["id"] for k in catalog.equivalents(kp_id) if kp_lang(k["id"]) == lang]
    marks = ",".join("?" * len(kps))
    return db.q(
        "SELECT i.*, (SELECT COUNT(*) FROM attempts a WHERE a.item_id=i.id AND a.user_id=?) AS done, "
        "(SELECT COUNT(*) FROM attempts a WHERE a.item_id=i.id AND a.user_id=? AND a.correct=1) AS ok, "
        "(SELECT MIN(CASE WHEN x.kp_id=? THEN 0 ELSE 1 END) FROM item_kps x WHERE x.item_id=i.id AND x.kp_id IN (" + marks + ")) AS other "
        "FROM items i WHERE i.status='active' "
        "AND i.id IN (SELECT item_id FROM item_kps WHERE kp_id IN (" + marks + ")) "
        "AND i.id NOT IN (SELECT target_id FROM flags WHERE user_id=? AND target='item') "
        # 试卷原题来自某个孩子的卷子，只给这个孩子自己用
        "AND (i.source<>'paper' OR i.id IN (SELECT pi.item_id FROM paper_items pi JOIN papers p ON p.id=pi.paper_id WHERE p.user_id=?))",
        user_id, user_id, kp_id, *kps, *kps, user_id, user_id)


def flagged_by(user_id: int) -> set:
    return {r["target_id"] for r in db.q("SELECT target_id FROM flags WHERE user_id=? AND target='item'", user_id)}


def record(item_id: str, correct: bool, dont_know=False, ms=None):
    """每次作答更新这道题的统计：以后按正确率、用时判断题目难度和质量。"""
    db.run("UPDATE items SET n_attempts=n_attempts+1, n_correct=n_correct+?, n_dont_know=n_dont_know+?, "
           "n_timed=n_timed+?, total_ms=total_ms+?, updated_at=? WHERE id=?",
           1 if correct else 0, 1 if dont_know else 0, 1 if ms else 0, int(ms or 0), db.now(), item_id)


# ------------------------------------------------------------------ 讲解、背景、短文

def find_content(kind: str, kp_id="", lang="", grade="", topic="", exclude=()) -> dict | None:
    sql = "SELECT * FROM contents WHERE kind=? AND status='active'"
    args = [kind]
    for col, v in (("kp_id", kp_id), ("lang", lang), ("topic", topic)):
        if v:
            sql += f" AND {col}=?"
            args.append(v)
    if exclude:
        sql += f" AND id NOT IN ({','.join('?' * len(exclude))})"
        args += list(exclude)
    # 同年级的优先，其次用得少的（让好几篇轮着用）
    rows = db.q(sql + " ORDER BY CASE WHEN grade=? THEN 0 ELSE 1 END, uses, id LIMIT 1", *args, grade)
    if not rows:
        return None
    r = rows[0]
    db.run("UPDATE contents SET uses=uses+1 WHERE id=?", r["id"])
    return {**dict(r), "body": db.jload(r["body"], {})}


def save_content(kind: str, body: dict, kp_id="", lang="", grade="", topic="", title="", origin="ai", meta=None,
                 created_by=None) -> int:
    h = fingerprint(kind, kp_id, lang, body)
    old = db.one("SELECT id FROM contents WHERE qhash=?", h)
    if old:  # 一模一样的内容只存一份；以前没记下的话题 / 年级补上
        db.run("UPDATE contents SET topic=CASE WHEN topic='' THEN ? ELSE topic END, "
               "grade=CASE WHEN grade='' THEN ? ELSE grade END, uses=uses+1 WHERE id=?", topic[:200], grade, old["id"])
        return old["id"]
    now = db.now()
    return db.insert("INSERT INTO contents(kind,kp_id,lang,grade,topic,title,body,origin,gen_meta,qhash,uses,created_by,created_at,updated_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", kind, kp_id, lang, grade, topic[:200], title[:200], db.jdump(body),
                     origin, db.jdump(meta or {}), h, 1, created_by, now, now)


def teach(kp: dict, grade: str, user_id=None) -> dict:
    """分步讲解：库里有就直接用，没有再生成并存下。"""
    c = find_content("teach", kp["id"], grade=grade)
    if c:
        return {**c["body"], "content_id": c["id"]}
    body = llm.teach(kp, catalog.packs[kp["pack"]], grade, user_id=user_id)
    cid = save_content("teach", body, kp["id"], kp_lang(kp["id"]), grade, title=kp["name"], meta=gen_meta("teach"),
                       created_by=user_id)
    return {**body, "content_id": cid}


def context(kp: dict, grade: str, user_id=None) -> dict:
    c = find_content("context", kp["id"], grade=grade)
    if c:
        return {**c["body"], "content_id": c["id"]}
    body = llm.kp_context(kp, catalog.packs[kp["pack"]], grade, user_id=user_id)
    cid = save_content("context", body, kp["id"], kp_lang(kp["id"]), grade, title=kp["name"], meta=gen_meta("context"),
                       created_by=user_id)
    return {**body, "content_id": cid}


def passage(user_id: int, lang: str, grade: str, topic: str, length: str, review_words: list[str]) -> tuple[dict, int]:
    """阅读短文：同语言、同年级、同话题、这个孩子没读过的库存短文先用（含要复习的词多的优先）；没有就现场写一篇存下。"""
    read = [r["content_id"] for r in db.q("SELECT content_id FROM readings WHERE user_id=? AND content_id IS NOT NULL", user_id)]
    if topic:
        rows = db.q("SELECT id, body FROM contents WHERE kind='passage' AND status='active' AND lang=? AND grade=? AND topic=?"
                    + (f" AND id NOT IN ({','.join('?' * len(read))})" if read else "") + " ORDER BY uses, id",
                    lang, grade, topic.strip()[:200], *read)
        if rows:  # 用得少的、含要复习的词多的优先
            def score(r):
                text = (db.jload(r["body"], {}).get("body") or "").lower()
                return -sum(w.lower() in text for w in review_words)
            r = min(rows, key=score)
            db.run("UPDATE contents SET uses=uses+1 WHERE id=?", r["id"])
            return db.jload(r["body"], {}), r["id"]
    body = llm.make_passage(lang, grade, topic, length, review_words, user_id=user_id)
    cid = save_content("passage", body, "", lang, grade, topic=topic.strip(), title=body.get("title", ""),
                       meta=gen_meta("passage", length=length, words=review_words[:8]), created_by=user_id)
    return body, cid


# ------------------------------------------------------------------ 标记有问题

def flag(user_id: int, target: str, target_id: str, reason: str) -> str:
    """返回这条内容现在的状态。家长 / 管理员标记一次就暂停，孩子要两个不同的人。"""
    table = {"item": "items", "content": "contents"}[target]
    row = db.one(f"SELECT id, status FROM {table} WHERE id=?", int(target_id) if target == "content" else target_id)
    if not row:
        raise KeyError(target_id)
    db.run("INSERT INTO flags(user_id,target,target_id,reason,created_at) VALUES(?,?,?,?,?) "
           "ON CONFLICT(user_id,target,target_id) DO NOTHING", user_id, target, str(target_id), reason, db.now())
    n = db.one("SELECT COUNT(DISTINCT user_id) AS n FROM flags WHERE target=? AND target_id=?", target, str(target_id))["n"]
    grown_up = db.one("SELECT role FROM users WHERE id=?", user_id)["role"] in ("parent", "admin")
    status = row["status"]
    if status == "active" and (n >= FLAGS_TO_REVIEW or grown_up):
        status = "review"
    db.run(f"UPDATE {table} SET n_flags=?, status=? WHERE id=?", n, status, row["id"])
    return status


def set_status(target: str, target_id, status: str):
    table = {"item": "items", "content": "contents"}[target]
    db.run(f"UPDATE {table} SET status=?, updated_at=? WHERE id=?", status, db.now(), target_id)
    if status == "active":  # 管理员确认没问题：清掉标记
        db.run("DELETE FROM flags WHERE target=? AND target_id=?", target, str(target_id))
        db.run(f"UPDATE {table} SET n_flags=0 WHERE id=?", target_id)


# ------------------------------------------------------------------ 启动时补齐旧数据

def sync():
    """幂等：给老题补上语言、去重指纹和知识点关联；把以前 AI 写的阅读短文收进 contents。"""
    for r in db.q("SELECT * FROM items WHERE qhash IS NULL"):
        it = row_to_item(r)
        db.run("UPDATE items SET qhash=?, lang=? WHERE id=?", item_hash(it), kp_lang(r["kp_id"]), r["id"])
        for k in it["kp_ids"]:
            link(r["id"], k, "main" if k == r["kp_id"] else "also")
    for r in db.q("SELECT r.*, u.grade FROM readings r JOIN users u ON u.id=r.user_id "
                  "WHERE r.source='ai' AND r.content_id IS NULL"):
        body = {"title": r["title"], "body": r["body"], "questions": db.jload(r["questions"], [])}
        cid = save_content("passage", body, "", r["lang"], r["grade"] or "", title=r["title"], created_by=r["user_id"])
        db.run("UPDATE readings SET content_id=? WHERE id=?", cid, r["id"])


# ------------------------------------------------------------------ 管理后台

def overview() -> dict:
    by_source = {r["source"]: r["n"] for r in db.q("SELECT source, COUNT(*) AS n FROM items WHERE status='active' GROUP BY source")}
    by_kind = {r["kind"]: r["n"] for r in db.q("SELECT kind, COUNT(*) AS n FROM contents WHERE status='active' GROUP BY kind")}
    per_kp = {r["kp_id"]: r["n"] for r in db.q(
        "SELECT x.kp_id, COUNT(*) AS n FROM item_kps x JOIN items i ON i.id=x.item_id WHERE i.status='active' GROUP BY x.kp_id")}
    packs = []
    for p in sorted(catalog.packs.values(), key=lambda p: (p.subject, p.id)):
        ids = p.kp_ids
        have = [per_kp.get(k, 0) for k in ids]
        packs.append({"id": p.id, "name": f"{p.subject_name} · {p.edition}", "kps": len(ids),
                      "covered": sum(1 for n in have if n), "enough": sum(1 for n in have if n >= 3), "items": sum(have)})
    packs.sort(key=lambda x: -x["items"])
    tot = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(n_attempts),0) AS a, COALESCE(SUM(n_correct),0) AS c FROM items WHERE status='active'")
    review = db.q("SELECT * FROM items WHERE status='review' ORDER BY updated_at DESC LIMIT 50")
    review_c = db.q("SELECT id, kind, kp_id, title, n_flags, created_at FROM contents WHERE status='review' ORDER BY id DESC LIMIT 50")
    reasons = {}
    for f in db.q("SELECT target, target_id, reason FROM flags"):
        reasons.setdefault((f["target"], f["target_id"]), []).append(FLAG_REASONS.get(f["reason"], f["reason"]))
    hard = db.q("SELECT * FROM items WHERE status='active' AND n_attempts>=5 ORDER BY 1.0*n_correct/n_attempts, n_attempts DESC LIMIT 10")
    return {"items": tot["n"], "attempts": tot["a"], "acc": round(100 * tot["c"] / tot["a"]) if tot["a"] else None,
            "by_source": by_source, "by_kind": by_kind, "packs": packs,
            "review": [{**row_to_item(r), "n_flags": r["n_flags"], "why": reasons.get(("item", r["id"]), [])} for r in review],
            "review_contents": [{**dict(r), "why": reasons.get(("content", str(r["id"])), [])} for r in review_c],
            "hard": [{**row_to_item(r), "n": r["n_attempts"], "acc": round(100 * r["n_correct"] / r["n_attempts"])} for r in hard]}


def export() -> dict:
    """整个题库导出成 JSON（不含任何孩子的作答记录，只有题目本身和汇总统计）。"""
    items = []
    for r in db.q("SELECT * FROM items ORDER BY created_at"):
        if r["source"] in ("paper", "bank"):
            continue  # 试卷原题来自孩子的卷子；卷库的题来自真题、名校卷，有版权，都不导出
        items.append({**row_to_item(r), "lang": r["lang"], "grade": r["grade"], "purpose": r["purpose"], "status": r["status"],
                      "gen": db.jload(r["gen_meta"], {}), "stats": {"attempts": r["n_attempts"], "correct": r["n_correct"],
                      "dont_know": r["n_dont_know"], "avg_ms": round(r["total_ms"] / r["n_timed"]) if r["n_timed"] else None},
                      "kps": [x["kp_id"] for x in db.q("SELECT kp_id FROM item_kps WHERE item_id=?", r["id"])]})
    contents = [{k: (db.jload(r[k], {}) if k in ("body", "gen_meta") else r[k]) for k in
                 ("id", "kind", "kp_id", "lang", "grade", "topic", "title", "body", "origin", "gen_meta", "uses", "status", "created_at")}
                for r in db.q("SELECT * FROM contents ORDER BY id")]
    return {"exported_at": db.now(), "items": items, "contents": contents}
