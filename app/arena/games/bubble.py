"""泡泡堂（合作打怪）：两个人在格子地图上放泡泡，炸开砖块、打败所有小怪兽就过关。前端：app/static/play/bubble.js。

- 被泡泡炸到或被怪兽碰到：困在泡泡里。队友碰一下马上救出来（不算倒下）；没人救就答题自救（答对一道就起来，所有游戏一样）。
- 砖块里会掉道具：🔥 火力（泡泡炸得更远）、🫧 多一个泡泡、👟 跑得快。掉什么由开房间的那台设备按关卡种子决定，两边一样。

关卡（levels/bubble/NN.json）：
  map  11～13 行 × 17～21 列（单数）：# 打不坏的墙  + 砖块  * 藏着星星的砖块  . 空  1 / 2 两个人的出生点（3 / 4 可以不写）
       s 史莱姆（慢）  b 蝙蝠（快）  g 幽灵（能穿砖块）  k 铁甲小龙（要炸两次）
  par  三星目标秒数；tip 开局提示；mspeed 怪兽速度倍数（不写是 1）
  有 * 的关是「找星星」任务：怪兽全打败、星星全捡到才过关。
前 8 关手做，第 9 关起按关数生成（make），一共 1000 关：越往后砖块越密、怪兽越多越快，快的蝙蝠、穿墙的幽灵、
要炸两次的铁甲小龙越来越多，地图越来越大，隔几关有「找星星」任务；每个世界的第 1 关比上一个世界的最后一关松一点。
3、4 个人玩（for_team）：在空着的角落加出生点，每多一个人多约 1/3 的怪兽，「被困答题」那颗星也多放宽 1 次。
"""
import math
import random

from . import Game, register

COLS, ROWS = 17, 11                 # 最小的地图；生成的关卡越往后越大
SIZES = ((17, 11), (19, 11), (19, 13), (21, 13))
TILES = set("#+*.1234sbgk")
MONS = "sbgk"
PLACES = {   # 每个场景的地名：生成的关卡名 = 形容词 + 地名
    "candy": ("糖果街", "棒棒糖林", "巧克力河", "奶油城堡", "果冻山", "饼干屋", "棉花糖田", "蛋糕塔"),
    "ice": ("雪人村", "冰柱洞", "极光湖", "冰滑梯", "雪球谷", "冻湖", "冰晶塔", "暴风雪口"),
    "jungle": ("藤蔓桥", "神庙门", "瀑布后", "树屋村", "古井", "蘑菇林", "猴子岛", "石像谷"),
    "lava": ("熔岩桥", "火山口", "黑石路", "岩浆河", "火龙巢", "炎之门", "烟囱山", "灰烬城"),
    "sea": ("珊瑚礁", "沉船舱", "海藻林", "贝壳宫", "章鱼洞", "灯塔下", "海沟", "鲸鱼湾"),
    "cloud": ("彩虹桥", "风车岛", "云朵城", "星星梯", "月亮湾", "雷电塔", "天空花园", "飞艇港"),
}
MODS = ("热闹的", "安静的", "迷宫般的", "弯弯的", "秘密", "古老的", "闪亮的", "神秘", "狭窄的", "大大的", "危险的", "深深的")


