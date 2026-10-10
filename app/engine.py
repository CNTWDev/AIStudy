"""学习引擎：掌握度、间隔复习、诊断（向后回溯）、每日计划（补弱/回溯/复习/预习/阅读）。"""
import json
from datetime import date, timedelta

from . import bank, bankflow, db, evidence, itemtypes, llm
from . import streak as _streak
from .catalog import catalog, stage_rank

# ------------------------------------------------------------------ 掌握度

# 游戏和冲刺里的快题：答得快慢受玩法影响，不进题目的用时统计，也不参与「做得慢」的发现
QUICK_MODES = ("game", "sprint")
STATUS_LABEL = {"unknown": "未测", "weak": "薄弱", "learning": "学习中", "mastered": "已掌握"}


def get_mastery(user_id: int) -> dict[str, dict]:
    return {r["kp_id"]: dict(r) for r in db.q("SELECT * FROM mastery WHERE user_id=?", user_id)}


def update_mastery(user_id: int, kp_id: str, correct: bool, weight=1.0, source="practice", item=None, fmt=None,
                   dont_know=False, kind="answer") -> str:
    """一次作答证据进来：记进学习事件表，按这个孩子的学习方式重新估算掌握状态（见 app/evidence.py）。"""
    return evidence.record(user_id, kp_id, correct, item=item, mode=source, fmt=fmt, weight=weight, dont_know=dont_know, kind=kind)


def set_mastery(user_id: int, kp_id: str, score: float, status: str, source: str):
    """没有作答的推断（同一概念、前置、自评、导入）：只给一个概率，不会直接变成「掌握」。status 由模型按概率定。"""
    return evidence.infer(user_id, kp_id, score, source)


# ------------------------------------------------------------------ 间隔复习卡片

def add_card(user_id: int, kind: str, front: str, back: str, extra=None, kp_id=None, starred=0, due=None) -> int | None:
    front = front.strip()[:300]
    if not front:
        return None
    exist = db.one("SELECT id FROM cards WHERE user_id=? AND kind=? AND front=?", user_id, kind, front)
    if exist:
        if starred:
            db.run("UPDATE cards SET starred=1 WHERE id=?", exist["id"])
        return exist["id"]
    return db.insert(
        "INSERT INTO cards(user_id,kind,front,back,extra,kp_id,box,due,created_at,starred) VALUES(?,?,?,?,?,?,0,?,?,?)",
        user_id, kind, front, back, db.jdump(extra or {}), kp_id, (due or db.today() + timedelta(days=1)).isoformat(), db.now(), starred)


def review_card(user_id: int, card_id: int, grade: str):
    """grade: again(忘了) / hard(模糊) / good(记得)。下次复习按这个孩子学习方式的记忆模型排期
    （默认：记得的概率降到 85% 的那天）。只用于没法出题考的卡（意思还没查到的词等）；
    能出题的卡由 app/recall.py 出题、判对错，再调 schedule_card。"""
    c = db.one("SELECT * FROM cards WHERE id=? AND user_id=?", card_id, user_id)
    if not c:
        return None
    r = schedule_card(user_id, c, grade)
    if c["kp_id"]:
        update_mastery(user_id, c["kp_id"], grade != "again", weight={"again": 0.6, "hard": 0.3, "good": 0.6}[grade],
                       source="review", fmt="recall", kind="review")
    return r


def schedule_card(user_id: int, c, grade: str, due=None, extra=None, fmt="recall") -> dict:
    """按记忆模型排下次复习；due 给了就用它（比如错题重做错了，明天再做）。extra 给了就一起存。"""
    mem = evidence.profile(user_id).memory
    days = evidence.days_since(c["last_review"]) if c["last_review"] else 0
    s, d = mem.review(c["stability"] or 0, c["difficulty"] or 5, days, grade)
    lapses = c["lapses"] + (1 if grade == "again" else 0)
    if grade == "again":
        box, due = 0, due or db.today()  # 默认今天再来一次
    else:
        box = c["box"] + 1 if grade == "good" else max(c["box"], 1)
        due = due or db.today() + timedelta(days=mem.interval(s))
    due = due if isinstance(due, str) else due.isoformat()
    db.run("UPDATE cards SET box=?, due=?, lapses=?, reviews=reviews+1, last_review=?, stability=?, difficulty=?, extra=? WHERE id=?",
           box, due, lapses, db.now(), round(s, 3), round(d, 3),
           db.jdump(extra) if extra is not None else c["extra"], c["id"])
    evidence.log(user_id, "card", c["id"], "review", correct=grade != "again", fmt=fmt, data={"grade": grade, "kind": c["kind"]})
    return {"box": box, "due": due}


CARD_GROUPS = {"words": ("word", "phrase", "term"), "mistakes": ("mistake",), "other": ("kp",)}


def due_cards(user_id: int, limit=50, group: str | None = None):
    if group in CARD_GROUPS:
        kinds = CARD_GROUPS[group]
        marks = ",".join("?" * len(kinds))
        return db.q(f"SELECT * FROM cards WHERE user_id=? AND due<=? AND kind IN ({marks}) ORDER BY box, due LIMIT ?",
                    user_id, db.today().isoformat(), *kinds, limit)
    return db.q("SELECT * FROM cards WHERE user_id=? AND due<=? ORDER BY box, due LIMIT ?",
                user_id, db.today().isoformat(), limit)


# ------------------------------------------------------------------ 题库

def load_seed_items(seed_dir):
    """把 seed/items_*.json 导入题库（幂等）。"""
    n = 0
    for path in sorted(seed_dir.glob("items_*.json")):
        for it in json.loads(path.read_text(encoding="utf-8")):
            kps = it.get("kp") or []
            if not kps:
                continue
            data = {k: v for k, v in it.items() if k not in ("id", "kp", "kid", "difficulty", "type")}
            db.run("INSERT INTO items(id,kp_id,kp_ids,type,difficulty,data,source,created_at) VALUES(?,?,?,?,?,?,?,?) "
                   "ON CONFLICT(id) DO NOTHING",
                   it["id"], kps[0], db.jdump(kps), it["type"], it.get("difficulty", 2), db.jdump(data), "seed", db.now())
            n += 1
    return n


_item_row_to_dict = bank.row_to_item
save_items = bank.save_items


