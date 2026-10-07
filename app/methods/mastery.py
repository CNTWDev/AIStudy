"""掌握模型：一条条学习证据进来，估算一个孩子对一个知识点「真懂的概率」和状态。

接口（换模型时实现这几个方法即可）：
  step(state, ev) -> state     纯函数：旧状态 + 一条证据 = 新状态。实时更新和按历史重放都只走它
  judge(p, evidence) -> 状态    unknown / weak / learning / mastered
  missing(m) / explain(m)       离掌握还差什么（给孩子和家长看）
  needs_work(m)                 要不要排进「攻克 / 补弱」
  now_p(m)                      现在真懂的概率（考虑遗忘）
状态 state = {p, s, d, last, n, ev, status}；证据 ev = {kind, correct, at, fmt, guess, item_id, mode,
weight, dont_know, value, stats}，见 app/evidence.py。
"""
from datetime import datetime


def days_between(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    try:
        return max(0.0, (datetime.fromisoformat(b[:19]) - datetime.fromisoformat(a[:19])).total_seconds() / 86400)
    except ValueError:
        return 0.0


def empty_evidence() -> dict:
    return {"days": [], "items": [], "fmts": [], "wrong": [], "dk": 0}


class BKT:
    """贝叶斯知识追踪（BKT，Corbett & Anderson 1994）+ 交叉验证的掌握标准 + 记忆模型的遗忘。

    - 每次作答按题目的「蒙对概率」和「会也做错的概率」（粗心）更新真懂的概率：
      选择题答对只加一点，换几道不同的题都答对，概率才会高；粗心错一次也不会掉到底。
    - 掌握 = 概率 ≥ master_p，且至少 min_days 天、min_items 道不同的题、min_fmts 种题型答对过
      （只有一种题型可用时，alt_items 道不同的题也行）。
    - 薄弱也要证据：不同的题错过两次以上、或者自己说了「还不会」。
    - 题目难度（题库里所有孩子的作答统计）也算进去：人人都对的题答对说明得少，人人都错的题答错说明得少。
    """
    id, version = "bkt", 1
    defaults = {"master_p": 0.85, "weak_p": 0.35, "prior": 0.3, "diag_prior": 0.5, "learn_t": 0.12, "slip": 0.1,
                "min_days": 2, "min_items": 2, "min_fmts": 2, "alt_items": 3, "shaky_p": 0.6}
    TEST_MODES = ("diagnose", "probe", "exam", "paper")  # 测出来的，不算「做一题学一点」

    def __init__(self, memory, **params):
        self.memory = memory
        self.params = {**self.defaults, **params}
        for k, v in self.params.items():
            setattr(self, k, v)

    # ---- 单条证据 ----
    def item_params(self, ev: dict) -> tuple[float, float]:
        guess, slip = ev.get("guess", 0.15), self.slip
        st = ev.get("stats")
        if st and st.get("n_attempts", 0) >= 5:
            acc = st["n_correct"] / st["n_attempts"]
            if acc > 0.8:
                guess = max(guess, min(0.5, (acc - 0.8) * 2.5))
            elif acc < 0.4:
                slip = min(0.3, self.slip + (0.4 - acc) * 0.5)
        if ev.get("dont_know"):
            slip = 0.03  # 自己说不会：很可靠的证据
        return guess, slip

    @staticmethod
    def posterior(p: float, correct: bool, guess: float, slip: float) -> float:
        if correct:
            return p * (1 - slip) / (p * (1 - slip) + (1 - p) * guess)
        return p * slip / (p * slip + (1 - p) * (1 - guess))

    def step(self, state: dict | None, ev: dict) -> dict:
        at = ev["at"]
        if ev.get("kind") == "infer":  # 没有作答的推断（同一概念、前置、自评、导入）：只给一个概率，不算交叉验证的证据
            st = dict(state) if state else {"p": self.prior, "s": 0.0, "d": 5.0, "last": None, "n": 0, "ev": empty_evidence()}
            st["p"] = float(ev["value"])
            st["last"] = st["last"] or at
            st["status"] = "weak" if st["p"] < self.weak_p else "learning"
            return st
        if state is None:
            state = {"p": self.diag_prior if ev.get("mode") in ("diagnose", "probe") else self.prior,
                     "s": 0.0, "d": 5.0, "last": None, "n": 0, "ev": empty_evidence()}
        fmt, weight, correct = ev.get("fmt") or "other", float(ev.get("weight") or 1.0), bool(ev.get("correct"))
        t = days_between(state["last"], at)
        s0, d0 = state["s"] or 0, state["d"] or 5
        p = state["p"] * self.memory.recall(t, s0)
        guess, slip = self.item_params(ev)
        p = p + weight * (self.posterior(p, correct, guess, slip) - p)
        if ev.get("mode") not in self.TEST_MODES:
            p = p + (1 - p) * self.learn_t * weight  # 练过一次，本身也在学
        grade = "good" if correct else "again"
        if correct and fmt == "recall" and weight <= 0.3:
            grade = "hard"
        s, d = self.memory.review(s0, d0, t, grade)
        e = {k: (list(v) if isinstance(v, list) else v) for k, v in state["ev"].items()}
        day, ref = at[:10], ev.get("item_id") or f"{fmt}:{at[:10]}"
        if correct:
            e["days"] = (e["days"] + [day])[-10:]
            e["items"] = (e["items"] + [ref])[-20:]
            e["fmts"] = sorted(set(e["fmts"]) | {fmt})
            if e.get("dk") and len(set(e["days"])) >= 2:
                e["dk"] = 0  # 之前说不会，后来隔天会了
        else:
            e["wrong"] = (e["wrong"] + [ref])[-10:]
            e["dk"] = e.get("dk", 0) + (1 if ev.get("dont_know") else 0)
        return {"p": p, "s": s, "d": d, "last": at, "n": state["n"] + 1, "ev": e, "status": self.judge(p, e)}

    # ---- 判定 ----
    def judge(self, p: float, ev: dict) -> str:
        days, items, fmts = len(set(ev["days"])), len(set(ev["items"])), len(set(ev["fmts"]))
        if p >= self.master_p and days >= self.min_days and items >= self.min_items \
                and (fmts >= self.min_fmts or items >= self.alt_items):
            return "mastered"
        if p < self.weak_p and (len(set(ev["wrong"])) >= 2 or ev.get("dk")):
            return "weak"
        return "learning"

    def now_p(self, m: dict, days: float) -> float:
        """m 是 mastery 表的一行；days = 距上次证据多少天。"""
        return (m.get("score") or 0) * self.memory.recall(days, m.get("stability") or 0)

    def needs_work(self, m: dict) -> bool:
        return m.get("status") == "weak" or (m.get("status") == "learning" and (m.get("score") or 0) < self.shaky_p)

    def missing(self, ev: dict, p: float) -> list[str]:
        out = []
        items = len(set(ev["items"]))
        if items < self.min_items:
            out.append("再答对一道不同的题")
        if len(set(ev["days"])) < self.min_days:
            out.append("隔天再答对一次" if self.min_days <= 2 else f"在 {self.min_days} 个不同的日子答对")
        if len(set(ev["fmts"])) < self.min_fmts and items < self.alt_items:
            out.append("换一种题型答对")
        if p < self.master_p and not out:
            out.append("再多答对几次")
        return out
