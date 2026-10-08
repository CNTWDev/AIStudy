"""冲刺：做完今天的任务以后，题目不限量地往前冲，连续答对倍数越来越高（详见 docs/DESIGN.md 5.14）。

- 出题和乐园共用一条线（app/arena/sources.py）：数学口算、英语单词和术语（含自己的生词卡）、学过的知识点上的短选择题，
  都按孩子自己的水平出（目标答对率 80%），能力值 θ 和乐园共用，所以不会越刷越简单。
- 倍数（×1 → ×2 → ×3）看的是答对和专注：连对 5 题升到 ×2，再连对 7 题升到 ×3。
  答错只降一档、不清零；3 秒内答错（像在猜）回到 ×1；一题拖了一分钟以上（发呆、走开）也降一档；
  点「这题不会」不降档也不加分：诚实比瞎猜好。
- 每答对一题得「当前倍数」分（冲刺分）。冲刺分只在三个地方用：今天每 10 分换 1 颗 ⭐、乐园多玩几分钟（封顶）、每日目标。
  不能换东西，没有抽奖，也没有排行榜，只跟自己比（最高的一天是个人纪录）。
- 作答照常记进学习记录（mode = sprint），作为掌握度证据记 0.75（四选一短题，比正式练习轻一点）。
- 连续冲了 20 分钟提醒休息眼睛；今天一共学满 90 分钟时也提醒一次。冲多少都不影响明天的任务量。
"""
import random
import time

from . import db, engine
from .arena import fair, sources

TARGET = 0.8
SPRINT_WEIGHT = 0.75
TIER_AT = (0, 5, 12)        # 热度达到多少升到 ×1 / ×2 / ×3
FAST_MS = 3000
IDLE_MS = 60_000
PENDING_KEEP_S = 90
REST_AFTER_S = 20 * 60
LONG_DAY_MIN = 90
POINTS_PER_STAR = 10


class SprintError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def tier_of(heat: int) -> int:
    return sum(1 for t in TIER_AT if heat >= t)


def points_on(user_id: int, day: str) -> int:
    return (db.one("SELECT COALESCE(SUM(points),0) AS s FROM sprint_runs WHERE user_id=? AND day=?", user_id, day) or {}).get("s") or 0


def points_today(user_id: int) -> int:
    return points_on(user_id, db.today().isoformat())


def stars_by_day(user_id: int) -> dict[str, int]:
    """冲刺换来的 ⭐：每天的冲刺分 ÷ 10（取整）。"""
    return {r["day"]: (r["s"] or 0) // POINTS_PER_STAR
            for r in db.q("SELECT day, SUM(points) AS s FROM sprint_runs WHERE user_id=? GROUP BY day", user_id)}


def best_day(user_id: int, before: str | None = None) -> int:
    """个人纪录：冲刺分最多的一天（before 之前）。"""
    sql, args = "SELECT day, SUM(points) AS s FROM sprint_runs WHERE user_id=?", [user_id]
    if before:
        sql += " AND day<?"
        args.append(before)
    rows = db.q(sql + " GROUP BY day", *args)
    return max((r["s"] or 0 for r in rows), default=0)


def is_open(kid: dict) -> tuple[bool, int]:
    """冲刺什么时候开：今天的任务全做完（今天没有任务就直接开）。返回（开没开，还差几项）。"""
    plan = engine.today_plan(kid["id"])["plan"]
    left = sum(1 for t in plan if not t.get("done"))
    return left == 0, left


def _run(kid: dict, run_id) -> dict:
    r = db.one("SELECT * FROM sprint_runs WHERE id=? AND user_id=?", run_id, kid["id"])
    if not r:
        raise SprintError("找不到这次冲刺", 404)
    if r["ended_at"]:
        raise SprintError("这次冲刺已经结束了", 409)
    return {**dict(r), "state": db.jload(r["data"], {}) or {}}


def _save(run_id: int, st: dict, **cols):
    sets = ", ".join(f"{k}=?" for k in cols)
    db.run(f"UPDATE sprint_runs SET data=?{', ' + sets if sets else ''} WHERE id=?", db.jdump(st), *cols.values(), run_id)


