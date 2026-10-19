"""一口气（docs/DESIGN.md §5.36，讨论稿 docs/心流专注与单词概念-讨论稿.md）：把学习切成一段一段，
每段长度按这个人自己现在能撑住的时间来，撑住了才一点点加长。

- 档位按学段起步（小学 5 分钟、初中 10、高中 15、成人 15），第一周只量不加（MEASURE_DAYS）。
- 之后用「逐步提高标准」（changing-criterion，Hartmann & Hall 1976）：连续撑住 HOLD_UP 口 → 加一档；
  连续 FAIL_DOWN 口没撑住 → 退一档；掉线后提前收尾的那口气不加也不减。档位从历史一口一口重放出来，不单独存。
- 撑住 = 到了目标时间、中间没有离开超过 LEAVE_OK_MS。离开（切走 / 很久没动）每次记一笔（focus_leaves）。
- 走神不让孩子自己记，前端从作答里自己看（反应时忽快忽慢、太快、错后乱点、离开），服务器给它这个人
  平时的反应时（baseline）；「会的题突然错了」用 attempts.p_exp 事后算，给家长看。
"""
import math
import statistics
from datetime import date, datetime, timedelta

from . import db
from .catalog import is_adult, stage_rank

MIN = 60000
MEASURE_DAYS = 7      # 第一周只量不加
HOLD_UP = 3           # 连续撑住几口加一档
FAIL_DOWN = 2         # 连续几口没撑住退一档
FLOOR_MS = 3 * MIN    # 再退也不低于 3 分钟
LEAVE_OK_MS = 15000   # 15 秒内回来不算断（看一眼消息很正常）
STALE_MIN = 30        # 一口气 30 分钟没有消息就算走了
SLIP_P = 0.9          # 答之前估计答对概率至少这么高、却错了：「会的题突然错了」


def profile(grade: str | None) -> dict:
    """学段决定起步长度、上限、每次加多少、口气之间休息多久、休息时要不要跟着动一动。"""
    if is_adult(grade):
        p = dict(stage="adult", start=15, cap=45, step=2, rest=300, move=False)
    else:
        r = stage_rank(grade or "")
        if r <= 6:
            p = dict(stage="primary", start=5, cap=20, step=1, rest=120, move=True)
        elif r < 10 or r >= 99:
            p = dict(stage="junior", start=10, cap=25, step=2, rest=300, move=False)
        else:
            p = dict(stage="senior", start=15, cap=40, step=2, rest=300, move=False)
    return {**p, "start_ms": p["start"] * MIN, "cap_ms": p["cap"] * MIN, "step_ms": p["step"] * MIN}


def _counts(b: dict) -> bool:
    """没开始几秒就走了的（打开页面又去了乐园）不算一口气。"""
    return b["ended"] != "open" and not (b["ended"] == "quit" and b["active_ms"] < MIN)


def level_from(blocks: list[dict], prof: dict, today: date) -> dict:
    """按历史重放出现在的档位。blocks 按时间先后，每条要有 day / ended / held / active_ms。"""
    blocks = [b for b in blocks if _counts(b)]
    start, step, cap = prof["start_ms"], prof["step_ms"], prof["cap_ms"]
    if not blocks:
        return {"target_ms": start, "measuring": True, "days_left": MEASURE_DAYS}
    week_end = date.fromisoformat(blocks[0]["day"]) + timedelta(days=MEASURE_DAYS)
    if today < week_end:
        return {"target_ms": start, "measuring": True, "days_left": (week_end - today).days}
    week = [b for b in blocks if date.fromisoformat(b["day"]) < week_end]
    lvl = start
    if len(week) >= 3 and sum(1 for b in week if b["held"]) / len(week) < 0.5:
        lvl = max(FLOOR_MS, start - step)   # 第一周大多撑不住：起步就短一档
    ups = downs = 0
    for b in blocks[len(week):]:
        if b["ended"] == "early":
            ups = 0
            continue
        if b["held"]:
            ups, downs = ups + 1, 0
            if ups >= HOLD_UP:
                lvl, ups = min(cap, lvl + step), 0
        else:
            ups, downs = 0, downs + 1
            if downs >= FAIL_DOWN:
                lvl, downs = max(FLOOR_MS, lvl - step), 0
    return {"target_ms": lvl, "measuring": False, "days_left": 0}


def _blocks(user_id: int) -> list[dict]:
    return db.q("SELECT * FROM focus_blocks WHERE user_id=? ORDER BY id", user_id)


def level(user: dict) -> dict:
    return level_from(_blocks(user["id"]), profile(user["grade"]), db.today())


