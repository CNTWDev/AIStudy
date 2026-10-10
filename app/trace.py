"""追根：一个知识点卡住了，当场找出「为什么不会」。

触发：同一个知识点今天错第二次，或者点了「这道题还不会」（错一次可能是粗心，不触发）；一天最多追两次。
做法：最多 4 道小题，像二分查找一样沿前置往回走——
  1. 先看错在哪：出题时 AI 给错误选项标了「选它说明哪个前置没懂」（items.data.traps），孩子选的那个错项直接指向最可疑的前置。
  2. 英文授课的理科（姐姐的物理）：先用中文问一次同一个知识点。中文会、英文不会，根就在术语，不用去补物理。
  3. 给候选前置打分（没测过 / 薄弱 / 只是推断的 / 快忘了的排前；必须前置、错项指向、好几个卡住的点共用的加分），
     测最可疑的：答对就排除它和它下面的整支，答错就把它当新的「卡点」再往下追一层。
  4. 结论两种都有用：「根在 X」→ 今天先补 X，再回来打原来的点；「基础都没问题」→ 是这个点本身没学会，换个讲法再学。
结论直接插进今天的任务清单（排在没做的任务最前面），家长在「系统发现」里能看到这条链。
"""
from datetime import timedelta

from . import bank, db, engine, evidence, explore, itemtypes
from .catalog import catalog

MAX_STEPS = 4
MAX_PER_DAY = 2
COUNT_MODES = ("practice", "diagnose", "exam", "paper")  # 这些模式里的错才算「卡住」；游戏、冲刺、摸底不算


def _today() -> str:
    return db.today().isoformat()


def _name(kp_id: str) -> str:
    kp = catalog.kp(kp_id)
    return kp["name"] if kp else kp_id


# ------------------------------------------------------------------ 嫌疑前置

def suspects(user_id: int, kp_id: str, mastery: dict | None = None, exclude: set | None = None, hint: str = "") -> list[dict]:
    """kp_id 卡住时，最可能是哪个前置没懂：[{kp, score, depth, why}]，最可疑的在前。"""
    m = engine.get_mastery(user_id) if mastery is None else mastery
    exclude = exclude or set()
    mem = evidence.profile(user_id).memory
    cands: dict[str, dict] = {}

    def add(p, depth, required, why):
        if p["id"] in exclude or p["id"] == kp_id:
            return
        c = cands.setdefault(p["id"], {"kp": p["id"], "depth": depth, "required": required, "why": why})
        c["depth"], c["required"] = min(c["depth"], depth), c["required"] or required

    for p, d in catalog.ancestors(kp_id, depth=3):
        add(p, d, True, f"「{_name(kp_id)}」要用到它")
    for p in catalog.prereqs(kp_id):
        add(p, 1, p["strength"] == "必须", f"「{_name(kp_id)}」要用到它")
    for r in catalog.related(kp_id, types=["uses"]):  # 跨学科：物理要用到的数学
        if r["dir"] == "out":
            add(r["kp"], 1, True, f"{catalog.packs[r['kp']['pack']].subject_name}里的知识，「{_name(kp_id)}」要用到")
    shaky = evidence.needs_work(user_id, m)
    out = []
    for c in cands.values():
        st = m.get(c["kp"], {})
        status, src = st.get("status"), st.get("source")
        if status in (None, "unknown"):
            s = 3.0
        elif status == "weak":
            s = 5.0
        elif src == "inferred":  # 只是推断会，没真测过
            s = 2.5
        elif status == "learning":
            s = 2.0
        else:  # 真掌握过：只有按遗忘规律快忘了才值得怀疑
            r = mem.recall(evidence.days_since(st.get("last_ev")), st.get("stability") or 0) if st.get("stability") else 1.0
            if r >= mem.target_r:
                continue
            s = 1.5
        s += 1.5 if c["required"] else 0
        s -= 0.8 * (c["depth"] - 1)
        if hint and c["kp"] == hint:
            s += 6
        elif hint and any(p["id"] == hint for p, _ in catalog.ancestors(c["kp"], depth=2)):
            s += 2  # 错项指向的点在它下面：先测它，顺着往下走
        s += min(3, sum(1 for k in shaky if k != kp_id and any(p["id"] == c["kp"] for p, _ in catalog.ancestors(k, depth=3))))
        out.append({**c, "score": round(s, 2)})
    out.sort(key=lambda c: -c["score"])
    return out


# ------------------------------------------------------------------ 要不要追