def start(kid: dict) -> dict:
    ok, left = is_open(kid)
    if not ok:
        raise SprintError(f"先做完今天的任务（还差 {left} 项），就能开始冲刺！", 403)
    day = db.today().isoformat()
    for r in db.q("SELECT id FROM sprint_runs WHERE user_id=? AND ended_at IS NULL", kid["id"]):
        db.run("UPDATE sprint_runs SET ended_at=? WHERE id=?", db.now(), r["id"])
    rid = db.insert("INSERT INTO sprint_runs(user_id, day, started_at, data) VALUES(?,?,?,?)",
                    kid["id"], day, db.now(), db.jdump({"heat": 0, "t0": time.time()}))
    return {"run_id": rid, "points_today": points_today(kid["id"]), "best": best_day(kid["id"], day),
            "tier_at": TIER_AT}


def question(kid: dict, run_id, rng: random.Random | None = None) -> dict:
    r = _run(kid, run_id)
    st, now = r["state"], time.time()
    pend = st.get("pending")
    if not pend or now - pend["issued"] > PENDING_KEEP_S:
        pend = {"item": sources.pick(kid, "mix", TARGET, rng or random.Random()), "issued": now}
        st["pending"] = pend
        _save(r["id"], st)
    return {"item": sources.public(pend["item"])}


def answer(kid: dict, run_id, item_id: str, ans, dont_know=False) -> dict:
    r = _run(kid, run_id)
    st, now = r["state"], time.time()
    pend = st.get("pending")
    if not pend or pend["item"]["id"] != item_id:
        raise SprintError("这道题已经过期了，换一道", 409)
    it = pend["item"]
    ms = int((now - pend["issued"]) * 1000)
    correct = (not dont_know) and bool(sources.itemtypes.check(it, ans))
    sources.record(kid, it, correct, ans, dont_know, ms, mode="sprint", weight=SPRINT_WEIGHT)
    theta, n = fair.stored_ability(kid["id"], it["dim"]) or (it["theta"], 0)
    b, nb = fair.stored_b(it["family"], it["level"]) or (it["b"], 0)
    fair.update(kid["id"], it["dim"], it["family"], it["level"], correct, theta, n, b, nb)

    heat = st.get("heat", 0)
    before = tier_of(heat)
    drop = TIER_AT[max(0, before - 2)]      # 降一档后的热度
    gain, why = 0, ""
    if correct:
        if ms > IDLE_MS:
            heat, why = drop, "idle"
        gain = tier_of(heat)
        heat += 1
    elif not dont_know:
        if ms < FAST_MS:
            heat, why = 0, "guess"
        else:
            heat, why = drop, "wrong"
    tier = tier_of(heat)
    st["heat"] = heat
    st.pop("pending", None)
    points, answered = r["points"] + gain, r["answered"] + 1
    right_n, best_tier = r["right_n"] + (1 if correct else 0), max(r["best_tier"], tier)
    rest = False
    if not st.get("rested") and now - st.get("t0", now) >= REST_AFTER_S:
        st["rested"] = rest = True
    long_day = False
    if not st.get("long_day"):
        mins = (db.one("SELECT minutes FROM days WHERE user_id=? AND day=?", kid["id"], db.today().isoformat()) or {}).get("minutes") or 0
        if mins >= LONG_DAY_MIN:
            st["long_day"] = long_day = True
    _save(r["id"], st, points=points, answered=answered, right_n=right_n, best_tier=best_tier)
    today = points_today(kid["id"])
    nxt = next((t for t in TIER_AT if t > heat), None)
    return {"correct": correct, "dont_know": dont_know, "answer": sources.itemtypes.display(it),
            "explain": it.get("explain", ""), "gain": gain, "tier": tier, "tier_up": tier > before,
            "tier_down": tier < before, "why": why, "heat": heat, "to_next": (nxt - heat) if nxt else 0,
            "points": points, "points_today": today, "stars_today": today // POINTS_PER_STAR,
            "rest": rest, "long_day": long_day, "freeze_ms": 0}


def end(kid: dict, run_id) -> dict:
    r = _run(kid, run_id)
    st = r["state"]
    st.pop("pending", None)
    _save(r["id"], st, ended_at=db.now())
    day = db.today().isoformat()
    today, best = points_today(kid["id"]), best_day(kid["id"], day)
    return {"points": r["points"], "answered": r["answered"], "right": r["right_n"], "best_tier": r["best_tier"],
            "points_today": today, "stars_today": today // POINTS_PER_STAR, "best": best,
            "new_best": today > best > 0, "minutes": round((time.time() - st.get("t0", time.time())) / 60)}


def week(user_id: int, start: str, end: str) -> int:
    return (db.one("SELECT COALESCE(SUM(points),0) AS s FROM sprint_runs WHERE user_id=? AND day>=? AND day<?",
                   user_id, start, end) or {}).get("s") or 0