def baseline(user_id: int) -> dict | None:
    """这个人平时做一道题用多久（取对数后的中位数和离散程度）：前端拿来判断「忽快忽慢」，只跟自己比。"""
    rows = db.q("SELECT ms FROM attempts WHERE user_id=? AND ms IS NOT NULL AND mode NOT IN ('game','sprint') "
                "ORDER BY id DESC LIMIT 300", user_id)
    logs = [math.log(r["ms"]) for r in rows if r["ms"] and r["ms"] > 0]
    if len(logs) < 20:
        return None
    med = statistics.median(logs)
    mad = statistics.median(abs(x - med) for x in logs) * 1.4826
    return {"med": round(med, 3), "mad": round(max(mad, 0.25), 3)}


def _close_stale(user_id: int, keep: int | None = None):
    """还开着的旧一口气：超过 STALE_MIN 没消息、或者开了新的，都算走了。"""
    cut = (datetime.fromisoformat(db.now()) - timedelta(minutes=STALE_MIN)).isoformat(timespec="seconds")
    for b in db.q("SELECT id, updated_at FROM focus_blocks WHERE user_id=? AND ended='open'", user_id):
        if b["id"] != keep and (keep is not None or b["updated_at"] < cut):
            db.run("UPDATE focus_blocks SET ended='quit', held=0 WHERE id=?", b["id"])


def _public(b: dict, user: dict) -> dict:
    prof = profile(user["grade"])
    return {"id": b["id"], "target_ms": b["target_ms"], "active_ms": b["active_ms"], "away_ms": b["away_ms"],
            "longest_ms": b["longest_ms"], "n_items": b["n_items"], "n_right": b["n_right"], "segs": db.jload(b["segs"], []),
            "rest_s": prof["rest"], "move": prof["move"], "stage": prof["stage"], "baseline": baseline(user["id"])}


def start(user: dict, resume=None) -> dict:
    """开始（或接着）一口气。换页面时前端带着上一口气的 id 来，还开着就接着算。"""
    _close_stale(user["id"])
    if resume:
        b = db.one("SELECT * FROM focus_blocks WHERE id=? AND user_id=? AND ended='open'", _int(resume), user["id"])
        if b:
            return _public(b, user)
    _close_stale(user["id"], keep=-1)
    lv = level(user)
    now = db.now()
    bid = db.insert("INSERT INTO focus_blocks(user_id,day,target_ms,started_at,updated_at) VALUES(?,?,?,?,?)",
                    user["id"], db.today().isoformat(), lv["target_ms"], now, now)
    return _public(db.one("SELECT * FROM focus_blocks WHERE id=?", bid), user)


def _int(v, lo=0, hi=6 * 3600 * 1000) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def _segs(v) -> list:
    """[[毫秒, 1/0], ...]，相邻同状态合并，最多 200 段。"""
    out: list = []
    for s in (v if isinstance(v, list) else [])[:400]:
        if isinstance(s, (list, tuple)) and len(s) == 2:
            ms, on = _int(s[0], 0, 3600 * 1000), 1 if s[1] else 0
            if ms:
                if out and out[-1][1] == on:
                    out[-1][0] += ms
                else:
                    out.append([ms, on])
    return out[:200]


def sync(user: dict, body: dict) -> dict | None:
    b = db.one("SELECT * FROM focus_blocks WHERE id=? AND user_id=? AND ended='open'", _int(body.get("id")), user["id"])
    if not b:
        return None
    db.run("UPDATE focus_blocks SET active_ms=?, away_ms=?, longest_ms=?, n_items=?, n_right=?, segs=?, updated_at=? WHERE id=?",
           max(b["active_ms"], _int(body.get("active_ms"))), max(b["away_ms"], _int(body.get("away_ms"))),
           max(b["longest_ms"], _int(body.get("longest_ms"))), max(b["n_items"], _int(body.get("n_items"), 0, 10000)),
           max(b["n_right"], _int(body.get("n_right"), 0, 10000)), db.jdump(_segs(body.get("segs")) or db.jload(b["segs"], [])),
           db.now(), b["id"])
    return db.one("SELECT * FROM focus_blocks WHERE id=?", b["id"])


LEAVE_KINDS = ("away", "idle")
LEAVE_WHAT = ("after_wrong", "waiting", "reading", "item", "rest")


def leave(user: dict, body: dict):
    """离开一次：多久、是切走还是没动、当时在做什么。15 秒内的也记（家长页只数超过 15 秒的）。"""
    ms = _int(body.get("ms"))
    if ms < 3000:
        return
    bid = _int(body.get("block"))
    if not db.one("SELECT id FROM focus_blocks WHERE id=? AND user_id=?", bid, user["id"]):
        bid = None
    kind = body.get("kind") if body.get("kind") in LEAVE_KINDS else "away"
    what = body.get("what") if body.get("what") in LEAVE_WHAT else "item"
    db.run("INSERT INTO focus_leaves(user_id,block_id,day,ms,kind,what,task,created_at) VALUES(?,?,?,?,?,?,?,?)",
           user["id"], bid, db.today().isoformat(), ms, kind, what, str(body.get("task") or "")[:60], db.now())


