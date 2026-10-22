"""单词跑酷（单机，闯关）：三条跑道往前冲，按顺序撞上对的字母把词拼出来，路上的石头要换道躲开、栏杆要跳过去。
前端：app/static/play/runner.js。

词就是玩法：拼哪个词、给多少提示都由服务器按这个人的单词等级定（ask，sources.WordSource._run）——
新词整个词淡淡地写在上面照着拼，认得了只给中文和首字母，拼得出了只给中文和读音。一个词拼完记一次作答：
一路没撞错字母算拼对，撞错过就算没拼对（这个词隔几个还会再来，提示也多给一档），记进同一套单词等级和能力值。
撞错字母只减速，不算摔；撞上石头、栏杆才摔倒，和所有游戏一样答对 1 道就起来（这道题就是正在拼的那个词）。
一关拼够 words 个词就过关；越往后词越多、跑得越快、石头和栏杆越密，词的难度不跟关卡涨，一直按自己的水平出。
- 三颗星：过关；一次都没摔；八成的词一次拼对。
study：学单词的游戏，成人自己安排时间（rules.free_for），从「我的词」进来。
"""
from . import Game, register

CLEAN_SHARE = 0.8


@register
class Runner(Game):
    id, name, icon, color, tag = "runner", "单词跑酷", "🏃", "#ff7a3d", "单词"
    desc = "换道、跳跃，撞上对的字母把单词拼出来，越跑越快！"
    css = True
    target, max_seconds = 0.8, 600
    freeze = False
    ask = ("words", "run")
    study = True
    record = "seconds"
    count = 1000
    per_world = 6
    stars = (("clear", "过关"), ("safe", "一次都没摔"), ("clean", "八成的词一次拼对"))
    worlds = (("清晨跑道", "day"), ("糖果街", "candy"), ("黄昏海边", "sunset"), ("星空公路", "night"), ("火山口", "lava"))
    PLACES = ("起跑", "弯道", "长坡", "隧道", "大桥", "冲刺")

    def title(self, n):
        return f"{self.world_of(n)['name']} · {self.PLACES[(n - 1) % self.per_world]}"

    def make(self, n, rng):
        words = min(10, 4 + (n - 1) // 12)
        return {"name": self.title(n), "words": words, "par": words * 14,
                "speed": round(min(1.8, 1 + (n - 1) * 0.008), 3),          # 跑多快（第 1 关的倍数）
                "rocks": round(min(0.7, 0.22 + (n - 1) * 0.012), 3),        # 一排里放石头的机会
                "bars": 0 if n < 3 else round(min(0.45, 0.12 + (n - 3) * 0.01), 3),   # 第 3 关起有要跳的栏杆
                "seed": rng.randrange(1 << 30)}

    def check(self, lv, name):
        if not 1 <= int(lv.get("words") or 0) <= 12:
            raise ValueError(f"{self.id}/{name}: words 要在 1–12 之间")

    def checks(self, lv, rep, focus):
        return {"clear": True, "safe": rep["deaths"] == 0, "clean": rep["score"] >= round(lv["words"] * CLEAN_SHARE)}