def offer(user_id: int, kp_id: str, dont_know: bool = False) -> dict | None:
    """答错 / 还不会之后调用：该追根了就返回 {kp, name}，前端显示「找找根在哪」按钮。"""
    if not catalog.kp(kp_id):
        return None
    day = _today()
    if db.one("SELECT id FROM traces WHERE user_id=? AND kp_id=? AND day=?", user_id, kp_id, day):
        return None
    if db.one("SELECT COUNT(*) AS n FROM traces WHERE user_id=? AND day=?", user_id, day)["n"] >= MAX_PER_DAY:
        return None
    if not dont_know:
        marks = ",".join("?" * len(COUNT_MODES))
        n = db.one(f"SELECT COUNT(*) AS n FROM attempts WHERE user_id=? AND kp_id=? AND correct=0 AND created_at>=? "
                   f"AND mode IN ({marks})", user_id, kp_id, day, *COUNT_MODES)["n"]
        if n < 2:
            return None
    if not suspects(user_id, kp_id):
        return None
    return {"kp": kp_id, "name": _name(kp_id)}


def start(user_id: int, kp_id: str, item_id: str = "", answer=None) -> int:
    hint = ""
    row = db.one("SELECT data FROM items WHERE id=?", item_id) if item_id else None
    if row and answer not in (None, ""):
        traps = db.jload(row["data"], {}).get("traps") or {}
        h = traps.get(str(answer)) if isinstance(traps, dict) else None
        if h and catalog.kp(h):
            hint = h
    return db.insert("INSERT INTO traces(user_id,kp_id,day,item_id,hint_kp,created_at) VALUES(?,?,?,?,?,?)",
                     user_id, kp_id, _today(), item_id or "", hint, db.now())


def get(user_id: int, tid: int):
    return db.one("SELECT * FROM traces WHERE id=? AND user_id=?", tid, user_id)


# ------------------------------------------------------------------ 一步一步

def _lang_item(user_id: int, t) -> dict | None:
    """英文授课的非英语学科、卡住的那道题没有中文翻译：找一道这个知识点带中文的题，用中文问一次。"""
    kp = catalog.kp(t["kp_id"])
    pack = catalog.packs[kp["pack"]]
    if pack.teach_lang != "en" or pack.subject == "english" or not t["item_id"]:
        return None
    orig = db.one("SELECT data FROM items WHERE id=?", t["item_id"])
    if not orig or db.jload(orig["data"], {}).get("zh"):
        return None  # 原题本来就有中文翻译，那就不是读不懂题干的问题
    rows = sorted(bank.candidates(user_id, t["kp_id"]), key=lambda r: r["done"])
    for r in rows:
        it = bank.row_to_item(r)
        if r["id"] != t["item_id"] and it.get("zh") and not itemtypes.of(it).self_rated:
            return {**it, "kp_id": t["kp_id"]}
    return None


def _state(t) -> tuple[list, str, set]:
    steps = db.jload(t["steps"], [])
    focus, exclude = t["kp_id"], set()
    for s in steps:
        if s.get("kind") != "kp":
            continue
        exclude.add(s["kp"])
        if s.get("correct"):
            exclude |= {p["id"] for p, _ in catalog.ancestors(s["kp"], depth=3)}
        elif not s.get("skip"):
            focus = s["kp"]
    return steps, focus, exclude


def _counted(steps) -> int:
    return sum(1 for s in steps if not s.get("skip"))


def next_step(user_id: int, tid: int, grade: str) -> dict:
    t = get(user_id, tid)
    if t["status"] == "done":
        return result(user_id, t)
    steps, focus, exclude = _state(t)
    if _counted(steps) >= MAX_STEPS:
        return finish(user_id, t)
    if not any(s.get("kind") == "lang" for s in steps):
        it = _lang_item(user_id, t)
        if it:
            q = {**itemtypes.public(it), "q": it["zh"], "zh": ""}
            return {"done": False, "step": {"kind": "lang", "kp": t["kp_id"], "name": _name(t["kp_id"]), "n": _counted(steps) + 1,
                                            "max": MAX_STEPS, "why": "同一个知识点，用中文问一次：看看是不是英文卡住了"}, "item": q}
        steps.append({"kind": "lang", "skip": True})
    m = engine.get_mastery(user_id)
    for c in suspects(user_id, focus, m, exclude | {t["kp_id"]}, t["hint_kp"])[:4]:
        items = engine.items_for(user_id, c["kp"], n=1, purpose="diagnose", grade=grade)
        if items:
            db.run("UPDATE traces SET steps=? WHERE id=?", db.jdump(steps), tid)
            kp = catalog.kps[c["kp"]]
            return {"done": False, "step": {"kind": "kp", "kp": c["kp"], "name": kp["name"], "n": _counted(steps) + 1,
                                            "max": MAX_STEPS, "why": c["why"],
                                            "subject": catalog.packs[kp["pack"]].subject_name},
                    "item": itemtypes.public({**items[0], "kp_id": c["kp"]})}
        steps.append({"kind": "kp", "kp": c["kp"], "skip": True})  # 没有题可出：跳过这个点
    db.run("UPDATE traces SET steps=? WHERE id=?", db.jdump(steps), tid)
    return finish(user_id, get(user_id, tid))