def items_for(user_id: int, kp_id: str, n=3, purpose="practice", grade="") -> list[dict]:
    """取题：先用题库里的（这个知识点的，加上别的教材里同一概念的），优先没做过的、卷库真题优先、难度从低到高；
    不够且配置了 AI 时现场出题，存进题库，以后别的孩子也能用。"""
    rows = bank.candidates(user_id, kp_id)
    fresh = [r for r in rows if r["done"] == 0 and not r["gdone"]]
    # 同型组里做过别的题（换了数字的同一道题）：排在新题后面；做错过的那组，先出同组没做过的，再出原题
    twins = [r for r in rows if r["done"] == 0 and r["gdone"]]
    redo = [r for r in rows if r["done"] > 0 and r["ok"] == 0]  # 做错过的题，换个时间再做
    # 交叉验证：优先没用过的题型（已经用选择题答对过，就先给填空 / 计算）
    m = db.one("SELECT evidence FROM mastery WHERE user_id=? AND kp_id=?", user_id, kp_id)
    seen_fmts = set(db.jload(m["evidence"], {}).get("fmts", [])) if m and m["evidence"] else set()
    # 卷库里管理员核对过的真题 / 名校卷 / 名师卷，比 AI 出的题优先
    pool = sorted(fresh, key=lambda r: (r["other"] or 0, r["source"] != "bank", itemtypes.fmt(r["type"]) in seen_fmts,
                                        bankflow.level(r))) + sorted(twins, key=bankflow.level) + redo
    if purpose == "diagnose":
        pool = sorted(rows, key=lambda r: (r["done"] > 0, r["other"] or 0, abs(r["difficulty"] - 2)))
    seen, uniq = set(), []
    for r in pool:  # 一次不出同一组的两道题
        if r["near_key"] and r["near_key"] in seen:
            continue
        seen.add(r["near_key"])
        uniq.append(r)
    picked = [{**bank.row_to_item(r), "kp_id": kp_id} for r in uniq[:n]]  # 共用的题，这次记在正在学的知识点上
    if len(picked) < n and llm.enabled():
        grade = grade or catalog.default_grade
        kp = catalog.kp(kp_id)
        pack = catalog.packs[catalog.kp_pack[kp_id]]
        try:
            new = llm.generate_items(kp, pack, grade, n=max(3, n - len(picked)), purpose=purpose, user_id=user_id)
            have = {p["id"] for p in picked} | bank.flagged_by(user_id)
            saved = [it for it in save_items(kp_id, new, purpose=purpose, grade=grade, meta=bank.gen_meta("items", purpose=purpose))
                     if it["id"] not in have]
            picked += saved[: n - len(picked)]
        except llm.LLMError:
            pass
    return picked[:n]


def check_answer(item: dict, answer) -> bool | None:
    return itemtypes.check(item, answer)


def answer_display(it: dict) -> str:
    return itemtypes.display(it)


def record_attempt(user_id: int, item: dict | None, kp_id: str, mode: str, correct: bool, answer="", dont_know=False,
                   touch=True, ms=None, weight=1.0):
    """dont_know=True：孩子点了「这道题还不会」。算一次没答对，但掌握度只轻微下调，并把讲解放进错题本。
    weight：这条证据的分量（游戏里限时作答记 0.5，见 app/arena/sources.py）。"""
    try:
        ms = int(ms) if ms and 500 <= int(ms) <= 30 * 60000 else None  # 做题用时（毫秒），太短/太长的不算
    except (TypeError, ValueError):
        ms = None
    db.run("INSERT INTO attempts(user_id,item_id,kp_id,mode,correct,answer,dont_know,ms,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
           user_id, item["id"] if item else None, kp_id, mode, 1 if correct else 0, str(answer)[:500],
           1 if dont_know else 0, ms, db.now())
    if item and item.get("id"):  # 游戏里限时作答，用时不算进题目的平均用时
        bank.record(item["id"], correct, dont_know, None if mode in QUICK_MODES else ms)
    # 诊断、摸底答对也只是一条证据：概率升高，要隔天换题再对才算掌握
    status = update_mastery(user_id, kp_id, correct, weight=weight, source=mode, item=item, dont_know=dont_know)
    if correct and status in ("mastered", "learning"):  # 别的教材里同一概念、还没测过的：推断为「学习中」
        infer_equivalents(user_id, kp_id)
    if item and not correct and _goes_to_mistakes(item, mode, dont_know, ms):
        # 错题自动进错题本（以卡片形式参与间隔复习）
        back = f"{itemtypes.display(item)}\n{item.get('explain', '')}"
        add_card(user_id, "mistake", item["q"], back.strip(), {"item_id": item["id"], "zh": item.get("zh", ""),
                 "my_answer": "（还不会）" if dont_know else str(answer)}, kp_id)
    if touch:
        _touch_day(user_id)
    return status


MISTAKE_MODES = ("practice", "diagnose", "probe", "paper", "exam", "review")
QUICK_GUESS_MS = 3000  # 游戏、冲刺里 3 秒内答错多半是手快蒙的，不进错题本


def _goes_to_mistakes(item: dict, mode: str, dont_know: bool, ms) -> bool:
    """哪些做错的题进错题本：正式练习、诊断、摸底、试卷、知识点回顾都进；
    游戏和冲刺里题库的题（不是现场生成的口算），认真想了还错、或点了不会的也进——以前这两处做错的从来回不来。"""
    if mode in MISTAKE_MODES:
        return True
    if mode in QUICK_MODES and (dont_know or (ms or 0) >= QUICK_GUESS_MS):
        return bool(item.get("id")) and bool(db.one("SELECT id FROM items WHERE id=?", item["id"]))
    return False


# ------------------------------------------------------------------ 跨教材融合（见 catalog 的 concepts / links）

def infer_equivalents(user_id: int, kp_id: str) -> int:
    """学会了一个知识点：其它教材里同一概念的知识点，还没测过的推断为「学习中」（换教材、转学不用从零开始）。"""
    n = 0
    for k in catalog.equivalents(kp_id):
        cur = db.one("SELECT status FROM mastery WHERE user_id=? AND kp_id=?", user_id, k["id"])
        if not cur or cur["status"] == "unknown":
            set_mastery(user_id, k["id"], 0.65, "learning", "concept")
            n += 1
    return n


BRIDGE_LABEL = {"same": "同一个知识点", "uses": "要用到", "used_by": "会用在", "language": "另一种语言", "context": "相关背景"}