@register
class Bubble(Game):
    id, name, icon, color, tag = "bubble", "泡泡堂", "🫧", "#2f8fdc", "联网合作"
    desc = "2～4 个人放泡泡炸开砖块、打败小怪兽。被泡泡困住时，队友碰一下就能救你，不然就答题自救。"
    players, freeze, max_seconds = (2, 4), False, 900   # 谁倒下谁答题：答错不冷冻，换一道接着答
    roles = {"red": ("红泡泡", "🔴"), "blue": ("蓝泡泡", "🔵"), "yellow": ("黄泡泡", "🟡"), "green": ("绿泡泡", "🟢")}
    stars = (("clear", "打败所有怪兽"), ("time", "进入目标时间"), ("safe", "被困答题不超过 2 次"))
    worlds = (("糖果小镇", "candy"), ("冰雪城堡", "ice"), ("丛林遗迹", "jungle"), ("熔岩地牢", "lava"),
              ("海底宫殿", "sea"), ("云端花园", "cloud"))
    count = 1000
    TIPS = ("怪兽越来越多，两个人分头炸，记得留一条退路。", "蝙蝠跑得快，先炸开一条长走廊等它过来。",
            "幽灵能穿过砖块，别躲在砖块后面。", "拿到 🔥 火力的人去前面，另一个人跟在后面救人。",
            "被困住时喊队友来碰你一下，比答题快。", "先把出生点周围炸开，地方大了才好躲。")

    def title(self, n):
        if n <= len(self.handmade):
            return self.handmade[n - 1]["name"]
        p, scene = (n - 1) % self.per_world, self.world_of(n)["scene"]
        return MODS[((n - 1) // 4 * 5 + p * 7) % len(MODS)] + PLACES[scene][((n - 1) // 24 + p * 2) % 8]

    def make(self, n, rng):
        k = n - 8
        d = max(1, k - 6 + 2 * ((n - 1) % self.per_world))   # 难度：每个世界第 1 关松一点，第 4 关最难
        cols, rows = rng.choice([sz for sz, need in zip(SIZES, (0, 30, 70, 140)) if d >= need])
        g = [["#" if r in (0, rows - 1) or c in (0, cols - 1) or (r % 2 == 0 and c % 2 == 0) else "." for c in range(cols)]
             for r in range(rows)]
        corners = [(1, 1), (1, cols - 2), (rows - 2, 1), (rows - 2, cols - 2)]
        a = corners[k % 4]
        b = corners[(k % 4 + (1, 2, 3)[k % 3]) % 4]   # 两个人有时在对角，有时在同一边
        safe = {p for s in (a, b) for p in (s, (s[0], s[1] + (1 if s[1] == 1 else -1)), (s[0] + (1 if s[0] == 1 else -1), s[1]))}
        dense = min(0.74, 0.5 + 0.015 * d) + rng.uniform(-0.04, 0.02)
        for r in range(rows):
            for c in range(cols):
                if g[r][c] == "." and (r, c) not in safe and rng.random() < dense:
                    g[r][c] = "+"
        g[a[0]][a[1]], g[b[0]][b[1]] = "1", "2"
        area = cols * rows / (COLS * ROWS)
        per = min(12, 6 + d // 2) + min(4, max(0, d - 12) // 90)   # 17×11 的地图上有几只，地图大了按面积多放
        total = round(per * area)
        tough = round(total * min(0.25, max(0, d - 20) / 400))
        ghosts = min((total - tough) // 3, 1 + d // 4)
        bats = round((total - tough - ghosts) * min(0.6, 0.45 + d / 1000))
        kinds = ["k"] * tough + ["g"] * ghosts + ["b"] * bats + ["s"] * (total - tough - ghosts - bats)
        far = [(r, c) for r in range(1, rows - 1) for c in range(1, cols - 1)
               if g[r][c] in ".+" and min(abs(r - s[0]) + abs(c - s[1]) for s in (a, b)) >= 6]
        rng.shuffle(far)
        for (r, c), m in zip(far, kinds):
            g[r][c] = m
            for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):   # 怪兽身边至少空一格，能动
                if g[r + dr][c + dc] == "+":
                    g[r + dr][c + dc] = "."
                    break
        stars = min(8, 3 + d // 120) if d >= 10 and n % 3 == 0 else 0   # 隔几关一个「找星星」任务
        bricks = [(r, c) for r in range(rows) for c in range(cols) if g[r][c] == "+"]
        for r, c in rng.sample(bricks, min(stars, len(bricks))):
            g[r][c] = "*"
        mspeed = round(1 + 0.3 * (1 - math.exp(-d / 350)), 2)
        tips = []
        if stars:
            tips.append(f"有 {stars} 块砖里藏着 ⭐，炸开捡起来，怪兽全打败、星星全捡到才过关。")
        if tough:
            tips.append("铁甲小龙要炸两次才会被打败，炸完一次赶紧躲开再炸。")
        if (cols, rows) != (COLS, ROWS) and not tips:
            tips.append("地图变大了，两个人分头炸，别走太远，随时能去救队友。")
        tips.append(self.TIPS[(k - 1) % len(self.TIPS)])
        return {"name": self.title(n), "tip": tips[0], "par": 100 + 10 * total + 10 * stars, "mspeed": mspeed,
                "map": ["".join(row) for row in g]}

    def check(self, lv, name):
        rows = lv.get("map") or []
        if (len(rows[0]) if rows else 0, len(rows)) not in SIZES or any(len(r) != len(rows[0]) for r in rows):
            raise ValueError(f"bubble/{name}: 地图的大小只能是 {SIZES}（列 × 行）")
        bad = {ch for r in rows for ch in r} - TILES
        if bad:
            raise ValueError(f"bubble/{name}: 不认识的格子 {bad}")
        for ch in "12":
            if sum(r.count(ch) for r in rows) != 1:
                raise ValueError(f"bubble/{name}: {ch} 必须正好有一个")
        for ch in "34":
            if sum(r.count(ch) for r in rows) > 1:
                raise ValueError(f"bubble/{name}: {ch} 最多一个")
        lv["monsters"] = sum(sum(r.count(m) for m in MONS) for r in rows)
        lv["stars"] = sum(r.count("*") for r in rows)
        if not lv["monsters"]:
            raise ValueError(f"bubble/{name}: 至少要有一只怪兽")

    def for_team(self, lv, k):
        k = max(2, min(4, int(k)))
        if k == 2:
            return lv
        rng = random.Random(f"bubble:{lv['n']}:{k}")
        g = [list(r) for r in lv["map"]]
        ROWS, COLS = len(g), len(g[0])
        CORNERS = ((1, 1), (1, COLS - 2), (ROWS - 2, 1), (ROWS - 2, COLS - 2))
        at = lambda ch: next(((r, c) for r in range(ROWS) for c in range(COLS) if g[r][c] == ch), None)
        spawns = [p for p in (at(ch) for ch in "1234") if p]
        dist = lambda p, q: abs(p[0] - q[0]) + abs(p[1] - q[1])
        pool = []   # 离新出生点太近、要挪走的怪兽
        for ch in "34"[:k - 2]:
            if at(ch):
                continue
            spots = sorted(CORNERS, key=lambda p: -min(dist(p, s) for s in spawns))
            spots += sorted(((r, c) for r in range(1, ROWS - 1) for c in range(1, COLS - 1) if g[r][c] == "."),
                            key=lambda p: -min(dist(p, s) for s in spawns))
            for r, c in spots:
                if g[r][c] in "#1234" or min(dist((r, c), s) for s in spawns) < 4:
                    continue
                around = [(r + dr, c + dc) for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)) if g[r + dr][c + dc] != "#"]
                if len(around) < 2:
                    continue
                if g[r][c] in MONS:
                    pool.append(g[r][c])
                g[r][c] = ch
                for rr, cc in around:   # 出生点旁边留空地：能放第一个泡泡再躲开
                    if g[rr][cc] in "+*":
                        g[rr][cc] = "."
                spawns.append((r, c))
                break
        for r in range(ROWS):   # 怪兽离每个出生点都要够远
            for c in range(COLS):
                if g[r][c] in MONS and min(dist((r, c), s) for s in spawns) < 5:
                    pool.append(g[r][c])
                    g[r][c] = "."
        have = [ch for row in g for ch in row if ch in MONS]
        extra = round(lv["monsters"] * (k - 2) / 3)
        pool += [rng.choice(have or ["s"]) for _ in range(extra)]
        far = [(r, c) for r in range(1, ROWS - 1) for c in range(1, COLS - 1)
               if g[r][c] in ".+" and min(dist((r, c), s) for s in spawns) >= 6]
        rng.shuffle(far)
        for (r, c), m in zip(far, pool):
            g[r][c] = m
            for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):   # 怪兽身边至少空一格，能动
                if g[r + dr][c + dc] == "+":
                    g[r + dr][c + dc] = "."
                    break
        rows = ["".join(r) for r in g]
        return {**lv, "map": rows, "monsters": sum(sum(r.count(m) for m in MONS) for r in rows), "stars": sum(r.count("*") for r in rows),
                "players": k, "safe": k}

    def checks(self, lv, rep, focus):
        return {"clear": True, "time": rep["seconds"] <= lv["par"], "safe": rep["deaths"] <= lv.get("safe", 2)}