def answer(user_id: int, tid: int, body: dict) -> dict:
    t = get(user_id, tid)
    row = db.one("SELECT * FROM items WHERE id=?", body.get("item_id"))
    if not t or not row or t["status"] == "done":
        return {"error": "这一步已经结束了"}
    it = engine._item_row_to_dict(row)
    kind = "lang" if body.get("kind") == "lang" else "kp"
    kp_id = t["kp_id"] if kind == "lang" else body.get("kp")
    if not catalog.kp(kp_id):
        return {"error": "没有这个知识点"}
    dk = bool(body.get("dont_know"))
    if not dk and itemtypes.of(it).self_rated:
        if "self" not in body:
            return {"reveal": True, "answer": it.get("model", ""), "points": it.get("points", []), "explain": it.get("explain", "")}
        correct = body["self"] == "ok"
    else:
        correct = False if dk else bool(engine.check_answer(it, body.get("answer")))
    engine.record_attempt(user_id, it, kp_id, "probe", correct, body.get("answer", body.get("self", "")), dont_know=dk, ms=body.get("ms"))
    # 答对：顺带推断它下面的前置；答错：这里自己往下追，不再排进摸底队列（depth 记满）
    explore.after_probe(user_id, kp_id, correct, 0 if correct else explore.MAX_DEPTH, t["kp_id"])
    steps = db.jload(t["steps"], [])
    steps.append({"kind": kind, "kp": kp_id, "correct": correct})
    db.run("UPDATE traces SET steps=? WHERE id=?", db.jdump(steps), tid)
    out = {"correct": correct, "dont_know": dk, "answer": engine.answer_display(it), "explain": it.get("explain", "")}
    if (kind == "lang" and correct) or _counted(steps) >= MAX_STEPS:
        out["result"] = finish(user_id, get(user_id, tid))
    return out


# ------------------------------------------------------------------ 结论

def finish(user_id: int, t) -> dict:
    steps = db.jload(t["steps"], [])
    real = [s for s in steps if not s.get("skip")]
    wrong = [s["kp"] for s in real if s["kind"] == "kp" and not s["correct"]]
    if any(s["kind"] == "lang" and s["correct"] for s in real):
        res, root = "lang", ""
    elif wrong:
        res, root = "root", wrong[-1]  # 一路往下追，最后一个错的就是最深的根
    else:
        res, root = "self", ""
    db.run("UPDATE traces SET status='done', result=?, root_kp=?, done_at=? WHERE id=?", res, root, db.now(), t["id"])
    _apply(user_id, t, res, root)
    return result(user_id, get(user_id, t["id"]))


def _apply(user_id: int, t, res: str, root: str):
    if res == "lang":
        _terms_due(user_id, t["kp_id"])
    add_tasks(user_id, outcome_tasks(get(user_id, t["id"])))


def outcome_tasks(t, mastery: dict | None = None) -> list[dict]:
    """追根结论变成今天的任务。每日计划重排时也从这里来（app/plan.py 的 trace 来源），所以重排不会丢。"""
    kp_id, name, root = t["kp_id"], _name(t["kp_id"]), t["root_kp"]
    done = lambda k: (mastery or {}).get(k, {}).get("status") == "mastered"  # noqa: E731
    pid = f"tr{t['id']}"
    if t["result"] == "root":
        tasks = [{"type": "backfill", "kp": root, "for": kp_id, "title": f"追根：先补「{_name(root)}」",
                  "why": f"「{name}」卡住的根在这里", "minutes": 10, "url": f"/learn/{root}?task=backfill", "id": f"{pid}-0"},
                 {"type": "weak", "kp": kp_id, "title": f"再战：{name}", "why": f"「{_name(root)}」补好了，回来再做几题",
                  "minutes": 8, "url": f"/learn/{kp_id}?task=weak", "id": f"{pid}-1"}]
    elif t["result"] == "lang":
        tasks = [{"type": "words", "title": f"术语过关：{name}", "why": "中文会做、英文卡住了：先把这几个英文词弄熟",
                  "minutes": 5, "url": "/review?group=words", "id": f"{pid}-0"}]
    elif t["result"] == "self":
        tasks = [{"type": "weak", "kp": kp_id, "title": f"换个讲法再学：{name}", "why": "前置都没问题，是这个点本身还没学会",
                  "minutes": 10, "url": f"/learn/{kp_id}?task=weak", "id": f"{pid}-0"}]
    else:
        tasks = []
    return [{**x, "keep": True, "trace": True, "pack": catalog.kp_pack.get(x.get("kp") or kp_id)} for x in tasks
            if not (x.get("kp") and done(x["kp"]))]


