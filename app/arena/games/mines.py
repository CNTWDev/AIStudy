"""翻花田（1～4 人，闯关）：花田的草皮下面睡着小刺猬，翻开草皮看数字（周围睡着几只），推理出刺猬在哪就插一朵小花，
把安全的格子全翻开就过关。前端：app/static/play/mines.js；设计：docs/扫雷-讨论稿（项目文件），设计稿 ui-2026-11-11-mines/。

扫雷（Minesweeper）那一类推理游戏，画面和规矩是我们自己的：没有地雷、红旗、爆炸——刺猬只是在睡觉，吵醒了答对 1 道题
（共用的复活规矩）它就又睡着，这一格插上小花，接着玩；不会输，只是少一颗星。

一关：
- kind = "sq" 方格（一圈 8 个邻居），"hex" 六边形格（奇数行往右错半格，一圈 6 个）；rows × cols；
- holes：糖石（不是格子：糖果园起，花田变出形状）；
- mines：{格子: 几只}，大刺猬算 2 只（星空岛起）；
- tunnels：[[a, b]] 地道两头也算挨着（火山口起）；
- start：第一下点的格子（周围一圈都没有刺猬，一点就翻开一大片），前端标出来；
- qs：题格（金色花苞，下面一定安全）：点它答一道题，答对开出探照花——周围安全的格子翻开、刺猬自动插上小花；
  答错再选，连错两次亮出答案照样开（不算星）。翻开一大片的波浪不会替你翻开题格，题格要自己点，要全部打开才过关；
- honey：藏着蜂蜜的格子，翻到就收。
每一关都「不用猜」：生成后用求解器从 start 开始只靠推理（题格可以随时打开）翻完整块花田，翻不完就重新生成。
- 几个人一起：每人一块自己的花田（seats，按角色），是同一关翻过、转过的样子，一样难，抄不了（variant）。
- 三颗星：大家都翻完；一只刺猬都没吵醒（几个人一起，每多 1 人多放宽 1 次）；题格都一次答对（同上）。
"""
import random

from . import Game, register
from .link import neighbors_hex

SQ = tuple((a, b) for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b)


def cells(lv: dict) -> list[int]:
    holes = set(lv.get("holes") or [])
    return [i for i in range(lv["rows"] * lv["cols"]) if i not in holes]


def graph(lv: dict) -> dict[int, list[int]]:
    """格子 → 挨着的格子（糖石不算格子；地道两头也挨着）。和前端 mines.js 的 graph() 一样。"""
    rows, cols, hexed = lv["rows"], lv["cols"], lv.get("kind", "sq") == "hex"
    holes = set(lv.get("holes") or [])
    adj: dict[int, list[int]] = {}
    for i in cells(lv):
        r, c = divmod(i, cols)
        nbs = neighbors_hex(r, c, rows, cols) if hexed else \
            ((r + a, c + b) for a, b in SQ if 0 <= r + a < rows and 0 <= c + b < cols)
        adj[i] = [rr * cols + cc for rr, cc in nbs if rr * cols + cc not in holes]
    for a, b in lv.get("tunnels") or []:
        if b not in adj[a]:
            adj[a].append(b)
            adj[b].append(a)
    return adj


def weights(lv: dict) -> dict[int, int]:
    return {int(k): int(v) for k, v in (lv.get("mines") or {}).items()}


def numbers(lv: dict, adj: dict[int, list[int]] | None = None) -> dict[int, int]:
    adj = adj or graph(lv)
    w = weights(lv)
    return {i: sum(w.get(j, 0) for j in nb) for i, nb in adj.items()}


