"""公共卷库：管理员导入的真题、名校卷、名师卷、考后回忆版。

- 只有管理员能导入（家长拍的卷子仍在 app/papers.py，只给自己家用）。
- 导入：拍照 / 粘贴 → AI 拆题、对上知识点、给出答案和讲解 → 存成草稿（题目 status='draft'，学习者看不到）。
- 管理员逐题核对（改题干、选项、答案、知识点，删掉认错的题）后发布：题目进公共题库（items.source='bank'），
  带上来源（比如「2024 北京海淀 期末 · 名校卷」），练习时比 AI 出的题优先。
- 学习者可以把发布的整套卷子当模拟考做：复用试卷流程（papers.bank_paper_id 指回来源），做完同样出诊断。
- 卷子只存在服务器的数据库和数据目录里，不进代码仓库，也不随题库导出。
"""
import base64
import shutil
import uuid

from . import bank, config, db, itemtypes, llm
from .catalog import catalog, stage_rank

KINDS = {"real": "真题", "school": "名校卷", "teacher": "名师卷", "recall": "回忆版"}
STATUS = {"draft": "待核对", "published": "已发布", "retired": "已下架"}
DIR = config.DATA_DIR / "bank_papers"
LETTERS = "ABCDEFGH"


def label(bp) -> str:
    """来源标签：2024 北京海淀 期末 · 名校卷。"""
    head = " ".join(x for x in (bp["year"], bp["region"], bp["org"], bp["exam"]) if x)
    return f"{head} · {KINDS.get(bp['kind'], '')}".strip(" ·") if head else KINDS.get(bp["kind"], "")


def get(bp_id: int):
    return db.one("SELECT * FROM bank_papers WHERE id=?", bp_id)


def create(admin_id: int, pack_id: str, *, kind="real", stage="", title="", exam="", year="", region="", org="",
           minutes=0, images=None, text="", notes="") -> int:
    """images: [(media_type, bytes)]。先让 AI 拆题（失败就什么都不保存），再存成草稿。"""
    pack = catalog.packs[pack_id]
    stage = stage if stage in pack.stages else pack.stages[0]
    imgs = [(mt, base64.b64encode(b).decode()) for mt, b in (images or [])]
    # 真题会考到整套教材 / 大纲里的任何地方：候选知识点给到这个学段为止的全部
    cands = [catalog.kps[k] for k in pack.kp_ids if stage_rank(catalog.kps[k]["stage"]) <= stage_rank(stage)]
    data = llm.parse_paper(pack, stage, cands, images=imgs, text=text, user_id=admin_id)
    qs = data.get("questions", [])
    if not qs:
        raise llm.LLMError("没有从卷子里认出题目。照片请拍清楚、拍正，一页一张；也可以改用粘贴文字")
    valid, now = set(pack.kp_ids), db.now()
    with db.tx() as t:
        bp = t.insert(
            "INSERT INTO bank_papers(pack_id,kind,stage,title,exam,year,region,org,minutes,raw_text,notes,status,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", pack_id, kind if kind in KINDS else "real", stage,
            (title or data.get("title") or "试卷")[:80], exam[:40], year[:10], region[:40], org[:60], int(minutes or 0),
            text[:20000], (notes or str(data.get("notes") or ""))[:500], "draft", admin_id, now)
        for i, q in enumerate(qs):
            kp = q.get("kp_id") if q.get("kp_id") in valid else None
            iid = _insert_item(t, itemtypes.normalize(q), kp, now, bp)
            page = q.get("page")
            t.run("INSERT INTO bank_paper_items(bank_paper_id,seq,label,item_id,points,page) VALUES(?,?,?,?,?,?)",
                  bp, i + 1, str(q.get("label") or i + 1)[:20], iid, itemtypes._num(q.get("score")),
                  int(page) if str(page).isdigit() else None)
    if images:
        d = DIR / str(bp)
        d.mkdir(parents=True, exist_ok=True)
        names = []
        for n, (mt, b) in enumerate(images, 1):
            name = f"{n}.{ {'image/png': 'png', 'image/webp': 'webp'}.get(mt, 'jpg') }"
            (d / name).write_bytes(b)
            names.append(name)
        db.run("UPDATE bank_papers SET images=? WHERE id=?", db.jdump(names), bp)
    _group(bp)
    return bp