def today_tasks(user_id: int, mastery: dict | None = None) -> list[dict]:
    return [x for t in db.q("SELECT * FROM traces WHERE user_id=? AND day=? AND status='done' ORDER BY id", user_id, _today())
            for x in outcome_tasks(t, mastery)]


def _terms_due(user_id: int, kp_id: str):
    """这个知识点的英文术语：已有术语卡的今天就复习；没有的从讲解里取（英文 = 中文）加成卡片。"""
    day = _today()
    n = db.run("UPDATE cards SET due=? WHERE user_id=? AND kp_id=? AND kind='term'", day, user_id, kp_id)
    if n:
        return
    kp = catalog.kp(kp_id)
    try:
        grade = (db.one("SELECT grade FROM users WHERE id=?", user_id) or {}).get("grade") or ""
        terms = bank.teach(kp, grade, user_id=user_id).get("terms") or []
    except Exception:  # noqa: BLE001  没配置 AI 等：退回知识点自带的关键词
        terms = []
    for x in terms[:6]:
        if isinstance(x, dict) and x.get("en") and x.get("zh"):
            engine.add_card(user_id, "term", x["en"], x["zh"], {"source": "追根"}, kp_id, due=db.today())
    if not terms:
        for w in (kp.get("terms") or [])[:6]:
            engine.add_card(user_id, "term", w, kp["name"], {"source": "追根"}, kp_id, due=db.today())


def add_tasks(user_id: int, tasks: list[dict]):
    """插进今天的清单：排在还没做的任务最前面（做完当前这项，下一项就是它）。同样的任务没做完就不重复加。"""
    plan = engine.today_plan(user_id)["plan"]
    have = {(t["type"], t.get("kp")) for t in plan if not t.get("done")}
    new = [{**t, "done": False} for t in tasks if (t["type"], t.get("kp")) not in have]
    if not new:
        return
    at = next((i for i, t in enumerate(plan) if not t.get("done")), len(plan))
    plan[at:at] = new
    db.run("UPDATE days SET plan=? WHERE user_id=? AND day=?", db.jdump(plan), user_id, _today())


def result(user_id: int, t) -> dict:
    steps = [s for s in db.jload(t["steps"], []) if not s.get("skip")]
    chain = [{"kp": s["kp"], "name": _name(s["kp"]), "correct": s["correct"], "kind": s["kind"]} for s in steps]
    name, root = _name(t["kp_id"]), t["root_kp"]
    msg = {"root": f"找到了：「{name}」卡住，根在「{_name(root)}」。先把它补上，再回来打「{name}」。",
           "lang": f"用中文你会做！「{name}」本身懂了，卡在英文术语上。先把这几个英文词弄熟。",
           "self": f"前面的基础都没问题，是「{name}」本身还没学会。换个讲法再学一遍。"}.get(t["result"], "")
    nxt = outcome_tasks(t) if t["day"] == _today() else []
    return {"done": True, "result": t["result"], "kp": t["kp_id"], "name": name,
            "root": {"kp": root, "name": _name(root)} if root else None, "chain": chain, "message": msg,
            "next": [{"title": x["title"], "url": x["url"]} for x in nxt]}


def recent(user_id: int, days: int = 14, limit: int = 5) -> list[dict]:
    """家长页、系统发现用：最近的追根结论。"""
    since = (db.today() - timedelta(days=days)).isoformat()
    rows = db.q("SELECT * FROM traces WHERE user_id=? AND status='done' AND day>=? ORDER BY id DESC LIMIT ?", user_id, since, limit)
    return [result(user_id, r) | {"day": r["day"]} for r in rows]