def bridges(user_id: int, kp_id: str, mastery: dict | None = None, limit: int = 6) -> list[dict]:
    """学这个知识点时可以连起来的别的学科 / 别的教材内容：同一概念、用到的、背景、另一种语言。
    孩子已经会的排前面（讲解时拿来类比）；只在孩子自己教材里的才给链接。"""
    mastery = get_mastery(user_id) if mastery is None else mastery
    mine = {r["pack_id"] for r in db.q("SELECT pack_id FROM enrollments WHERE user_id=? AND active=1", user_id)}
    out = [{"kp": k, "type": "same", "note": ""} for k in catalog.equivalents(kp_id)]
    for r in catalog.related(kp_id):
        t = "used_by" if r["type"] == "uses" and r["dir"] == "in" else r["type"]
        out.append({"kp": r["kp"], "type": t, "note": r["note"]})
    for b in out:
        p = catalog.packs[b["kp"]["pack"]]
        b.update(label=BRIDGE_LABEL[b["type"]], subject=p.subject_name, edition=p.edition, mine=p.id in mine,
                 status=mastery.get(b["kp"]["id"], {}).get("status", "unknown"))
    out.sort(key=lambda b: (b["status"] not in ("mastered", "learning"), not b["mine"]))
    return out[:limit]


def cross_topics(user_id: int, lang: str, limit: int = 3) -> list[dict]:
    """语言阅读的跨学科话题：孩子最近在别的学科学过（学校教过或练过）的知识点。
    英语阅读拿物理、科学、历史的内容来读（用英语学内容）；中文阅读拿历史、道法、科学的内容来读。"""
    since = (db.today() - timedelta(days=21)).isoformat()
    recent = [r["kp_id"] for r in db.q(
        "SELECT kp_id, MAX(updated_at) AS t FROM mastery WHERE user_id=? AND updated_at>=? AND status IN ('learning','mastered') "
        "GROUP BY kp_id ORDER BY t DESC LIMIT 60", user_id, since)]
    recent += [r["kp_id"] for r in db.q("SELECT kp_id FROM kp_taught WHERE user_id=? ORDER BY marked_at DESC LIMIT 30", user_id)]
    out, seen = [], set()
    for k in recent:
        kp = catalog.kp(k)
        if not kp or k in seen:
            continue
        p = catalog.packs[kp["pack"]]
        if p.lang or (lang == "zh" and p.domain == "math"):  # 语言课本身不算「别的学科」
            continue
        seen.add(k)
        en = kp.get("name_en") or ""
        out.append({"kp": kp, "subject": p.subject_name,
                    "topic": (f"{p.subject_name}里学过的「{kp['name']}」{('(' + en + ')') if en else ''}：用英文讲讲它是什么、生活里在哪见到"
                              if lang == "en" else f"和{p.subject_name}里学过的「{kp['name']}」有关的故事或科普")})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ 诊断（向后回溯）

MAX_DIAG = 18
MAX_DEPTH = 3


def enrollment_stage(user_id: int, pack_id: str) -> str | None:
    r = db.one("SELECT stage FROM enrollments WHERE user_id=? AND pack_id=?", user_id, pack_id)
    return r["stage"] if r else None


def enroll_track(user_id: int, pack_id: str) -> str:
    """孩子在这套教材里选的方向（没选就是默认方向；没有方向的教材返回空）。"""
    r = db.one("SELECT track FROM enrollments WHERE user_id=? AND pack_id=?", user_id, pack_id)
    return catalog.packs[pack_id].track(r["track"] if r else "") if pack_id in catalog.packs else ""


def my_ids(user_id: int, pack_id: str) -> list[str]:
    """这个孩子在这套教材里要学的知识点（按他选的方向）。"""
    return catalog.ids_for(pack_id, enroll_track(user_id, pack_id))


def start_diagnosis(user_id: int, pack_id: str, stage: str) -> int:
    """从当前学段（及上一学段）里选核心知识点做探测；做错就沿「必须」前置往回查，最多 3 级。"""
    pack = catalog.packs[pack_id]
    idx = pack.stages.index(stage) if stage in pack.stages else len(pack.stages) - 1
    window = pack.stages[max(0, idx - 1): idx + 1]
    cands = [k for k in catalog.pack_kps(pack_id, track=enroll_track(user_id, pack_id)) if k["stage"] in window]
    has_items = {r["kp_id"] for r in db.q("SELECT DISTINCT x.kp_id FROM item_kps x JOIN items i ON i.id=x.item_id WHERE i.status='active'")}
    # 高频 > 有现成题 > 当前学段；每个板块至少一个
    cands.sort(key=lambda k: (not k.get("hot"), k["id"] not in has_items, k["stage"] != stage))
    queue, seen_strand = [], set()
    for k in cands:
        if k["strand"] not in seen_strand:
            queue.append(k["id"]); seen_strand.add(k["strand"])
    for k in cands:
        if len(queue) >= 8:
            break
        if k["id"] not in queue:
            queue.append(k["id"])
    state = {"queue": [{"kp": q, "depth": 0, "from": None} for q in queue], "done": [], "current": None}
    return db.insert("INSERT INTO diag_sessions(user_id,pack_id,state,created_at) VALUES(?,?,?,?)",
                  user_id, pack_id, db.jdump(state), db.now())


def diag_next(user_id: int, sid: int, grade: str):
    s = db.one("SELECT * FROM diag_sessions WHERE id=? AND user_id=?", sid, user_id)
    if not s or s["status"] != "running":
        return None
    state = db.jload(s["state"])
    if state.get("current"):
        return state["current"]
    while state["queue"] and len(state["done"]) < MAX_DIAG:
        nxt = state["queue"].pop(0)
        if any(d["kp"] == nxt["kp"] for d in state["done"]):
            continue
        items = items_for(user_id, nxt["kp"], n=1, purpose="diagnose", grade=grade)
        cur = {**nxt, "item": items[0] if items else None, "n": len(state["done"]) + 1}
        state["current"] = cur
        db.run("UPDATE diag_sessions SET state=? WHERE id=?", db.jdump(state), sid)
        return cur
    finish_diagnosis(user_id, sid, state)
    return None