def _insert_item(t, it: dict, kp, now, bp_id) -> str:
    iid = "BP-" + uuid.uuid4().hex[:10]
    body = {k: v for k, v in it.items() if k != "type"}
    t.run("INSERT INTO items(id,kp_id,kp_ids,type,difficulty,data,source,created_at,lang,purpose,gen_meta,qhash,status,updated_at) "
          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", iid, kp or "", db.jdump([kp] if kp else []), it["type"], 2, db.jdump(body),
          "bank", now, bank.kp_lang(kp) if kp else "", "paper", db.jdump({"bank_paper": bp_id}), bank.item_hash(it), "draft", now)
    if kp:
        t.run("INSERT INTO item_kps(item_id, kp_id, role) VALUES(?,?,'main')", iid, kp)
    return iid


def _group(bp_id: int):
    from . import bankflow
    for r in db.q("SELECT i.id, i.kp_id FROM bank_paper_items x JOIN items i ON i.id=x.item_id WHERE x.bank_paper_id=?", bp_id):
        bankflow.assign_near(r["id"], kp_id=r["kp_id"])


def rows(bp_id: int) -> list[dict]:
    out = []
    for r in db.q("SELECT * FROM bank_paper_items WHERE bank_paper_id=? ORDER BY seq", bp_id):
        it = db.one("SELECT * FROM items WHERE id=?", r["item_id"])
        if it:
            item = bank.row_to_item(it)
            out.append({**dict(r), "item": item, "kp": catalog.kp(item["kp_id"]) if item["kp_id"] else None,
                        "answer_text": answer_text(item)})
    return out


def answer_text(it: dict) -> str:
    """管理后台编辑框里的答案：选择题写字母，填空题多个答案用 | 隔开，简答题是参考答案。"""
    if it["type"] == "mcq":
        try:
            return LETTERS[int(it["answer"])]
        except (TypeError, ValueError, IndexError, KeyError):
            return ""
    if it["type"] == "short":
        return it.get("model", "")
    a = it.get("answer")
    return " | ".join(map(str, a)) if isinstance(a, list) else str(a if a is not None else "")


def update_item(bp_id: int, bpi_id: int, f: dict) -> str:
    """管理员改一道题。返回改完的题型（答案不合格会退成简答题，页面上会看到）。"""
    r = db.one("SELECT * FROM bank_paper_items WHERE id=? AND bank_paper_id=?", bpi_id, bp_id)
    if not r:
        raise KeyError(bpi_id)
    old = bank.row_to_item(db.one("SELECT * FROM items WHERE id=?", r["item_id"]))
    typ = f.get("type") if f.get("type") in itemtypes.TYPES else old["type"]
    raw = {k: old.get(k) for k in itemtypes.FIELDS}
    raw.update(type=typ, q=(f.get("q") or "").strip(), explain=(f.get("explain") or "").strip())
    ans = (f.get("answer") or "").strip()
    if typ == "mcq":
        raw["options"] = [x.strip() for x in (f.get("options") or "").splitlines() if x.strip()]
        raw["answer"] = LETTERS.index(ans.upper()) if len(ans) == 1 and ans.upper() in LETTERS else ans
    elif typ == "fill":
        raw["answer"] = [x.strip() for x in ans.split("|") if x.strip()]
    elif typ == "short":
        raw["model"], raw["answer"] = ans, None
    else:
        raw["answer"] = ans
    if typ != "mcq":
        raw["options"] = None
    it = itemtypes.normalize(raw)
    data = db.jload(db.one("SELECT data FROM items WHERE id=?", r["item_id"])["data"], {})
    body = {k: v for k, v in it.items() if k != "type"}
    if data.get("src"):
        body["src"] = data["src"]
    kp = f.get("kp_id") or None
    pack = catalog.packs[get(bp_id)["pack_id"]]
    if kp and kp not in pack.kp_ids:
        kp = None
    with db.tx() as t:
        t.run("UPDATE items SET type=?, data=?, kp_id=?, kp_ids=?, qhash=?, lang=?, updated_at=? WHERE id=?",
              it["type"], db.jdump(body), kp or "", db.jdump([kp] if kp else []), bank.item_hash(it),
              bank.kp_lang(kp) if kp else "", db.now(), r["item_id"])
        t.run("DELETE FROM item_kps WHERE item_id=?", r["item_id"])
        if kp:
            t.run("INSERT INTO item_kps(item_id, kp_id, role) VALUES(?,?,'main')", r["item_id"], kp)
        t.run("UPDATE bank_paper_items SET label=?, points=? WHERE id=?", (f.get("label") or r["label"])[:20],
              itemtypes._num(f.get("points")), bpi_id)
        t.run("UPDATE items SET verified=0, verify_note='' WHERE id=?", r["item_id"])  # 改过的题重新校对
    from . import bankflow
    bankflow.assign_near(r["item_id"], kp_id=kp or "")
    return it["type"]


