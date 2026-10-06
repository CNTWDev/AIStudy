"""掌握判定：多证据交叉验证 + 遗忘模型。

一道题答对不等于懂了，答错一次也不等于不懂。这里用三样有研究支撑的东西来判断：

1. 贝叶斯知识追踪（BKT，Corbett & Anderson 1994，智能辅导系统里常用）：
   不直接给分，而是估算「真懂的概率」。每次作答按题目的「蒙对概率」（四选一是 25%）和
   「会也做错的概率」（粗心）更新：选择题答对只加一点，换几道不同的题都答对，概率才会高；
   粗心错一次也不会一下子掉到底。
2. 遗忘模型（FSRS 的记忆公式，Anki 等复习软件在用）：每个知识点、每张卡片有一个「记忆稳定性」S（天），
   现在还记得的概率 R = (1 + t / 9S)^-1，t 是距上次作答的天数。隔得越久、R 越低时答对，S 涨得越多
   （间隔效应）；当天反复答对，S 几乎不涨。R 掉到 85% 左右就该复习。
3. 交叉验证的掌握标准：真懂的概率 ≥ 85%，而且
   - 至少在两天里答对过（隔天还会，不是刚看完答案的短时记忆），
   - 答对过至少两道不同的题，
   - 至少两种题型（比如选择 + 填空 / 计算 / 回忆卡片）；只有一种题型可用时，换三道不同的题也行。
   「薄弱」也要证据：不同的题错过两次以上、或者自己点了「还不会」，才算薄弱。

题目难度（来自题库里所有孩子的作答统计）也算进去：大家都做对的题，答对说明不了多少；
大家都做错的题，做错也不太说明问题。
"""
import json
import math
from datetime import datetime

from . import db

TARGET_R = 0.85      # 记得的概率低于它就安排复习
MASTER_P = 0.85      # 「掌握」要求的真懂概率
WEAK_P = 0.35
PRIOR = 0.3          # 没有任何证据时，默认懂的概率
LEARN_T = 0.12       # 练习 / 复习时，做完一题本身带来的学习（BKT 的 transit）
SLIP = 0.1           # 会也做错（粗心）的概率

FMT = {"mcq": "choice", "fill": "recall", "num": "calc", "short": "explain"}


# ------------------------------------------------------------------ 遗忘模型（FSRS 记忆公式，参数按孩子取得保守些）

def recall(days: float, stability: float) -> float:
    """隔了 days 天，现在还记得的概率。stability 为 0 表示还没有记忆数据，按 1 处理（不衰减）。"""
    if not stability or days <= 0:
        return 1.0
    return (1 + days / (9 * stability)) ** -1


def interval(stability: float, target: float = TARGET_R) -> int:
    """记得的概率降到 target 要多少天：下一次复习排在那时。"""
    return max(1, int(round(9 * stability * (1 / target - 1))))


def init_state(grade: str) -> tuple[float, float]:
    """第一次作答后的 (稳定性, 难度)。grade：again 忘了 / hard 模糊 / good 记得。"""
    return {"again": (0.4, 7.0), "hard": (1.2, 6.0), "good": (3.0, 5.0)}[grade]


def next_state(s: float, d: float, r: float, grade: str) -> tuple[float, float]:
    """作答后新的 (稳定性, 难度)。r 是作答前「还记得的概率」。"""
    g = {"again": 1, "hard": 2, "good": 3}[grade]
    d = min(10.0, max(1.0, 0.9 * (d - 0.8 * (g - 3)) + 0.1 * 5))
    if grade == "again":  # 忘了：稳定性回落，但比从零开始高一点（学过的东西重学更快）
        s = min(s, 1.9 * d ** -0.1 * ((s + 1) ** 0.3 - 1) * math.exp(1.5 * (1 - r)))
        return max(0.3, s), d
    grow = math.exp(1.2) * (11 - d) * s ** -0.1 * (math.exp(1 - r) - 1)
    return s * (1 + grow * (0.5 if grade == "hard" else 1.0)), d


