"""一笔连花路（1～4 人，闯关）：小蜜蜂从起点出发，一笔走满整个花园，按顺序经过每朵花，最后飞回蜂巢。
前端：app/static/play/link.js；设计：docs/一笔连花路-调研与建议（项目文件），设计稿 ui-2026-11-03-zip/。

「一笔画走满格子、按顺序经过节点」那一类解谜（LinkedIn Zip、Flow Free、Numbrix 一路），画面和规矩是我们自己的。
题就是玩法：花上写的是要学的东西（算式、字母），连的顺序就是答案——按得数从小到大、按字母拼出单词。
题按「排序题」出（shape = "sequence"，sources.as_sequence），一关一道，开局时游戏要来，按个数把花摆在路上。

一关的格子是一张图（graph）：
- kind = "sq" 方格（rows × cols），"hex" 六边形格（奇数行往右错半格）；
- walls：[[a, b]] 两格中间有篱笆，走不过去（只在方格上）；
- bridges：[a] 这一格有小桥，横着、竖着可以各走一次（小桥竖着走的那一次记成 N + a，N = 格子数）；
- warps：[[a, b]] 两朵魔法花，在一朵上可以直接连到另一朵。
path 是生成时找到的一条走满全图的路（每个点正好一次）：保证这一关走得通；孩子走出别的路也算，
不要求唯一解（顺序是知识，蒙不出来）。花摆在 path 上（前端按题的个数均匀摆，第一朵是起点、最后一朵是蜂巢）。
前 2 关是教玩法的（tut）：花上写 1、2、3，不出题。
每个世界加一样新东西，从那个世界起一直有：篱笆小院起有篱笆、小溪起有小桥、魔法花园起有魔法花；蜂巢世界是六边形格子。
- 三颗星：大家都走满；没用「帮帮我」（几个人一起，每多 1 人多放宽 1 次）；每一步都一次连对（同上）。
"""
import random

from . import Game, register

DIRS = ((0, 1), (1, 0), (0, -1), (-1, 0))


def neighbors_hex(r: int, c: int, rows: int, cols: int):
    """奇数行往右错半格的六边形格：左右两个，上下各两个。"""
    d = ((-1, 0), (-1, 1), (1, 0), (1, 1)) if r % 2 else ((-1, -1), (-1, 0), (1, -1), (1, 0))
    for dr, dc in ((0, -1), (0, 1)) + d:
        rr, cc = r + dr, c + dc
        if 0 <= rr < rows and 0 <= cc < cols:
            yield rr, cc


def graph(lv: dict) -> dict[int, set[int]]:
    """一关的图：点 → 相邻的点。和前端 link.js 的 graph() 一样。"""
    rows, cols, kind = lv["rows"], lv["cols"], lv.get("kind", "sq")
    n = rows * cols
    walls = {frozenset(w) for w in lv.get("walls") or []}
    bridges = set(lv.get("bridges") or [])
    adj: dict[int, set[int]] = {i: set() for i in range(n)}
    for b in bridges:
        adj[n + b] = set()

    def slot(i: int, vertical: bool) -> int:
        return n + i if i in bridges and vertical else i

    for r in range(rows):
        for c in range(cols):
            a = r * cols + c
            nbs = neighbors_hex(r, c, rows, cols) if kind == "hex" else \
                ((r + dr, c + dc) for dr, dc in DIRS if 0 <= r + dr < rows and 0 <= c + dc < cols)
            for rr, cc in nbs:
                b = rr * cols + cc
                if frozenset((a, b)) in walls:
                    continue
                v = kind != "hex" and rr != r
                adj[slot(a, v)].add(slot(b, v))
    for a, b in lv.get("warps") or []:
        adj[a].add(b)
        adj[b].add(a)
    return adj


