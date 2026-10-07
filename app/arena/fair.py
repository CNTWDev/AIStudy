"""公平的出题：能力值 θ 和难度 b 放在同一把尺子上（Rasch / 一参数 IRT），在线校准（Elo 式）。

- 答对概率 p = 1 / (1 + e^-(θ − b))。
- 每个孩子在每个「能力维度」上有一个 θ（arena_ability.kp_id）：知识点 id，或者「vocab:en」这类词汇维度。
- 每个（题族, 级别）有一个难度 b（arena_calib），由全体孩子的作答校准；没有记录时用先验。
- 出题时选让 p 最接近目标的级别（在目标两侧的两级之间按比例混着出，平均下来正好是目标）。
这一层只管数学，不知道题从哪来（见 sources.py），也不知道经验值（见 rules.py）。
"""
import math
import random

from .. import db


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, x))))


def logit(p: float) -> float:
    p = min(0.95, max(0.05, p))
    return math.log(p / (1 - p))


def prior_b(level: int) -> float:
    return 1.0 * (level - 3)


def calib(family: str, levels, prior=prior_b) -> dict[int, tuple[float, int]]:
    rows = {r["level"]: (r["b"], r["n"]) for r in db.q("SELECT level, b, n FROM arena_calib WHERE family=?", family)}
    return {lv: rows.get(lv, (prior(lv), 0)) for lv in levels}


def stored_ability(kid_id: int, dim: str) -> tuple[float, int] | None:
    row = db.one("SELECT theta, n FROM arena_ability WHERE user_id=? AND kp_id=?", kid_id, dim)
    return (row["theta"], row["n"]) if row else None


def stored_b(family: str, level: int) -> tuple[float, int] | None:
    row = db.one("SELECT b, n FROM arena_calib WHERE family=? AND level=?", family, level)
    return (row["b"], row["n"]) if row else None


def elo_step(theta: float, n: int, b: float, nb: int, correct: bool) -> tuple[float, float]:
    """答完一题：能力值和难度各往「意外」的方向挪一步。新孩子步子大（很快定位），答得多了就稳定；题目难度的步子更小。"""
    p = sigmoid(theta - b)
    y = 1.0 if correct else 0.0
    k_theta = max(0.25, 1.2 / math.sqrt(1 + n / 3))
    k_b = max(0.02, 0.4 / math.sqrt(1 + nb))
    return theta + k_theta * (y - p), b - k_b * (y - p)


def update(kid_id: int, dim: str, family: str, level: int, correct: bool, theta: float, n: int, b: float, nb: int) -> float:
    theta2, b2 = elo_step(theta, n, b, nb, correct)
    db.run("INSERT INTO arena_ability(user_id,kp_id,theta,n,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,kp_id) "
           "DO UPDATE SET theta=excluded.theta, n=excluded.n, updated_at=excluded.updated_at",
           kid_id, dim, theta2, n + 1, db.now())
    db.run("INSERT INTO arena_calib(family,level,b,n) VALUES(?,?,?,?) ON CONFLICT(family,level) "
           "DO UPDATE SET b=excluded.b, n=excluded.n", family, level, b2, nb + 1)
    return theta2


def pick_level(theta: float, levels: dict[int, tuple[float, int]], target: float, rng: random.Random) -> int:
    """选级别：在答对概率刚好高于、刚好低于目标的两级之间按比例混着出，平均下来答对概率正好是目标；
    目标超出这个题族的范围（最简单的也太难，或最难的也太简单）就出最接近的一级。"""
    ps = {lv: sigmoid(theta - b) for lv, (b, _) in levels.items()}
    easy = [lv for lv in ps if ps[lv] >= target]
    hard = [lv for lv in ps if ps[lv] < target]
    if not easy or not hard:
        return min(ps, key=lambda lv: abs(ps[lv] - target))
    e, h = min(easy, key=lambda lv: ps[lv]), max(hard, key=lambda lv: ps[lv])
    w = (target - ps[h]) / (ps[e] - ps[h])
    return e if rng.random() < w else h


def fit_gap(theta: float, levels: dict[int, tuple[float, int]], target: float) -> float:
    """最合适的一级离目标答对率还差多少（0 = 正好；越大说明这个维度对这个孩子太难或太简单）。"""
    return min(abs(sigmoid(theta - b) - target) for b, _ in levels.values())