def solve(lv: dict, budget: int = 40000) -> bool:
    """只靠推理（加上随时能点的题格）能不能翻完所有安全的格子。和孩子能做的一样：
    看翻开的数字，找出一定安全 / 一定有刺猬的格子（把挨在一起的未知格子成组枚举所有可能）；推不下去就点一个题格
    （题格下面安全，答完以后周围安全的翻开、刺猬插上花）；最后用「一共还剩几只」收尾。"""
    adj, w = graph(lv), weights(lv)
    num = numbers(lv, adj)
    big = bool(lv.get("big"))
    qs = list(lv.get("qs") or [])
    qset = set(qs)
    safe = [i for i in adj if i not in w]
    opened: set[int] = set()
    known: dict[int, int] = {}   # 认出来的刺猬（几只）

    def open_(i: int) -> None:
        todo = [i]
        while todo:
            x = todo.pop()
            if x in opened:
                continue
            opened.add(x)
            if num[x] == 0 and x not in qset:
                todo.extend(y for y in adj[x] if y not in opened and y not in qset and y not in w)

    def use_q(q: int) -> None:
        qset.discard(q)
        opened.add(q)
        for y in adj[q]:
            if y in w:
                known[y] = w[y]
            elif y not in qset:
                open_(y)

    def unknown(x: int) -> bool:
        return x not in opened and x not in known and x not in qset

    open_(lv["start"])
    while True:
        if all(i in opened for i in safe):
            return True
        progress = False
        # 约束：翻开的数字 - 认出来的刺猬 = 周围未知格子加起来
        cons = []
        for x in opened:
            if x in qset:
                continue
            u = [y for y in adj[x] if unknown(y)]
            if u:
                cons.append((u, num[x] - sum(known.get(y, 0) for y in adj[x])))
        # 按共用的约束把未知格子分组
        var_cons: dict[int, list[int]] = {}
        for ci, (u, _) in enumerate(cons):
            for y in u:
                var_cons.setdefault(y, []).append(ci)
        seen: set[int] = set()
        for v0 in list(var_cons):
            if v0 in seen:
                continue
            comp, ccs, stack = [], set(), [v0]
            seen.add(v0)
            while stack:
                v = stack.pop()
                comp.append(v)
                for ci in var_cons[v]:
                    if ci in ccs:
                        continue
                    ccs.add(ci)
                    for y in cons[ci][0]:
                        if y not in seen:
                            seen.add(y)
                            stack.append(y)
            got = enum(comp, [cons[ci] for ci in ccs], 2 if big else 1, [0], budget)
            if got is None:
                continue
            for v, vals in got.items():
                if vals == {0}:
                    open_(v)
                    progress = True
                elif len(vals) == 1:
                    known[v] = next(iter(vals))
                    progress = True
        if progress:
            continue
        if qs and any(q in qset for q in qs):
            use_q(next(q for q in qs if q in qset))
            continue
        # 收尾：一共还剩几只（插了花的不算）都认出来了，剩下的都安全
        left = sum(w.values()) - sum(known.values())
        rest = [i for i in adj if unknown(i)]
        if left == 0 and rest:
            for i in rest:
                open_(i)
            continue
        if rest and left == len(rest) * (2 if big else 1):
            for i in rest:
                known[i] = 2 if big else 1
            continue
        return False


def enum(comp: list[int], cons: list, top: int, steps: list, budget: int) -> dict[int, set[int]] | None:
    """一组未知格子所有说得通的放法里，每格可能有几只。太多了算不完就返回 None（这一组先不管）。"""
    if len(comp) > 26:
        return None
    idx = {v: k for k, v in enumerate(comp)}
    cs = [([idx[y] for y in u], r) for u, r in cons]
    by_var: list[list[int]] = [[] for _ in comp]
    for ci, (u, _) in enumerate(cs):
        for k in u:
            by_var[k].append(ci)
    # 先填约束多的格子，剪枝早
    order = sorted(range(len(comp)), key=lambda k: -len(by_var[k]))
    val = [0] * len(comp)
    acc = [0] * len(cs)
    left = [len(u) for u, _ in cs]
    poss: list[set[int]] = [set() for _ in comp]
    found = [0]

    def ok(ci: int) -> bool:
        r = cs[ci][1]
        return acc[ci] <= r <= acc[ci] + left[ci] * top

    def go(d: int) -> bool:
        steps[0] += 1
        if steps[0] > budget:
            return False
        if d == len(order):
            found[0] += 1
            for k in range(len(comp)):
                poss[k].add(val[k])
            return True
        k = order[d]
        for x in range(top + 1):
            val[k] = x
            for ci in by_var[k]:
                acc[ci] += x
                left[ci] -= 1
            if all(ok(ci) for ci in by_var[k]):
                if not go(d + 1) and steps[0] > budget:
                    for ci in by_var[k]:
                        acc[ci] -= x
                        left[ci] += 1
                    return False
            for ci in by_var[k]:
                acc[ci] -= x
                left[ci] += 1
        val[k] = 0
        return True

    go(0)
    if steps[0] > budget or not found[0]:
        return None
    return {comp[k]: poss[k] for k in range(len(comp))}


def variant(lv: dict, k: int) -> dict:
    """第 k 种样子（k = 0 原样）：上下翻、左右翻、转半圈（不转 90°：花田竖着放，手机上宽度不变）。
    六边形格只有一种翻法还是同样的格子：行数是单数时上下翻，双数时转半圈。只换格子号，难度一样。"""
    rows, cols = lv["rows"], lv["cols"]
    if lv.get("kind", "sq") == "hex":
        flips = [(False, False), (True, rows % 2 == 0)]
    else:
        flips = [(False, False), (True, False), (False, True), (True, True)]
    v, h = flips[k % len(flips)]

    def cell(i: int) -> int:
        r, c = divmod(int(i), cols)
        return (rows - 1 - r if v else r) * cols + (cols - 1 - c if h else c)

    return {**lv, "holes": sorted(cell(i) for i in lv.get("holes") or []),
            "mines": {str(cell(i)): n for i, n in (lv.get("mines") or {}).items()},
            "tunnels": [sorted((cell(a), cell(b))) for a, b in lv.get("tunnels") or []],
            "start": cell(lv["start"]), "qs": [cell(q) for q in lv.get("qs") or []],
            "honey": sorted(cell(i) for i in lv.get("honey") or []),
            "seed": (lv.get("seed", 0) + k * 7919) % (1 << 30)}


