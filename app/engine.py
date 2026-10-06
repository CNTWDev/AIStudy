"""学习引擎：掌握度、间隔复习、诊断（向后回溯）、每日计划（补弱/回溯/复习/预习/阅读）。"""
import json
import random
import uuid
from datetime import date, timedelta

from . import bank, db, llm
from .catalog import catalog, stage_rank

# ------------------------------------------------------------------ 掌握度

STATUS_LABEL = {"unknown": "未测", "weak": "薄弱", "learning": "学习中", "mastered": "已掌握"}


def _status(score: float, attempts: int, correct: int) -> str:
    if attempts == 0:
        return "unknown"
    if score >= 0.8 and correct >= 2:
        return "mastered"
    if score < 0.4:
        return "weak"
    return "learning"


def get_mastery(user_id: int) -> dict[str, dict]:
    return {r["kp_id"]: dict(r) for r in db.q("SELECT * FROM mastery WHERE user_id=?", user_id)}


def update_mastery(user_id: int, kp_id: str, correct: bool, weight=1.0, source="practice"):
    row = db.one("SELECT * FROM mastery WHERE user_id=? AND kp_id=?", user_id, kp_id)
    score = row["score"] if row else 0.3
    attempts = (row["attempts"] if row else 0) + 1
    ncorrect = (row["correct"] if row else 0) + (1 if correct else 0)
    if correct:
        score = score + (1 - score) * 0.35 * weight
    else:
        score = score - score * 0.45 * weight
    status = _status(score, attempts, ncorrect)
    db.run(
        "INSERT INTO mastery(user_id,kp_id,score,attempts,correct,status,source,updated_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id,kp_id) DO UPDATE SET score=excluded.score, attempts=excluded.attempts, "
        "correct=excluded.correct, status=excluded.status, source=excluded.source, updated_at=excluded.updated_at",
        user_id, kp_id, score, attempts, ncorrect, status, source, db.now())
    return status


def set_mastery(user_id: int, kp_id: str, score: float, status: str, source: str):
    db.run(
        "INSERT INTO mastery(user_id,kp_id,score,attempts,correct,status,source,updated_at) VALUES(?,?,?,1,?,?,?,?) "
        "ON CONFLICT(user_id,kp_id) DO UPDATE SET score=excluded.score, status=excluded.status, "
        "source=excluded.source, updated_at=excluded.updated_at",
        user_id, kp_id, score, 1 if status == "mastered" else 0, status, source, db.now())


# ------------------------------------------------------------------ 间隔复习卡片

INTERVALS = [1, 2, 4, 7, 15, 30, 60]


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
    """grade: again(忘了) / hard(模糊) / good(记得)"""
    c = db.one("SELECT * FROM cards WHERE id=? AND user_id=?", card_id, user_id)
    if not c:
        return None
    box = c["box"]
    if grade == "again":
        box, lapses = 0, c["lapses"] + 1
        due = db.today()  # 今天再来一次
    else:
        lapses = c["lapses"]
        box = box + 1 if grade == "good" else max(box, 1)
        due = db.today() + timedelta(days=INTERVALS[min(box, len(INTERVALS) - 1)] if grade == "good" else 1)
    db.run("UPDATE cards SET box=?, due=?, lapses=?, reviews=reviews+1, last_review=? WHERE id=?",
           box, due.isoformat(), lapses, db.now(), card_id)
    if c["kp_id"]:
        update_mastery(user_id, c["kp_id"], grade == "good", weight=0.5, source="review")
    return {"box": box, "due": due.isoformat()}


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


