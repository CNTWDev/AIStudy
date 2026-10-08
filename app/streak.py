"""坚持：把一天切成小节、保底、连续天数、补签卡、每日目标（详见 docs/DESIGN.md 5.13）。

- 小节：今天的任务按顺序切成 10–15 分钟一节（凑满 12 分钟或 3 项就成一节），学完一节停一下、亮一格。
- 保底：做完第 1 节就保住连续天数（今天没有任务时，学满 10 分钟也算）。状态不好的日子做完保底也算坚持。
- 补签卡：连续天数每满 7 天得一张，手里最多 2 张。哪天没达到保底，第二天打开「今天」时自动用卡补上，
  连续天数不断（补上的那天不加天数）。卡是坚持来的，不是抽来的。
- 每日目标：孩子自己选「轻松 / 认真 / 冲刺 / 超神」，分别是保底、全部任务、全部任务 + 冲刺分 50 / 150。

连续天数的规则从 RULE_FROM 开始按保底算；以前的日子沿用老规则（那天学过就算），不让已有的连续天数突然断掉。
"""
from datetime import date, timedelta

from . import db

RULE_FROM = "2026-10-08"
SECTION_MIN = 12          # 一节至少凑够多少分钟
SECTION_MAX_TASKS = 3     # 一节最多几项
BASE_MINUTES = 10         # 今天没有任务时，学满多少分钟算保底
FREEZE_EVERY = 7          # 连续天数每满几天得一张补签卡
FREEZE_MAX = 2            # 手里最多几张

GOALS = {
    # id: (名字, 说明, 需要的冲刺分；None = 只要保底)
    "base": ("轻松", "做完第 1 节（保底）", None),
    "all": ("认真", "做完今天全部任务", 0),
    "sprint": ("冲刺", "全部任务 + 冲刺 50 分", 50),
    "super": ("超神", "全部任务 + 冲刺 150 分", 150),
}
DEFAULT_GOAL = "all"


# ------------------------------------------------------------------ 小节和保底

def sections(plan: list[dict]) -> list[dict]:
    """把任务按顺序切成小节：每节凑满 SECTION_MIN 分钟或 SECTION_MAX_TASKS 项。最后剩得太少就并进上一节。"""
    out, cur, mins = [], [], 0
    for i, t in enumerate(plan):
        cur.append(i)
        mins += int(t.get("minutes") or 5)
        if mins >= SECTION_MIN or len(cur) >= SECTION_MAX_TASKS:
            out.append((cur, mins))
            cur, mins = [], 0
    if cur:
        if out and mins < SECTION_MIN / 2:
            out[-1] = (out[-1][0] + cur, out[-1][1] + mins)
        else:
            out.append((cur, mins))
    res = []
    for n, (idx, m) in enumerate(out, 1):
        done = sum(1 for i in idx if plan[i].get("done"))
        res.append({"n": n, "tasks": idx, "minutes": m, "done": done, "total": len(idx), "complete": done == len(idx)})
    return res


def base_done(plan: list[dict], minutes: int) -> bool:
    """今天的保底：做完第 1 节；今天没有任务时，学满 BASE_MINUTES 分钟。"""
    if not plan:
        return (minutes or 0) >= BASE_MINUTES
    return sections(plan)[0]["complete"]


def _day_counts(day: str, plan: list[dict], minutes: int, checked_in: bool) -> bool:
    if day < RULE_FROM:   # 老规则：那天学过就算
        return bool(checked_in or (minutes or 0) > 0)
    return base_done(plan, minutes)


def _days(user_id: int, since: str | None = None) -> dict[str, bool]:
    sql, args = "SELECT day, plan, minutes, checked_in FROM days WHERE user_id=?", [user_id]
    if since:
        sql += " AND day>=?"
        args.append(since)
    return {r["day"]: _day_counts(r["day"], db.jload(r["plan"], []), r["minutes"], bool(r["checked_in"]))
            for r in db.q(sql, *args)}


def _used(user_id: int) -> set[str]:
    return {r["day"] for r in db.q("SELECT day FROM streak_freezes WHERE user_id=? AND kind='used'", user_id)}