def walk(adj: dict[int, set[int]], rng: random.Random, start: int | None = None, budget: int = 20000) -> list[int] | None:
    """在图上找一条走满所有点的路（随机深搜：先走出路少的点，带一点随机，走不通就退回）。找不到返回 None。
    剪枝：没走到的点里，一条路都不剩的（走不到了）不行；只剩一条路的（只能当终点）多于一个也不行。"""
    nodes = list(adj)
    total, path, seen, steps = len(nodes), [], set(), [0]

    def free(x: int) -> int:
        return sum(1 for y in adj[x] if y not in seen)

    def stuck(head: int) -> bool:
        ends, left = 0, total - len(path)
        for x in adj:
            if x in seen:
                continue
            f = free(x)
            if head in adj[x]:
                if f == 0 and left > 1:   # 只能下一步走到它、它又是死胡同
                    return True
            elif f == 0:
                return True
            elif f == 1:
                ends += 1
                if ends > 1:
                    return True
        return False

    def go(v: int) -> bool:
        path.append(v)
        seen.add(v)
        if len(path) == total:
            return True
        steps[0] += 1
        if steps[0] > budget or stuck(v):
            path.pop()
            seen.discard(v)
            return False
        nxt = [u for u in adj[v] if u not in seen]
        nxt.sort(key=lambda u: free(u) + rng.random() * 0.9)
        for u in nxt:
            if go(u):
                return True
            if steps[0] > budget:
                break
        path.pop()
        seen.discard(v)
        return False

    s = start if start is not None else rng.choice(nodes)
    return list(path) if go(s) else None


def starts(lv: dict, adj: dict[int, set[int]]) -> list[int]:
    """能当起点的点。方格是黑白相间的（相邻两格颜色不同）：黑白格数不一样多时，走满的路只能从多的那种颜色出发；
    魔法花总是连着一黑一白（make 里挑的），不破坏黑白相间。"""
    nodes = list(adj)
    if lv.get("kind", "sq") == "hex":
        return nodes
    n, cols = lv["rows"] * lv["cols"], lv["cols"]
    color = {v: sum(divmod(v % n, cols)) % 2 for v in nodes}
    black = sum(color.values())
    if black * 2 == len(nodes):
        return nodes
    big = 1 if black * 2 > len(nodes) else 0
    return [v for v in nodes if color[v] == big]


def find(lv: dict, rng: random.Random, tries: int = 40) -> list[int] | None:
    """换几个起点找走满的路（一个起点搜不通很快放弃，换下一个）。"""
    adj = graph(lv)
    ok = starts(lv, adj)
    for _ in range(tries):
        p = walk(adj, rng, rng.choice(ok), budget=3000)
        if p:
            return p
    return None


def is_path(adj: dict[int, set[int]], path: list[int]) -> bool:
    return len(path) == len(adj) == len(set(path)) and all(b in adj[a] for a, b in zip(path, path[1:]))


def turns(lv: dict, path: list[int]) -> int:
    """路拐了几个弯（方格）：弯多的路走起来有意思，挑它。"""
    n, cols = lv["rows"] * lv["cols"], lv["cols"]
    pos = [divmod(p % n, cols) for p in path]
    d = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(pos, pos[1:])]
    return sum(1 for x, y in zip(d, d[1:]) if x != y)


