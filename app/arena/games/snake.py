"""贪吃蛇大作战（合作长大）：两条小蛇在大场地里吃豆子长大，电脑蛇撞到你们身上就会散成一地豆子；
两条蛇加起来够长（target）就过关，时间（time 秒）到了还不够长就差一点。前端：app/static/play/snake.js。

- 自己的头撞到电脑蛇或墙：倒下答题（欠题规则同所有联网游戏），身体散成豆子，答对后从小蛇重新开始。
- 两条小蛇是一队，互相穿过去不会撞。
- 豆子和电脑蛇由开房间的那台设备算，另一台跟着显示；各自的蛇各自算，所以操作没有延迟。

关卡（levels/snake/NN.json）：
  size   [宽, 高] 场地大小（像素）    target 两条蛇加起来要多长    time 限时秒数    par 三星目标秒数
  ai     电脑蛇几条    ai_len 电脑蛇多长    ai_speed 电脑蛇速度（小蛇是 1）    ai_aggr 电脑蛇多爱拦路（0–1）
  food   场上同时有多少豆子；tip 开局提示
  rocks  场上的石头 [[x, y, 半径], …]（撞到就倒下，可以不写）
  gold   金星星的位置 [[x, y], …]（「收集金星」任务：大家够长、金星全吃到才过关，可以不写）
前 8 关手做，第 9 关起按关数生成（make），一共 1000 关：场地更大、目标更长、电脑蛇更多更快更爱拦路，
越往后石头越多，隔几关有「收集金星」任务；每个世界的第 1 关比上一个世界的最后一关松一点。
3、4 个人玩（for_team）：目标长度和豆子按人数加，「倒下」那颗星也多放宽 1 次。
"""
import math

from . import Game, register

FIELDS = ("size", "target", "time", "ai", "ai_len", "ai_speed", "ai_aggr", "food")
PLACES = {   # 每个场景的地名：生成的关卡名 = 形容词 + 地名
    "day": ("小花园", "蒲公英坡", "苹果园", "小溪边", "风车田", "向日葵地", "蘑菇圈", "野餐草坪"),
    "night": ("流星雨", "银河边", "月亮湖", "萤火虫林", "北斗七星", "星星码头", "彗星尾巴", "猫头鹰树"),
    "desert": ("沙丘", "绿洲", "仙人掌林", "金字塔", "骆驼道", "沙漠驿站", "风沙谷", "古城墙"),
    "snow": ("雪地", "冰湖", "雪松林", "企鹅岛", "冰屋村", "雪橇道", "冰川", "极光下"),
    "sea": ("珊瑚礁", "海草林", "沉船", "水母湾", "海底火山", "海龟岛", "深海峡谷", "珍珠贝"),
    "volcano": ("熔岩河", "火山口", "黑曜石林", "温泉", "火焰山", "岩浆湖", "烟雾谷", "火龙洞"),
}
MODS = ("热闹的", "安静的", "长长的", "弯弯的", "秘密", "古老的", "闪亮的", "神秘", "拥挤的", "大大的", "危险的", "遥远的")