def diag_answer(user_id: int, sid: int, answer, self_rating: str | None = None):
    s = db.one("SELECT * FROM diag_sessions WHERE id=? AND user_id=?", sid, user_id)
    state = db.jload(s["state"])
    cur = state.get("current")
    if not cur:
        return None
    item = cur.get("item")
    if item and item["type"] != "short":
        ok = check_answer(item, answer)
    else:  # 没有题或简答题：孩子自评
        ok = self_rating == "know"
    if item:
        record_attempt(user_id, item, cur["kp"], "diagnose", bool(ok), answer if answer is not None else self_rating)
    else:
        db.run("INSERT INTO attempts(user_id,item_id,kp_id,mode,correct,answer,created_at) VALUES(?,?,?,?,?,?,?)",
               user_id, None, cur["kp"], "diagnose-self", 1 if ok else 0, self_rating or "", db.now())
        if ok:
            set_mastery(user_id, cur["kp"], 0.7, "learning", "diagnose-self")
        else:
            update_mastery(user_id, cur["kp"], False, source="diagnose-self")
    state["done"].append({"kp": cur["kp"], "ok": bool(ok), "depth": cur["depth"], "from": cur["from"]})
    if not ok and cur["depth"] < MAX_DEPTH:
        for p in catalog.prereqs(cur["kp"], required_only=True):
            if not any(d["kp"] == p["id"] for d in state["done"]):
                state["queue"].insert(0, {"kp": p["id"], "depth": cur["depth"] + 1, "from": cur["kp"]})
    state["current"] = None
    db.run("UPDATE diag_sessions SET state=? WHERE id=?", db.jdump(state), sid)
    return {"ok": ok, "item": item}


def finish_diagnosis(user_id: int, sid: int, state=None):
    if state is None:
        state = db.jload(db.one("SELECT state FROM diag_sessions WHERE id=?", sid)["state"])
    # 答对的点，其「必须」前置视为大概率已掌握（未测过的才标）
    m = get_mastery(user_id)
    for d in state["done"]:
        if d["ok"]:
            for p, _ in catalog.ancestors(d["kp"], depth=2):
                if p["id"] not in m:
                    set_mastery(user_id, p["id"], 0.65, "learning", "inferred")
    state["current"] = None
    db.run("UPDATE diag_sessions SET state=?, status='done', finished_at=? WHERE id=?", db.jdump(state), db.now(), sid)


def diag_report(sid: int):
    s = db.one("SELECT * FROM diag_sessions WHERE id=?", sid)
    state = db.jload(s["state"])
    weak = [d for d in state["done"] if not d["ok"]]
    ok = [d for d in state["done"] if d["ok"]]
    # 根源：做错的点中，其必须前置都已通过（或没有前置）的——这是该先补的地方
    failed = {d["kp"] for d in weak}
    roots = [d for d in weak if not any(p["id"] in failed for p in catalog.prereqs(d["kp"], True))]
    return {"session": dict(s), "weak": weak, "ok": ok, "roots": roots}


# ------------------------------------------------------------------ 每日计划

def _touch_day(user_id: int, offline_minutes: int = 0):
    """确保今天有一行记录。学习时长由页面自动计时（beat），这里只加孩子登记的线下学习（读纸质书等）。"""
    day = db.today().isoformat()
    m = max(0, int(offline_minutes or 0))
    db.run("INSERT INTO days(user_id,day,plan,minutes,offline_minutes) VALUES(?,?,'[]',?,?) "
           "ON CONFLICT(user_id,day) DO UPDATE SET minutes=days.minutes+?, offline_minutes=days.offline_minutes+?",
           user_id, day, m, m, m, m)


BEAT_MAX = 75             # 一次心跳最多记 75 秒（前端每 30 秒报一次）
DAY_MAX = 14 * 3600       # 一天最多记 14 小时，防止异常数据


def beat(user_id: int, seconds) -> int:
    """页面自动计时：前端只在页面在前台、孩子最近有操作时累计秒数，每 30 秒报一次。返回今天的总分钟数。"""
    try:
        s = max(0, min(int(seconds or 0), BEAT_MAX))
    except (TypeError, ValueError):
        s = 0
    day, now = db.today().isoformat(), db.now()
    _touch_day(user_id)
    if s:
        db.run("UPDATE days SET active_seconds=active_seconds+?, first_at=COALESCE(first_at, ?), last_at=? WHERE user_id=? AND day=?",
               s, now, now, user_id, day)
        r = db.one("SELECT active_seconds, offline_minutes, minutes FROM days WHERE user_id=? AND day=?", user_id, day)
        sec = min(r["active_seconds"], DAY_MAX)
        auto = round(sec / 60) + (r["offline_minutes"] or 0)
        if auto > r["minutes"]:
            db.run("UPDATE days SET minutes=? WHERE user_id=? AND day=?", auto, user_id, day)
    return db.one("SELECT minutes FROM days WHERE user_id=? AND day=?", user_id, day)["minutes"]


def frontier(user_id: int, pack_id: str, stage: str, mastery: dict) -> list[dict]:
    """预习候选：前置都已掌握/学习中、自己还没学的知识点，限当前学段及下一学段。"""
    pack = catalog.packs[pack_id]
    idx = pack.stages.index(stage) if stage in pack.stages else 0
    window = set(pack.stages[idx: idx + 2])
    out = []
    for k in catalog.pack_kps(pack_id, track=enroll_track(user_id, pack_id)):
        if k["stage"] not in window or k["id"] in mastery and mastery[k["id"]]["status"] != "unknown":
            continue
        reqs = catalog.prereqs(k["id"], required_only=True)
        if all(mastery.get(p["id"], {}).get("status") in ("mastered", "learning") for p in reqs):
            out.append(k)
    out.sort(key=lambda k: (stage_rank(k["stage"]), not k.get("hot")))
    return out


# ------------------------------------------------------------------ 课程进度（学校学到哪了）

PROGRESS_STALE_DAYS = 7   # 最多一周问一次「这周学校学了啥」（不是每天的任务，可以跳过）


def taught_set(user_id: int) -> set[str]:
    return {r["kp_id"] for r in db.q("SELECT kp_id FROM kp_taught WHERE user_id=?", user_id)}


def set_progress(user_id: int, pack_id: str, current_kp: str | None, taught: list[str] | None = None,
                 stage: str | None = None) -> None:
    """孩子 / 家长更新进度：current_kp = 现在学校正在学的知识点；taught = 本学段里学校已经学过的。"""
    pack = catalog.packs[pack_id]
    now = db.now()
    with db.tx() as t:
        if stage and stage in pack.stages:
            t.run("UPDATE enrollments SET stage=? WHERE user_id=? AND pack_id=?", stage, user_id, pack_id)
        t.run("UPDATE enrollments SET progress_kp=?, progress_at=? WHERE user_id=? AND pack_id=?",
              current_kp if current_kp in pack.kp_ids else None, now, user_id, pack_id)
        if taught is not None:
            cur_stage = stage or (t.one("SELECT stage FROM enrollments WHERE user_id=? AND pack_id=?", user_id, pack_id) or {}).get("stage")
            stage_ids = [k for k in pack.kp_ids if catalog.kps[k]["stage"] == cur_stage]
            if stage_ids:
                marks = ",".join("?" * len(stage_ids))
                t.run(f"DELETE FROM kp_taught WHERE user_id=? AND kp_id IN ({marks})", user_id, *stage_ids)
            for k in set(taught) | ({current_kp} if current_kp else set()):
                if k in pack.kp_ids:
                    t.run("INSERT INTO kp_taught(user_id,kp_id,marked_at) VALUES(?,?,?) ON CONFLICT(user_id,kp_id) DO NOTHING",
                          user_id, k, now)