def items_for(user_id: int, kp_id: str, n=3, purpose="practice", grade="G3") -> list[dict]:
    """取题：先用题库里的（这个知识点的，加上别的教材里同一概念的），优先没做过的、难度从低到高；
    不够且配置了 AI 时现场出题，存进题库，以后别的孩子也能用。"""
    rows = bank.candidates(user_id, kp_id)
    fresh = [r for r in rows if r["done"] == 0]
    redo = [r for r in rows if r["done"] > 0 and r["ok"] == 0]  # 做错过的题，换个时间再做
    pool = sorted(fresh, key=lambda r: (r["other"] or 0, r["difficulty"])) + redo
    if purpose == "diagnose":
        pool = sorted(rows, key=lambda r: (r["done"] > 0, r["other"] or 0, abs(r["difficulty"] - 2)))
    picked = [{**bank.row_to_item(r), "kp_id": kp_id} for r in pool[:n]]  # 共用的题，这次记在正在学的知识点上
    if len(picked) < n and llm.enabled():
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
    t = item["type"]
    if t == "short":
        return None  # 自评
    if answer is None or str(answer).strip() == "":
        return False
    if t == "mcq":
        try:
            return int(answer) == int(item["answer"])
        except (TypeError, ValueError):
            return False
    if t == "num":
        try:
            val = float(str(answer).replace(",", "").split()[0])
            target = float(item["answer"])
        except (TypeError, ValueError, IndexError):
            return False
        tol = float(item.get("tol") or 0) or max(abs(target) * 0.01, 1e-9)
        return abs(val - target) <= tol + 1e-12
    if t == "fill":
        accepted = item["answer"] if isinstance(item["answer"], list) else [item["answer"]]
        norm = lambda s: "".join(str(s).lower().split()).strip("。.!！")
        return norm(answer) in {norm(a) for a in accepted}
    return False


def answer_display(it: dict) -> str:
    a = it.get("answer")
    if it["type"] == "mcq":
        try:
            return f"{'ABCD'[int(a)]}. {it['options'][int(a)]}"
        except (TypeError, ValueError, IndexError, KeyError):
            return str(a)
    if it["type"] == "short":
        return it.get("model", "")
    if isinstance(a, list):
        return " / ".join(map(str, a))
    return f"{a} {it.get('unit', '')}".strip()


def record_attempt(user_id: int, item: dict | None, kp_id: str, mode: str, correct: bool, answer="", dont_know=False,
                   touch=True, ms=None):
    """dont_know=True：孩子点了「这道题还不会」。算一次没答对，但掌握度只轻微下调，并把讲解放进错题本。"""
    try:
        ms = int(ms) if ms and 500 <= int(ms) <= 30 * 60000 else None  # 做题用时（毫秒），太短/太长的不算
    except (TypeError, ValueError):
        ms = None
    db.run("INSERT INTO attempts(user_id,item_id,kp_id,mode,correct,answer,dont_know,ms,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
           user_id, item["id"] if item else None, kp_id, mode, 1 if correct else 0, str(answer)[:500],
           1 if dont_know else 0, ms, db.now())
    if item and item.get("id"):
        bank.record(item["id"], correct, dont_know, ms)
    if mode in ("diagnose", "probe") and correct:  # 诊断 / 摸底答对：直接算掌握
        set_mastery(user_id, kp_id, 0.8, "mastered", mode)
        status = "mastered"
    else:
        status = update_mastery(user_id, kp_id, correct, weight=0.5 if dont_know else 1.0, source=mode)
    if status == "mastered":
        infer_equivalents(user_id, kp_id)
    if item and not correct and mode in ("practice", "diagnose", "probe", "paper", "exam"):
        # 错题自动进错题本（以卡片形式参与间隔复习）
        ans = item.get("answer")
        if item["type"] == "mcq":
            try:
                ans = item["options"][int(ans)]
            except (TypeError, ValueError, IndexError, KeyError):
                pass
        elif item["type"] == "short":
            ans = item.get("model", "")
        elif isinstance(ans, list):
            ans = " / ".join(map(str, ans))
        back = f"{ans}{(' ' + item.get('unit', '')) if item.get('unit') else ''}\n{item.get('explain', '')}"
        add_card(user_id, "mistake", item["q"], back.strip(), {"item_id": item["id"], "zh": item.get("zh", ""),
                 "my_answer": "（还不会）" if dont_know else str(answer)}, kp_id)
    if touch:
        _touch_day(user_id)
    return status


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
        if p.subject in ("english", "chinese") or (lang == "zh" and p.subject == "math"):
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

