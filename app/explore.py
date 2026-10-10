"""摸底探索：孩子不管几年级进来，过去学过的知识点系统都不知道（冷启动）。
这里不靠一次长考试，而是每天在「热身」和练习里穿插一两道「以前学过的」小题，慢慢把过去摸清：

- 选题：在「以前学过」的范围里，挑信息量最大的知识点——答对能顺带推断一串前置，答错就沿前置往回追；
  和学校正在学的内容有关的、高频考点优先。
- 答对：这个点记为掌握，它的必须前置（未测过的）标为「推断掌握」。
- 答错 / 还不会：这个点记为薄弱，进错题本；它的必须前置排进队列，之后几天接着测（最多往回 3 层）。
- 英语：再穿插以前学段的核心词闪测（四选一），不认识的词自动加进单词复习，并估算词汇量。

摸清了多少（覆盖度）、今天点亮了几个知识点、前方要学什么，也在这里算。
"""
import random
from datetime import timedelta

from . import db, engine, evidence
from .catalog import catalog, stage_label, stage_rank

MAX_DEPTH = 3
RECENT_DAYS = 7  # 测过的点一周内不再抽到


# ------------------------------------------------------------------ 范围与覆盖度

def _enrolls(user_id: int):
    return [e for e in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", user_id) if e["pack_id"] in catalog.packs]


def past_range(user_id: int, e, taught: set | None = None) -> list[str]:
    """「以前学过」的知识点：当前学段之前的，加上本学段学校已经教过的。"""
    taught = engine.taught_set(user_id) if taught is None else taught
    cur = stage_rank(e["stage"])
    return [k for k in catalog.ids_for(e["pack_id"], e["track"])
            if stage_rank(catalog.kps[k]["stage"]) < cur or k in taught]


def coverage(user_id: int, mastery: dict | None = None) -> list[dict]:
    """每个学科摸清了多少：测过（含推断）的 / 以前学过的。"""
    mastery = engine.get_mastery(user_id) if mastery is None else mastery
    taught = engine.taught_set(user_id)
    out = []
    for e in _enrolls(user_id):
        rng = past_range(user_id, e, taught)
        if not rng:
            continue
        st = [mastery.get(k, {}) for k in rng]
        known = sum(1 for m in st if m.get("status") not in (None, "unknown"))
        # 按学段分开看：哪个年级的旧知识还没摸、哪里只是推断会、哪里薄弱（家长页显示）
        stages: dict[str, dict] = {}
        for k, m in zip(rng, st):
            g = stages.setdefault(catalog.kps[k]["stage"], {"total": 0, "known": 0, "inferred": 0, "weak": 0})
            g["total"] += 1
            g["known"] += m.get("status") not in (None, "unknown")
            g["inferred"] += m.get("source") == "inferred"
            g["weak"] += m.get("status") == "weak"
        out.append({"pack": catalog.packs[e["pack_id"]], "total": len(rng), "known": known,
                    "inferred": sum(1 for m in st if m.get("source") == "inferred"),
                    "weak": sum(1 for m in st if m.get("status") == "weak"),
                    "pct": round(100 * known / len(rng)),
                    "stages": [{"stage": sg, "label": stage_label(sg), **v, "pct": round(100 * v["known"] / v["total"])}
                               for sg, v in sorted(stages.items(), key=lambda x: stage_rank(x[0]))]})
    return out


def lit_today(user_id: int) -> list[dict]:
    rows = db.q("SELECT kp_id FROM lights WHERE user_id=? AND day=?", user_id, db.today().isoformat())
    return [catalog.kp(r["kp_id"]) for r in rows if catalog.kp(r["kp_id"])]


def light(user_id: int, kp_id: str, old_status: str | None, new_status: str) -> bool:
    """第一次真正掌握某个知识点 → 点亮。返回是否是新点亮的。"""
    if new_status != "mastered" or old_status == "mastered":
        return False
    return db.run("INSERT INTO lights(user_id,kp_id,day) VALUES(?,?,?) ON CONFLICT(user_id,kp_id) DO NOTHING",
                  user_id, kp_id, db.today().isoformat()) == 1


# ------------------------------------------------------------------ 选题

def _recent(user_id: int, kind="kp") -> set[str]:
    since = (db.today() - timedelta(days=RECENT_DAYS)).isoformat()
    col = "kp_id" if kind == "kp" else "ref"
    return {r[col] for r in db.q(f"SELECT {col} FROM probes WHERE user_id=? AND kind=? AND status='done' AND done_at>=?",
                                 user_id, kind, since)}


def _queued(user_id: int) -> list:
    return db.q("SELECT * FROM probes WHERE user_id=? AND kind='kp' AND status='queued' ORDER BY priority DESC, id",
                user_id)


def _today_kps(user_id: int) -> list[str]:
    """今天清单里要学 / 要练的知识点（只读已经排好的清单，不触发排计划，避免互相调用）。"""
    row = db.one("SELECT plan FROM days WHERE user_id=? AND day=?", user_id, db.today().isoformat())
    return [t["kp"] for t in db.jload(row["plan"], []) if t.get("kp") and catalog.kp(t["kp"])] if row else []


def _near(kp_id: str, depth: int = 3) -> list[tuple[dict, int]]:
    """和一个知识点「有关的旧知识」：它的必须前置（往回 depth 层），加上它要用到的别科知识（物理用到的数学）。"""
    out = list(catalog.ancestors(kp_id, depth=depth))
    have = {p["id"] for p, _ in out}
    for r in catalog.related(kp_id, types=["uses"]):
        if r["dir"] == "out" and r["kp"]["id"] not in have:
            out.append((r["kp"], 1))
    return out


def candidates(user_id: int, mastery: dict | None = None, near_kp: str | None = None) -> list[dict]:
    """按信息量排序的摸底 / 校正候选。每项：{kp, reason, depth, from}。

    「以前学过的」不只是测一次就完：没测过的要摸底，只是推断会的要确认，学会很久的按遗忘规律要复查。
    挑哪个讲相关性——和正在学的、今天要学的、学校正在教的有关的旧知识排前面；
    再讲覆盖面——哪个学科的哪一块、哪个年级摸得最少，就往那里多放一点，慢慢把过去整个过一遍。
    near_kp：在学某个知识点时，优先它没测过 / 没确认的前置。"""
    mastery = engine.get_mastery(user_id) if mastery is None else mastery
    unknown = lambda k: mastery.get(k, {}).get("status") in (None, "unknown")  # noqa: E731
    guessed = lambda k: mastery.get(k, {}).get("source") == "inferred" and mastery[k].get("status") != "weak"  # noqa: E731
    recent = _recent(user_id)
    out, seen = [], set()

    def add(k, reason, depth=0, frm=""):
        if k not in seen and k in catalog.kps and k not in recent:
            seen.add(k)
            out.append({"kp": k, "reason": reason, "depth": depth, "from": frm})

    if near_kp:
        for p, d in _near(near_kp):
            if unknown(p["id"]):
                add(p["id"], f"「{catalog.kps[near_kp]['name']}」要用到它", d, near_kp)
        for p, d in _near(near_kp):
            if guessed(p["id"]):
                add(p["id"], f"「{catalog.kps[near_kp]['name']}」要用到它，确认一下真的会", d, near_kp)
    # 之前答错留下的「往回追」队列
    for q in _queued(user_id):
        if unknown(q["kp_id"]) or guessed(q["kp_id"]):
            add(q["kp_id"], q["reason"], q["depth"], q["from_kp"])
    taught = engine.taught_set(user_id)
    # 今天要学的知识点用到的旧知识：先把地基摸清
    today_near: dict[str, str] = {}
    for k in _today_kps(user_id):
        for p, _ in _near(k):
            today_near.setdefault(p["id"], catalog.kps[k]["name"])
    scored = []
    for e in _enrolls(user_id):
        rng = past_range(user_id, e, taught)
        rset = set(rng)
        cur = catalog.kp(e["progress_kp"]) if e["progress_kp"] else None
        near = {p["id"] for p, _ in _near(cur["id"])} if cur else set()
        # 覆盖面：每一块（知识线 strand）、每个学段摸清了多少，摸得少的地方加分
        cells: dict[tuple, list] = {}
        for k in rng:
            kp = catalog.kps[k]
            known = not unknown(k) and not guessed(k)
            cells.setdefault(("s", kp.get("strand")), []).append(known)
            cells.setdefault(("g", kp["stage"]), []).append(known)
        gap = {c: 1 - sum(v) / len(v) for c, v in cells.items()}
        for k in rng:
            kp = catalog.kps[k]
            spread = 1.5 * gap[("s", kp.get("strand"))] + 1.5 * gap[("g", kp["stage"])]
            rel = 4 if k in near else 3 if k in today_near else 0
            hot = 2 if kp.get("hot") else 0
            if unknown(k):
                # 答对能推断的未测前置越多越值得测；被越多旧知识点依赖越基础
                up = sum(1 for p, _ in catalog.ancestors(k, depth=3) if unknown(p["id"]))
                down = sum(1 for sc in catalog.successors(k) if sc["id"] in rset and unknown(sc["id"]))
                score = 2 * up + down + rel + hot + spread + random.random()
            elif guessed(k):  # 只是推断会：信息量比未测的小，但相关的、覆盖少的地方也值得确认
                score = 0.5 * (rel + hot) + spread + random.random()
                if score < 2:
                    continue
            else:
                continue
            why = ("和学校正在学的内容有关" if k in near else f"今天要学的「{today_near[k]}」要用到它" if k in today_near
                   else "以前推断你会，确认一下" if guessed(k) else "高频考点" if hot else "以前学过，看看还记得吗")
            scored.append((score, e["pack_id"], k, why))
    # 学会很久了、按遗忘规律快忘了的：穿插一两道（复习任务里没排到的）
    for v in evidence.due_checks(user_id, mastery, limit=4):
        k = v["kp_id"]
        if catalog.kp(k):
            scored.append((3 + (3 if k in today_near else 0) + random.random(), catalog.kp_pack[k], k,
                           f"{int(evidence.days_since(v['last_ev']))} 天没碰了，看看还记得吗"))
    scored.sort(reverse=True)
    # 各学科轮流，避免一天全是同一科
    by_pack: dict[str, list] = {}
    for sc in scored:
        by_pack.setdefault(sc[1], []).append(sc)
    lists = list(by_pack.values())
    i = 0
    while any(i < len(lst) for lst in lists):
        for lst in lists:
            if i < len(lst):
                add(lst[i][2], lst[i][3])
        i += 1
    return out


def pick(user_id: int, n: int, grade: str, near_kp: str | None = None, exclude: set | None = None) -> list[dict]:
    """取 n 道摸底题（知识点题）。返回题目字典，带 probe 信息。"""
    out, tries = [], 0
    exclude = exclude or set()
    for c in candidates(user_id, near_kp=near_kp):
        if len(out) >= n or tries >= 2 * n + 1:  # 没现成题时每次都要 AI 出题，最多试几次，别拖慢页面
            break
        if c["kp"] in exclude:
            continue
        tries += 1
        items = engine.items_for(user_id, c["kp"], n=1, purpose="diagnose", grade=grade)
        if items:
            kp = catalog.kps[c["kp"]]
            out.append({**items[0], "kp_id": c["kp"], "probe": {"kp": c["kp"], "name": kp["name"], "reason": c["reason"],
                                                                "depth": c["depth"], "from": c["from"],
                                                                "subject": catalog.packs[kp["pack"]].subject_name}})
    return out


# ------------------------------------------------------------------ 作答后

def after_probe(user_id: int, kp_id: str, correct: bool, depth: int = 0, from_kp: str = "") -> dict:
    """摸底题答完：记结果、推断前置 / 往回追。返回 {inferred: n, queued: n}。"""
    now = db.now()
    n = db.run("UPDATE probes SET status='done', result=?, done_at=? WHERE user_id=? AND kind='kp' AND kp_id=? AND status='queued'",
               1 if correct else 0, now, user_id, kp_id)
    if not n:
        db.run("INSERT INTO probes(user_id,kind,kp_id,reason,from_kp,depth,status,result,created_at,done_at) "
               "VALUES(?,'kp',?,'',?,?,'done',?,?,?)", user_id, kp_id, from_kp, depth, 1 if correct else 0, now, now)
    mastery = engine.get_mastery(user_id)
    inferred = queued = 0
    if correct:
        for p, _ in catalog.ancestors(kp_id, depth=2):
            if mastery.get(p["id"], {}).get("status") in (None, "unknown"):
                engine.set_mastery(user_id, p["id"], 0.65, "learning", "inferred")
                inferred += 1
    elif depth < MAX_DEPTH:
        name = catalog.kps[kp_id]["name"]
        for p in catalog.prereqs(kp_id, required_only=True):
            st = mastery.get(p["id"], {})
            if st.get("status") == "mastered" and st.get("source") != "inferred":
                continue
            if db.one("SELECT id FROM probes WHERE user_id=? AND kind='kp' AND kp_id=? AND status='queued'", user_id, p["id"]):
                continue
            db.run("INSERT INTO probes(user_id,kind,kp_id,reason,from_kp,depth,priority,created_at) VALUES(?,'kp',?,?,?,?,?,?)",
                   user_id, p["id"], f"「{name}」没做对，查查它的基础", kp_id, depth + 1, 10 - depth, now)
            queued += 1
    return {"inferred": inferred, "queued": queued}


# ------------------------------------------------------------------ 英语旧单词闪测

def _list_hi(stage: str) -> int:
    """词表学段「G4-G5」取上限年级。"""
    return stage_rank(stage.split("-")[-1]) if stage else 0


def word_lists(user_id: int) -> list[dict]:
    """这个孩子要摸底的词表：词表声明给哪个学科（subject）或哪几套教材（packs）用，学到当前年级（含）为止。
    比如英语核心词给所有学英语的孩子，IGCSE 物理术语只给选了剑桥物理的孩子。"""
    from .content import content
    user = db.one("SELECT grade FROM users WHERE id=?", user_id)
    packs = {e["pack_id"] for e in _enrolls(user_id) if e["pack_id"] in catalog.packs}
    subjects = {catalog.packs[p].subject for p in packs}
    g = stage_rank(user["grade"] or catalog.default_grade)
    return [wl for wl in content.word_lists.values()
            if (wl.get("subject") in subjects or packs & set(wl.get("packs") or []))
            and stage_rank(wl["stage"].split("-")[0]) <= g]


def word_stats(user_id: int) -> dict:
    """每个词表测了几个、认识几个，估算词汇量（拉普拉斯平滑，测得越多越准）。"""
    rows = db.q("SELECT kp_id, ref, reason, result FROM probes WHERE user_id=? AND kind='word' AND status='done' ORDER BY id", user_id)
    # 每个词一个结果：认出来算认识；之后复查没写出来，就改回不认识（交叉验证）
    by_word: dict[tuple, int] = {}
    for r in rows:
        key = (r["kp_id"], r["ref"])
        if r["reason"] == "recheck":
            if key in by_word and not r["result"]:
                by_word[key] = 0
        else:
            by_word[key] = r["result"] or 0
    per: dict[str, list[int]] = {}
    for (lid, _), v in by_word.items():
        per.setdefault(lid, []).append(v)
    lists, est, tested = [], 0.0, 0
    for wl in word_lists(user_id):
        res = per.get(wl["id"], [])
        rate = (sum(res) + 1) / (len(res) + 2)
        est += rate * len(wl["words"])
        tested += len(res)
        lists.append({"id": wl["id"], "title": wl["title"], "count": len(wl["words"]), "tested": len(res),
                      "known": sum(res), "rate": round(100 * rate)})
    return {"lists": lists, "tested": tested, "estimate": int(round(est / 10.0) * 10) if tested >= 10 else None,
            "total": sum(len(wl["words"]) for wl in word_lists(user_id))}


def pick_words(user_id: int, n: int) -> list[dict]:
    """抽 n 个旧词做四选一：优先测得少、离当前年级近的词表。干扰项取同一词表里别的词义。"""
    lists = word_lists(user_id)
    if not lists or n <= 0:
        return []
    stats = {s["id"]: s for s in word_stats(user_id)["lists"]}
    have = {r["f"] for r in db.q("SELECT LOWER(front) AS f FROM cards WHERE user_id=?", user_id)}
    done = {r["ref"] for r in db.q("SELECT ref FROM probes WHERE user_id=? AND kind='word'", user_id)}
    top = max(_list_hi(wl["stage"]) for wl in lists)
    weight = lambda wl: (1 + 0.5 * (_list_hi(wl["stage"]) == top) + 0.3 * (_list_hi(wl["stage"]) >= top - 2)) \
        / (1 + stats[wl["id"]]["tested"])  # noqa: E731
    out = []
    # 交叉验证：三天前四选一认出来、还没复查过的词，抽一个换成「看中文写英文」
    since = (db.today() - timedelta(days=3)).isoformat()
    rechecked = {r["ref"] for r in db.q("SELECT ref FROM probes WHERE user_id=? AND kind='word' AND reason='recheck'", user_id)}
    known = [r for r in db.q("SELECT kp_id, ref FROM probes WHERE user_id=? AND kind='word' AND result=1 AND COALESCE(reason,'')<>'recheck' "
                             "AND done_at<? ORDER BY id DESC LIMIT 50", user_id, since) if r["ref"] not in rechecked]
    from .content import content
    for r in known:
        wl = content.word_lists.get(r["kp_id"])
        w = next((x for x in (wl or {}).get("words", []) if x["w"] == r["ref"]), None)
        if w and w.get("zh") and n > 1:
            out.append({"id": "w:" + w["w"], "type": "fill", "q": f"「{w['zh']}」用英文怎么写？",
                        "zh": f"{w.get('pos', '')} · {len(w['w'])} 个字母 · 首字母 {w['w'][0]}",
                        "word": {"w": w["w"], "list": wl["id"], "pos": w.get("pos", ""), "title": wl["title"], "recheck": 1}})
            break
    for _ in range(n - len(out)):
        wl = max(lists, key=lambda x: weight(x) + random.random() * 0.05)
        pool = [w for w in wl["words"] if w["w"].lower() not in have and w["w"] not in done and w.get("zh")]
        if len(pool) < 4:
            lists = [x for x in lists if x is not wl]
            if not lists:
                break
            continue
        w = random.choice(pool)
        done.add(w["w"])
        stats[wl["id"]]["tested"] += 1
        others = random.sample([x["zh"] for x in wl["words"] if x["zh"] != w["zh"]], 3)
        opts = others + [w["zh"]]
        random.shuffle(opts)
        out.append({"id": "w:" + w["w"], "type": "mcq", "q": f"{w['w']}  的意思是？", "options": opts,
                    "word": {"w": w["w"], "list": wl["id"], "pos": w.get("pos", ""), "title": wl["title"]}})
    return out


def answer_word(user_id: int, word: str, list_id: str, choice, dont_know=False, recheck=False) -> dict:
    """recheck：几天前四选一认出了这个词，这次看中文写英文（换一种题型交叉验证）。"""
    from .content import content
    wl = content.word_lists.get(list_id)
    w = next((x for x in (wl or {}).get("words", []) if x["w"] == word), None)
    if not w:
        return {"error": "没有这个词"}
    if recheck:
        ok = (not dont_know) and str(choice or "").strip().lower() == w["w"].lower()
    else:
        ok = (not dont_know) and str(choice or "").strip() == w["zh"]
    now = db.now()
    db.run("INSERT INTO probes(user_id,kind,kp_id,ref,reason,status,result,created_at,done_at) VALUES(?,'word',?,?,?,'done',?,?,?)",
           user_id, list_id, word, "recheck" if recheck else wl["title"], 1 if ok else 0, now, now)
    evidence.log(user_id, "word", f"{list_id}:{word}", "answer", correct=ok, mode="probe", fmt="recall" if recheck else "choice",
                 dont_know=dont_know)
    added = False
    if not ok:  # 不认识的词：明天开始进单词复习
        added = bool(engine.add_card(user_id, "word", w["w"], w["zh"], {"pos": w.get("pos", ""), "list": list_id, "probe": 1}))
    return {"correct": ok, "answer": w["w"] if recheck else w["zh"], "word": w["w"], "pos": w.get("pos", ""), "added": added}


# ------------------------------------------------------------------ 前方：接下来学什么、准备好了没有

def ahead(user_id: int, mastery: dict | None = None) -> list[dict]:
    """每个学科的「下一站」：进度之后的下一个知识点（没填进度就用可以预习的），
    以及它的必须前置准备好了几个、还差哪个。"""
    mastery = engine.get_mastery(user_id) if mastery is None else mastery
    taught = engine.taught_set(user_id)
    out = []
    for e in _enrolls(user_id):
        nxt = None
        if e["progress_kp"] and catalog.kp(e["progress_kp"]):
            nxt = engine.next_after(e["pack_id"], e["progress_kp"], mastery, taught, e["track"])
        if not nxt:
            cur = stage_rank(e["stage"])
            nxt = next((catalog.kps[k] for k in catalog.ids_for(e["pack_id"], e["track"])
                        if stage_rank(catalog.kps[k]["stage"]) in (cur, cur + 1) and k not in taught
                        and mastery.get(k, {}).get("status") in (None, "unknown")), None)
        if not nxt:
            continue
        reqs = catalog.prereqs(nxt["id"], required_only=True)
        ready = [p for p in reqs if p["id"] in taught or mastery.get(p["id"], {}).get("status") in ("mastered", "learning")]
        missing = [p for p in reqs if p not in ready]
        uses = [s["name"] for s in catalog.successors(nxt["id"])][:2]
        out.append({"pack": catalog.packs[e["pack_id"]], "kp": nxt, "ready": len(ready), "need": len(reqs),
                    "missing": missing[:2], "uses": uses})
    return out
