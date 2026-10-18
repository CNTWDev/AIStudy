"""双人闯关：自动生成第 17 关以后的关卡，写到 app/arena/games/levels/duo_more.json（一行一关）。

做法：地图分成 2～3 层（band），每层一条走廊；用「模块」拼路线：
  按钮门、门 + 拉杆、毒水桥、岩浆 / 水池桥（只有一个人能蹚过去按按钮）、熔岩门、气闸（拉杆 + 一开一关的两扇门）、
  升降台高台（上去靠升降台，下来走台阶）、远处的按钮（按钮在前面的楼层）；
  层和层之间：台阶（自己爬）、升降台（要队友按）、直接跳下去的洞。
布局：
  serp   一条路之字形往上爬（或往下走）
  lanes  上下分开走：每人一条路，你的门、桥、升降台，按钮在队友那条路上
  towers 左右两座塔：每人爬一座，升降台和门的按钮在对面的塔里
每关用固定的种子（duo:关数、duo:关数:第几次）生成，用 tools/duo_fast.py 检查（比 duo_solver 更保守的跳跃）：
必须能过关、宝石都拿得到、不会卡死；步数要落在这一关的难度区间里（越往后越难，每 4 关一个小起伏）。

用法：python tools/gen_duo_levels.py [--to 1000] [--jobs 4]
"""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import duo_fast  # noqa: E402

COLS, ROWS = 32, 15
FIRST = 17                    # 手做的是 1～16 关
OUT = ROOT / "app/arena/games/levels/duo_more.json"
HANDMADE = ROOT / "app/arena/games/levels/duo"


# ---------------------------------------------------------------- 难度
def target(n: int) -> float:
    """第 n 关想要的步数：慢慢变难、越来越平；每个世界（4 关）里一关比一关难，下个世界第一关稍微松一点。"""
    w, p = (n - 1) // 4, (n - 1) % 4
    return 12 + 12 * (1 - math.exp(-(w - 4) / 55)) + 1.3 * p


def band_of(n: int):
    t = target(n)
    return t - 2.5, t + 3.5


# ---------------------------------------------------------------- 画布
class Band:
    """一层走廊：能走的行 top..L-1，地板在第 L 行；列 x0..x1。"""

    def __init__(self, top, L, x0, x1):
        self.top, self.L, self.x0, self.x1 = top, L, x0, x1
        self.h, self.W = L - top, x1 - x0 + 1


