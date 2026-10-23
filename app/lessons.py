"""学过的课：学习者在学习页把一课走完（点「完成这一项」）记一笔（lessons 表，docs/DESIGN.md 5.46）。

成人学原则的课，「再见一次」不该是把讲解和案例再看一遍——再读的效果在各种学习方法里排得很低
（Dunlosky 等 2013），同一个案例看第二遍几乎没有增量。所以成人课：
- 学过的课不再当新课排：上次学完（或回来复习完）以后，按 REST_DAYS 隔几天才会再排进计划（plan.build_plan）；
- 回来时学习页换成「回访」：先不看讲解，用一两句话回想这一课说什么、什么时候不适用（提取练习）→ 对照一下
  → 一个新案例（题库里没做过的题，AI 出新题时避开已有的，见 engine.items_for）→ 用到自己这周的事上。
孩子的课照常：同一个点点了两次「还不会」时，要的正是把这一课从头学一遍（app/insights.py）。
"""
from datetime import date, timedelta

from . import db

REST_DAYS = (3, 7, 14, 30, 60)   # 第 n 次学完 / 回访完以后，隔几天才再排这一课


def done(user_id: int, kp_id: str) -> None:
    """一课走完：第一次记 first_done；以后每天最多算一次。"""
    today = db.today().isoformat()
    r = db.one("SELECT last_done FROM lessons WHERE user_id=? AND kp_id=?", user_id, kp_id)
    if not r:
        db.run("INSERT INTO lessons(user_id,kp_id,first_done,last_done,n) VALUES(?,?,?,?,1) ON CONFLICT(user_id,kp_id) DO NOTHING",
               user_id, kp_id, today, today)
    elif r["last_done"] != today:
        db.run("UPDATE lessons SET last_done=?, n=n+1 WHERE user_id=? AND kp_id=?", today, user_id, kp_id)


def learned(user_id: int) -> dict:
    """{kp_id: 那一行}：学过的课。"""
    return {r["kp_id"]: dict(r) for r in db.q("SELECT * FROM lessons WHERE user_id=?", user_id)}


def revisit(user_id: int, kp_id: str) -> bool:
    """这一课以前（今天以前）学完过：再打开就是回访。"""
    r = db.one("SELECT first_done FROM lessons WHERE user_id=? AND kp_id=?", user_id, kp_id)
    return bool(r) and r["first_done"] < db.today().isoformat()


def next_on(r) -> date:
    return date.fromisoformat(r["last_done"]) + timedelta(days=REST_DAYS[min(r["n"], len(REST_DAYS)) - 1])


def resting(user_id: int) -> set:
    """学完不久、还没到再来的日子的课：今天不排。"""
    today = db.today()
    return {k for k, r in learned(user_id).items() if next_on(r) > today}
