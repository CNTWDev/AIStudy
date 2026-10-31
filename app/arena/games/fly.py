"""左右飞（1～4 人，闯关）：小蜜蜂在两面墙之间来回飞，点一下往上扑，碰一下墙走一步，从刺的口子里钻过去。
前端：app/static/play/fly.js；设计：docs/左右飞-讨论稿（项目文件），设计稿 ui-2026-10-31-fly/。

一关 = 碰满 walls 下墙。rows[i] 是碰完第 i 下以后，对面那面墙上长出来的刺：一串 SLOTS 个字母，s 刺、. 空；
'q' * SLOTS 是「题墙」：对面墙上开 2～4 朵答案花（前端拿到题以后按选项个数开），上下两排刺变成软云，这一趟飞得慢。
题就是玩法：撞到哪朵花就是选了哪个（记一次作答）。对了花开、金色护盾 3 下（撞碎迎面的刺）；
错了花蔫、软软弹回去再选；连错两次对的那朵亮起来、照样过（没有护盾）。题墙前小蜜蜂在泡泡里停住，题看完点一下才出发。
题要写在花上，所以这款游戏的题都按「选一个」出（shape = "choices"，sources.as_choices）；倒下答题复活也是同一种题。
每个世界加一样新东西，从那个世界起一直有：糖果屋起会上下滑的刺（slide）、黄昏谷起中间飘着的刺球（ball）、
星空岛起一闪一闪的刺（blink）、火山口起一阵阵的风（wind）。
纪录比蜂蜜（每一趟时间是定的，比用时没意义）。几个人一起：同一关各飞各的屋子，谁先碰满谁第一，大家都碰满才算过关
（开房间的人报：蜂蜜、题墙一次撞对的都是大家加起来）。
- 三颗星：大家都碰满；没倒下（几个人一起，每多 1 人多放宽 1 次）；题墙一次撞对（几个人一起，每多 1 人多放宽 1 道）。
"""
import random

from . import Game, register

SLOTS = 9


