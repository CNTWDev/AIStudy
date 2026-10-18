"""游戏登记：一款游戏一个类，平台（房间、进度、记录、答题、贴纸）要知道的都写在这里，玩法在前端 app/static/play/<id>.js。

单机、两人联网、全家一起玩是同一套东西，只差 players 那一行（房间里放几个人）。
加一款游戏：
1. 在这个目录写 <id>.py：一个 Game 子类，@register 登记，在下面 import；
2. 有关卡的话放 levels/<id>/NN.json，一关一个文件（check 检查格式）；关卡要按进度算的（火柴人大战）就改写 stage；
3. 前端写 app/static/play/<id>.js，用 PlayShell（shell.js）只写玩法。
"""
import json
from functools import cached_property
from pathlib import Path

from ..errors import ArenaError

LEVELS_DIR = Path(__file__).parent / "levels"
REGISTRY: dict[str, "Game"] = {}


def register(cls):
    REGISTRY[cls.id] = cls()
    return cls


def get(game: str) -> "Game":
    g = REGISTRY.get(game)
    if not g:
        raise ArenaError("没有这个游戏", 404)
    return g


class Game:
    id = name = icon = tag = desc = ""
    color = "#2f6f5e"
    players = (1, 1)              # 房间里最少、最多几个人：单机 (1, 1)，两人联网 (2, 2)，全家一起 (2, 6)
    target = 0.75                 # 题目的目标答对率（按每个孩子自己的水平出题）
    freeze = True                 # 答错冷冻几秒；联网游戏改成「多欠一道题」，不冷冻
    max_seconds = 600             # 一局最多算多少秒的游戏时间
    roles: dict = {}              # 角色 id → (名字, 图标)；第一个是开房间的人默认的角色
    stars: tuple = ()             # 三颗星：(key, 文字)；第一颗总是「过关」
    worlds: tuple = ()            # (世界名, 场景[, 打通后解锁 (id, 图标, 名字, 说明)])，每 per_world 关一个世界
    per_world = 4
    record = "seconds"            # 每关的纪录比什么：seconds（越短越好）、score（越高越好）、""（只比星星）
    css = False                   # 有没有自己的样式表 app/static/play/<id>.css

    @property
    def multi(self) -> bool:
        return self.players[1] > 1

    # ---- 关卡
    @cached_property
    def levels(self) -> tuple:
        out = []
        for p in sorted((LEVELS_DIR / self.id).glob("*.json")):
            lv = json.loads(p.read_text(encoding="utf-8"))
            if not lv.get("name") or not lv.get("par"):
                raise ValueError(f"{self.id}/{p.name}: 要有 name 和 par")
            self.check(lv, p.name)
            out.append(lv)
        for i, lv in enumerate(out, 1):
            lv["n"] = i
        return tuple(out)

    def check(self, lv: dict, name: str) -> None:
        """检查一关的数据对不对（不对就报错，启动和测试时都会查）。"""

    def level(self, n) -> dict:
        try:
            n = int(n)
        except (TypeError, ValueError):
            raise ArenaError("没有这一关", 404)
        if not 1 <= n <= len(self.levels):
            raise ArenaError("没有这一关", 404)
        return self.levels[n - 1]

    def world_of(self, n: int) -> dict:
        if not self.worlds:
            return {"n": 1, "name": "", "scene": ""}
        i = min(len(self.worlds) - 1, (n - 1) // self.per_world)
        return {"n": i + 1, "name": self.worlds[i][0], "scene": self.worlds[i][1]}

    def stage(self, n, prog: dict) -> dict:
        """开局时发给前端的这一关（prog：这支队伍的进度，有的游戏按它调整关卡）。没有关卡的游戏是 {}。"""
        if not self.levels:
            return {}
        lv = self.level(n)
        return {**lv, "world": self.world_of(lv["n"])}

    def public_levels(self) -> list[dict]:
        """大厅和地图上用的关卡（联网游戏的预览要整关数据）。"""
        return [{**lv, "world": self.world_of(lv["n"])} for lv in self.levels]

    # ---- 一关结束
    def clean(self, lv: dict, rep: dict) -> dict:
        """前端报上来的数据：只留认识的、限定范围。seconds、deaths、score 所有游戏都有。"""
        try:
            return {"seconds": round(max(0.0, min(3600.0, float(rep.get("seconds") or 0))), 1),
                    "deaths": max(0, min(999, int(rep.get("deaths") or 0))),
                    "score": max(0, min(100_000, int(rep.get("score") or 0)))}
        except (TypeError, ValueError):
            raise ArenaError("数据不对")

    def checks(self, lv: dict, rep: dict, focus) -> dict:
        """过关时三颗星各自达成没有：{key: True/False}。focus：这一局大家答题的专注值（没答够题是 None）。"""
        return {"clear": True}

    def run_stickers(self, rep: dict, before: dict, after: dict) -> list[str]:
        """过关时全队都得的贴纸（before / after：过关前后的进度）。"""
        if not self.multi:
            return []
        return ["mp1"] + (["mp_clean"] if rep["deaths"] == 0 else []) + (["mp_all"] if after["cleared"] == after["total"] else [])

    def match_stickers(self, result: str, stats: dict) -> list[str]:
        """每个人自己这一局得的贴纸（玩法相关的，答题相关的在 awards.after_match）。"""
        return []

    def card(self) -> dict:
        """乐园首页的卡片。"""
        return {"id": self.id, "name": self.name, "icon": self.icon, "color": self.color, "tag": self.tag, "desc": self.desc,
                "multi": self.multi}

    def describe(self) -> dict:
        """游戏页面要知道的：几个人、角色、星星、关卡。"""
        return {**self.card(), "players": list(self.players), "record": self.record, "per_world": self.per_world,
                "roles": [{"id": k, "name": v[0], "icon": v[1]} for k, v in self.roles.items()],   # 列表：保持顺序
                "stars": [{"key": k, "label": t} for k, t in self.stars], "levels": self.public_levels()}


from . import bubble, defense, duo, race, snake, stickman  # noqa: E402,F401  （登记游戏；首页按这个顺序）

ORDER = ("stickman", "race", "defense", "duo", "bubble", "snake")
GAMES = {k: REGISTRY[k] for k in ORDER} | REGISTRY

__all__ = ["REGISTRY", "GAMES", "Game", "get", "register"]