@register
class Snake(Game):
    id, name, icon, color, tag = "snake", "贪吃蛇大作战", "🐍", "#3aa860", "联网合作"
    desc = "2～4 条小蛇一起吃豆子长大，躲开电脑蛇，让它们撞到你们身上，大家加起来够长就过关。"
    players, freeze, max_seconds = (2, 4), False, 900   # 谁倒下谁答题：答错不冷冻，改成「多欠一道题」
    roles = {"green": ("小绿", "🟢"), "pink": ("小粉", "🩷"), "blue": ("小蓝", "🔵"), "orange": ("小橙", "🟠")}
    stars = (("clear", "一起长到目标长度"), ("time", "进入目标时间"), ("safe", "倒下不超过 1 次"))
    worlds = (("草地", "day"), ("星空", "night"), ("沙漠", "desert"), ("雪原", "snow"), ("海底", "sea"), ("火山", "volcano"))
    count = 1000
    TIPS = ("电脑蛇越来越多，一个吃豆子、一个放哨。", "两条蛇一前一后，把电脑蛇引到队友身上。",
            "电脑蛇会抢着拦路，别贴着它的头走。", "场地很大，看看右下角的小地图，豆子多的地方一起去。",
            "倒下了别慌，答对题就能回来，队友先帮你守着。", "电脑蛇更快了，转弯要早一点。")

    def title(self, n):
        if n <= len(self.handmade):
            return self.handmade[n - 1]["name"]
        p, scene = (n - 1) % self.per_world, self.world_of(n)["scene"]
        return MODS[((n - 1) // 4 * 5 + p * 7) % len(MODS)] + PLACES[scene][((n - 1) // 24 + p * 2) % 8]

    def make(self, n, rng):
        k = n - 8   # 比最后一关手做的（第 8 关）难多少
        d = max(1, k - 6 + 2 * ((n - 1) % self.per_world))   # 难度：每个世界第 1 关松一点，第 4 关最难
        grow = lambda big, fast, slow: big * (1 - math.exp(-d / fast)) if slow is None else big * (1 - math.exp(-d / fast)) + slow * (1 - math.exp(-d / 400))
        W, H = round(2000 + grow(400, 20, 800)), round(1200 + grow(240, 20, 560))
        target = round(220 + grow(380, 25, 600))
        area = W * H / (2000 * 1200)
        rocks, gold = [], []
        def spot(r, gap):   # 离场地中间（大家出生的地方）、别的石头、金星都远一点的地方
            for _ in range(60):
                x, y = rng.randint(100, W - 100), rng.randint(100, H - 100)
                if math.hypot(x - W / 2, y - H / 2) < 450 + r:
                    continue
                if all(math.hypot(x - a, y - b) > r + c + gap for a, b, c in rocks) and all(math.hypot(x - a, y - b) > r + gap for a, b in gold):
                    return x, y
            return None
        for _ in range(min(16, round(max(0, d - 12) / 30 * area))) if d > 12 else ():
            r = rng.randint(30, 60 + min(30, d // 20))
            p = spot(r, 160)
            if p:
                rocks.append([p[0], p[1], r])
        if d >= 8 and n % 3 == 1:   # 隔几关一个「收集金星」任务
            for _ in range(min(6, 3 + d // 150)):
                p = spot(20, 300)
                if p:
                    gold.append(list(p))
        time = round(target / (1.1 + 0.48 * (1 - math.exp(-d / 25))) + 10 * len(gold) + 2 * len(rocks))
        tips = []
        if gold:
            tips.append(f"场上有 {len(gold)} 颗金星，分头去吃！大家够长、金星全吃到才过关。")
        if rocks:
            tips.append("小心石头，撞上就会倒下；把电脑蛇引到石头旁边，它转弯就慢了。")
        tips.append(self.TIPS[(k - 1) % len(self.TIPS)])
        return {"name": self.title(n), "tip": tips[0], "size": [W, H], "target": target, "time": time, "par": round(time * 0.7),
                "ai": min(16, 6 + min(d, 24) // 3 + max(0, d - 24) // 120), "ai_len": min(110, 40 + 2 * min(d, 24) + max(0, d - 24) // 20),
                "ai_speed": round(1 + 0.18 * (1 - math.exp(-d / 250)), 2), "ai_aggr": round(0.5 + 0.38 * (1 - math.exp(-d / 40)), 3),
                "food": round((100 + 5 * min(d, 24)) * area), "rocks": rocks, "gold": gold}

    def check(self, lv, name):
        miss = [f for f in FIELDS if f not in lv]
        if miss:
            raise ValueError(f"snake/{name}: 缺 {miss}")
        if lv["par"] > lv["time"]:
            raise ValueError(f"snake/{name}: par 不能比限时长")
        W, H = lv["size"]
        for x, y, *r in lv.get("rocks", []) + lv.get("gold", []):
            if not (0 < x < W and 0 < y < H):
                raise ValueError(f"snake/{name}: 石头 / 金星在场地外面")

    def for_team(self, lv, k):
        k = max(2, min(4, int(k)))
        if k == 2:
            return lv
        return {**lv, "target": round(lv["target"] * k / 2), "food": round(lv["food"] * k / 2), "players": k, "safe": k - 1}

    def clean(self, lv, rep):
        out = super().clean(lv, rep)
        out["score"] = min(out["score"], 10_000)
        return out

    def checks(self, lv, rep, focus):
        return {"clear": True, "time": rep["seconds"] <= lv["par"], "safe": rep["deaths"] <= lv.get("safe", 1)}