def set_progress_chapters(user_id: int, pack_id: str, current: str | None, done: list[str], stage: str | None = None) -> None:
    """按章报进度：current = 正在学的那一章，done = 学过的章。正在学的知识点 = 这一章里第一个还没掌握的。"""
    e = db.one("SELECT stage, track FROM enrollments WHERE user_id=? AND pack_id=?", user_id, pack_id)
    chs = catalog.chapters(pack_id, stage or e["stage"], e["track"])
    done_set = set(done or [])
    taught = [k for ch in chs if ch["key"] in done_set for k in ch["kps"]]
    cur_ch = next((ch for ch in chs if ch["key"] == current), None)
    cur_kp = None
    if cur_ch:
        m = get_mastery(user_id)
        cur_kp = next((k for k in cur_ch["kps"] if m.get(k, {}).get("status") != "mastered"), cur_ch["kps"][0])
    set_progress(user_id, pack_id, cur_kp, taught, stage)


def mark_taught(user_id: int, kp_ids) -> None:
    """学校教过了（比如考试卷里考到了）。"""
    now = db.now()
    for k in set(kp_ids):
        if catalog.kp(k):
            db.run("INSERT INTO kp_taught(user_id,kp_id,marked_at) VALUES(?,?,?) ON CONFLICT(user_id,kp_id) DO NOTHING", user_id, k, now)


def advance_progress(user_id: int, e, mastery: dict, taught: set) -> str | None:
    """进度自动往前走，不用孩子天天报：
    - 正在学的知识点掌握了 → 换成这一章里下一个还没掌握的；
    - 后面的章里已经有学校教过的内容（比如导入的考试卷考到了）→ 正在学的章往后挪到那一章。"""
    cur = e["progress_kp"]
    chs = catalog.chapters(e["pack_id"], e["stage"], e["track"])
    if not cur or not chs:
        return cur
    ci = next((i for i, ch in enumerate(chs) if cur in ch["kps"]), None)
    if ci is None:
        return cur
    li = max((i for i, ch in enumerate(chs) if taught & set(ch["kps"])), default=ci)
    new = cur
    if li > ci:
        mark_taught(user_id, [k for ch in chs[:li] for k in ch["kps"]])
        new = next((k for k in chs[li]["kps"] if mastery.get(k, {}).get("status") != "mastered"), chs[li]["kps"][0])
    elif mastery.get(cur, {}).get("status") == "mastered":
        new = next((k for k in chs[ci]["kps"] if mastery.get(k, {}).get("status") != "mastered"), cur)
    if new != cur:
        mark_taught(user_id, [new])
        db.run("UPDATE enrollments SET progress_kp=? WHERE user_id=? AND pack_id=?", new, user_id, e["pack_id"])
    return new


def progress_prompt(user: dict) -> list[str]:
    """今天页上方的小提示「这周学校学了啥？」：哪些课一周没更新了。不是任务，点「这周跳过」就一周不再问。"""
    snooze = (db.jload(user.get("settings"), {}) or {}).get("progress_snooze", "")
    if snooze and snooze > (db.today() - timedelta(days=PROGRESS_STALE_DAYS)).isoformat():
        return []
    out = []
    for e in db.q("SELECT * FROM enrollments WHERE user_id=?", user["id"]):
        pack = catalog.packs.get(e["pack_id"])
        if not pack or not pack.chapters or pack.system == "cert" or not catalog.chapters(pack.id, e["stage"], e["track"]):
            continue
        if not e["progress_at"] or e["progress_at"] < (db.today() - timedelta(days=PROGRESS_STALE_DAYS)).isoformat():
            out.append(pack.subject_name)
    return out


def snooze_progress(user_id: int) -> None:
    u = db.one("SELECT settings FROM users WHERE id=?", user_id)
    st = db.jload(u["settings"], {}) if u else {}
    st["progress_snooze"] = db.today().isoformat()
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), user_id)


def progress_view(user_id: int, e) -> dict:
    """一个教材包的进度概况：学过的（以前学段 + 本学段勾选的）、正在学的、接下来的。"""
    pack = catalog.packs[e["pack_id"]]
    ids = catalog.ids_for(pack.id, e["track"])
    taught = taught_set(user_id)
    cur = e["stage"]
    learned = [k for k in ids if stage_rank(catalog.kps[k]["stage"]) < stage_rank(cur) or k in taught]
    stale = not e["progress_at"] or e["progress_at"] < (db.today() - timedelta(days=PROGRESS_STALE_DAYS)).isoformat()
    return {"pack": pack, "stage": cur, "current": catalog.kp(e["progress_kp"]) if e["progress_kp"] else None,
            "learned": learned, "taught_here": [k for k in ids if catalog.kps[k]["stage"] == cur and k in taught],
            "stage_kps": [catalog.kps[k] for k in ids if catalog.kps[k]["stage"] == cur], "track": pack.track_name(e["track"]),
            "updated": e["progress_at"], "stale": stale, "chapters": _chapter_rows(e, taught)}


def _chapter_rows(e, taught: set) -> list[dict]:
    rows = []
    for ch in catalog.chapters(e["pack_id"], e["stage"], e["track"]):
        rows.append({**ch, "current": e["progress_kp"] in ch["kps"], "done": all(k in taught for k in ch["kps"])})
    return rows


def next_after(pack_id: str, kp_id: str, mastery: dict, taught: set, track: str | None = None) -> dict | None:
    """进度之后的下一个知识点：优先同一条线（strand）里、本学段或下一学段、前置都已学过的。"""
    pack = catalog.packs[pack_id]
    cur = catalog.kp(kp_id)
    if not cur:
        return None
    ids = catalog.ids_for(pack.id, track)
    i = ids.index(kp_id) if kp_id in ids else -1
    rank = stage_rank(cur["stage"])
    for k in ids[i + 1:] + ids[:i]:
        kp = catalog.kps[k]
        if kp.get("strand") != cur.get("strand") or k in taught or mastery.get(k, {}).get("status") not in (None, "unknown"):
            continue
        if not (rank <= stage_rank(kp["stage"]) <= rank + 1):
            continue
        if all(p["id"] in taught or mastery.get(p["id"], {}).get("status") in ("mastered", "learning")
               for p in catalog.prereqs(k, required_only=True)):
            return kp
    return None