@register
class Link(Game):
    id, name, icon, color, tag = "link", "一笔连花路", "🌼", "#ff9fc4", "1～4 人"
    desc = "小蜜蜂一笔走满花园，按顺序采过每朵花——花上写的是要学的东西，顺序要自己想。一个人、全家一起都能玩。"
    players = (1, 4)
    roles = {"sun": ("太阳蜂", "🟡"), "berry": ("莓果蜂", "🔴"), "mint": ("薄荷蜂", "🟢"), "sky": ("天空蜂", "🔵")}
    css = True
    target, max_seconds = 0.75, 600
    freeze = False
    shape = "sequence"
    record = "seconds"
    count = 1000
    per_world = 6
    stars = (("clear", "大家都走满"), ("safe", "没用「帮帮我」"), ("gates", "每一步都一次连对"))
    worlds = (("晴空花园", "day"), ("篱笆小院", "candy"), ("小溪", "day"), ("魔法花园", "night"), ("蜂巢", "sunset"))
    PLACES = ("小苗圃", "蒲公英坡", "向日葵田", "风车旁", "花篱笆", "大花园")
    TUT = 2   # 前几关教玩法：花上写 1、2、3，不出题

    def title(self, n):
        return f"{self.world_of(n)['name']} · {self.PLACES[(n - 1) % self.per_world]}"

    def make(self, n, rng):
        k = n - 1
        w = k // self.per_world                              # 第几个世界（从 0 数，过了一轮新东西都在）
        hexed = w % len(self.worlds) == 4
        size = 4 if k < 3 else 5 if k < 18 else 6 if k < 60 else 7 if k < 240 else 8
        rows = cols = min(size, 7) if hexed else size
        lv = {"name": self.title(n), "kind": "hex" if hexed else "sq", "rows": rows, "cols": cols,
              "walls": [], "bridges": [], "warps": [], "tut": n <= self.TUT, "seed": rng.randrange(1 << 30)}
        cells = rows * cols
        inner = [r * cols + c for r in range(1, rows - 1) for c in range(1, cols - 1)]
        if w >= 2 and not hexed:                              # 小溪起：小桥（大花园两座）
            # 黑白格数差不能超过 1（不然走不满）：小桥那一格多算一个点，按颜色挑
            diff = sum(1 if (r + c) % 2 else -1 for r in range(rows) for c in range(cols))
            picked = []
            for i in rng.sample(inner, len(inner)):
                d = 1 if sum(divmod(i, cols)) % 2 else -1
                if abs(diff + d) <= 1:
                    picked.append(i)
                    diff += d
                    if len(picked) == (1 if size < 7 else 2):
                        break
            lv["bridges"] = sorted(picked)
        if w >= 3:                                            # 魔法花园起：一对魔法花，离得远一点
            free = [i for i in range(cells) if i not in lv["bridges"]]
            for _ in range(50):
                a, b = rng.sample(free, 2)
                (ra, ca), (rb, cb) = divmod(a, cols), divmod(b, cols)
                if abs(ra - rb) + abs(ca - cb) >= size - 1 and (hexed or (ra + ca + rb + cb) % 2):   # 方格：一黑一白，还是黑白相间
                    lv["warps"] = [sorted((a, b))]
                    break
        best = None
        for _ in range(3):                                    # 找几条走满的路，挑拐弯多的
            p = find(lv, rng)
            if p and (best is None or turns(lv, p) > turns(lv, best)):
                best = p
        if best is None:                                      # 找不到（极少）：不要魔法花、小桥再找
            lv["warps"], lv["bridges"] = [], []
            best = find(lv, rng, 400)
        lv["path"] = best
        if w >= 1 and not hexed:                              # 篱笆小院起：没走的边上随机围篱笆（路还是走得通）
            used = {frozenset((a % cells, b % cells)) for a, b in zip(best, best[1:])}
            free = [(a, a + d) for a in range(cells) for d in (1, cols)
                    if (d == cols or (a + 1) % cols) and a + d < cells and frozenset((a, a + d)) not in used]
            rng.shuffle(free)
            lv["walls"] = [list(e) for e in free[:int(len(free) * min(0.45, 0.15 + k * 0.002))]]
        lv["par"] = int(len(best) * (2.2 if k < 30 else 1.8)) + 10
        return lv

    def check(self, lv, name):
        try:
            ok = 2 <= lv["rows"] <= 9 and 2 <= lv["cols"] <= 9 and lv.get("kind", "sq") in ("sq", "hex") \
                and is_path(graph(lv), lv["path"])
        except (KeyError, TypeError, IndexError):
            ok = False
        if not ok:
            raise ValueError(f"{self.id}/{name}: path 要走满整张图（每个点正好一次，一步一格）")

    def stage(self, n, prog):
        out = super().stage(n, prog)
        best = next((lv["best"] for lv in prog.get("levels", []) if lv["n"] == int(n)), None)
        return {**out, "best": best}

    def for_team(self, lv, k):
        return {**lv, "team": max(1, k)} if k > 1 else lv

    def clean(self, lv, rep):
        out = super().clean(lv, rep)
        try:
            out["gates"] = max(0, min(99, int(rep.get("gates") or 0)))
        except (TypeError, ValueError):
            out["gates"] = 0
        return out

    def checks(self, lv, rep, focus):
        k = lv.get("team", 1)   # gates：一次连对的人数（一人一道题）；人多每多 1 人放宽 1 个，和左右飞一样
        return {"clear": True, "safe": rep["deaths"] <= k - 1, "gates": bool(lv.get("tut")) or rep["gates"] >= k - (k - 1)}
