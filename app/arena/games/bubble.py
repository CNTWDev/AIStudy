"""泡泡堂（合作打怪）：两个人在格子地图上放泡泡，炸开砖块、打败所有小怪兽就过关。前端：app/static/play/bubble.js。

- 被泡泡炸到或被怪兽碰到：困在泡泡里。队友碰一下马上救出来（不算倒下）；没人救就答题自救（欠题规则同所有联网游戏）。
- 砖块里会掉道具：🔥 火力（泡泡炸得更远）、🫧 多一个泡泡、👟 跑得快。掉什么由开房间的那台设备按关卡种子决定，两边一样。

关卡（levels/bubble/NN.json）：
  map  11 行 × 17 列：# 打不坏的墙  + 砖块  . 空  1 / 2 两个人的出生点  s 史莱姆（慢）  b 蝙蝠（快）  g 幽灵（能穿砖块）
  par  三星目标秒数；tip 开局提示
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
    worlds = (("糖果小镇", "candy"), ("冰雪城堡", "ice"))

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
