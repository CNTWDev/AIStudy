"""向下跳（1～4 人，闯关）：一根柱子上一层层圆盘，转柱子让球从洞里掉下去，躲开刺，最快掉到底。
前端：app/static/play/helix.js；设计：docs/向下跳-讨论稿（项目文件）。

圆盘一层 12 格（SECTORS），每层写成一串字母：n 普通（弹一下）、g 洞（掉下去）、s 刺（倒下）；
q 是「题门」那一层：只放普通格，前端拿到题以后按选项个数在上面开 2～4 个洞、洞口立答案牌。
题就是玩法：球一直原地弹，想好了把对的洞转到球下面。第一次掉进哪个洞就是答案（记一次作答）：
对了变火球（连穿几层不落地也变火球，火球砸得碎普通格和刺）；错了洞口有盖子弹回来，再选；连错两次亮出答案照样过。
题门的题要能写成 2～4 个短选项，所以这款游戏的题都按「选一个」出（shape = "choices"，sources.as_choices）；
倒下答题复活也是同一种题，和所有游戏一样答对 1 道就起来。
用时：球停在题门上想题的前 GATE_FREE 秒不算（前端报上来的 seconds 已经扣掉），比的是转得准、下得快。
几个人一起：同一座塔（同一关）各跳各的，各答各的题；右边的深度条上看得到大家掉到哪了，谁先到底谁第一，
大家都到底才算这一关过（开房间的人报：用时是最后一个人到底的用时，题门是大家一次答对的加起来）。
- 三颗星：大家都到底；没倒下（几个人一起，每多 1 人多放宽 1 次）；题门一次答对（几个人一起，每多 1 人多放宽 1 道）。
"""
import random

from . import Game, register

SECTORS = 12
GATE_FREE = 8          # 题门上想题，前几秒不计时（前端用）


@register
class Helix(Game):
    id, name, icon, color, tag = "helix", "向下跳", "🌀", "#ffb21e", "1～4 人"
    desc = "转动柱子，让小球从洞里一层层掉下去，躲开刺，穿过题门，看谁先到底！一个人、全家一起都能玩。"
    players = (1, 4)
    roles = {"sun": ("太阳球", "🟡"), "berry": ("莓果球", "🔴"), "mint": ("薄荷球", "🟢"), "sky": ("天空球", "🔵")}
    css = True
    target, max_seconds = 0.75, 600
    freeze = False
    shape = "choices"
    record = "seconds"
    count = 1000
    per_world = 6
    stars = (("clear", "大家都到底"), ("safe", "不倒下"), ("gates", "题门一次答对"))
    # 第一个世界是「白天」场景：孩子选的主题换它的颜色（DESIGN 5.45）；后面的世界各有一样新花样（make 里的 spin、blink）
    worlds = (("晴空塔", "day"), ("糖果塔", "candy"), ("黄昏塔", "sunset"), ("星空塔", "night"), ("火山塔", "lava"))
    PLACES = ("塔顶", "云层", "旋梯", "风口", "深井", "塔底")

    def title(self, n):
        return f"{self.world_of(n)['name']} · {self.PLACES[(n - 1) % self.per_world]}"

    def make(self, n, rng):
        k = n - 1
        layers = min(60, 14 + k // 3)
        gates = max(2, min(7, layers // 8))
        spike = min(0.42, 0.08 + k * 0.006)                 # 不是洞的格子里，刺占多少
        hole = 3 if k < 6 else 2 if k < 30 else 1           # 一个洞几格宽
        holes2 = 0.6 if k < 6 else max(0.15, 0.5 - k * 0.004)   # 一层有两个洞的机会
        spin = 0 if k < 6 else round(min(0.35, 0.1 + (k - 6) * 0.004), 3)    # 第 2 个世界起：会自己转的层占多少
        blink = 0 if k < 12 else round(min(0.3, 0.08 + (k - 12) * 0.004), 3)  # 第 3 个世界起：一闪一闪的刺
        rows = build(layers, gates, spike, hole, holes2, forgive=k < 6, rng=rng)
        return {"name": self.title(n), "par": layers * 2, "rows": rows, "gates": sum(r == "q" * SECTORS for r in rows),
                "spin": spin, "blink": blink, "seed": rng.randrange(1 << 30)}

    def check(self, lv, name):
        rows = lv.get("rows") or []
        if not 4 <= len(rows) <= 80:
            raise ValueError(f"{self.id}/{name}: rows 要 4–80 层")
        for i, r in enumerate(rows):
            if len(r) != SECTORS or set(r) - set("ngsq"):
                raise ValueError(f"{self.id}/{name}: 第 {i + 1} 层要是 {SECTORS} 个 n/g/s/q")
            if r != "q" * SECTORS and ("g" not in r or "n" not in r):
                raise ValueError(f"{self.id}/{name}: 第 {i + 1} 层要有洞，也要有能落脚的地方")

    def stage(self, n, prog):
        """开局：带上这一关自己的纪录，深度条上的 👻「上次的我」按它匀速往下走。"""
        out = super().stage(n, prog)
        best = next((lv["best"] for lv in prog.get("levels", []) if lv["n"] == int(n)), None)
        return {**out, "best": best}

    def for_team(self, lv, k):
        return {**lv, "team": max(1, k)} if k > 1 else lv

    def checks(self, lv, rep, focus):
        k = lv.get("team", 1)   # score：大家题门一次答对的加起来
        return {"clear": True, "safe": rep["deaths"] <= k - 1, "gates": rep["score"] >= lv["gates"] * k - (k - 1)}


def build(layers: int, gates: int, spike: float, hole: int, holes2: float, forgive: bool, rng: random.Random) -> list[str]:
    """一关的圆盘（从上往下）。题门均匀分在中间（第一层、最后一层不放）；题门前后一层不放刺，球从题门掉下来不会马上撞刺。
    forgive（前几关）：洞正下方的那几格不放刺，直直掉下去不会死。"""
    at = {round((i + 1) * layers / (gates + 1)) for i in range(gates)}
    at = {min(layers - 2, max(1, a)) for a in at}
    rows, prev = [], None
    for i in range(layers):
        if i in at:
            rows.append("q" * SECTORS)
            prev = None
            continue
        cells = ["n"] * SECTORS
        start = rng.randrange(SECTORS)
        for j in range(hole):
            cells[(start + j) % SECTORS] = "g"
        if rng.random() < holes2:
            s2 = (start + SECTORS // 2 + rng.randrange(-1, 2)) % SECTORS
            for j in range(max(1, hole - 1)):
                cells[(s2 + j) % SECTORS] = "g"
        calm = i == 0 or (i - 1) in at or (i + 1) in at
        if not calm:
            for j in range(SECTORS):
                if cells[j] == "n" and rng.random() < spike and not (forgive and prev and prev[j] == "g"):
                    cells[j] = "s"
            if "n" not in cells:   # 总要有能落脚的地方
                cells[next(j for j in range(SECTORS) if cells[j] == "s")] = "n"
        prev = "".join(cells)
        rows.append(prev)
    return rows