def days_since(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        t = datetime.fromisoformat(ts[:19])
    except ValueError:
        return 0.0
    return max(0.0, (datetime.fromisoformat(db.now()[:19]) - t).total_seconds() / 86400)


# ------------------------------------------------------------------ 贝叶斯知识追踪

def item_params(item: dict | None, fmt: str) -> tuple[float, float]:
    """这道题的（蒙对概率, 粗心概率）。四选一蒙对 25%；填空、计算几乎蒙不对；自己判的题和回忆卡片介于中间。
    题库统计：几乎人人做对的题，答对说明得少；大多数人做错的题，做错说明得少。"""
    if item and item.get("type") == "mcq":
        guess = 1 / max(2, len(item.get("options") or []) or 4)
    elif fmt in ("calc", "recall") and item:
        guess = 0.05
    else:
        guess = 0.15  # 自评题、回忆卡片（自己说记得）
    slip = SLIP
    st = None
    if item and item.get("id"):
        st = db.one("SELECT n_attempts, n_correct FROM items WHERE id=?", item["id"])
    if st and st["n_attempts"] >= 5:
        acc = st["n_correct"] / st["n_attempts"]
        if acc > 0.8:
            guess = max(guess, min(0.5, (acc - 0.8) * 2.5))
        elif acc < 0.4:
            slip = min(0.3, SLIP + (0.4 - acc) * 0.5)
    return guess, slip


def posterior(p: float, correct: bool, guess: float, slip: float) -> float:
    if correct:
        return p * (1 - slip) / (p * (1 - slip) + (1 - p) * guess)
    return p * slip / (p * slip + (1 - p) * (1 - guess))


# ------------------------------------------------------------------ 证据和状态

def _ev(row) -> dict:
    ev = db.jload(row["evidence"], None) if row and row.get("evidence") else None
    return ev or {"days": [], "items": [], "fmts": [], "wrong": [], "dk": 0}


def judge(p: float, ev: dict) -> str:
    """按交叉验证标准给状态。"""
    days, items, fmts = len(set(ev["days"])), len(set(ev["items"])), len(set(ev["fmts"]))
    if p >= MASTER_P and days >= 2 and items >= 2 and (fmts >= 2 or items >= 3):
        return "mastered"
    if p < WEAK_P and (len(set(ev["wrong"])) >= 2 or ev.get("dk")):
        return "weak"
    return "learning"


def missing(m: dict | None) -> list[str]:
    """离「掌握」还差哪些证据（给孩子和家长看）。"""
    if not m:
        return ["还没做过题"]
    ev, p = _ev(m), now_p(m)
    out = []
    if len(set(ev["items"])) < 2:
        out.append("再答对一道不同的题")
    if len(set(ev["days"])) < 2:
        out.append("隔天再答对一次")
    if len(set(ev["fmts"])) < 2 and len(set(ev["items"])) < 3:
        out.append("换一种题型答对")
    if p < MASTER_P and not out:
        out.append("再多答对几次")
    return out


def explain(m: dict | None) -> dict | None:
    """学习页上给孩子看的一行：真懂的概率、现在还记得多少、有哪些证据、还差什么。"""
    if not m or not m.get("attempts"):
        return None
    ev = _ev(m)
    r = recall(days_since(m.get("last_ev")), m.get("stability") or 0)
    return {"p": int(round(now_p(m) * 100)), "recall": int(round(r * 100)) if m.get("stability") else None,
            "items": len(set(ev["items"])), "days": len(set(ev["days"])), "fmts": len(set(ev["fmts"])),
            "missing": [] if m.get("status") == "mastered" else missing(m),
            "next": interval(m["stability"]) if m.get("stability") else None}


def now_p(m: dict) -> float:
    """现在真懂的概率：上次的估计 × 还记得的概率。"""
    return (m.get("score") or 0) * recall(days_since(m.get("last_ev")), m.get("stability") or 0)


def step(state: dict | None, *, correct: bool, at: str, item=None, mode="practice", fmt=None, weight=1.0,
         dont_know=False) -> dict:
    """纯函数：一条作答证据进来，返回新状态。state = {p, s, d, last, n, ev}；at 是作答时间。
    实时更新（update）和按历史重算（rebuild）都走这一个函数，换方法时只改这里。"""
    st = state or {"p": PRIOR, "s": 0.0, "d": 5.0, "last": None, "n": 0, "ev": _ev(None)}
    fmt = fmt or FMT.get((item or {}).get("type"), "other")
    t = days_since_between(st["last"], at) if st["last"] else 0
    s0, d0 = st["s"] or 0, st["d"] or 5
    r = recall(t, s0)
    p = (st["p"] if st["n"] else PRIOR) * (r if s0 else 1)
    if mode in ("diagnose", "probe") and not st["n"]:
        p = 0.5  # 摸底：没有先验，各一半
    guess, slip = item_params(item, fmt)
    if dont_know:
        slip = 0.03  # 自己说不会：很可靠的证据
    p = p + weight * (posterior(p, correct, guess, slip) - p)
    if mode not in ("diagnose", "probe", "exam", "paper"):
        p = p + (1 - p) * LEARN_T * weight  # 练过一次，本身也在学
    grade = "good" if correct else "again"
    if correct and fmt == "recall" and weight <= 0.3:
        grade = "hard"
    s, d = next_state(s0, d0, r, grade) if s0 else init_state(grade)
    ev = {k: (list(v) if isinstance(v, list) else v) for k, v in st["ev"].items()}
    day, ref = at[:10], (item or {}).get("id") or f"{fmt}:{at[:10]}"
    if correct:
        ev["days"] = (ev["days"] + [day])[-10:]
        ev["items"] = (ev["items"] + [ref])[-20:]
        ev["fmts"] = sorted(set(ev["fmts"]) | {fmt})
        if ev.get("dk") and len(set(ev["days"])) >= 2:
            ev["dk"] = 0  # 之前说不会，后来隔天会了
    else:
        ev["wrong"] = (ev["wrong"] + [ref])[-10:]
        ev["dk"] = ev.get("dk", 0) + (1 if dont_know else 0)
    return {"p": p, "s": s, "d": d, "last": at, "n": st["n"] + 1, "ev": ev, "status": judge(p, ev)}


def _state(row) -> dict | None:
    if not row or not row["attempts"]:
        return None
    return {"p": row["score"] or 0, "s": row.get("stability") or 0, "d": row.get("difficulty") or 5,
            "last": row.get("last_ev"), "n": row["attempts"], "ev": _ev(row)}


def update(user_id: int, kp_id: str, correct: bool, *, item=None, mode="practice", fmt=None, weight=1.0,
           dont_know=False) -> str:
    row = db.one("SELECT * FROM mastery WHERE user_id=? AND kp_id=?", user_id, kp_id)
    row = dict(row) if row else None
    st = _state(row)
    if st is None and row and row["score"]:  # 推断 / 自评过的：拿它的概率当先验
        st = {"p": row["score"], "s": 0.0, "d": 5.0, "last": None, "n": 1, "ev": _ev(None)}
    now = db.now()
    new = step(st, correct=correct, at=now, item=item, mode=mode, fmt=fmt, weight=weight, dont_know=dont_know)
    attempts = (row["attempts"] if row else 0) + 1
    ncorrect = (row["correct"] if row else 0) + (1 if correct else 0)
    db.run("INSERT INTO mastery(user_id,kp_id,score,attempts,correct,status,source,updated_at,stability,difficulty,last_ev,evidence) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,kp_id) DO UPDATE SET score=excluded.score, "
           "attempts=excluded.attempts, correct=excluded.correct, status=excluded.status, source=excluded.source, "
           "updated_at=excluded.updated_at, stability=excluded.stability, difficulty=excluded.difficulty, "
           "last_ev=excluded.last_ev, evidence=excluded.evidence",
           user_id, kp_id, round(new["p"], 4), attempts, ncorrect, new["status"], mode, now, round(new["s"], 3),
           round(new["d"], 3), now, json.dumps(new["ev"], ensure_ascii=False))
    return new["status"]


def due_checks(mastery: dict, limit=5) -> list[dict]:
    """学会过（掌握 / 学习中且有记忆数据）的知识点里，现在记得的概率掉到 85% 以下的，最该复查的排前面。"""
    out = []
    for m in mastery.values():
        if m.get("status") not in ("mastered", "learning") or not m.get("stability") or not m.get("last_ev"):
            continue
        r = recall(days_since(m["last_ev"]), m["stability"])
        if r < TARGET_R:
            out.append({**m, "recall": r})
    out.sort(key=lambda m: (m["status"] != "mastered", m["recall"]))
    return out[:limit]


# ------------------------------------------------------------------ 升级时把旧数据按新规则重算

def rebuild(user_id: int | None = None) -> int:
    """按 attempts 里的作答记录（时间顺序）把掌握状态重算一遍。只处理还没有 evidence 的行（幂等）。
    以前「诊断答对一题就掌握」的，现在会回到「学习中」，等隔天换题再确认。"""
    rows = db.q("SELECT * FROM mastery WHERE evidence IS NULL" + (" AND user_id=?" if user_id else ""),
                *([user_id] if user_id else []))
    n = 0
    for m in rows:
        atts = db.q("SELECT a.*, i.type AS itype, i.data AS idata FROM attempts a LEFT JOIN items i ON i.id=a.item_id "
                    "WHERE a.user_id=? AND a.kp_id=? ORDER BY a.id", m["user_id"], m["kp_id"])
        if not atts:  # 没有作答记录（推断 / 自评 / 导入的）：保留概率，记上空证据
            status = m["status"] if m["status"] in ("unknown", "learning", "weak") else "learning"
            db.run("UPDATE mastery SET evidence=?, status=?, last_ev=COALESCE(last_ev, updated_at) WHERE user_id=? AND kp_id=?",
                   json.dumps(_ev(None)), status, m["user_id"], m["kp_id"])
            n += 1
            continue
        st = None
        for a in atts:
            item = {**db.jload(a["idata"], {}), "id": a["item_id"], "type": a["itype"]} if a["item_id"] and a["itype"] else None
            st = step(st, correct=bool(a["correct"]), at=a["created_at"], item=item, mode=a["mode"], dont_know=bool(a["dont_know"]))
        db.run("UPDATE mastery SET score=?, status=?, stability=?, difficulty=?, last_ev=?, evidence=? WHERE user_id=? AND kp_id=?",
               round(st["p"], 4), st["status"], round(st["s"], 3), round(st["d"], 3), st["last"],
               json.dumps(st["ev"], ensure_ascii=False), m["user_id"], m["kp_id"])
        n += 1
    # 卡片：旧的固定间隔换算成稳定性
    db.run("UPDATE cards SET stability=CASE box WHEN 0 THEN 0.5 WHEN 1 THEN 1 WHEN 2 THEN 2 WHEN 3 THEN 4 WHEN 4 THEN 7 "
           "WHEN 5 THEN 15 WHEN 6 THEN 30 ELSE 60 END WHERE stability=0 AND reviews>0")
    return n


def days_since_between(a: str, b: str) -> float:
    try:
        return max(0.0, (datetime.fromisoformat(b[:19]) - datetime.fromisoformat(a[:19])).total_seconds() / 86400)
    except ValueError:
        return 0.0