@register
class Fly(Game):
    id, name, icon, color, tag = "fly", "左右飞", "🐝", "#ffc21a", "1～4 人"
    desc = "小蜜蜂在两面墙中间来回飞，点一下往上扑，从刺的口子里钻过去，撞对题墙上的答案花！一个人、全家一起都能玩。"
    players = (1, 4)
    roles = {"sun": ("太阳蜂", "🟡"), "berry": ("莓果蜂", "🔴"), "mint": ("薄荷蜂", "🟢"), "sky": ("天空蜂", "🔵")}
    css = True
    target, max_seconds = 0.75, 600
    freeze = False
    shape = "choices"
    record = "score"
    count = 1000
    per_world = 6
    stars = (("clear", "大家都碰满"), ("safe", "不倒下"), ("gates", "题墙一次撞对"))
    # 第一个世界是「白天」场景：孩子选的主题换它的颜色（DESIGN 5.45）
    worlds = (("晴空花园", "day"), ("糖果屋", "candy"), ("黄昏谷", "sunset"), ("星空岛", "night"), ("火山口", "lava"))
    PLACES = ("蒲公英", "小风车", "向日葵", "蜂巢口", "花篱笆", "大花园")

    def title(self, n):
        return f"{self.world_of(n)['name']} · {self.PLACES[(n - 1) % self.per_world]}"

    def make(self, n, rng):
        k, w = n - 1, (n - 1) // self.per_world      # w：第几个世界（从 0 数，过了一轮新东西都在）
        walls = min(40, 12 + k // 6)
        gates = max(2, min(6, walls // 7))
        gap = 3 if k < 24 else 2                     # 口子几格高（一面墙 SLOTS 格）
        fill = min(0.9, 0.35 + k * 0.01)             # 口子以外的格子，刺占多少
        shift = 3 if k < 6 else 4 if k < 36 else 6   # 两面墙的口子最多差几格（来得及飞过去）
        rows = build(walls, gates, gap, fill, shift, 3 if w >= 4 else 4, rng)   # 有风的世界往下差得少一点（风往上吹时掉得慢）
        return {"name": self.title(n), "par": walls * 2, "walls": walls, "rows": rows,
                "gates": sum(r == "q" * SLOTS for r in rows),
                "cross": round(max(1.1, 1.7 - k * 0.006), 2),                          # 横穿一趟几秒
                "slide": 0 if w < 1 else round(min(0.5, 0.2 + (k - 6) * 0.004), 3),    # 会上下滑的刺占几面墙
                "ball": 0 if w < 2 else round(min(0.5, 0.18 + (k - 12) * 0.002), 3),   # 刺球左右飘多快（屋宽 / 秒）
                "blink": 0 if w < 3 else round(min(0.4, 0.15 + (k - 18) * 0.003), 3),  # 一闪一闪的刺占几面墙
                "wind": 0 if w < 4 else round(min(0.42, 0.25 + (k - 24) * 0.001), 3),  # 风多大（屋高 / 秒²，重力是 1.6）
                "seed": rng.randrange(1 << 30)}

    def check(self, lv, name):
        rows = lv.get("rows") or []
        if not 4 <= len(rows) <= 60 or lv.get("walls") != len(rows):
            raise ValueError(f"{self.id}/{name}: rows 要 4–60 面墙，walls 和它一样多")
        for i, r in enumerate(rows):
            if len(r) != SLOTS or set(r) - set("s.q") or ("q" in r and r != "q" * SLOTS):
                raise ValueError(f"{self.id}/{name}: 第 {i + 1} 面墙要是 {SLOTS} 个 s/.，或者整面 q（题墙）")
            if r != "q" * SLOTS and ".." not in r:
                raise ValueError(f"{self.id}/{name}: 第 {i + 1} 面墙要留一个口子")

    def stage(self, n, prog):
        """开局：带上这一关自己的纪录（蜂蜜），花路上的 👻「上次的我」跟着它走。"""
        out = super().stage(n, prog)
        best = next((lv["best"] for lv in prog.get("levels", []) if lv["n"] == int(n)), None)
        return {**out, "best": best}

    def for_team(self, lv, k):
        return {**lv, "team": max(1, k)} if k > 1 else lv

    def clean(self, lv, rep):
        out = super().clean(lv, rep)
        try:
            out["gates"] = max(0, min(999, int(rep.get("gates") or 0)))
        except (TypeError, ValueError):
            out["gates"] = 0
        return out

    def checks(self, lv, rep, focus):
        k = lv.get("team", 1)   # gates：大家题墙一次撞对的加起来
        return {"clear": True, "safe": rep["deaths"] <= k - 1, "gates": rep["gates"] >= lv["gates"] * k - (k - 1)}


def gap_at(row: str) -> tuple[int, int]:
    """一面墙上最长的那个口子：(从第几格起, 几格高)。"""
    best, i = (0, 0), 0
    while i < len(row):
        if row[i] == ".":
            j = i
            while j < len(row) and row[j] == ".":
                j += 1
            if j - i > best[1]:
                best = (i, j - i)
            i = j
        else:
            i += 1
    return best


def build(walls: int, gates: int, gap: int, fill: float, shift: int, down: int, rng: random.Random) -> list[str]:
    """一关的墙（按碰墙的顺序）。题墙均匀分在中间（头两面、最后一面不放）；口子不贴着上下两排刺（两头的格子总是刺），
    和上一面墙的口子差得不远（往上最多 shift 格、往下最多 down 格：按横穿的时间来得及扑上去 / 掉下来）；题墙后第一面墙刺少一点。"""
    at = {round((i + 1) * walls / (gates + 1)) for i in range(gates)}
    at = {min(walls - 2, max(2, a)) for a in at}
    rows, prev, calm = [], SLOTS // 2 - gap // 2, True
    for i in range(walls):
        if i in at:
            rows.append("q" * SLOTS)
            calm = True
            continue
        lo, hi = max(1, prev - shift), min(SLOTS - 1 - gap, prev + min(shift, down))
        start = rng.randint(lo, hi)
        cells = ["."] * SLOTS
        p = fill * (0.5 if calm or i == 0 else 1)
        for j in range(SLOTS):
            if not start <= j < start + gap and rng.random() < p:
                cells[j] = "s"
        for j in (start - 1, start + gap, 0, SLOTS - 1):   # 口子两边总有刺，看得出口子在哪；贴着上下两排刺的角上也是刺
            cells[j] = "s"
        rows.append("".join(cells))
        prev, calm = start, False
    return rows
