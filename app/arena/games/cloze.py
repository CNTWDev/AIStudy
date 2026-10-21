"""句子补洞（单机，闯关）：把对的词补进句子，补上一个洞，小路上就搭好一块木板，小人往前走一格，走到旗子就过关。
前端：app/static/play/cloze.js。

词本身就是玩法：题只出句子补洞（ask，app/words.py 的 cloze_item），词按这个孩子的单词包、要复习的词、刚学会的词出，
隔天拼得出来的词就要自己把它拼进句子里。答错不扣命、不冰冻（freeze = False）：看懂了点下一题，这个词隔几道还会再来一次。
一关 6 个洞起，越往后洞越多（最多 10 个），题的难度不跟关卡涨，一直按孩子自己的水平出。
- 三颗星：过关；专注值 90+；一口气连着补对 5 个。
"""
from . import Game, register

STAR_FOCUS = 90
STAR_STREAK = 5


@register
class Cloze(Game):
    id, name, icon, color, tag = "cloze", "句子补洞", "🧩", "#e0892b", "单词"
    desc = "补对句子里的词，搭好木板走到旗子。"
    css = True
    target, max_seconds = 0.8, 600
    freeze = False
    ask = ("words", "cloze")
    record = "seconds"
    count = 1000
    per_world = 6
    stars = (("clear", "过关"), ("focus", "专注值 90+"), ("streak", f"连着补对 {STAR_STREAK} 个"))
    worlds = (("小河边", "day"), ("糖果小路", "candy"), ("黄昏山谷", "sunset"), ("星空吊桥", "night"), ("火山岛", "lava"))
    PLACES = ("木桥", "石滩", "小溪", "山坡", "树林", "花田")

    def holes(self, n: int) -> int:
        return min(10, 6 + (n - 1) // 24)

    def title(self, n):
        return f"{self.world_of(n)['name']} · {self.PLACES[(n - 1) % self.per_world]}"

    def make(self, n, rng):
        h = self.holes(n)
        return {"name": self.title(n), "holes": h, "par": h * 15, "gaps": [rng.choice((1, 1, 2)) for _ in range(h)]}

    def check(self, lv, name):
        if not 1 <= int(lv.get("holes") or 0) <= 12:
            raise ValueError(f"{self.id}/{name}: holes 要在 1–12 之间")

    def checks(self, lv, rep, focus):
        return {"clear": True, "focus": focus is not None and focus >= STAR_FOCUS, "streak": rep["score"] >= STAR_STREAK}
