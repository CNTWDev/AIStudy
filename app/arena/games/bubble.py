"""泡泡堂（合作打怪）：两个人在格子地图上放泡泡，炸开砖块、打败所有小怪兽就过关。前端：app/static/play/bubble.js。

- 被泡泡炸到或被怪兽碰到：困在泡泡里。队友碰一下马上救出来（不算倒下）；没人救就答题自救（欠题规则同所有联网游戏）。
- 砖块里会掉道具：🔥 火力（泡泡炸得更远）、🫧 多一个泡泡、👟 跑得快。掉什么由开房间的那台设备按关卡种子决定，两边一样。

关卡（levels/bubble/NN.json）：
  map  11 行 × 17 列：# 打不坏的墙  + 砖块  . 空  1 / 2 两个人的出生点  s 史莱姆（慢）  b 蝙蝠（快）  g 幽灵（能穿砖块）
  par  三星目标秒数；tip 开局提示
前 8 关手做，第 9 关起按关数生成（make）：砖块更密、怪兽更多，快的蝙蝠、能穿墙的幽灵越来越多。
"""
from . import Game, register

COLS, ROWS = 17, 11
TILES = set("#+.12sbg")


@register
class Bubble(Game):
    id, name, icon, color, tag = "bubble", "泡泡堂", "🫧", "#2f8fdc", "联网合作"
    desc = "放泡泡炸开砖块、打败小怪兽。被泡泡困住时，队友碰一下就能救你，不然就答题自救。"
    players, freeze, max_seconds = (2, 2), False, 900   # 谁倒下谁答题：答错不冷冻，改成「多欠一道题」
    roles = {"red": ("红泡泡", "🔴"), "blue": ("蓝泡泡", "🔵")}
    stars = (("clear", "打败所有怪兽"), ("time", "进入目标时间"), ("safe", "被困答题不超过 2 次"))
    worlds = (("糖果小镇", "candy"), ("冰雪城堡", "ice"), ("丛林遗迹", "jungle"), ("熔岩地牢", "lava"),
              ("海底宫殿", "sea"), ("云端花园", "cloud"))
    count = 24
    NAMES = ("藤蔓小路", "丛林探险", "古老神庙", "遗迹深处", "熔岩入口", "火焰走廊", "岩浆迷宫", "黑暗城堡",
             "珊瑚迷宫", "沉船宝藏", "章鱼来了", "海底深处", "云朵小路", "彩虹桥", "风之神殿", "怪兽之王")
    TIPS = ("怪兽越来越多，两个人分头炸，记得留一条退路。", "蝙蝠跑得快，先炸开一条长走廊等它过来。",
            "幽灵能穿过砖块，别躲在砖块后面。", "拿到 🔥 火力的人去前面，另一个人跟在后面救人。",
            "被困住时喊队友来碰你一下，比答题快。", "先把出生点周围炸开，地方大了才好躲。")

    def make(self, n, rng):
        k = n - 8
        g = [["#" if r in (0, ROWS - 1) or c in (0, COLS - 1) or (r % 2 == 0 and c % 2 == 0) else "." for c in range(COLS)]
             for r in range(ROWS)]
        corners = [(1, 1), (1, COLS - 2), (ROWS - 2, 1), (ROWS - 2, COLS - 2)]
        a = corners[k % 4]
        b = corners[(k % 4 + (1, 2, 3)[k % 3]) % 4]   # 两个人有时在对角，有时在同一边
        safe = {p for s in (a, b) for p in (s, (s[0], s[1] + (1 if s[1] == 1 else -1)), (s[0] + (1 if s[0] == 1 else -1), s[1]))}
        dense = min(0.74, 0.5 + 0.015 * k)
        for r in range(ROWS):
            for c in range(COLS):
                if g[r][c] == "." and (r, c) not in safe and rng.random() < dense:
                    g[r][c] = "+"
        g[a[0]][a[1]], g[b[0]][b[1]] = "1", "2"
        total = min(12, 6 + k // 2)
        ghosts = min(total // 3, 1 + k // 4)
        bats = (total - ghosts) // 2
        kinds = ["g"] * ghosts + ["b"] * bats + ["s"] * (total - ghosts - bats)
        far = [(r, c) for r in range(1, ROWS - 1) for c in range(1, COLS - 1)
               if g[r][c] in ".+" and min(abs(r - s[0]) + abs(c - s[1]) for s in (a, b)) >= 6]
        rng.shuffle(far)
        for (r, c), m in zip(far, kinds):
            g[r][c] = m
            for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):   # 怪兽身边至少空一格，能动
                if g[r + dr][c + dc] == "+":
                    g[r + dr][c + dc] = "."
                    break
        return {"name": self.NAMES[(k - 1) % len(self.NAMES)], "tip": self.TIPS[(k - 1) % len(self.TIPS)],
                "par": 120 + 6 * k, "map": ["".join(row) for row in g]}

    def check(self, lv, name):
        rows = lv.get("map") or []
        if len(rows) != ROWS or any(len(r) != COLS for r in rows):
            raise ValueError(f"bubble/{name}: 地图必须是 {ROWS} 行 × {COLS} 列")
        bad = {ch for r in rows for ch in r} - TILES
        if bad:
            raise ValueError(f"bubble/{name}: 不认识的格子 {bad}")
        for ch in "12":
            if sum(r.count(ch) for r in rows) != 1:
                raise ValueError(f"bubble/{name}: {ch} 必须正好有一个")
        lv["monsters"] = sum(r.count("s") + r.count("b") + r.count("g") for r in rows)
        if not lv["monsters"]:
            raise ValueError(f"bubble/{name}: 至少要有一只怪兽")

    def checks(self, lv, rep, focus):
        return {"clear": True, "time": rep["seconds"] <= lv["par"], "safe": rep["deaths"] <= 2}
