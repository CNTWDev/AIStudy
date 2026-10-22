"""游戏登记：一款游戏一个类，平台（房间、进度、记录、答题、贴纸）要知道的都写在这里，玩法在前端 app/static/play/<id>.js。

单机、两人联网、全家一起玩是同一套东西，只差 players 那一行（房间里放几个人）。
加一款游戏：
1. 在这个目录写 <id>.py：一个 Game 子类，@register 登记，在下面 import；
2. 有关卡的话放 levels/<id>/NN.json，一关一个文件（check 检查格式）；后面更难的关卡可以不手做：
   写 count（一共几关）、make(n, rng) 和 title(n)，手做的之后用到哪关才按关数生成哪关（种子固定，每次生成的都一样）；
   关卡要按进度算的（火柴人大战）就改写 stage；
3. 前端写 app/static/play/<id>.js，用 PlayShell（shell.js）只写玩法；开局要按队伍现算的参数（对战的让球）写 team_setup。
"""
import json
import random
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
    ask: tuple | None = None      # 只出一种题：(题源, 题型)，比如单词跑酷 ("words", "run")；不写就按孩子的科目混着出
    study = False                 # 学单词这类学习游戏：成人玩不受乐园时长限制（rules.free_for），孩子照常算游戏时间
    freeze = True                 # 答错冷冻几秒；联网游戏不冷冻，答对一道就起来
    max_seconds = 600             # 一局最多算多少秒的游戏时间
    roles: dict = {}              # 角色 id → (名字, 图标)；第一个是开房间的人默认的角色
    stars: tuple = ()             # 三颗星：(key, 文字)；第一颗总是「过关」
    worlds: tuple = ()            # (世界名, 场景[, 打通后解锁 (id, 图标, 名字, 说明)])，每 per_world 关一个世界
    per_world = 4
    count = 0                     # 一共几关（算上手做的）：比 levels/<id>/ 里手做的多出来的，由 make 生成
    record = "seconds"            # 每关的纪录比什么：seconds（越短越好）、score（越高越好）、""（只比星星）
    css = False                   # 有没有自己的样式表 app/static/play/<id>.css

    @property
    def multi(self) -> bool:
        return self.players[1] > 1

    # ---- 关卡：手做的在 levels/<id>/，之后的用到哪一关才生成哪一关（关卡可以有上千关，不一次全算出来）
    @cached_property
    def handmade(self) -> tuple:
        out = []
        for i, p in enumerate(sorted((LEVELS_DIR / self.id).glob("*.json")), 1):
            out.append(self._checked(json.loads(p.read_text(encoding="utf-8")), i, p.name))
        return tuple(out)

    @property
    def total(self) -> int:
        """一共几关（没有关卡的游戏是 0）。"""
        return max(len(self.handmade), self.count)

    def _checked(self, lv: dict, n: int, name: str) -> dict:
        if not lv.get("name") or not lv.get("par"):
            raise ValueError(f"{self.id}/{name}: 要有 name 和 par")
        self.check(lv, name)
        lv["n"] = n
        return lv

    def make(self, n: int, rng: random.Random) -> dict:
        """生成第 n 关（手做的关卡之后）：越往后越难。rng 的种子按游戏和关数固定，每次生成的都一样。"""
        raise NotImplementedError

    def title(self, n: int) -> str:
        """第 n 关的名字（地图上用，不用生成整关）。生成的关卡要改写它，和 make 里的名字一样。"""
        return self.level(n)["name"]

    def check(self, lv: dict, name: str) -> None:
        """检查一关的数据对不对（不对就报错，启动和测试时都会查）。"""

    def level(self, n) -> dict:
        try:
            n = int(n)
        except (TypeError, ValueError):
            raise ArenaError("没有这一关", 404)
        if not 1 <= n <= self.total:
            raise ArenaError("没有这一关", 404)
        if n <= len(self.handmade):
            return self.handmade[n - 1]
        cache = self.__dict__.setdefault("_made", {})
        if n not in cache:
            if len(cache) > 512:   # 只留最近用到的
                cache.clear()
            cache[n] = self._checked(self.make(n, random.Random(f"{self.id}:{n}")), n, f"第 {n} 关（生成）")
        return cache[n]

    def world_of(self, n: int) -> dict:
        """第 n 关在第几个世界。世界的场景按 worlds 一轮一轮用下去，第二轮起名字后面加上第几轮（糖果小镇 2）；
        登记的解锁只在第一轮。"""
        if not self.worlds:
            return {"n": 1, "name": "", "scene": "", "unlock": None}
        i = (n - 1) // self.per_world
        w = self.worlds[i % len(self.worlds)]
        rnd = i // len(self.worlds) + 1
        unlock = {"id": w[2][0], "icon": w[2][1], "name": w[2][2], "how": w[2][3]} if len(w) > 2 and rnd == 1 else None
        return {"n": i + 1, "name": w[0] if rnd == 1 else f"{w[0]} {rnd}", "scene": w[1], "unlock": unlock}

    @property
    def world_count(self) -> int:
        return -(-self.total // self.per_world) if self.worlds else (1 if self.total else 0)

    def stage(self, n, prog: dict) -> dict:
        """开局时发给前端的这一关（prog：这支队伍的进度，有的游戏按它调整关卡）。没有关卡的游戏是 {}。"""
        if not self.total:
            return {}
        lv = self.level(n)
        return {**lv, "world": self.world_of(lv["n"])}

    def for_team(self, lv: dict, k: int) -> dict:
        """这一关给 k 个人玩时的样子（开局发给前端、过关算星星都用它）：人多的游戏按人数加怪、加目标。默认不变。
        lv 是共用的关卡数据，要改就先复制。"""
        return lv

    def team_setup(self, team: str) -> dict:
        """开局时按这支队伍现算、发给前端的参数（并进 stage，没有关卡的游戏也有，比如对战游戏的让球）。team：队伍键。"""
        return {}

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
        """过关时全队都得的、这款游戏自己的贴纸（before / after：过关前后的进度）。联网游戏共有的在 awards.after_run。"""
        return []

    def match_stickers(self, result: str, stats: dict) -> list[str]:
        """每个人自己这一局得的贴纸（玩法相关的，答题相关的在 awards.after_match）。"""
        return []

    def card(self) -> dict:
        """乐园首页的卡片。"""
        return {"id": self.id, "name": self.name, "icon": self.icon, "color": self.color, "tag": self.tag, "desc": self.desc,
                "multi": self.multi}

    def describe(self) -> dict:
        """游戏页面要知道的：几个人、角色、星星、一共几关（关卡数据跟着房间走：room_info 的 preview、开局的 stage）。"""
        return {**self.card(), "players": list(self.players), "record": self.record, "per_world": self.per_world,
                "roles": [{"id": k, "name": v[0], "icon": v[1]} for k, v in self.roles.items()],   # 列表：保持顺序
                "stars": [{"key": k, "label": t} for k, t in self.stars], "total": self.total}


from . import bubble, defense, duo, pingpong, race, runner, snake, stickman  # noqa: E402,F401  （登记游戏；首页按这个顺序）

ORDER = ("runner", "stickman", "race", "defense", "duo", "bubble", "snake", "pingpong")
GAMES = {k: REGISTRY[k] for k in ORDER} | REGISTRY

__all__ = ["REGISTRY", "GAMES", "Game", "get", "register"]