def sentence(segs: list, adult: bool) -> str:
    """结束卡上的一句话：只说好的，不打分、不出百分比。"""
    total = sum(s[0] for s in segs)
    if not total or all(s[1] for s in segs):
        return "这一段一直在状态。" if adult else "这口气你一直在线 🔥"
    tail, acc = 0.4 * total, 0
    for ms, on in reversed(segs):
        if not on:
            break
        acc += ms
    if acc >= tail:
        return "后半段一直在状态。" if adult else "后半段你一直在线 🔥"
    best = max((ms for ms, on in segs if on), default=0)
    if best >= 2 * MIN:
        m = round(best / MIN)
        return f"最长连续专注 {m} 分钟。" if adult else f"中间有 {m} 分钟一口气没停 💪"
    return "这一段有些分心，下一段试试先把手机放远一点。" if adult else "坐下来就是开始，下一口气会更稳 🌱"


def end(user: dict, body: dict) -> dict | None:
    """一口气结束：target 到点 / early 掉线后提前收尾 / quit 没做完走了。返回结束卡要显示的东西。"""
    b = sync(user, body)
    if not b:
        return None
    before = level(user)
    ended = body.get("ended") if body.get("ended") in ("target", "early", "quit") else "quit"
    broke = db.one("SELECT COUNT(*) AS n FROM focus_leaves WHERE block_id=? AND ms>?", b["id"], LEAVE_OK_MS)["n"]
    held = ended == "target" and not broke and b["active_ms"] >= b["target_ms"] * 0.95
    db.run("UPDATE focus_blocks SET ended=?, held=?, updated_at=? WHERE id=?", ended, 1 if held else 0, db.now(), b["id"])
    after = level(user)
    adult = is_adult(user["grade"])
    segs = db.jload(b["segs"], [])
    m_after = round(after["target_ms"] / MIN)
    news = ""
    if not after["measuring"] and after["target_ms"] > before["target_ms"]:
        news = f"下一段加长到 {m_after} 分钟。" if adult else f"下一口气加长到 {m_after} 分钟 💪"
    elif not after["measuring"] and after["target_ms"] < before["target_ms"]:
        news = f"下一段先 {m_after} 分钟。" if adult else f"下一口气先 {m_after} 分钟，稳稳的"
    return {"held": held, "ended": ended, "minutes": max(1, round(b["active_ms"] / MIN)), "n_items": b["n_items"],
            "segs": segs, "sentence": sentence(segs, adult), "news": news, "next_ms": after["target_ms"]}


def _fmt_min(ms: int) -> str:
    m = ms / MIN
    return f" {m:.0f} " if m >= 1 else "不到 1 "


WHAT_LABEL = {"after_wrong": "刚答错", "waiting": "在等题", "reading": "在读的时候", "item": "在做题时", "rest": "休息时"}


def parent_summary(user: dict) -> dict | None:
    """家长页：现在每口气多长、今天撑住了几口、离开几次、在哪类内容上容易掉线、会的题突然错了几次。"""
    uid = user["id"]
    blocks = _blocks(uid)
    if not blocks:
        return None
    lv = level_from(blocks, profile(user["grade"]), db.today())
    day = db.today().isoformat()
    today = [b for b in blocks if b["day"] == day and _counts(b)]
    leaves = [x for x in db.q("SELECT * FROM focus_leaves WHERE user_id=? AND day=? AND ms>?", uid, day, LEAVE_OK_MS)]
    since = (db.today() - timedelta(days=6)).isoformat()
    by_task: dict[str, list] = {}
    for x in db.q("SELECT task, what FROM focus_leaves WHERE user_id=? AND day>=? AND ms>?", uid, since, LEAVE_OK_MS):
        by_task.setdefault(x["task"] or "其他", []).append(x["what"])
    where = sorted(by_task.items(), key=lambda kv: -len(kv[1]))[:2]
    slips = db.one("SELECT COUNT(*) AS n FROM attempts WHERE user_id=? AND correct=0 AND p_exp>=? AND created_at>=?",
                   uid, SLIP_P, day)["n"]
    week = [b for b in blocks if b["day"] >= since and _counts(b)]
    return {"target_min": round(lv["target_ms"] / MIN), "measuring": lv["measuring"], "days_left": lv["days_left"],
            "blocks": len(today), "held": sum(1 for b in today if b["held"]),
            "leaves": len(leaves), "away_min": _fmt_min(sum(x["ms"] for x in leaves)),
            "longest_min": _fmt_min(max((b["longest_ms"] for b in today), default=0)) if today else "",
            "week_blocks": len(week), "week_held": sum(1 for b in week if b["held"]), "slips": slips,
            "where": [{"task": t, "n": len(w), "what": WHAT_LABEL.get(max(set(w), key=w.count), "")} for t, w in where]}