def remove_item(bp_id: int, bpi_id: int):
    """删掉认错 / 看不清的题。已经有人做过的题只下架，不删（作答记录还指着它）。"""
    r = db.one("SELECT * FROM bank_paper_items WHERE id=? AND bank_paper_id=?", bpi_id, bp_id)
    if not r:
        return
    used = db.one("SELECT 1 AS ok FROM attempts WHERE item_id=? LIMIT 1", r["item_id"])
    with db.tx() as t:
        t.run("DELETE FROM bank_paper_items WHERE id=?", bpi_id)
        if used:
            t.run("UPDATE items SET status='retired' WHERE id=?", r["item_id"])
        else:
            t.run("DELETE FROM item_kps WHERE item_id=?", r["item_id"])
            t.run("DELETE FROM items WHERE id=?", r["item_id"])


def update_meta(bp_id: int, f: dict):
    bp = get(bp_id)
    pack = catalog.packs[bp["pack_id"]]
    db.run("UPDATE bank_papers SET title=?, kind=?, stage=?, exam=?, year=?, region=?, org=?, minutes=?, notes=? WHERE id=?",
           (f.get("title") or bp["title"])[:80], f.get("kind") if f.get("kind") in KINDS else bp["kind"],
           f.get("stage") if f.get("stage") in pack.stages else bp["stage"], (f.get("exam") or "")[:40],
           (f.get("year") or "")[:10], (f.get("region") or "")[:40], (f.get("org") or "")[:60],
           int(f.get("minutes") or 0) if str(f.get("minutes") or "0").isdigit() else 0, (f.get("notes") or "")[:500], bp_id)
    if bp["status"] == "published":
        _stamp(bp_id)


def _stamp(bp_id: int):
    """把来源标签写进每道题，练习时题目上会显示「真题 · 2024 北京海淀 期末」。"""
    src = label(get(bp_id))
    for r in db.q("SELECT i.id, i.data FROM bank_paper_items x JOIN items i ON i.id=x.item_id WHERE x.bank_paper_id=?", bp_id):
        db.run("UPDATE items SET data=? WHERE id=?", db.jdump({**db.jload(r["data"], {}), "src": src}), r["id"])


def publish(bp_id: int) -> int:
    """发布：题目进公共题库（status=active）。返回发布的题数。"""
    ids = [r["item_id"] for r in db.q("SELECT item_id FROM bank_paper_items WHERE bank_paper_id=?", bp_id)]
    if not ids:
        raise ValueError("这份卷子没有题")
    _stamp(bp_id)
    marks = ",".join("?" * len(ids))
    with db.tx() as t:
        t.run(f"UPDATE items SET status='active', updated_at=? WHERE id IN ({marks}) AND status IN ('draft','retired')", db.now(), *ids)
        t.run("UPDATE bank_papers SET status='published', published_at=? WHERE id=?", db.now(), bp_id)
    return len(ids)


def retire(bp_id: int):
    """下架：题目不再出给学习者；已经开始的模拟考还能做完。"""
    with db.tx() as t:
        t.run("UPDATE items SET status='retired', updated_at=? WHERE id IN "
              "(SELECT item_id FROM bank_paper_items WHERE bank_paper_id=?)", db.now(), bp_id)
        t.run("UPDATE bank_papers SET status='retired' WHERE id=?", bp_id)