PROGRESS_STALE_DAYS = 14


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
            "updated": e["progress_at"], "stale": stale}


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


# ------------------------------------------------------------------ 阅读 / 单词进度（tracks）

TRACK_KINDS = {"read_zh": "中文名著", "read_en": "英文阅读", "words": "每天新词"}


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
    """一天的任务，按固定顺序从上到下做：（进度提醒）→ 单词 → 错题 → 英语阅读 → 中文阅读 →
    跟上学校 → 试卷订正 → 诊断 → 补弱 / 补前置 → 回顾小检查 → 预习 → 知识点回顾。
    单词、错题、阅读每天都有（坚持比做对更重要）；学知识点的任务按每天可用时间截断。"""
    user = db.one("SELECT * FROM users WHERE id=?", user_id)
    budget = user["daily_minutes"] or 60
    mastery = get_mastery(user_id)
    enrolls = db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", user_id)
    active_tracks = tracks(user_id)
    fixed: list[dict] = []

    new_words = add_daily_words(user_id)
    n_words = len(due_cards(user_id, 300, "words"))
    if n_words:
        title = f"单词：复习 {n_words} 个" + (f"（含新词 {new_words} 个）" if new_words else "")
        fixed.append({"type": "words", "title": title, "why": "记得点「记得」，忘了就点「忘了」，明天再来",
                      "minutes": min(15, 3 + n_words // 4), "url": "/review?group=words"})
    n_mis = len(due_cards(user_id, 300, "mistakes"))
    if n_mis:
        fixed.append({"type": "mistakes", "title": f"错题回顾 {min(n_mis, 8)} 道", "why": "先想再翻答案，想不起来也没关系",
                      "minutes": min(10, 2 + min(n_mis, 8)), "url": "/review?group=mistakes"})

    langs = {catalog.packs[e["pack_id"]].lang for e in enrolls if e["pack_id"] in catalog.packs} - {""}
    for kind, lang, label in (("read_en", "en", "英语阅读"), ("read_zh", "zh", "名著接着读")):
        tr = [t for t in active_tracks if t["kind"] == kind]
        if tr:
            t = tr[0]
            seg = track_today(t)
            fixed.append({"type": kind, "track": t["id"], "title": f"{label}：《{t['title']}》{seg['label']}",
                          "why": f"读 {t['daily_minutes']} 分钟，读完用一句话说说讲了什么",
                          "minutes": t["daily_minutes"], "url": f"/track/{t['id']}"})
        elif lang in langs:
            fixed.append({"type": kind, "lang": lang, "title": f"{'英文' if lang == 'en' else '中文'}阅读 15 分钟",
                          "why": "读一篇短文，不懂的词点一下就查，收藏后自动进单词复习", "minutes": 15,
                          "url": f"/reading?lang={lang}"})

    # 热身：几道小题，混着以前学过的知识点和旧单词——不知不觉中把过去摸清（见 explore.py）
    from . import explore
    has_probe = bool(explore.candidates(user_id, mastery)[:1])
    has_words = bool(explore.word_lists(user_id))
    if has_probe or has_words:
        n = (3 if has_probe else 0) + (2 if has_words else 0)
        fixed.insert(0, {"type": "warmup", "title": f"热身 {n} 题", "why": "先动动脑：混着以前学过的内容，答完系统更懂你",
                         "minutes": 4, "url": "/warmup"})

    weak_all, back_all, pre_all, diag_needed, sync_all, check_all = [], [], [], [], [], []
    taught = taught_set(user_id)
    stale_packs = []
    day_seed = db.today().toordinal()
    for e in enrolls:
        pack_id, stage = e["pack_id"], e["stage"]
        if pack_id not in catalog.packs:
            continue
        pv = progress_view(user_id, e)
        if pv["stale"]:
            stale_packs.append(catalog.packs[pack_id].subject_name)
        # 跟上学校：正在学的知识点没掌握，就练它；进度之后的下一个可以预习
        if e["progress_kp"] and catalog.kp(e["progress_kp"]):
            cur = catalog.kp(e["progress_kp"])
            if mastery.get(cur["id"], {}).get("status") != "mastered":
                sync_all.append({"type": "sync", "kp": cur["id"], "title": f"跟上学校：{cur['name']}",
                                 "why": "学校正在学这个，趁热练一练", "minutes": 12, "pack": pack_id})
            nxt = next_after(pack_id, cur["id"], mastery, taught, e["track"])
            if nxt:
                pre_all.append({"type": "preview", "kp": nxt["id"], "title": f"预习：{nxt['name']}",
                                "why": "学校马上要学，先看一眼", "minutes": 8, "pack": pack_id})
        # 往回巩固：学校学过、但系统里还没检测过的知识点，每天轮一个做个小检查
        unchecked = [k for k in pv["learned"] if mastery.get(k, {}).get("status") in (None, "unknown")
                     and stage_rank(catalog.kps[k]["stage"]) >= stage_rank(stage) - 2]
        if unchecked:
            k = catalog.kps[unchecked[day_seed % len(unchecked)]]
            check_all.append({"type": "check", "kp": k["id"], "title": f"回顾小检查：{k['name']}",
                              "why": f"{'学校学过' if k['id'] in taught else '以前学过'}，看看还记得吗（会就很快过）",
                              "minutes": 6, "pack": pack_id})
        diag = db.one("SELECT id FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='done'", user_id, pack_id)
        if not diag:
            diag_needed.append(pack_id)
        kp_ids = set(catalog.ids_for(pack_id, e["track"]))
        weak = [mastery[k] for k in kp_ids if k in mastery and
                (mastery[k]["status"] == "weak" or mastery[k]["status"] == "learning" and mastery[k]["score"] < 0.6)]
        weak.sort(key=lambda m: m["score"])
        for w in weak[:3]:
            kp = catalog.kp(w["kp_id"])
            gap = [p for p, _ in catalog.ancestors(w["kp_id"], depth=3)
                   if mastery.get(p["id"], {}).get("status") in (None, "weak", "unknown")]
            if gap:
                g = gap[0]
                back_all.append({"type": "backfill", "kp": g["id"], "for": w["kp_id"],
                                 "title": f"补前置：{g['name']}", "why": f"「{kp['name']}」要用到它", "minutes": 10, "pack": pack_id})
            else:
                weak_all.append({"type": "weak", "kp": w["kp_id"], "title": f"攻克：{kp['name']}",
                                 "why": f"掌握度 {int(w['score'] * 100)}%，{'刚学' if w['status'] == 'learning' else '薄弱'}",
                                 "minutes": 12, "pack": pack_id})
        fr = frontier(user_id, pack_id, stage, mastery)[:2] if diag and not e["progress_kp"] else []  # 诊断前不安排预习
        for k in fr:
            pre_all.append({"type": "preview", "kp": k["id"], "title": f"预习：{k['name']}",
                            "why": "前置已具备" + ("，高频考点" if k.get("hot") else ""), "minutes": 8, "pack": pack_id})

    # 自动发现的问题（根源前置、反复还不会、常问、做对但慢、久未复习……）排在同类任务最前面
    from . import insights
    auto = insights.plan_tasks(insights.refresh(user_id))
    auto_kps = {t["kp"] for lst in auto.values() for t in lst}
    back_all = auto["backfill"] + [t for t in back_all if t["kp"] not in auto_kps]
    weak_all = auto["weak"] + [t for t in weak_all if t["kp"] not in auto_kps]
    check_all = auto["check"] + [t for t in check_all if t["kp"] not in auto_kps]

    flex: list[dict] = []
    # 顺序：跟上学校 → 摸底诊断 → 补弱 / 补前置（交替）→ 回顾小检查 → 预习
    for t in sync_all[:2]:
        t["url"] = f"/learn/{t['kp']}?task=sync"
        flex.append(t)
    # 系统自动发现的最重要的一条，紧跟在「跟上学校」后面（不会因为时间不够被截掉）
    top = (auto["backfill"] + auto["weak"])[:1]
    for t in top:
        t["url"] = f"/learn/{t['kp']}?task={t['type']}"
        t["keep"] = True
        flex.append(t)
    back_all = [t for t in back_all if t not in top]
    weak_all = [t for t in weak_all if t not in top]
    # 导入了还没订正完的试卷
    for p in db.q("SELECT id, title FROM papers WHERE user_id=? AND status='ready' ORDER BY id LIMIT 1", user_id):
        flex.append({"type": "paper", "title": f"试卷订正：{p['title']}", "why": "把卷子上的题在线再做一遍，做完看诊断",
                     "minutes": 20, "url": f"/papers/{p['id']}"})
    for p in diag_needed[:1]:
        pk = catalog.packs[p]
        flex.append({"type": "diagnose", "pack": p, "title": f"{pk.subject_name}摸底诊断（约 10 分钟）",
                     "why": "先找到真正的薄弱点，计划才准", "minutes": 12, "url": f"/diagnose/{p}"})

    def interleave(*lists):
        out, i = [], 0
        while any(i < len(l) for l in lists):
            for l in lists:
                if i < len(l):
                    out.append(l[i])
            i += 1
        return out

    for t in interleave(weak_all, back_all) + check_all[:2] + pre_all[:1]:
        t["url"] = f"/learn/{t['kp']}?task={t['type']}"
        flex.append(t)
    n_other = len(due_cards(user_id, 100, "other"))
    if n_other:
        flex.append({"type": "review", "title": f"知识点回顾 {n_other} 张", "why": "间隔复习学过的知识点",
                     "minutes": min(10, 2 + n_other), "url": "/review?group=other"})

    if stale_packs:
        fixed.insert(0, {"type": "progress", "title": "更新一下学校进度（1 分钟）",
                         "why": f"{'、'.join(stale_packs[:3])}：告诉系统学校学到哪了，计划才跟得上", "minutes": 2, "url": "/progress"})
    out, used, n_flex = [], 0, 0
    for is_flex, t in [(False, t) for t in fixed] + [(True, t) for t in flex]:
        if is_flex and n_flex and not t.get("keep") and used + t["minutes"] > budget:
            continue
        n_flex += is_flex
        t["id"] = f"t{len(out)}"
        t["done"] = False
        out.append(t)
        used += t["minutes"]
    return out


def today_plan(user_id: int, rebuild=False) -> dict:
    day = db.today().isoformat()
    row = db.one("SELECT * FROM days WHERE user_id=? AND day=?", user_id, day)
    plan = db.jload(row["plan"], []) if row else []
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
    for t in plan:
        if all(t.get(k) == v for k, v in match.items()):
            t["done"] = True
    db.run("UPDATE days SET plan=? WHERE user_id=? AND day=?", db.jdump(plan), user_id, day)


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
    """⭐ = 完成的任务数（只看做没做，不看对错）。"""
    n = 0
    for r in db.q("SELECT plan FROM days WHERE user_id=?", user_id):
        n += sum(1 for t in db.jload(r["plan"], []) if t.get("done"))
    return n


def streak(user_id: int) -> int:
    days = {r["day"] for r in db.q("SELECT day FROM days WHERE user_id=? AND (checked_in=1 OR minutes>0)", user_id)}
    d, n = db.today(), 0
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


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
                    "future": dt > today, "today": dt == today,
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
