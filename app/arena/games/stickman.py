"""火柴人大战（单机打电脑，闯关）：一张地图，5 个世界 × 6 关，每个世界的第 6 关是大怪兽。前端：app/static/play/stickman.js。

越往后越难的是游戏本身：电脑血更厚、更快、更凶、会格挡，大怪兽带护盾（要答对几题才能破盾）。
题目难度不跟关卡涨，一直按孩子自己的水平出（target），所以公平还在。
- 每关最多 3 颗星：过关 1 颗、专注值 ≥ 90 1 颗、血量剩一半以上 1 颗。没拿满可以回去再打。
- 每打通一个世界解锁一样新东西（worlds 的第 3 项），地图上能看到下一样是什么。
- 关卡参数按关数和已经打通了几个世界现算（stage），开局时发给前端；赢没赢由前端报，星星和解锁在服务器算。
"""
from functools import cached_property

from . import Game, register

STAR_FOCUS = 90          # 第 2 颗星：专注值
STAR_HP = 50             # 第 3 颗星：剩下的血量
SHOP_START = ("stick",)  # 一开始就能买的装备


@register
class Stickman(Game):
    id, name, icon, color, tag = "stickman", "火柴人大战", "🥋", "#2f6f5e", "对战"
    desc = "30 关闯关！能量只靠答题补，打败每个世界的大怪兽，解锁新装备。"
    css = True
    target, max_seconds = 0.75, 600
    per_world = 6
    record = ""
    stars = (("clear", "过关"), ("focus", "专注值 90+"), ("hp", "血量剩一半"))
    worlds = (
        ("草地擂台", "day", ("shield", "🛡️", "护盾", "装备店里可以买护盾了")),
        ("黄昏山谷", "sunset", ("sword", "🗡️", "剑", "装备店里可以买剑了")),
        ("糖果城堡", "candy", ("laser", "🔫", "激光", "装备店里可以买激光了")),
        ("星空屋顶", "night", ("super", "💥", "超级必杀", "必杀伤害更高、打得更远")),
        ("火山之巅", "lava", ("belt", "🥋", "大师腰带", "打通全部 30 关")),
    )

    @cached_property
    def levels(self) -> tuple:
        return tuple({"n": n, "name": f"第 {n} 关", "boss": n % self.per_world == 0}
                     for n in range(1, len(self.worlds) * self.per_world + 1))

    def stage(self, n, prog):
        return {**super().stage(n, prog), "params": self.params(int(n), prog["worlds_done"])}

    def params(self, n: int, worlds_done: int) -> dict:
        """第 n 关（1 起）的参数。t 从 0 到 1：第 1 关最弱，第 30 关最强。"""
        w, k = divmod(n - 1, self.per_world)
        t = (n - 1) / (len(self.levels) - 1)
        boss = k == self.per_world - 1
        weapon = ["fist", "stick", "stick", "sword", "sword"][w]
        return {
            "ai_hp": round((70 + 110 * t) * (1.4 if boss else 1)),
            "ai_dmg": round(0.6 + 0.65 * t, 2),          # 电脑打人的伤害倍数
            "ai_speed": round(0.65 + 0.45 * t, 2),       # 走路速度
            "ai_aggr": round(0.35 + 0.4 * t, 2),         # 多想出手
            "ai_dodge": round(0.4 + 2.6 * t, 2),         # 看到你出拳时，每秒跳开的机会
            "ai_guard": round(0.3 * t, 2),               # 挨打时格挡（伤害减到 4 成）的机会
            "ai_think": round(6 - 2.5 * t, 1),           # 电脑答题要几秒
            "ai_weapon": "laser" if boss and w == 4 else weapon,
            "ai_shield": 40 if (boss and w >= 2) or w >= 3 else 0,
            "start_energy": 260 if n == 1 else 200 if n == 2 else 120,
            "regen": 6 if n <= 2 else 0,                 # 前两关能量会慢慢回一点（到 100 为止），让孩子先学会玩
            "seconds": 180 if boss else 150,
            "boss": {"pips": 3 + w // 2, "window": 8} if boss else None,
            "shop": list(SHOP_START) + [self.worlds[i][2][0] for i in range(min(worlds_done, 3))],
            "super": worlds_done >= 4,
        }

    def clean(self, lv, rep):
        out = super().clean(lv, rep)
        try:
            out["hp_left"] = max(0, min(100, int(rep.get("hp_left") or 0)))
        except (TypeError, ValueError):
            out["hp_left"] = 0
        return out

    def checks(self, lv, rep, focus):
        return {"clear": True, "focus": focus is not None and focus >= STAR_FOCUS, "hp": rep["hp_left"] >= STAR_HP}

    def run_stickers(self, rep, before, after):
        return (["sm_w1"] if after["worlds_done"] >= 1 else []) + (["sm_stars"] if after["stars"] >= 30 else []) + \
               (["sm_master"] if after["cleared"] == after["total"] else [])

    def match_stickers(self, result, stats):
        return ["first_win"] if result == "win" else []