class Canvas:
    def __init__(self):
        self.g = [["#"] * COLS for _ in range(ROWS)]
        self.objs = []
        self.nid = 0
        self.used = set()     # 机关、池子、出生点、门占的格子（不再放装饰和宝石）
        self.tags = set()     # 用到了哪些机关（写提示用）

    def new_id(self):
        k = self.nid
        self.nid += 1
        return "abcd"[k % 4] + (str(k // 4) if k >= 4 else "")

    def carve(self, b: Band):
        for r in range(b.top, b.L):
            for c in range(b.x0, b.x1 + 1):
                self.g[r][c] = "."


class View:
    """按走的方向看一层：本地 x 从这层的起点往前数（d=+1 往右，-1 往左）。"""

    def __init__(self, cv: Canvas, b: Band, d: int):
        self.cv, self.b, self.d = cv, b, d
        self.L, self.top, self.h, self.W = b.L, b.top, b.h, b.W

    def gx(self, lx, w=1):
        return self.b.x0 + lx if self.d > 0 else self.b.x1 - lx - (w - 1)

    def get(self, r, lx):
        return self.cv.g[r][self.gx(lx)]

    def set(self, r, lx, ch, use=False):
        c = self.gx(lx)
        self.cv.g[r][c] = ch
        if use:
            self.cv.used.add((r, c))

    def free(self, lx):   # 这一格能放按钮：空的、脚下是石头、没被占
        c = self.gx(lx)
        r = self.L - 1
        return self.cv.g[r][c] == "." and self.cv.g[r + 1][c] == "#" and (r, c) not in self.cv.used

    def btn(self, lx, oid, r=None):
        r = self.L - 1 if r is None else r
        c = self.gx(lx)
        self.cv.objs.append({"k": "btn", "x": c, "y": r, "id": oid})
        self.cv.used.add((r, c))

    def lever(self, lx, oid, r=None):
        r = self.L - 1 if r is None else r
        c = self.gx(lx)
        self.cv.objs.append({"k": "lever", "x": c, "y": r, "id": oid})
        self.cv.used.add((r, c))
        self.cv.tags.add("lever")

    def gate(self, lx, oid, inv=False, r0=None, h=None, w=1):
        r0 = self.top if r0 is None else r0
        h = self.L - r0 if h is None else h
        c = self.gx(lx, w)
        o = {"k": "gate", "x": c, "y": r0, "w": w, "h": h, "id": oid}
        if inv:
            o["inv"] = True
        self.cv.objs.append(o)
        self.cv.used.update((r0 + i, c + j) for i in range(h) for j in range(w))

    def bridge(self, lx, w, oid, r):
        c = self.gx(lx, w)
        self.cv.objs.append({"k": "bridge", "x": c, "y": r, "w": w, "id": oid})
        self.cv.used.update((r, c + j) for j in range(w))
        self.cv.tags.add("bridge")

    def lift(self, lx, w, y, to, oid):
        c = self.gx(lx, w)
        self.cv.objs.append({"k": "lift", "x": c, "y": y, "w": w, "to": to, "id": oid})
        self.cv.used.update((r, c + j) for r in range(min(y, to) - 1, max(y, to) + 1) for j in range(w))
        self.cv.tags.add("lift")

    def pool(self, lx, w, kind):
        for j in range(w):
            self.set(self.L, lx + j, kind, use=True)
            self.cv.used.add((self.L - 1, self.gx(lx + j)))


# ---------------------------------------------------------------- 模块（按往右走画；View 负责翻过来）
# 每个模块：plan(rng, band) -> 参数（含宽度 w）或 None；draw(v, x, p, cv) -> 需要别处放的按钮 [(id, "remote"/"earlier")]
def p_gate_pair(rng, b):
    s = rng.choice((2, 3, 3))
    return {"w": 2 * s + 1, "s": s}


def d_gate_pair(v, x, p, cv):
    i = cv.new_id()
    s = p["s"]
    v.btn(x, i)
    v.gate(x + s, i)
    v.btn(x + 2 * s, i)
    cv.tags.add("gate_pair")
    return []


def p_gate_lever(rng, b):
    return {"w": 7}


def d_gate_lever(v, x, p, cv):
    a, k = cv.new_id(), cv.new_id()
    v.btn(x, a)
    v.gate(x + 3, f"{a}|{k}")
    v.lever(x + 6, k)
    cv.tags.add("gate_lever")
    return []


def p_lever_gate(rng, b):
    return {"w": 5, "back": rng.random() < 0.5}


def d_lever_gate(v, x, p, cv):   # 简单的：拉杆一推，门一直开着
    k = cv.new_id()
    v.lever(x, k)
    v.gate(x + 3, k)
    return []


def p_pool_g(rng, b):
    pw = rng.choice((4, 4, 5))
    return {"w": pw + 4, "pw": pw}


def d_pool_g(v, x, p, cv):
    i = cv.new_id()
    pw = p["pw"]
    v.btn(x, i)
    v.pool(x + 2, pw, "G")
    v.bridge(x + 2, pw, i, v.L - 1)
    v.btn(x + pw + 3, i)
    cv.tags.add("pool_g")
    return []


def p_pool_role(rng, b):
    pw = rng.choice((4, 5, 6))
    return {"w": pw + 3, "pw": pw, "kind": rng.choice("FW")}


def d_pool_role(v, x, p, cv):
    i = cv.new_id()
    pw, kind = p["pw"], p["kind"]
    v.pool(x + 1, pw, kind)
    v.bridge(x + 1, pw, i, v.L - 1)
    v.btn(x + pw + 2, i)
    cv.tags.add("pool_F" if kind == "F" else "pool_W")
    return []


def p_lava_gate(rng, b):
    return {"w": 13, "pw": 4, "kind": rng.choice("FW")}


def d_lava_gate(v, x, p, cv):
    i = cv.new_id()
    pw, kind = p["pw"], p["kind"]
    v.pool(x + 1, pw, kind)
    v.bridge(x + 1, pw, i, v.L - 1)
    v.btn(x + pw + 2, i)
    v.gate(x + pw + 5, i)
    v.btn(x + pw + 8, i)
    cv.tags.add("lava_gate_F" if kind == "F" else "lava_gate_W")
    return []


def p_airlock(rng, b):
    return {"w": 10}


def d_airlock(v, x, p, cv):
    lv, k = cv.new_id(), cv.new_id()
    v.lever(x, lv)
    v.gate(x + 3, f"{lv}|{k}")
    v.gate(x + 6, lv, inv=True)
    v.btn(x + 9, k)
    cv.tags.add("airlock")
    return []


def p_ledge(rng, b):
    if b.h < 6:
        return None
    lw = rng.choice((4, 5))
    return {"w": 4 + lw + 2, "lw": lw}


def d_ledge(v, x, p, cv, remote=False):
    """升降台高台：前面 4 格高上不去，坐升降台上去；后面有两级台阶，下来、回去都行。"""
    i = cv.new_id()
    L, lw = v.L, p["lw"]
    if not remote:
        v.btn(x, i)
    for j in range(lw):
        for r in range(L - 4, L):
            v.set(r, x + 4 + j, "#")
    if not remote:   # 后面的两级台阶：走过去的人还能回来帮忙（分开走的不用回来）
        for r in range(L - 2, L):
            v.set(r, x + 4 + lw, "#")
    v.lift(x + 2, 2, L - 1, L - 4, i)
    if not remote:
        v.btn(x + 4 + lw - 2, i, r=L - 5)
    cv.tags.add("ledge")
    return [(i, "remote")] if remote else []


def p_far_gate(rng, b):
    return {"w": 6}


def d_far_gate(v, x, p, cv):
    """门的一个按钮在前面（之前走过的地方），另一个在门后。"""
    i = cv.new_id()
    v.gate(x + 1, i)
    v.btn(x + 4, i)
    cv.tags.add("far_gate")
    return [(i, "earlier")]


# 分开走（lanes / towers）用的：只有一个按钮，在队友那条路上
def p_r_gate(rng, b):
    return {"w": 3}


def d_r_gate(v, x, p, cv):
    i = cv.new_id()
    v.gate(x + 1, i)
    cv.tags.add("remote")
    return [(i, "remote")]


def p_r_pool(rng, b):
    return {"w": 6, "pw": 4}


def d_r_pool(v, x, p, cv):
    i = cv.new_id()
    v.pool(x + 1, 4, "G")
    v.bridge(x + 1, 4, i, v.L - 1)
    cv.tags.add("remote")
    cv.tags.add("pool_g")
    return [(i, "remote")]


def d_r_ledge(v, x, p, cv):
    cv.tags.add("remote")
    return d_ledge(v, x, p, cv, remote=True)


# name: (plan, draw, 从第几关开始有, 权重, 预计步数)
SHARED = {
    "gate_pair": (p_gate_pair, d_gate_pair, 17, 3.0, 3.0),
    "gate_lever": (p_gate_lever, d_gate_lever, 17, 1.5, 2.5),
    "lever_gate": (p_lever_gate, d_lever_gate, 17, 0.6, 1.0),
    "pool_g": (p_pool_g, d_pool_g, 17, 2.5, 3.0),
    "pool_role": (p_pool_role, d_pool_role, 17, 2.0, 2.0),
    "ledge": (p_ledge, d_ledge, 17, 2.5, 4.0),
    "far_gate": (p_far_gate, d_far_gate, 24, 1.5, 3.0),
    "lava_gate": (p_lava_gate, d_lava_gate, 28, 2.0, 4.0),
    "airlock": (p_airlock, d_airlock, 36, 2.0, 5.0),
}
REMOTE = {
    "r_gate": (p_r_gate, d_r_gate, 17, 3.0, 2.0),
    "r_pool": (p_r_pool, d_r_pool, 17, 2.0, 2.0),
    "r_ledge": (p_ledge, d_r_ledge, 17, 2.0, 3.0),
}


# ---------------------------------------------------------------- 层和层之间
# 台阶模板：从上一层的地板（第 U 行）到下一层地板上面一行；最右边一列靠墙（e=0）
STAIRS = {
    3: ["###...", "......", "....##", "....##"],
    4: ["###...", "....##", "....##", "...###", "...###"],
    5: ["###...", "......", "....##", "....##", "...###", "...###"],
    6: ["##....", "..#...", "......", "....##", "....##", "...###", "...###"],
}
CONN_W = {"stairs": 6, "lift": 5, "rlift": 2, "drop": 3}


def draw_conn(cv, low: Band, up: Band, end: int, kind: str):
    """在 low 和 up 两层的 end 端（+1 右、-1 左）画连接；返回需要别处放的按钮。"""
    U, L = up.L, low.L

    def gx(e):
        return low.x1 - e if end > 0 else low.x0 + e

    if kind == "stairs":
        rows = STAIRS[low.h]
        for i, row in enumerate(rows):
            for k, ch in enumerate(row):
                e = 5 - k
                cv.g[U + i][gx(e)] = ch
                cv.used.add((U + i, gx(e)))
                if U + i > U:
                    cv.used.add((U + i, gx(e)))
        for e in range(6):
            cv.used.add((U - 1, gx(e)))
        return []
    if kind == "drop":
        for e in (0, 1):
            cv.g[U][gx(e)] = "."
            cv.used.add((U, gx(e)))
        for e in range(3):
            cv.used.add((U - 1, gx(e)))
            cv.used.add((L - 1, gx(e)))
        cv.tags.add("drop")
        return []
    # 升降台：靠墙两格宽
    i = cv.new_id()
    x = low.x1 - 1 if end > 0 else low.x0
    for e in (0, 1):
        cv.g[U][gx(e)] = "."
    cv.objs.append({"k": "lift", "x": x, "y": U, "w": 2, "to": L - 1, "id": i})   # 平时在上面堵着洞；按住按钮降下来接人
    if kind == "lift":
        cv.objs.append({"k": "btn", "x": gx(3), "y": L - 1, "id": i})
        cv.objs.append({"k": "btn", "x": gx(3), "y": U - 1, "id": i})
        out = []
    else:                # rlift：按钮在队友那条路上
        out = [(i, "remote")]
    for r in range(U - 1, L):
        for e in (0, 1):
            cv.used.add((r, gx(e)))
    for r in (U - 1, L - 1):
        for e in (2, 3, 4):
            cv.used.add((r, gx(e)))
    cv.tags.add("lift")
    return out


# ---------------------------------------------------------------- 一条路
class Gap:
    def __init__(self, v, a, b, order):
        self.v, self.a, self.b, self.order = v, a, b, order


class Fail(Exception):
    pass


def build_path(cv, rng, bands, d0, conns, mods, ends):
    """bands：按走的顺序；d0：第一层往哪走；conns：层间连接；mods：每层的模块 [(name, table)]；
    ends：(起点放什么, 终点放什么)，比如 ("12", "DE")。返回 gaps 和需要别处放的按钮。"""
    gaps, helpers = [], []
    order = 0
    d = d0
    for j, b in enumerate(bands):
        v = View(cv, b, d)
        a, z = 0, b.W
        if j == 0:
            a = len(ends[0]) + 1
            starts = list(ends[0])
            rng.shuffle(starts)
            slots = [0, 1] if len(starts) == 2 and rng.random() < 0.6 else [0, 2][:len(starts)]
            for ch, lx in zip(starts, slots):
                v.set(v.L - 1, lx, ch, use=True)
        else:
            a = CONN_W[conns[j - 1]]
        if j == len(bands) - 1:
            z = b.W - (len(ends[1]) + 1)
            doors = list(ends[1])
            rng.shuffle(doors)
            slots = [b.W - 1, b.W - 2] if len(doors) == 2 and rng.random() < 0.5 else [b.W - 1, b.W - 3][:len(doors)]
            for ch, lx in zip(doors, slots):
                v.set(v.L - 1, lx, ch, use=True)
        else:
            z = b.W - CONN_W[conns[j]]
        plans = mods[j]
        need = sum(p["w"] for _, _, p in plans) + 2 * (len(plans) + 1)
        room = z - a
        if need > room:
            raise Fail("放不下")
        extra = room - need
        cuts = sorted(rng.randint(0, extra) for _ in range(len(plans)))
        spaces = [2 + c2 - c1 for c1, c2 in zip([0] + cuts, cuts + [extra])]
        x = a
        for k, (name, draw, p) in enumerate(plans):
            gaps.append(Gap(v, x, x + spaces[k], order))
            x += spaces[k]
            for hid, kind in draw(v, x, p, cv):
                helpers.append((hid, kind, order))
            x += p["w"]
            order += 1
        gaps.append(Gap(v, x, z, order))
        if j < len(bands) - 1:
            low, up = (b, bands[j + 1]) if bands[j + 1].L < b.L else (bands[j + 1], b)
            for hid, kind in draw_conn(cv, low, up, d, conns[j]):
                helpers.append((hid, kind, order))
            if conns[j] in ("lift", "rlift"):
                order += 1
            d = -d
    return gaps, helpers


def place_btn(cv, rng, gaps, oid, margin=1):
    """在这些空地里找一格放按钮；离边上（门、池子）至少 margin+1 格，尽量离别的按钮远一点。"""
    cells = []
    for gp in gaps:
        for lx in range(gp.a + margin, gp.b - margin):
            if gp.v.free(lx) and gp.v.free(lx - 1) and gp.v.free(lx + 1):
                cells.append((gp, lx))
    if not cells:
        cells = [(gp, lx) for gp in gaps for lx in range(gp.a, gp.b) if gp.v.free(lx)]
    if not cells:
        raise Fail("没地方放按钮")

    def room(item):   # 同一行离最近的机关有几格
        gp, lx = item
        r, c = gp.v.L - 1, gp.v.gx(lx)
        d = 6
        for k in range(1, 6):
            if (r, c - k) in cv.used or (r, c + k) in cv.used:
                d = k
                break
        return d

    best = max(room(it) for it in cells)
    cells = [it for it in cells if room(it) >= min(best, 3)]
    gp, lx = rng.choice(cells)
    gp.v.btn(lx, oid)
    return gp


# ---------------------------------------------------------------- 装饰、宝石
def decorate(cv, rng, gaps, amount):
    """空地上放点地形：小石墩、矮台子、小水坑（跳过去）、浮台、天花板上垂下来的石头。"""
    for gp in gaps:
        v = gp.v
        lx = gp.a + 1 + rng.randint(0, 2)
        while lx < gp.b - 1:
            if rng.random() > amount:
                lx += rng.randint(2, 4)
                continue
            span = 0
            while lx + span < gp.b - 1 and v.free(lx + span) and v.free(lx + span - 1) and span < 5:
                span += 1
            if span == 0:
                lx += 1
                continue
            roll = rng.random()
            w = 0
            if roll < 0.22:                                      # 小石墩
                w = min(span, rng.choice((1, 1, 2)))
                for j in range(w):
                    v.set(v.L - 1, lx + j, "#", use=True)
            elif roll < 0.36 and v.h >= 4 and span >= 3:         # 矮台子（能站上去走）
                w = min(span, rng.randint(3, 5))
                for j in range(w):
                    v.set(v.L - 1, lx + j, "#", use=True)
            elif roll < 0.62:                                    # 小水坑：跳过去（自己的池子可以蹚）
                kind = rng.choice("GFWG")
                w = min(span, 2 if rng.random() < 0.5 else 1)
                v.pool(lx, w, kind)
            elif roll < 0.85 and v.h >= 4 and span >= 2:         # 浮台
                w = min(span, rng.choice((2, 2, 3)))
                for j in range(w):
                    v.set(v.L - 2, lx + j, "#")
                    v.cv.used.add((v.L - 1, v.gx(lx + j)))
            elif v.h >= 5:                                       # 天花板上垂下来的石头
                v.set(v.top, lx, "#")
                if v.h >= 6 and rng.random() < 0.5:
                    v.set(v.top + 1, lx, "#")
                w = 1
            lx += max(1, w) + rng.randint(2, 4)


def gem_spots(cv):
    """可能放宝石的格子：空中能跳到的地方、池子上面、石墩和浮台上面。"""
    g, out = cv.g, []
    for r in range(1, ROWS - 1):
        for c in range(1, COLS - 1):
            if g[r][c] != "." or (r, c) in cv.used:
                continue
            below = g[r + 1][c]
            if below in "FW":
                out.append((r, c, "f" if below == "F" else "w", 3))
            elif below == "#" and r + 2 < ROWS and g[r + 2][c] != "#" and (r + 1, c) not in cv.used:
                out.append((r, c, None, 2))          # 石墩、浮台上
            elif below == "." and r + 2 < ROWS and g[r + 2][c] in "#" and (r + 1, c) not in cv.used:
                out.append((r, c, None, 1))          # 地面上空一格（跳起来拿）
    return out


def place_gems(cv, rng, count):
    spots = gem_spots(cv)
    rng.shuffle(spots)
    spots.sort(key=lambda s: -s[3] - rng.random() * 2)
    want = {"f": count // 2 + count % 2, "w": count // 2}
    if rng.random() < 0.5:
        want = {"f": count // 2, "w": count // 2 + count % 2}
    taken = []
    for r, c, kind, _ in spots:
        if any(abs(r - r2) + abs(c - c2) < 3 for r2, c2 in taken):
            continue
        k = kind or rng.choice("fw")
        if want[k] <= 0:
            k = "w" if k == "f" else "f"
            if kind or want[k] <= 0:
                continue
        cv.g[r][c] = k
        want[k] -= 1
        taken.append((r, c))
        if want["f"] <= 0 and want["w"] <= 0:
            break


# ---------------------------------------------------------------- 布局
def split_heights(rng, nb):
    if nb == 3:
        hs = [3, 3, 4]
    else:
        hs = [5, 6]
    rng.shuffle(hs)
    return hs


def make_bands(hs, x0=1, x1=30):
    """hs：从下往上每层的高度。返回从下往上的 Band。"""
    bands, L = [], 13
    for h in hs:
        bands.append(Band(L - h, L, x0, x1))
        L = L - h - 1
    assert L == 0, hs
    return bands


def pick_mods(rng, table, n, count, bands_free):
    """挑 count 个模块，分到各层（bands_free：每层 (Band, 空地宽)）。"""
    names = [k for k, v in table.items() if v[2] <= n]
    weights = [table[k][3] for k in names]
    out = [[] for _ in bands_free]
    room = [w for _, w in bands_free]
    for _ in range(count):
        for _try in range(8):
            name = rng.choices(names, weights)[0]
            plan, draw = table[name][0], table[name][1]
            order = sorted(range(len(bands_free)), key=lambda j: -room[j] + rng.random() * 6)
            done = False
            for j in order:
                p = plan(rng, bands_free[j][0])
                if p and p["w"] + 2 <= room[j]:
                    out[j].append((name, draw, p))
                    room[j] -= p["w"] + 2
                    done = True
                    break
            if done:
                break
    for lst in out:
        rng.shuffle(lst)
    return out


def lay_serp(cv, rng, n, k):
    nb = rng.choice((2, 3, 3))
    hs = split_heights(rng, nb)
    bands = make_bands(hs)
    up = rng.random() < 0.7
    seq = bands if up else bands[::-1]
    for b in bands:
        cv.carve(b)
    d0 = rng.choice((1, -1))
    conns = []
    for j in range(nb - 1):
        hard = min(1.0, (n - 17) / 250)
        if up:
            kind = "lift" if rng.random() < 0.4 + 0.45 * hard else "stairs"
        else:
            kind = rng.choices(["stairs", "lift", "drop"], [3, 1 + 3 * hard, 1])[0]
        if kind == "stairs" and (seq[j] if up else seq[j + 1]).h not in STAIRS:
            kind = "lift"
        conns.append(kind)
    ends = ("12", "DE")
    free = []
    for j, b in enumerate(seq):
        w = b.W - (CONN_W[conns[j - 1]] if j else len(ends[0]) + 1) - (CONN_W[conns[j]] if j < nb - 1 else len(ends[1]) + 1)
        free.append((b, w - 2))
    coop_conns = sum(c == "lift" for c in conns)
    mods = pick_mods(rng, SHARED, n, max(1, k - coop_conns), free)
    gaps, helpers = build_path(cv, rng, seq, d0, conns, mods, ends)
    for hid, kind, order in helpers:
        if kind == "earlier":
            same = [gp for gp in gaps if gp.order == order]   # 中间不能隔着要帮忙的机关
            far = [gp for gp in same if gp.v.b is not same[-1].v.b]
            place_btn(cv, rng, far or same, hid)
        else:
            raise Fail("serp 不该有远处的按钮")
    layout = "up" if up else "down"
    return gaps, {"layout": layout, "nb": nb, "first_dir": d0}


def lay_split(cv, rng, n, k, towers=False):
    """分开走：火苗一条路、水滴一条路；每个机关的按钮在另一条路上（按顺序排好，保证能轮流过去）。"""
    if towers:
        regions = [(1, 14), (17, 30)]
        paths = []
        for x0, x1 in regions:
            hs = split_heights(rng, 3)
            bands = make_bands(hs, x0, x1)
            for b in bands:
                cv.carve(b)
            paths.append((bands, rng.choice((1, -1)), ["rlift", "rlift"]))
    else:
        nb = rng.choice((2, 3))
        hs = split_heights(rng, nb)
        bands = make_bands(hs)
        for b in bands:
            cv.carve(b)
        if nb == 2:
            groups = [[bands[0]], [bands[1]]]
        else:
            groups = [[bands[0]], [bands[1], bands[2]]] if rng.random() < 0.5 else [[bands[0], bands[1]], [bands[2]]]
        paths = []
        for grp in groups:
            seq = grp if rng.random() < 0.6 else grp[::-1]
            conns = []
            for j in range(len(seq) - 1):
                upward = seq[j + 1].L < seq[j].L
                conns.append("rlift" if upward else rng.choice(("stairs", "drop")))
            paths.append((seq, rng.choice((1, -1)), conns))
    roles = ["1", "2"]
    rng.shuffle(roles)
    built = []
    total_coop = sum(c == "rlift" for _, _, cs in paths for c in cs)
    raw = sum(b.W - 4 for seq, _, _ in paths for b in seq)
    per = max(0, min(k - total_coop, raw // 8 - total_coop))   # 一个机关连同队友那边的按钮大约占 8 格
    if towers:
        per = min(per, rng.choice((0, 1, 2, 2)))
    counts = [per // 2 + (per % 2 if i == 0 else 0) for i in range(2)]
    for idx, ((seq, d0, conns), role, cnt) in enumerate(zip(paths, roles, counts)):
        other = counts[1 - idx] + sum(c == "rlift" for c in paths[1 - idx][2])
        free = []
        for j, b in enumerate(seq):
            w = b.W - (CONN_W[conns[j - 1]] if j else 2) - (CONN_W[conns[j]] if j < len(seq) - 1 else 2)
            free.append((b, w - 2 - math.ceil(2 * other / len(seq))))   # 给队友的按钮留地方
        mods = pick_mods(rng, REMOTE, n, cnt, free)
        door = "D" if role == "1" else "E"
        gaps, helpers = build_path(cv, rng, seq, d0, conns, mods, (role, door))
        built.append((gaps, helpers, role))
    # 排顺序：两条路上的机关交错排，每个机关的按钮放在另一条路上「那时候走得到」的空地
    qa = [(0, h) for h in built[0][1]]
    qb = [(1, h) for h in built[1][1]]
    sched = []
    last = None
    while qa or qb:   # 尽量轮流：这次是你过机关，下次多半是队友
        if qa and qb:
            pick = (qb if last is qa else qa) if rng.random() < 0.75 else (qa if last is qa else qb)
        else:
            pick = qa or qb
        sched.append(pick.pop(0))
        last = pick
    passed = [0, 0]
    for side, (hid, kind, order) in sched:
        other = 1 - side
        gaps = [gp for gp in built[other][0] if gp.order == passed[other]]
        place_btn(cv, rng, gaps, hid)
        passed[side] += 1
    gaps = built[0][0] + built[1][0]
    fire_path = 0 if built[0][2] == "1" else 1
    info = {"layout": "towers" if towers else "lanes",
            "fire_low": None if towers else (paths[fire_path][0][0].L > paths[1 - fire_path][0][0].L),
            "fire_left": (fire_path == 0) if towers else None}
    return gaps, info


# ---------------------------------------------------------------- 名字、提示
PLACE = ["回廊", "石桥", "古塔", "密室", "阶梯", "峡谷", "河道", "花园", "隧道", "广场", "城堡", "迷宫", "洞窟", "平台",
         "瀑布", "栈道", "塔楼", "地宫", "庭院", "长廊", "钟楼", "矿坑", "神殿", "宝库", "山谷", "天井", "水渠", "工坊",
         "灯塔", "要塞", "遗迹", "暗河", "冰窖", "熔炉", "竹林", "石林", "风车", "吊桥", "古井", "书阁"]
ADJ = ["迷雾", "回声", "闪光", "秘密", "古老", "旋转", "安静", "月光", "星星", "彩虹", "晨曦", "黄昏", "翡翠", "琥珀",
       "银色", "金色", "紫晶", "蘑菇", "萤火", "雷鸣", "微风", "飞瀑", "沉睡", "勇气", "友谊", "默契", "机关", "齿轮",
       "铜铃", "石狮", "云端", "深谷", "火光", "水影", "双色", "幽蓝", "赤红", "翠绿", "雪白", "流沙"]
LAYOUT_NAME = {"towers": ["双塔", "双子塔", "两座塔"], "lanes": ["两条路", "上下层", "分岔路"]}

TIPS = {
    "gate_pair": "门两边都有按钮：一个人按住，另一个人过去，再从那边帮回来。",
    "gate_lever": "按住按钮先放一个人过去，门后的拉杆往右推，门就一直开着。",
    "pool_g": "毒水上的桥要有人踩住按钮才出来，过去以后记得帮队友按对面的按钮。",
    "pool_F": "岩浆只有火苗能蹚过去：火苗先过去踩按钮，桥就出来了。",
    "pool_W": "水池只有水滴能蹚过去：水滴先过去踩按钮，桥就出来了。",
    "lava_gate_F": "火苗先蹚过岩浆踩按钮，桥和门一起打开；水滴过了门再帮火苗按住。",
    "lava_gate_W": "水滴先蹚过水池踩按钮，桥和门一起打开；火苗过了门再帮水滴按住。",
    "airlock": "气闸：拉杆一推，前门开、后门关。一个人进去以后，外面的人把拉杆推回来，里面的人才能出去。",
    "ledge": "高台太高跳不上去：一个人按住按钮，另一个坐升降台上去，再在上面帮队友按。",
    "lift": "层和层之间的升降台：按住按钮，站在上面的人就升上去了。",
    "far_gate": "有的门，按钮在之前走过的地方，要有人留在后面帮忙按。",
    "remote": "你的门、桥和升降台，按钮都在队友那边，要轮流喊对方帮忙！",
    "drop": "洞跳下去就回不来了，两个人都准备好再跳。",
    "lever": "拉杆往右推打开，往左推关上。",
}


def make_name(rng, info, used):
    for _ in range(40):
        if info["layout"] in LAYOUT_NAME and rng.random() < 0.6:
            name = rng.choice(ADJ) + rng.choice(LAYOUT_NAME[info["layout"]])
        else:
            name = rng.choice(ADJ) + rng.choice(PLACE)
        if name not in used:
            return name
    return name


def make_tip(rng, info, tags, mirror):
    out = []
    lay = info["layout"]
    if lay == "towers":
        left = info["fire_left"] != mirror
        out.append(f"火苗爬{'左' if left else '右'}边的塔，水滴爬{'右' if left else '左'}边的塔。")
    elif lay == "lanes":
        out.append(f"火苗走{'下' if info['fire_low'] else '上'}面，水滴走{'上' if info['fire_low'] else '下'}面。")
    elif lay == "down":
        out.append("这一关要一层一层往下走。")
    order = ["remote", "airlock", "lava_gate_F", "lava_gate_W", "ledge", "far_gate", "gate_lever", "pool_F", "pool_W",
             "pool_g", "gate_pair", "lift", "drop"]
    have = [t for t in order if t in tags]
    if lay in ("towers", "lanes") and "remote" in have:
        out.append(TIPS["remote"])
        have.remove("remote")
    if have:
        top = have[:3]
        out.append(TIPS[rng.choice(top)])
    return "".join(out[:2])


def make_par(steps):
    return int(5 * round((25 + 7.5 * steps) / 5))


# ---------------------------------------------------------------- 生成一关
def mirror(lv):
    lv["map"] = [r[::-1] for r in lv["map"]]
    for o in lv["objects"]:
        o["x"] = COLS - 1 - o["x"] - (o.get("w", 1) - 1)


def candidate(n, rng, k, mode=None):
    cv = Canvas()
    roll = rng.random()
    t = target(n)
    split = 1.0 if mode == "split" else 0.4 if t < 16 else 0.3 if t < 20 else 0.15
    if n >= 30 and roll < split * 0.5:
        gaps, info = lay_split(cv, rng, n, k, towers=True)
    elif roll < split:
        gaps, info = lay_split(cv, rng, n, k)
    else:
        gaps, info = lay_serp(cv, rng, n, k)
    decorate(cv, rng, gaps, 0.6 + 0.35 * min(1.0, (n - 17) / 300))
    place_gems(cv, rng, rng.choice((2, 3, 4, 4, 5, 6)))
    lv = {"map": ["".join(r) for r in cv.g], "objects": cv.objs}
    flip = rng.random() < 0.5
    if flip:
        mirror(lv)
    return lv, info, cv.tags, flip


def drop_missing(lv, res):
    rows = [list(r) for r in lv["map"]]
    for role, cells in res["missing_gems"].items():
        for r, c in cells:
            rows[r][c] = "."
    lv["map"] = ["".join(r) for r in rows]


def gen_level(n, tries=60):
    """第 n 关：多试几次，挑步数最接近目标的。
    后面的世界，第一关有一半是「分开走」的（双塔 / 上下两条路）：步数少一点，换换口味。"""
    lo, hi = band_of(n)
    t = target(n)
    mode = None
    if n >= 60 and (n - 1) % 4 == 0 and random.Random(f"duo:{n}:mode").random() < 0.5:
        mode = "split"
        lo, t = min(lo, 11), min(t, 15)
    k = max(2, round(t / 3.6))
    best = None
    fails = 0
    work = 0   # 一共查了多少个状态（不按秒算，换台电脑生成的也一样）
    for i in range(tries):
        if best is not None and work > 400_000:   # 试太久了：用目前最好的
            break
        rng = random.Random(f"duo:{n}:{i}")
        try:
            lv, info, tags, flip = candidate(n, rng, k, mode)
        except Fail:
            fails += 1
            if fails % 3 == 0:
                k = max(2, k - 1)
            continue
        res = duo_fast.solve(lv, strict=True, limit=80_000)
        work += res["states"]
        if not res["ok"] or res["capped"] or res["dead"]:
            continue
        s = res["steps"]
        if res["missing_gems"]:
            drop_missing(lv, res)
        if sum(r.count("f") + r.count("w") for r in lv["map"]) < 2:
            continue
        if res["switches"] < 0.45 * s:
            continue
        loose = duo_fast.solve(lv, strict=False, limit=120_000, live=False)   # 会跳的孩子也不能抄近路跳过太多
        work += loose["states"]
        if not loose["ok"] or loose["steps"] < 0.75 * s:
            continue
        score = abs(s - t)
        if best is None or score < best[0]:
            best = (score, lv, info, tags, flip, res, i)
        if lo <= s <= hi:
            break
        # 太容易就多放几个机关，太难就少放
        if s < lo:
            k += 1
        elif s > hi:
            k = max(2, k - 1)
    if best is None:
        raise RuntimeError(f"第 {n} 关生成不出来")
    _, lv, info, tags, flip, res, i = best
    return {"n": n, "lv": lv, "info": info, "tags": sorted(tags), "flip": flip, "steps": res["steps"],
            "switches": res["switches"], "states": res["states"], "try": i}


def finish(items):
    """按顺序起名字、写提示和三星时间；去掉和前面重复的地图（重复的不太可能出现）。"""
    out, names_world, names_all = [], {}, set()
    for it in items:
        n = it["n"]
        rng = random.Random(f"duo:{n}:name")
        world = (n - 1) // 4
        used = names_world.setdefault(world, set()) | names_all
        name = make_name(rng, it["info"], used)
        names_world[world].add(name)
        names_all.add(name)
        tip = make_tip(rng, it["info"], set(it["tags"]), it["flip"])
        lv = it["lv"]
        out.append({"name": name, "par": make_par(it["steps"]), "tip": tip, "map": lv["map"], "objects": lv["objects"]})
    return out


def _work(n):
    t0 = time.time()
    it = gen_level(n)
    it["sec"] = round(time.time() - t0, 2)
    return it


def dump(levels, path):
    lines = [json.dumps(lv, ensure_ascii=False, separators=(",", ":")) for lv in levels]
    path.write_text("[\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", type=int, default=1000, help="生成到第几关")
    ap.add_argument("--from", dest="start", type=int, default=FIRST)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--stats", help="另存每关的步数、布局等（调参用）")
    a = ap.parse_args()
    t0 = time.time()
    ns = list(range(a.start, a.to + 1))
    if a.jobs > 1:
        from multiprocessing import Pool
        with Pool(a.jobs) as pool:
            items = []
            for it in pool.imap(_work, ns, chunksize=1):
                items.append(it)
                if it["n"] % 25 == 0:
                    print(f"第 {it['n']} 关  步数 {it['steps']}  {time.time() - t0:.0f}s", flush=True)
    else:
        items = [_work(n) for n in ns]
    seen = {tuple(json.loads(p.read_text(encoding="utf-8"))["map"]) for p in HANDMADE.glob("*.json")}
    for it in items:
        key = tuple(it["lv"]["map"])
        if key in seen:
            raise RuntimeError(f"第 {it['n']} 关地图重复")
        seen.add(key)
    levels = finish(items)
    dump(levels, Path(a.out))
    print(f"共 {len(levels)} 关，用时 {time.time() - t0:.0f}s")
    for i in range(0, len(items), 50):
        grp = items[i:i + 50]
        st = [x["steps"] for x in grp]
        sw = [x["switches"] for x in grp]
        print(f"第 {grp[0]['n']}-{grp[-1]['n']} 关：步数 {min(st)}-{max(st)} 平均 {sum(st) / len(st):.1f}；"
              f"换人 {min(sw)}-{max(sw)} 平均 {sum(sw) / len(sw):.1f}")
    if a.stats:
        Path(a.stats).write_text(json.dumps([{k: v for k, v in it.items() if k != "lv"} for it in items], ensure_ascii=False))


if __name__ == "__main__":
    main()
