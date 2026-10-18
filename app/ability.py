"""孩子的水平：能力值 θ 和题目难度 b 放在同一把尺子上（Rasch / 一参数 IRT），在线校准（Elo 式）。

整个系统只有这一个「水平」，和掌握模型（app/evidence.py）读的是同一份学习记录：
- 不管在哪儿答的题（每日练习、热身、追根、错题重做、试卷、快练、冲刺、游戏），engine.record_attempt
  记证据的同时调 observe 更新 θ；单词题（不挂知识点）由出题的地方调 observe。游戏和快练按 θ 定难度，
  所以在练习里学会了，游戏里的题也跟着变；反过来游戏里的作答也照常算进掌握。
- 掌握模型回答「懂没懂、还记得多少」，θ 回答「出多难的题答对率刚好」。一个知识点第一次要用 θ 时，
  从掌握模型的「现在还记得的概率」起步（start）。
- 答对概率 p = 1 / (1 + e^-(θ − b))。每个孩子在每个「能力维度」上有一个 θ（arena_ability.kp_id）：
  知识点 id，或者「vocab:en」这类词汇维度。
- 每个（题族, 级别）有一个难度 b（arena_calib），由全体孩子的作答校准；没有记录时用先验。
  现场生成的题按题族和级别（app/quickgen.py）；题库里存下来的题按题型分族（bank、bank:num……），
  级别看大家的正确率（bank_level）；自己判对错的简答题不算。
- 出题时选让 p 最接近目标的级别（在目标两侧的两级之间按比例混着出，平均下来正好是目标）。
这一层只管水平和难度，不知道题从哪来（见 arena/sources.py），也不知道经验值（见 arena/rules.py）。
（表名还叫 arena_*：最早只给游戏用。）
"""
import math
import random

from . import db, evidence, itemtypes


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


def bank_level(r) -> int:
    """题库题的难度分 5 级：做过的人多就看大家的正确率，少就看出题时标的难度。"""
    n, c = r["n_attempts"] or 0, r["n_correct"] or 0
    if n >= 5:
        acc = c / n
        return 1 if acc >= 0.85 else 2 if acc >= 0.7 else 3 if acc >= 0.55 else 4 if acc >= 0.4 else 5
    return {1: 2, 2: 3, 3: 4}.get(r["difficulty"] or 2, 3)


def bank_family(kind: str) -> str:
    """题库题按题型分族校准：四选一能蒙对，计算题、填空蒙不对，同一级别难度不一样。"""
    return "bank" if kind == "mcq" else f"bank:{kind}"


def scale_of(item: dict | None) -> tuple[str, int] | None:
    """一道题在尺子上的位置（题族, 级别）；没法定的（没有题、自评的简答题）返回 None。"""
    if not item:
        return None
    if item.get("family") and item.get("level"):
        return item["family"], int(item["level"])
    if not item.get("id") or itemtypes.of(item).self_rated:
        return None
    row = db.one("SELECT type, difficulty, n_attempts, n_correct FROM items WHERE id=?", item["id"])
    return (bank_family(row["type"]), bank_level(row)) if row else None


def start(kid_id: int, dim: str, prior_p: float | None = None) -> tuple[float, int]:
    """这个维度现在的 θ：有就用；没有就从掌握模型「现在还记得的概率」起步，再没有用 prior_p（默认 0.5）。"""
    got = stored_ability(kid_id, dim)
    if got:
        return got
    m = db.one("SELECT * FROM mastery WHERE user_id=? AND kp_id=?", kid_id, dim)
    if m and m["attempts"]:
        return logit(evidence.now_p(kid_id, dict(m))), 0
    return logit(prior_p if prior_p is not None else 0.5), 0


def observe(kid_id: int, dim: str, item: dict | None, correct: bool, scale: tuple[str, int] | None = None) -> float | None:
    """一次作答：θ 和这道题所在（题族, 级别）的难度各挪一步。点「不会」按答错算。"""
    scale = scale or scale_of(item)
    if not scale or not dim:
        return None
    family, level = scale
    given = item or {}   # 出题时已经算好的起点（arena/sources.py 按学段、题族先验估的）
    theta, n = stored_ability(kid_id, dim) or ((given["theta"], 0) if given.get("theta") is not None else start(kid_id, dim))
    b, nb = stored_b(family, level) or ((given["b"], 0) if given.get("b") is not None else (prior_b(level), 0))
    return update(kid_id, dim, family, level, correct, theta, n, b, nb)
