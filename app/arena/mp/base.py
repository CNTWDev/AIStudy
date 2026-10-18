"""联网游戏的公共部分：每款游戏登记角色、关卡、怎么算星星；房间、队伍纪录、实时转发对所有游戏都一样（rooms.py、bus.py）。

加一款联网游戏：
1. 这里写一个 MpGame 子类（角色、星星条件、关卡检查），放进 app/arena/mp/<id>.py，在 __init__.py 里 import；
2. 关卡放 app/arena/mp/levels/<id>/NN.json，一关一个文件；
3. 在 app/arena/matches.py 的 GAMES 里登记（名字、目标答对率、multi=True、freeze=False）；
4. 前端写 app/static/play/<id>.js，用 MpShell（mpshell.js）提供的联机、复活答题和结算，只写玩法。
"""
import json
from functools import cached_property
from pathlib import Path

from ..matches import ArenaError

LEVELS_DIR = Path(__file__).parent / "levels"
REGISTRY: dict[str, "MpGame"] = {}


def register(cls):
    REGISTRY[cls.id] = cls()
    return cls


def get(game: str) -> "MpGame":
    g = REGISTRY.get(game)
    if not g:
        raise ArenaError("没有这个游戏", 404)
    return g


class MpGame:
    id = ""
    roles: dict = {}              # 角色 id → (名字, 图标)；第一个是开房间的人默认的角色
    stars: tuple = ()             # 三颗星：(key, 文字)；第一颗总是「过关」
    worlds: tuple = ()            # (世界名, 场景) ，每 per_world 关一个世界
    per_world = 4
    lower_is_better = True        # 队伍纪录比时间（越短越好）；比分数的游戏设 False

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

    def public_levels(self) -> list[dict]:
        return [{**lv, "world": self.world_of(lv["n"])} for lv in self.levels]

    # ---- 过关
    def clean(self, lv: dict, rep: dict) -> dict:
        """前端报上来的过关数据：只留认识的、限定范围。seconds、deaths 所有游戏都有。"""
        try:
            return {"seconds": round(float(rep.get("seconds")), 1), "deaths": max(0, min(999, int(rep.get("deaths") or 0))),
                    "score": max(0, min(100_000, int(rep.get("score") or 0)))}
        except (TypeError, ValueError):
            raise ArenaError("数据不对")

    def checks(self, lv: dict, rep: dict) -> dict:
        """三颗星各自达成没有：{key: True/False}。"""
        return {"clear": True}

    def describe(self) -> dict:
        return {"id": self.id, "roles": [{"id": k, "name": v[0], "icon": v[1]} for k, v in self.roles.items()],   # 列表：保持顺序
                "stars": [{"key": k, "label": t} for k, t in self.stars], "lower_is_better": self.lower_is_better}
