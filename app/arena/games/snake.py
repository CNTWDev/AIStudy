"""贪吃蛇大作战（合作长大）：两条小蛇在大场地里吃豆子长大，电脑蛇撞到你们身上就会散成一地豆子；
两条蛇加起来够长（target）就过关，时间（time 秒）到了还不够长就差一点。前端：app/static/play/snake.js。

- 自己的头撞到电脑蛇或墙：倒下答题（欠题规则同所有联网游戏），身体散成豆子，答对后从小蛇重新开始。
- 两条小蛇是一队，互相穿过去不会撞。
- 豆子和电脑蛇由开房间的那台设备算，另一台跟着显示；各自的蛇各自算，所以操作没有延迟。

关卡（levels/snake/NN.json）：
  size   [宽, 高] 场地大小（像素）    target 两条蛇加起来要多长    time 限时秒数    par 三星目标秒数
  ai     电脑蛇几条    ai_len 电脑蛇多长    ai_speed 电脑蛇速度（小蛇是 1）    ai_aggr 电脑蛇多爱拦路（0–1）
  food   场上同时有多少豆子；tip 开局提示
前 8 关手做，第 9 关起按关数生成（make）：场地更大、目标更长、电脑蛇更多更快更爱拦路。
"""
from . import Game, register

FIELDS = ("size", "target", "time", "ai", "ai_len", "ai_speed", "ai_aggr", "food")


@register
class Snake(Game):
    id, name, icon, color, tag = "snake", "贪吃蛇大作战", "🐍", "#3aa860", "联网合作"
    desc = "两条小蛇一起吃豆子长大，躲开电脑蛇，让它们撞到你们身上，两条蛇加起来够长就过关。"
    players, freeze, max_seconds = (2, 2), False, 900   # 谁倒下谁答题：答错不冷冻，改成「多欠一道题」
    roles = {"green": ("小绿", "🟢"), "pink": ("小粉", "🩷")}
    stars = (("clear", "一起长到目标长度"), ("time", "进入目标时间"), ("safe", "倒下不超过 1 次"))
    worlds = (("草地", "day"), ("星空", "night"), ("沙漠", "desert"), ("雪原", "snow"), ("海底", "sea"), ("火山", "volcano"))
    count = 24
    NAMES = ("沙丘迷踪", "热浪来袭", "绿洲争夺", "沙漠之王", "雪地初行", "冰面打滑", "暴风雪", "极光之夜",
             "珊瑚礁", "海草森林", "暗流涌动", "深海巨蛇", "火山脚下", "熔岩河", "火山口", "蛇神降临")
    TIPS = ("电脑蛇越来越多，一个吃豆子、一个放哨。", "两条蛇一前一后，把电脑蛇引到队友身上。",
            "电脑蛇会抢着拦路，别贴着它的头走。", "场地很大，看看右下角的小地图，豆子多的地方一起去。",
            "倒下了别慌，答对题就能回来，队友先帮你守着。", "电脑蛇更快了，转弯要早一点。")

    def make(self, n, rng):
        k = n - 8   # 比最后一关手做的（第 8 关）难多少
        target = 220 + 20 * k
        time = round(target / (1.1 + 0.02 * k))
        return {"name": self.NAMES[(k - 1) % len(self.NAMES)], "tip": self.TIPS[(k - 1) % len(self.TIPS)],
                "size": [min(2600, 2000 + 50 * k), min(1600, 1200 + 30 * k)], "target": target, "time": time,
                "par": round(time * 0.7), "ai": 6 + k // 3, "ai_len": 40 + 2 * k, "ai_speed": round(min(1.15, 1.0 + 0.01 * k), 2),
                "ai_aggr": round(min(0.85, 0.5 + 0.025 * k), 3), "food": 100 + 5 * k}

    def check(self, lv, name):
        miss = [f for f in FIELDS if f not in lv]
        if miss:
            raise ValueError(f"snake/{name}: 缺 {miss}")
        if lv["par"] > lv["time"]:
            raise ValueError(f"snake/{name}: par 不能比限时长")

    def clean(self, lv, rep):
        out = super().clean(lv, rep)
        out["score"] = min(out["score"], 10_000)
        return out

    def checks(self, lv, rep, focus):
        return {"clear": True, "time": rep["seconds"] <= lv["par"], "safe": rep["deaths"] <= 1}
