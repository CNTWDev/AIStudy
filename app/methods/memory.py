"""记忆模型：一个东西（知识点、单词卡、错题卡）隔了多久还记得多少、下次该什么时候复习。

接口（换模型时实现这几个方法即可）：
  recall(days, s)              隔了 days 天，现在还记得的概率
  interval(s)                  记得的概率降到目标保持率要多少天（下次复习排在那天）
  review(s, d, days, grade)    复习一次后新的 (稳定性 s, 难度 d)；grade：again 忘了 / hard 模糊 / good 记得
"""
import math


class FSRS:
    """受 FSRS/DSR 启发的简化记忆模型；不是完整 FSRS，也没有宣称普适最优参数。
    R = (1 + t / 9S)^-1：S 是「记忆稳定性」（天）。隔得越久、快忘时答对，S 涨得越多（间隔效应）；
    当天反复答对，S 几乎不涨。"""
    id, version = "fsrs", 2
    defaults = {"target_r": 0.85, "max_interval": 180}

    def __init__(self, **params):
        self.params = {**self.defaults, **params}
        self.target_r = float(self.params["target_r"])
        self.max_interval = int(self.params["max_interval"])
        if not 0.7 <= self.target_r <= 0.97 or self.max_interval < 1:
            raise ValueError("记忆模型：target_r 应在 0.7–0.97，max_interval 应为正整数")

    def recall(self, days: float, s: float) -> float:
        """stability 为 0 表示还没有记忆数据：按不衰减处理。"""
        if not s or days <= 0:
            return 1.0
        return (1 + days / (9 * s)) ** -1

    def interval(self, s: float) -> int:
        return min(self.max_interval, max(1, int(round(9 * s * (1 / self.target_r - 1)))))

    @staticmethod
    def first(grade: str) -> tuple[float, float]:
        return {"again": (0.4, 7.0), "hard": (1.2, 6.0), "good": (3.0, 5.0)}[grade]

    def review(self, s: float, d: float, days: float, grade: str) -> tuple[float, float]:
        if not s:
            return self.first(grade)
        r = self.recall(days, s)
        g = {"again": 1, "hard": 2, "good": 3}[grade]
        d = min(10.0, max(1.0, 0.9 * (d - 0.8 * (g - 3)) + 0.1 * 5))
        if grade == "again":  # 忘了：稳定性回落，但比从零开始高一点（学过的东西重学更快）
            s2 = min(s, 1.9 * d ** -0.1 * ((s + 1) ** 0.3 - 1) * math.exp(1.5 * (1 - r)))
            return max(0.3, s2), d
        grow = math.exp(1.2) * (11 - d) * s ** -0.1 * (math.exp(1 - r) - 1)
        return s * (1 + grow * (0.5 if grade == "hard" else 1.0)), d