SHAPES = ("heart", "corners", "middle", "ring")


def holes_for(rows: int, cols: int, rng: random.Random) -> list[int]:
    """糖果园起：花田变出形状——四个角缺一块、中间几块糖石、两边一圈……不会把花田切成两半。"""
    kind = rng.choice(SHAPES)
    out = set()
    if kind in ("corners", "heart"):
        k = 2 if min(rows, cols) >= 8 else 1
        for r in range(k):
            for c in range(k - r):
                out |= {r * cols + c, r * cols + cols - 1 - c}
                if kind == "corners":
                    out |= {(rows - 1 - r) * cols + c, (rows - 1 - r) * cols + cols - 1 - c}
        if kind == "heart":   # 下面收成尖
            for r in range(rows - 2, rows):
                d = r - (rows - 3)
                for c in range(d):
                    out |= {r * cols + c, r * cols + cols - 1 - c}
    if kind in ("middle", "ring"):
        r0, c0 = rows // 2 - 1, cols // 2 - 1
        out |= {r0 * cols + c0, (r0 + 1) * cols + c0 + 1} if kind == "middle" else {r0 * cols + c0, r0 * cols + c0 + 1, (r0 + 1) * cols + c0, (r0 + 1) * cols + c0 + 1}
    return sorted(out)


def connected(lv: dict) -> bool:
    adj = graph(lv)
    if not adj:
        return False
    s = next(iter(adj))
    seen, todo = {s}, [s]
    while todo:
        x = todo.pop()
        for y in adj[x]:
            if y not in seen:
                seen.add(y)
                todo.append(y)
    return len(seen) == len(adj)