def delete(bp_id: int) -> bool:
    """只能删没发布过、也没人做过的卷子（草稿认错了就删掉重导）。"""
    bp = get(bp_id)
    if not bp or bp["published_at"] or db.one("SELECT 1 AS ok FROM papers WHERE bank_paper_id=? LIMIT 1", bp_id):
        return False
    for r in db.q("SELECT id FROM bank_paper_items WHERE bank_paper_id=?", bp_id):
        remove_item(bp_id, r["id"])
    db.run("DELETE FROM bank_papers WHERE id=?", bp_id)
    shutil.rmtree(DIR / str(bp_id), ignore_errors=True)
    return True


def listing(status="") -> list:
    sql = ("SELECT b.*, (SELECT COUNT(*) FROM bank_paper_items x WHERE x.bank_paper_id=b.id) AS n, "
           "(SELECT COUNT(*) FROM papers p WHERE p.bank_paper_id=b.id) AS used FROM bank_papers b")
    return db.q(sql + (" WHERE b.status=?" if status else "") + " ORDER BY b.id DESC", *([status] if status else []))


# ------------------------------------------------------------------ 学习者：整卷模拟考

def for_learner(user_id: int, enrolls: list) -> list[dict]:
    """学习者选了的课里、不超过他当前学段的已发布卷子（同学段的在前）。附上他做这套卷子的情况。"""
    out = []
    for e in enrolls:
        cur = stage_rank(e["stage"]) if e["stage"] else 99
        for b in db.q("SELECT b.*, (SELECT COUNT(*) FROM bank_paper_items x WHERE x.bank_paper_id=b.id) AS n "
                      "FROM bank_papers b WHERE b.pack_id=? AND b.status='published'", e["pack_id"]):
            if b["stage"] and stage_rank(b["stage"]) > cur:
                continue
            mine = db.q("SELECT id, status, report FROM papers WHERE user_id=? AND bank_paper_id=? ORDER BY id DESC", user_id, b["id"])
            out.append({"bp": b, "label": label(b), "pack": catalog.packs[b["pack_id"]], "mine": mine,
                        "open": next((m["id"] for m in mine if m["status"] == "ready"), None),
                        "rank": -(stage_rank(b["stage"]) if b["stage"] else cur)})
    year = lambda b: int(b["year"][:4]) if (b["year"] or "")[:4].isdigit() else 0  # noqa: E731
    out.sort(key=lambda x: (x["rank"], -year(x["bp"]), -x["bp"]["id"]))
    return out


def start_mock(user_id: int, bp_id: int) -> int:
    """开始做一套模拟考：有没做完的就接着做，否则新开一份（试卷流程，题目直接用卷库里的题）。"""
    bp = get(bp_id)
    if not bp or bp["status"] != "published":
        raise KeyError(bp_id)
    old = db.one("SELECT id FROM papers WHERE user_id=? AND bank_paper_id=? AND status='ready'", user_id, bp_id)
    if old:
        return old["id"]
    now = db.now()
    with db.tx() as t:
        pid = t.insert("INSERT INTO papers(user_id,pack_id,title,exam_date,source,raw_text,notes,status,created_by,created_at,bank_paper_id) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)", user_id, bp["pack_id"], bp["title"][:80], "", "bank", "",
                       label(bp)[:500], "ready", user_id, now, bp_id)
        for r in rows(bp_id):
            t.run("INSERT INTO paper_items(paper_id,seq,label,item_id,kp_id,points,page,orig,orig_answer) VALUES(?,?,?,?,?,?,?,?,?)",
                  pid, r["seq"], r["label"], r["item_id"], r["item"]["kp_id"] or None, r["points"], None, "", "")
    return pid


def last_mock_day(user_id: int, pack_id: str) -> str:
    r = db.one("SELECT MAX(created_at) AS d FROM papers WHERE user_id=? AND pack_id=? AND bank_paper_id IS NOT NULL", user_id, pack_id)
    return (r["d"] or "")[:10] if r else ""