def cards(user_id: int) -> int:
    r = db.one("SELECT SUM(CASE WHEN kind='earned' THEN 1 ELSE 0 END) AS e, SUM(CASE WHEN kind='used' THEN 1 ELSE 0 END) AS u "
               "FROM streak_freezes WHERE user_id=?", user_id) or {}
    return max(0, (r.get("e") or 0) - (r.get("u") or 0))


def _walk(counted: dict[str, bool], used: set[str], today: date) -> tuple[int, str | None]:
    """从今天（今天还没达到保底就从昨天）往回数连续天数；补签卡补上的日子不断也不加。返回（天数，最近一个算数的日子）。"""
    d = today
    if not counted.get(d.isoformat()):
        d -= timedelta(days=1)
    n, last = 0, None
    while True:
        k = d.isoformat()
        if counted.get(k):
            n += 1
            last = last or k
        elif k not in used:
            break
        d -= timedelta(days=1)
    return n, last


def streak(user_id: int) -> int:
    return _walk(_days(user_id), _used(user_id), db.today())[0]


def settle(user_id: int) -> dict:
    """打开「今天」时结算：昨天（以及前几天）没达到保底、手里有卡，就自动用卡补上；连续天数满 7 天发卡。
    返回 {"used": [补上的日子], "earned": 这次新得的卡数}。可以重复调用，不会重复用卡、发卡。"""
    counted, used = _days(user_id), _used(user_id)
    today = db.today()
    gap, d = [], today - timedelta(days=1)
    while not counted.get(d.isoformat()) and d.isoformat() not in used and len(gap) <= FREEZE_MAX:
        gap.append(d.isoformat())
        d -= timedelta(days=1)
    out = {"used": [], "earned": 0}
    # 缺的天数手里的卡够补、而且缺口前面确实有连续天数要保，才用卡
    if gap and len(gap) <= cards(user_id) and _walk(counted, used, d)[0] > 0:
        for k in gap:
            db.run("INSERT INTO streak_freezes(user_id, day, kind, created_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                   user_id, k, "used", db.now())
        used |= set(gap)
        out["used"] = sorted(gap)
    n, last = _walk(counted, used, today)
    if n and n % FREEZE_EVERY == 0 and last and cards(user_id) < FREEZE_MAX:
        before = db.one("SELECT 1 AS x FROM streak_freezes WHERE user_id=? AND day=? AND kind='earned'", user_id, last)
        if not before:
            db.run("INSERT INTO streak_freezes(user_id, day, kind, created_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                   user_id, last, "earned", db.now())
            out["earned"] = 1
    return out


def frozen_days(user_id: int, since: str) -> set[str]:
    return {d for d in _used(user_id) if d >= since}


# ------------------------------------------------------------------ 每日目标

def goal(kid: dict) -> str:
    g = (db.jload(kid.get("settings"), {}) or {}).get("goal")
    return g if g in GOALS else DEFAULT_GOAL


def set_goal(kid_id: int, g: str) -> bool:
    if g not in GOALS:
        return False
    u = db.one("SELECT settings FROM users WHERE id=?", kid_id)
    st = db.jload(u["settings"], {}) if u else {}
    st["goal"] = g
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), kid_id)
    return True


def today_state(kid: dict, plan: list[dict], minutes: int, sprint_points: int) -> dict:
    """今天页要的：小节、保底、目标进度、补签卡。"""
    secs = sections(plan)
    done = sum(1 for t in plan if t.get("done"))
    all_done = bool(plan) and done == len(plan)
    g = goal(kid)
    name, desc, need = GOALS[g]
    base = base_done(plan, minutes)
    if need is None:
        reached = base
        if base:
            pct = 100
        elif secs:
            pct = round(100 * secs[0]["done"] / secs[0]["total"])
        else:
            pct = round(100 * (minutes or 0) / BASE_MINUTES)
    else:
        task_part = (done / len(plan)) if plan else 1
        reached = (all_done or not plan) and sprint_points >= need
        pct = round(100 * (task_part + min(1, sprint_points / need)) / 2) if need else round(100 * task_part)
    return {"sections": secs, "base": base, "all_done": all_done, "goal": g, "goal_name": name,
            "goal_desc": desc, "goal_need": need, "goal_reached": reached, "goal_pct": min(100, pct),
            "cards": cards(kid["id"]), "sprint_points": sprint_points,
            "sprint_open": all_done or not plan}