# ------------------------------------------------------------------ 考试日期（按剩余天数排进度）

SPRINT_DAYS = 14  # 最后两周不再学新内容，留给冲刺：高频考点、薄弱点、错题


def set_exam_date(user_id: int, pack_id: str, day: str | None) -> bool:
    """设置 / 清除某门课的考试日期（YYYY-MM-DD；空 = 清除）。日期不合法返回 False。"""
    day = (day or "").strip()
    if day:
        try:
            day = date.fromisoformat(day).isoformat()
        except ValueError:
            return False
    db.run("UPDATE enrollments SET exam_date=? WHERE user_id=? AND pack_id=?", day, user_id, pack_id)
    return True


def exam_view(user_id: int, e, mastery: dict | None = None, taught: set | None = None) -> dict | None:
    """一门课离考试还有几天、还剩多少没学、每天要学几个。没有考试日期返回 None。
    已学 = 掌握度在「学习中 / 已掌握」，或者在「学习进度」里勾过学过的。"""
    if not e["exam_date"] or e["pack_id"] not in catalog.packs:
        return None
    try:
        exam = date.fromisoformat(e["exam_date"])
    except ValueError:
        return None
    mastery = get_mastery(user_id) if mastery is None else mastery
    taught = taught_set(user_id) if taught is None else taught
    ids = catalog.ids_for(e["pack_id"], e["track"])
    status = {k: mastery.get(k, {}).get("status") for k in ids}
    todo = [k for k in ids if status[k] not in ("learning", "mastered") and k not in taught]
    days_left = (exam - db.today()).days
    learn_days = days_left - SPRINT_DAYS
    return {"pack": catalog.packs[e["pack_id"]], "date": exam.isoformat(), "days_left": days_left,
            "total": len(ids), "learned": len(ids) - len(todo), "mastered": sum(v == "mastered" for v in status.values()),
            "todo": todo, "phase": "past" if days_left < 0 else "learn" if learn_days > 0 and todo else "sprint",
            "per_day": -(-len(todo) // learn_days) if learn_days > 0 and todo else 0}


# ------------------------------------------------------------------ 阅读 / 单词进度（tracks）

TRACK_KINDS = {"read_zh": "中文名著", "read_en": "英文阅读", "listen": "每天听书", "words": "每天新词"}


def tracks(user_id: int, active_only=True) -> list:
    sql = "SELECT * FROM tracks WHERE user_id=?" + (" AND active=1" if active_only else "") + " ORDER BY id"
    return db.q(sql, user_id)


def track_today(t) -> dict:
    """今天这一段读什么：返回 {from, to, label}。position = 已读完的单元数。"""
    units = db.jload(t["units"], [])
    start = t["position"] + 1
    end = start + max(1, t["daily_amount"]) - 1
    if t["total_units"]:
        end = min(end, t["total_units"])
    def name(n):
        if 0 < n <= len(units):
            u = units[n - 1]
            return u if isinstance(u, str) else u.get("title", f"第 {n} {t['unit_name']}")
        return f"第 {n} {t['unit_name']}"
    if t["total_units"] and start > t["total_units"]:
        return {"from": start, "to": start, "label": "已经读完啦，可以请家长换一本", "names": []}
    label = f"第 {start} {t['unit_name']}" if start == end else f"第 {start}–{end} {t['unit_name']}"
    return {"from": start, "to": end, "label": label, "names": [name(n) for n in range(start, end + 1)]}


def add_daily_words(user_id: int) -> int:
    """每天第一次排计划时，把词表里接下来的 N 个新词加进今天的复习。返回新加的个数。"""
    from .content import content
    today = db.today()
    added = 0
    for t in tracks(user_id):
        if t["kind"] != "words" or t["last_day"] == today.isoformat():
            continue
        wl = content.word_lists.get(t["ref"])
        words = wl["words"] if wl else []
        chunk = words[t["position"]: t["position"] + max(0, t["daily_amount"])]
        for w in chunk:
            if add_card(user_id, "word", w["w"], w.get("zh", ""), {"pos": w.get("pos", ""), "list": t["ref"], "new": 1},
                        due=today):
                added += 1
        pos = t["position"] + len(chunk)
        db.run("UPDATE tracks SET position=?, last_day=?, finished_at=? WHERE id=?", pos, today.isoformat(),
               db.now() if words and pos >= len(words) else None, t["id"])
    return added


def log_reading(user_id: int, track_id: int | None, *, to_pos=0, minutes=0, summary="", feeling="", pages="", title=""):
    t = db.one("SELECT * FROM tracks WHERE id=? AND user_id=?", track_id, user_id) if track_id else None
    from_pos = t["position"] + 1 if t else 0
    if t:
        to_pos = max(t["position"], min(int(to_pos or 0), t["total_units"] or 10_000))
        title = t["title"]
        db.run("UPDATE tracks SET position=?, finished_at=? WHERE id=?", to_pos,
               db.now() if t["total_units"] and to_pos >= t["total_units"] else None, t["id"])
    db.run("INSERT INTO reading_logs(user_id,track_id,day,title,from_pos,to_pos,pages,minutes,summary,feeling,created_at) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?)", user_id, track_id, db.today().isoformat(), title, from_pos, to_pos or 0,
           pages[:100], int(minutes or 0), summary[:500], feeling[:10], db.now())
    _touch_day(user_id, int(minutes or 0))  # 读书登记：多半是读纸质书，算线下学习时间
    if t:
        mark_task_by(user_id, type=t["kind"])


# ------------------------------------------------------------------ 每日任务

def build_plan(user_id: int) -> list[dict]:
    """一天的任务清单：来源和排法见 app/plan.py，排法由孩子的学习方式决定。"""
    from . import plan
    return plan.build_plan(user_id)


def today_plan(user_id: int, rebuild=False) -> dict:
    day = db.today().isoformat()
    row = db.one("SELECT * FROM days WHERE user_id=? AND day=?", user_id, day)
    plan = _upgrade_urls(db.jload(row["plan"], []) if row else [])
    if rebuild or not plan:
        old_done = [t for t in plan if t.get("done")]
        plan = build_plan(user_id)
        titles = {t["title"] for t in old_done}
        for t in plan:
            t["done"] = t["title"] in titles
        # 已经做完的任务即使新计划里没有了（比如进度更新后不再需要），也保留在今天的清单里
        kept = {t["title"] for t in plan}
        plan = [t for t in old_done if t["title"] not in kept] + plan
        db.run("INSERT INTO days(user_id,day,plan) VALUES(?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET plan=excluded.plan",
               user_id, day, db.jdump(plan))
        row = db.one("SELECT * FROM days WHERE user_id=? AND day=?", user_id, day)
    return {"day": day, "plan": plan, "minutes": row["minutes"], "checked_in": row["checked_in"], "reflection": row["reflection"]}


# 只有可能在线下完成的任务（读纸质书）才能手动打勾；其余任务做完自动打勾。
MANUAL_DONE = {"read_en", "read_zh"}


def mark_task(user_id: int, task_id: str, done=True, manual=False) -> bool:
    day = db.today().isoformat()
    row = db.one("SELECT plan FROM days WHERE user_id=? AND day=?", user_id, day)
    if not row:
        return
    plan = db.jload(row["plan"], [])
    hit = False
    for t in plan:
        if t["id"] == task_id and not (manual and t.get("type") not in MANUAL_DONE):
            t["done"] = done
            hit = True
    db.run("UPDATE days SET plan=? WHERE user_id=? AND day=?", db.jdump(plan), user_id, day)
    return hit


def mark_task_by(user_id: int, **match):
    day = db.today().isoformat()
    row = db.one("SELECT plan FROM days WHERE user_id=? AND day=?", user_id, day)
    if not row:
        return
    plan = db.jload(row["plan"], [])
    hit = []
    for t in plan:
        if all(t.get(k) == v for k, v in match.items()):
            t["done"] = True
            hit.append(t["id"])
    db.run("UPDATE days SET plan=? WHERE user_id=? AND day=?", db.jdump(plan), user_id, day)
    return hit


# ------------------------------------------------------------------ 一路做下去：今天的任务串成一条线（见 docs/DESIGN.md 5.16）

def plan_of_today(user_id: int) -> list[dict]:
    """只读今天已经排好的清单（不现场排计划）：每个页面都要用，必须便宜。"""
    row = db.one("SELECT plan FROM days WHERE user_id=? AND day=?", user_id, db.today().isoformat())
    return _upgrade_urls(db.jload(row["plan"], []) if row else [])


def _upgrade_urls(plan: list[dict]) -> list[dict]:
    """升级前排好的清单：每日阅读的入口改成 /reading/today（今天读过就直接打开那篇）。"""
    for t in plan:
        if (t.get("url") or "").startswith("/reading?lang="):
            t["url"] = t["url"].replace("/reading?", "/reading/today?", 1)
    return plan


def next_task(plan: list[dict], after: str | None = None) -> dict | None:
    """下一项：after 之后第一项没做的；后面都做完了，再从头找（先跳过的那项）。"""
    ids = [t["id"] for t in plan]
    start = ids.index(after) + 1 if after in ids else 0
    for t in plan[start:] + plan[:start]:
        if not t.get("done") and t["id"] != after:
            return t
    return None


def flow_state(user_id: int, current: str | None = None) -> dict:
    """做完一项（current）以后：今天做到哪了、下一项是什么、是不是刚好学完一节、是不是全做完了。"""
    plan = plan_of_today(user_id)
    done = sum(1 for t in plan if t.get("done"))
    nxt = next_task(plan, current)
    out = {"day": db.today().isoformat(), "done": done, "total": len(plan), "all_done": bool(plan) and done == len(plan),
           "next": None, "section": None, "just": any(t["id"] == current and t.get("done") for t in plan)}
    if nxt:
        out["next"] = {"id": nxt["id"], "n": plan.index(nxt) + 1, "title": nxt["title"], "why": nxt.get("why", ""),
                       "minutes": nxt.get("minutes"), "url": nxt["url"]}
    secs = _streak.sections(plan)
    out["secs_done"] = sum(1 for sc in secs if sc["complete"])
    idx = next((i for i, t in enumerate(plan) if t["id"] == current), None)
    sc = next((sc for sc in secs if idx in sc["tasks"]), None) if idx is not None else None
    # 刚好学完一节（而且前面几节也都完成了）：停一下、亮一格。先跳着做了后面的，不算「学完一节」
    if sc and sc["complete"] and len(secs) > 1 and not out["all_done"] and all(x["complete"] for x in secs[:sc["n"]]):
        out["section"] = {"n": sc["n"], "of": len(secs), "left": sum(1 for x in secs if not x["complete"]), "base": sc["n"] == 1}
    return out


def flow_match(plan: list[dict], path: str, query: dict, reading_lang: str | None = None) -> dict | None:
    """当前页面是今天清单里的哪一项（页面顶上的「今天 3/8」条用）。没做完的优先。"""
    def score(t):
        url = t.get("url") or ""
        tp, _, tq = url.partition("?")
        want = dict(x.split("=", 1) for x in tq.split("&") if "=" in x)
        if tp == path and all(query.get(k) == v for k, v in want.items()):
            return 3
        if reading_lang and t.get("type") == "read_" + reading_lang and not t.get("track"):  # AI 短文、今日新闻都在阅读器里读
            return 2
        for pre in ("/books/", "/papers/", "/track/"):
            if path.startswith(pre) and tp.startswith(pre) and path.split("/")[2] == tp.split("/")[2]:
                return 2
        return 0
    ranked = sorted(((score(t), not t.get("done"), -i, t) for i, t in enumerate(plan)), key=lambda x: x[:3], reverse=True)
    return ranked[0][3] if ranked and ranked[0][0] else None


# ------------------------------------------------------------------ 统计

def day_record(user_id: int, day: str) -> dict:
    """「一日记录」：这一天做了什么。"""
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    row = db.one("SELECT * FROM days WHERE user_id=? AND day=?", user_id, day)
    plan = db.jload(row["plan"], []) if row else []
    att = db.q("SELECT * FROM attempts WHERE user_id=? AND created_at>=? AND created_at<? ORDER BY id", user_id, day, nxt)
    return {
        "day": day, "plan": plan, "done": sum(1 for t in plan if t.get("done")),
        "minutes": row["minutes"] if row else 0, "checked_in": bool(row and row["checked_in"]),
        "reflection": row["reflection"] if row else "", "mood": (row["mood"] if row else "") or "",
        "attempts": [{**dict(a), "kp": catalog.kp(a["kp_id"])} for a in att],
        "right": sum(1 for a in att if a["correct"]), "dont_know": sum(1 for a in att if a["dont_know"]),
        "wrong": sum(1 for a in att if not a["correct"] and not a["dont_know"]),
        "reads": db.q("SELECT * FROM reading_logs WHERE user_id=? AND day=? ORDER BY id", user_id, day),
        "readings": db.q("SELECT id, title, lang, minutes FROM readings WHERE user_id=? AND finished_at>=? AND finished_at<?",
                         user_id, day, nxt),
        "new_cards": db.q("SELECT kind, front, back FROM cards WHERE user_id=? AND created_at>=? AND created_at<? ORDER BY id",
                          user_id, day, nxt),
        "lookups": db.q("SELECT query FROM lookups WHERE user_id=? AND created_at>=? AND created_at<? ORDER BY id",
                        user_id, day, nxt),
        "reviews": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND last_review>=? AND last_review<?",
                          user_id, day, nxt)["n"],
        "active_seconds": (row["active_seconds"] or 0) if row else 0, "offline_minutes": (row["offline_minutes"] or 0) if row else 0,
        "first_at": row["first_at"] if row else None, "last_at": row["last_at"] if row else None,
    }


def day_summary(rec: dict) -> dict:
    """自动生成「今天学了什么」：练过的知识点（会了 / 还要再练）、新收藏的词、读过的文章。不用孩子自己写。"""
    by_kp = {}
    for a in rec["attempts"]:
        if not a["kp"]:
            continue
        s = by_kp.setdefault(a["kp_id"], {"kp": a["kp"], "n": 0, "ok": 0})
        s["n"] += 1
        s["ok"] += 1 if a["correct"] else 0
    got = [s["kp"] for s in by_kp.values() if s["ok"] and s["ok"] * 2 >= s["n"]]
    todo = [s["kp"] for s in by_kp.values() if not (s["ok"] and s["ok"] * 2 >= s["n"])]
    words = [c["front"] for c in rec["new_cards"] if c["kind"] == "word"]  # 术语卡是选教材时自动加的，不算
    reads = [r["title"] for r in rec["readings"]] + [f"《{r['title']}》" for r in rec["reads"]]
    parts = []
    if got:
        parts.append(f"练会了「{'」「'.join(k['name'] for k in got[:3])}」" + (f"等 {len(got)} 个知识点" if len(got) > 3 else ""))
    if todo:
        parts.append(f"「{'」「'.join(k['name'] for k in todo[:2])}」还要再练练")
    if words:
        parts.append(f"收藏了 {len(words)} 个新词（{'、'.join(words[:4])}{'…' if len(words) > 4 else ''}）")
    if reads:
        parts.append(f"读了 {'、'.join(reads[:2])}")
    if rec["reviews"]:
        parts.append(f"复习了 {rec['reviews']} 张卡片")
    return {"got": got, "todo": todo, "words": words, "reads": reads,
            "text": "；".join(parts) + "。" if parts else ""}


STREAK_BADGES = [(3, "🌱", "坚持 3 天"), (7, "🌿", "坚持一周"), (14, "🌳", "坚持两周"), (30, "🏅", "坚持一个月"),
                 (60, "🏆", "坚持两个月"), (100, "👑", "坚持 100 天")]


def badges(n: int) -> dict:
    got = [b for b in STREAK_BADGES if n >= b[0]]
    nxt = next((b for b in STREAK_BADGES if n < b[0]), None)
    return {"got": got, "next": nxt, "to_next": (nxt[0] - n) if nxt else 0}


def total_stars(user_id: int) -> int:
    """⭐ = 完成的任务数（只看做没做，不看对错）+ 冲刺分换的星（每天每 10 分一颗，见 app/sprint.py）。"""
    from . import sprint
    n = 0
    for r in db.q("SELECT plan FROM days WHERE user_id=?", user_id):
        n += sum(1 for t in db.jload(r["plan"], []) if t.get("done"))
    return n + sum(sprint.stars_by_day(user_id).values())


def streak(user_id: int) -> int:
    """连续天数：每天做完保底（第 1 节）算一天，补签卡能补上漏掉的日子。规则在 app/streak.py。"""
    return _streak.streak(user_id)


def pack_summary(user_id: int, pack_id: str, mastery=None) -> dict:
    mastery = mastery if mastery is not None else get_mastery(user_id)
    ids = my_ids(user_id, pack_id)
    c = {"unknown": 0, "weak": 0, "learning": 0, "mastered": 0}
    for k in ids:
        c[mastery.get(k, {}).get("status", "unknown")] += 1
    c["total"] = len(ids)
    return c


def calendar(user_id: int, weeks=8, full_weeks=False) -> list[dict]:
    """每天的学习记录。full_weeks=True 时按周对齐：从 weeks 周前的周一到本周日，今天以后的日子标 future。"""
    today = db.today()
    if full_weeks:
        start = today - timedelta(days=today.weekday() + (weeks - 1) * 7)
    else:
        start = today - timedelta(days=weeks * 7 - 1)
    rows = {r["day"]: r for r in db.q("SELECT * FROM days WHERE user_id=? AND day>=?", user_id, start.isoformat())}
    frozen = _streak.frozen_days(user_id, start.isoformat())
    out = []
    for i in range(weeks * 7):
        dt = start + timedelta(days=i)
        d = dt.isoformat()
        r = rows.get(d)
        plan = db.jload(r["plan"], []) if r else []
        done = sum(1 for t in plan if t.get("done"))
        m = r["minutes"] if r else 0
        checked = bool(r and r["checked_in"])
        out.append({"day": d, "wd": "一二三四五六日"[dt.weekday()], "wi": dt.weekday(), "dn": dt.day, "month": dt.month,
                    "minutes": m, "checked": checked, "done": done, "total": len(plan),
                    "future": dt > today, "today": dt == today, "frozen": d in frozen,
                    "level": 4 if m >= 60 else 3 if m >= 40 else 2 if m >= 20 else 1 if (m > 0 or checked or done) else 0})
    return out


def seed_vocab(user_id: int, pack_id: str, seed_dir) -> int:
    """把 seed/vocab_*.json 里属于该教材包的术语卡加入孩子的复习卡片（幂等）。"""
    kp_ids = set(catalog.packs[pack_id].kp_ids)
    n = 0
    for path in sorted(seed_dir.glob("vocab_*.json")):
        for v in json.loads(path.read_text(encoding="utf-8")):
            if v.get("kp") in kp_ids:
                if add_card(user_id, "term", v["front"], v["back"], {"example": v.get("example", ""), "deck": v.get("deck", "")}, v["kp"]):
                    n += 1
    return n
