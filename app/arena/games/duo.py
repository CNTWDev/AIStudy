"""双人闯关（冰火人玩法）：一个小火苗、一个小水滴。火苗不怕岩浆（F）、水滴不怕水（W），绿色毒水（G）谁都不能碰；
按钮、拉杆、升降台、桥要两个人配合；两个人都站到自己的门前（D 火苗、E 水滴）才算过关。前端：app/static/play/duo.js。

关卡（levels/duo/NN.json）：
  map     15 行 × 32 列：# 墙  . 空  F 岩浆  W 水  G 毒水（池子在地面那一行）  f / w 火 / 水宝石
          1 / 2 火苗 / 水滴出生点  D / E 火苗 / 水滴的门（都写在角色站的那一格）
  objects 机关，坐标是格子（x 列、y 行）：
          {"k":"btn","x","y","id"}            按钮：有人踩着时 id 打开
          {"k":"lever","x","y","id"}          拉杆：往右推打开、往左推关上
          {"k":"gate","x","y","w","h","id"}   门：id 打开时升起来；"inv":true 反过来（id 打开时关上）
          {"k":"bridge","x","y","w","id"}     桥：id 打开时出现（一格厚）
          {"k":"lift","x","y","w","to","id"}  升降台：平时在第 y 行，id 打开时升到第 to 行
  par     三星目标秒数；tip 开局提示
前 16 关手做；第 17～1000 关是程序生成、逐关用解题器验证过的（tools/gen_duo_levels.py 生成 levels/duo_more.json，
能一起走到门口、宝石都拿得到、不会走进死路），越往后越要两个人轮流按机关。
"""
import json
from functools import cached_property

from . import LEVELS_DIR, Game, register

COLS, ROWS = 32, 15
TILES = set("#.FWGfw12DE")
KINDS = {"btn", "lever", "gate", "bridge", "lift"}


@register
class Duo(Game):
    id, name, icon, color, tag = "duo", "双人闯关", "🔥", "#e0582b", "联网合作"
    desc = "一个当小火苗、一个当小水滴，按按钮、坐升降台，互相帮忙一起走到门口。"
    players, freeze, max_seconds = (2, 2), False, 900   # 谁倒下谁答题：答错不冷冻，改成「多欠一道题」
    roles = {"fire": ("小火苗", "🔥"), "water": ("小水滴", "💧")}
    stars = (("clear", "一起过关"), ("gems", "宝石全拿到"), ("time", "进入目标时间"))
    worlds = (("森林神殿", "forest"), ("水晶洞穴", "cave"), ("火山之巅", "lava"), ("冰雪宫殿", "ice"))
    count = 1000

    @cached_property
    def more(self) -> list:
        """第 17 关起的关卡（生成好、验证过的，存在一个文件里，不在网站上现算）。"""
        return json.loads((LEVELS_DIR / "duo_more.json").read_text(encoding="utf-8"))

    def make(self, n, rng):
        return dict(self.more[n - len(self.handmade) - 1])

    def title(self, n):
        return self.handmade[n - 1]["name"] if n <= len(self.handmade) else self.more[n - len(self.handmade) - 1]["name"]

    def check(self, lv, name):
        rows = lv.get("map") or []
        if len(rows) != ROWS or any(len(r) != COLS for r in rows):
            raise ValueError(f"duo/{name}: 地图必须是 {ROWS} 行 × {COLS} 列")
        bad = {ch for r in rows for ch in r} - TILES
        if bad:
            raise ValueError(f"duo/{name}: 不认识的格子 {bad}")
        for ch in "12DE":
            if sum(r.count(ch) for r in rows) != 1:
                raise ValueError(f"duo/{name}: {ch} 必须正好有一个")
        for o in lv.get("objects", []):
            if o.get("k") not in KINDS or not o.get("id") or not (0 < o.get("x", 0) < COLS and 0 < o.get("y", 0) < ROWS):
                raise ValueError(f"duo/{name}: 机关不对 {o}")
        lv["gems"] = sum(r.count("f") + r.count("w") for r in rows)

    def clean(self, lv, rep):
        out = super().clean(lv, rep)
        try:
            out["gems"] = max(0, min(lv["gems"], int(rep.get("gems") or 0)))
        except (TypeError, ValueError):
            out["gems"] = 0
        out["score"] = out["gems"]
        return out

    def checks(self, lv, rep, focus):
        return {"clear": True, "gems": rep["gems"] >= lv["gems"], "time": rep["seconds"] <= lv["par"]}
