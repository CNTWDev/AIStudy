"""贪吃蛇大作战（合作长大）：两条小蛇在大场地里吃豆子长大，电脑蛇撞到你们身上就会散成一地豆子；
两条蛇加起来够长（target）就过关，时间（time 秒）到了还不够长就差一点。前端：app/static/play/snake.js。

- 自己的头撞到电脑蛇或墙：倒下答题（欠题规则同所有联网游戏），身体散成豆子，答对后从小蛇重新开始。
- 两条小蛇是一队，互相穿过去不会撞。
- 豆子和电脑蛇由开房间的那台设备算，另一台跟着显示；各自的蛇各自算，所以操作没有延迟。

关卡（levels/snake/NN.json）：
  size   [宽, 高] 场地大小（像素）    target 两条蛇加起来要多长    time 限时秒数    par 三星目标秒数
  ai     电脑蛇几条    ai_len 电脑蛇多长    ai_speed 电脑蛇速度（小蛇是 1）    ai_aggr 电脑蛇多爱拦路（0–1）
  food   场上同时有多少豆子；tip 开局提示
"""
from .base import MpGame, register

FIELDS = ("size", "target", "time", "ai", "ai_len", "ai_speed", "ai_aggr", "food")


@register
class Snake(MpGame):
    id = "snake"
    roles = {"green": ("小绿", "🟢"), "pink": ("小粉", "🩷")}
    stars = (("clear", "一起长到目标长度"), ("time", "进入目标时间"), ("safe", "倒下不超过 1 次"))
    worlds = (("草地", "day"), ("星空", "night"))

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

    def checks(self, lv, rep):
        return {"clear": True, "time": rep["seconds"] <= lv["par"], "safe": rep["deaths"] <= 1}