@register
class Mines(Game):
    id, name, icon, color, tag = "mines", "翻花田", "🦔", "#8fd16a", "1～4 人"
    desc = "翻开花田的草皮，看数字推出小刺猬睡在哪，插上小花别吵醒它；点金色花苞答题，开出探照花！一个人、全家一起都能玩。"
    players = (1, 4)
    roles = {"sun": ("太阳蜂", "🟡"), "berry": ("莓果蜂", "🔴"), "mint": ("薄荷蜂", "🟢"), "sky": ("天空蜂", "🔵")}
    css = True
    target, max_seconds = 0.75, 600
    freeze = False
    shape = "choices"
    record = "seconds"
    count = 1000
    per_world = 6
    stars = (("clear", "大家都翻完"), ("safe", "没吵醒刺猬"), ("gates", "题格都一次答对"))
    # 第一个世界是「白天」场景：孩子选的主题换它的颜色（DESIGN 5.45）
    worlds = (("晴空花园", "day"), ("糖果园", "candy"), ("黄昏谷", "sunset"), ("星空岛", "night"), ("火山口", "lava"))
    PLACES = ("小苗圃", "蒲公英坡", "向日葵田", "风车旁", "花篱笆", "大花田")
    # (从第几关起, 行, 列, 刺猬占多少)：宽最多 10，手机竖屏也放得下；电脑、手机同一关同一块花田
    SIZES = ((0, 6, 6, 0.11), (3, 7, 7, 0.13), (12, 8, 8, 0.14), (30, 10, 8, 0.15),
             (60, 11, 9, 0.16), (150, 12, 10, 0.17), (400, 12, 10, 0.18))

    def title(self, n):
        return f"{self.world_of(n)['name']} · {self.PLACES[(n - 1) % self.per_world]}"

    def make(self, n, rng):
        k = n - 1
        w = k // self.per_world % len(self.worlds)
        rnd = k // (self.per_world * len(self.worlds))       # 第几轮（一轮 30 关）：过了第一轮，新东西都在
        rows, cols, dens = next(s[1:] for s in reversed(self.SIZES) if k >= s[0])
        hexed = w == 2
        big = w == 3 or (rnd and w >= 3)
        has_holes = w >= 1 or rnd
        has_tunnels = w == 4 or (rnd and w >= 4)
        nq = 1 if k < 3 else 2 if k < 12 else 3 if k < 60 else 4
        for tries in range(400):
            d = dens - 0.01 * (tries // 80)                   # 很难生成就少放一点刺猬
            lv = {"name": self.title(n), "kind": "hex" if hexed else "sq", "rows": rows, "cols": cols,
                  "holes": holes_for(rows, cols, rng) if has_holes else [], "tunnels": [], "big": bool(big),
                  "seed": rng.randrange(1 << 30)}
            if not connected(lv):
                continue
            if has_tunnels:
                cs = cells(lv)
                for _ in range(2 if rows * cols > 80 else 1):
                    for _ in range(50):
                        a, b = rng.sample(cs, 2)
                        (ra, ca), (rb, cb) = divmod(a, cols), divmod(b, cols)
                        if abs(ra - rb) + abs(ca - cb) >= max(rows, cols) // 2 and not {a, b} & {x for t in lv["tunnels"] for x in t}:
                            lv["tunnels"].append(sorted((a, b)))
                            break
            adj = graph(lv)
            cs = list(adj)
            mid = [i for i in cs if 1 <= i // cols <= rows - 2 and 1 <= i % cols <= cols - 2 and len(adj[i]) >= (6 if hexed else 8)]
            start = rng.choice(mid or cs)
            ring = {start, *adj[start]}
            pool = [i for i in cs if i not in ring]
            m = max(3, round(len(cs) * d))
            ms = rng.sample(pool, min(m, len(pool) - 4))
            mines = {i: (2 if big and rng.random() < 0.3 else 1) for i in ms}
            lv["mines"] = {str(i): v for i, v in mines.items()}
            lv["start"] = start
            num = numbers(lv, adj)
            if num[start] != 0:
                continue
            # 一开始一下翻开的那片
            first, todo = set(), [start]
            while todo:
                x = todo.pop()
                if x in first:
                    continue
                first.add(x)
                if num[x] == 0:
                    todo.extend(y for y in adj[x] if y not in mines)
            if not max(6, len(cs) // 6) <= len(first) <= len(cs) * 0.5:
                continue
            # 题格：安全、不在一开始那片里，尽量散开
            cand = [i for i in cs if i not in mines and i not in first]
            rng.shuffle(cand)
            qs: list[int] = []
            for _ in range(nq):
                if not cand:
                    break
                best = max(cand, key=lambda i: min([abs(i // cols - q // cols) + abs(i % cols - q % cols) for q in qs] or [99]) + rng.random())
                qs.append(best)
                cand.remove(best)
            lv["qs"] = qs
            if len(qs) < nq or not solve(lv):
                continue
            safe = [i for i in cs if i not in mines and i not in qs and i != start]
            lv["honey"] = sorted(rng.sample(safe, min(len(safe), max(2, len(cs) // 16))))
            lv["par"] = int(len(cs) * (2.0 if k < 30 else 1.5)) + 20
            return lv
        raise RuntimeError(f"翻花田第 {n} 关生成不出来")

    def check(self, lv, name):
        try:
            adj = graph(lv)
            w = weights(lv)
            ok = 2 <= lv["rows"] <= 12 and 2 <= lv["cols"] <= 10 and lv.get("kind", "sq") in ("sq", "hex") \
                and lv["start"] in adj and lv["start"] not in w and all(i in adj for i in w) \
                and all(q in adj and q not in w for q in lv.get("qs") or []) and all(v in (1, 2) for v in w.values())
        except (KeyError, TypeError, ValueError, IndexError):
            ok = False
        if not ok:
            raise ValueError(f"{self.id}/{name}: 起点、题格要在安全的格子上，刺猬在花田里")

    def stage(self, n, prog):
        out = super().stage(n, prog)
        best = next((lv["best"] for lv in prog.get("levels", []) if lv["n"] == int(n)), None)
        return {**out, "best": best}

    def for_team(self, lv, k):
        """几个人一起：每个角色一块自己的花田（seats），前端按自己的角色取。同一关翻一翻、转半圈，一样难。"""
        if k <= 1:
            return lv
        keep = ("holes", "mines", "tunnels", "start", "qs", "honey", "seed")
        seats = {role: {f: x for f, x in variant(lv, i).items() if f in keep} for i, role in enumerate(self.roles)}
        return {**lv, "team": k, "seats": seats}

    def clean(self, lv, rep):
        out = super().clean(lv, rep)
        try:
            out["gates"] = max(0, min(99, int(rep.get("gates") or 0)))
        except (TypeError, ValueError):
            out["gates"] = 0
        return out

    def checks(self, lv, rep, focus):
        k = lv.get("team", 1)   # gates：题格都一次答对的人数；人多每多 1 人放宽 1 个，和一笔连花路一样
        return {"clear": True, "safe": rep["deaths"] <= k - 1, "gates": rep["gates"] >= 1}
