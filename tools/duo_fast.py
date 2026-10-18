"""双人闯关的快速检查（tests/duo_solver.py 的快速版，gen_duo_levels.py 和 tests/test_duo_more.py 用）。

规则和 duo_solver 一样（轮流走、跳、踩按钮、推拉杆、坐升降台；走的那个人自己踩的按钮不算），只是：
- 格子用整数、机关用位掩码，快很多；
- strict=True 时跳得更保守：跳 2 格高最多往前 2 格，起跳的地方头顶还要多空一格（不用贴着天花板硬跳）；
- 记下每一步能到哪，算出「走进去就再也过不了关」的死局（会卡死的关卡不要）；
- 宝石只算「还能过关」的状态下拿得到的。
strict=False 时步数、能不能过、宝石和 duo_solver 一样（测试里对过手做的 16 关）。
"""
from collections import deque

SOLID = set("#FWG")
KILL = ({"W", "G"}, {"F", "G"})   # 0 火苗、1 水滴


class Fast:
    def __init__(self, lv: dict, strict: bool = True):
        rows = lv["map"]
        self.R, self.C = R, C = len(rows), len(rows[0])
        self.strict = strict
        self.g = "".join(rows)
        self.static = [ch in SOLID for ch in self.g]
        self.kill = [[ch in KILL[k] for ch in self.g] for k in (0, 1)]
        self.start, self.door, self.gems = [0, 0], [0, 0], ([], [])
        for p, ch in enumerate(self.g):
            if ch in "12":
                self.start["12".index(ch)] = p
            elif ch in "DE":
                self.door["DE".index(ch)] = p
            elif ch in "fw":
                self.gems["fw".index(ch)].append(p)
        objs = lv.get("objects", [])
        names = []
        for o in objs:
            for i in str(o["id"]).split("|"):
                if i not in names:
                    names.append(i)
        bit = {n: 1 << k for k, n in enumerate(names)}

        def mask(oid):
            m = 0
            for i in str(oid).split("|"):
                m |= bit[i]
            return m

        self.btn = {}
        self.lever = {}
        for o in objs:
            p = o["y"] * C + o["x"]
            if o["k"] == "btn":
                self.btn[p] = self.btn.get(p, 0) | mask(o["id"])
            elif o["k"] == "lever":
                self.lever.setdefault(p, []).append(mask(o["id"]))
        self.gates = [(mask(o["id"]), bool(o.get("inv")),
                       frozenset((o["y"] + i) * C + o["x"] + j for i in range(o["h"]) for j in range(o["w"])))
                      for o in objs if o["k"] == "gate"]
        self.bridges = [(mask(o["id"]), frozenset(o["y"] * C + o["x"] + j for j in range(o["w"])))
                        for o in objs if o["k"] == "bridge"]
        self.lifts = [(mask(o["id"]), o["x"], o["w"], o["y"], o["to"]) for o in objs if o["k"] == "lift"]
        self._blk, self._reach, self._settle, self._graphs = {}, {}, {}, {}

    # ---- 机关
    def blocks(self, ids: int) -> frozenset:
        b = self._blk.get(ids)
        if b is None:
            out = set()
            for m, inv, cells in self.gates:
                if bool(m & ids) == inv:
                    out |= cells
            for m, cells in self.bridges:
                if m & ids:
                    out |= cells
            for m, x, w, y, to in self.lifts:
                row = to if m & ids else y
                out.update(row * self.C + x + j for j in range(w))
            b = self._blk[ids] = frozenset(out)
        return b

    def active(self, a: int, b: int, lev: int) -> int:
        return lev | self.btn.get(a, 0) | self.btn.get(b, 0)

    # ---- 一个人在机关不动时能到哪（站得住、不会死），顺带经过哪些格子
    def _env(self, role: int, ids: int):
        blk, st, R, C = self.blocks(ids), self.static, self.R, self.C
        kill, strict = self.kill[role], self.strict

        def solid(r, c):
            return r < 0 or c < 0 or r >= R or c >= C or st[r * C + c] or (r * C + c) in blk

        def stand(r, c):
            if solid(r, c) or not solid(r + 1, c):
                return False
            if r + 1 >= R:
                return True
            q = (r + 1) * C + c
            return q in blk or not kill[q]

        def fall(r, c, path):
            while not solid(r + 1, c):
                r += 1
                path.append(r * C + c)
                if r >= R - 1:
                    return None
            return r * C + c if stand(r, c) else None

        def moves(p):   # 从 p 一步（走一格 / 跳）能落到哪，经过哪些格子
            r, c = divmod(p, C)
            ends, touched = set(), set()
            for d in (-1, 1):
                paths = []
                if not solid(r, c + d):
                    paths.append([(r, c + d)])
                for k in (1, 2):
                    if any(solid(r - i, c) for i in range(1, k + 1)):
                        break
                    if strict and solid(r - k - 1, c):   # 头顶要多空一格
                        break
                    up = [(r - i, c) for i in range(1, k + 1)]
                    for i in range(1, (3 if k == 1 or not strict else 2) + 1):
                        if solid(r - k, c + d * i):
                            break
                        if strict and k == 2 and i == 1 and solid(r - 3, c + d):
                            break
                        paths.append(up + [(r - k, c + d * j) for j in range(1, i + 1)])
                for path in paths:
                    cells = [rr * C + cc for rr, cc in path]
                    e = fall(path[-1][0], path[-1][1], cells)
                    touched.update(cells)
                    if e is not None:
                        ends.add(e)
            return ends, touched

        return stand, moves

    def _graph(self, role: int, ids: int):
        """所有站得住的格子之间的一步移动，按强连通分量合起来，每个分量记下能到的格子和摸到的格子。"""
        key = (role, ids)
        gr = self._graphs.get(key)
        if gr is not None:
            return gr
        stand, moves = self._env(role, ids)
        C = self.C
        nodes = [r * C + c for r in range(self.R) for c in range(C) if stand(r, c)]
        adj, tch = {}, {}
        for p in nodes:
            e, t = moves(p)
            adj[p], tch[p] = e, t
        # Tarjan（不用递归）
        index, low, onst, stack, comp = {}, {}, set(), [], {}
        comps = []
        n = 0
        for v0 in nodes:
            if v0 in index:
                continue
            work = [(v0, iter(adj[v0]))]
            index[v0] = low[v0] = n; n += 1
            stack.append(v0); onst.add(v0)
            while work:
                v, it = work[-1]
                for w in it:
                    if w not in adj:
                        continue
                    if w not in index:
                        index[w] = low[w] = n; n += 1
                        stack.append(w); onst.add(w)
                        work.append((w, iter(adj[w])))
                        break
                    if w in onst:
                        low[v] = min(low[v], index[w])
                else:
                    work.pop()
                    if work:
                        u = work[-1][0]
                        low[u] = min(low[u], low[v])
                    if low[v] == index[v]:
                        members = []
                        while True:
                            w = stack.pop(); onst.discard(w)
                            comp[w] = len(comps)
                            members.append(w)
                            if w == v:
                                break
                        comps.append(members)
        # 分量按 Tarjan 的顺序出来：后继分量总是先出来，所以顺着算闭包就行
        cl_nodes, cl_touch = [], []
        for ci, members in enumerate(comps):
            ns, ts = set(members), set()
            for p in members:
                ts |= tch[p]
                for q in adj[p]:
                    cj = comp.get(q)
                    if cj is not None and cj != ci:
                        ns |= cl_nodes[cj]
                        ts |= cl_touch[cj]
            cl_nodes.append(frozenset(ns))
            cl_touch.append(frozenset(ts))
        gr = self._graphs[key] = (comp, cl_nodes, cl_touch, moves)
        return gr

    def reach(self, role: int, start: int, ids: int):
        key = (role, start, ids)
        hit = self._reach.get(key)
        if hit:
            return hit
        comp, cl_nodes, cl_touch, moves = self._graph(role, ids)
        ci = comp.get(start)
        if ci is not None:
            hit = (cl_nodes[ci], cl_touch[ci] | {start})
        else:   # 站在不寻常的地方（比如刚被挤开）：先走一步再查表
            ends, touched = moves(start)
            seen, tt = {start}, set(touched) | {start}
            for e in ends:
                cj = comp.get(e)
                if cj is not None:
                    seen |= cl_nodes[cj]
                    tt |= cl_touch[cj]
            hit = (seen, tt)
        self._reach[key] = hit
        return hit

    # ---- 机关变了：站在升降台上的跟着走，脚下空了就掉下去
    def settle(self, f: int, w: int, old: int, new: int):
        key = (f, w, old, new)
        if key in self._settle:
            return self._settle[key]
        C, R = self.C, self.R
        out = [f, w]
        for k, p in enumerate((f, w)):
            r, c = divmod(p, C)
            for m, x, wd, y, to in self.lifts:
                if x <= c < x + wd:
                    was = to if m & old else y
                    now = to if m & new else y
                    if r == was - 1 and was != now:
                        out[k] = (now - 1) * C + c
        blk = self.blocks(new)
        res = out
        for k in (0, 1):
            r, c = divmod(out[k], C)
            while r + 1 < R and not self.static[(r + 1) * C + c] and ((r + 1) * C + c) not in blk:
                r += 1
            q = (r + 1) * C + c
            if r + 1 >= R or (q not in blk and self.kill[k][q]):
                res = None
                break
            out[k] = r * C + c
        res = tuple(out) if res is not None else None
        self._settle[key] = res
        return res


def solve(lv: dict, strict: bool = True, limit: int = 200_000, live: bool = True) -> dict:
    """{ok, steps, switches, gems_ok, missing_gems, states, dead, capped}
    dead：能走到、但再也过不了关的状态数（卡死）；capped：状态太多没算完。"""
    L = Fast(lv, strict)
    C = L.C
    s0 = (L.start[0], L.start[1], 0)
    index = {s0: 0}
    states = [s0]
    succ = [[]] if live else None
    info = [(0, 0, -1)]   # 步数、换人次数、上一步谁走
    q = deque([0])
    goal = (L.door[0], L.door[1])
    best = None
    goals = []
    capped = False
    touched_by = ([], [])   # 每个状态下每个人摸到的格子（算宝石用）
    while q:
        si = q.popleft()
        f, w, lev = states[si]
        steps, sw, last = info[si]
        if (f, w) == goal:
            best = best or (steps, sw)
            goals.append(si)
            continue
        old = L.active(f, w, lev)
        for role in (0, 1):
            me, other = (f, w) if role == 0 else (w, f)
            ids = lev | L.btn.get(other, 0)
            ends, touched = L.reach(role, me, ids)
            touched_by[role].append((si, touched))
            for p in ends:
                if p == me:
                    continue
                lvs = [lev] + [lev ^ m for m in L.lever.get(p, ())]
                for lv2 in lvs:
                    new = lv2 | L.btn.get(p, 0) | L.btn.get(other, 0)
                    np = (p, other) if role == 0 else (other, p)
                    if new != old:
                        np = L.settle(np[0], np[1], old, new)
                        if np is None:
                            continue
                    key = (np[0], np[1], lv2)
                    j = index.get(key)
                    if j is None:
                        if len(states) >= limit:
                            capped = True
                            continue
                        j = index[key] = len(states)
                        states.append(key)
                        info.append((steps + 1, sw + (last != -1 and last != role), role))
                        if live:
                            succ.append([])
                        q.append(j)
                    if live:
                        succ[si].append(j)
            for m in L.lever.get(me, ()):
                lv2 = lev ^ m
                new = L.active(f, w, lv2)
                np = L.settle(f, w, old, new) if new != old else (f, w)
                if np is None:
                    continue
                key = (np[0], np[1], lv2)
                j = index.get(key)
                if j is None:
                    if len(states) >= limit:
                        capped = True
                        continue
                    j = index[key] = len(states)
                    states.append(key)
                    info.append((steps + 1, sw, role))
                    if live:
                        succ.append([])
                    q.append(j)
                if live:
                    succ[si].append(j)
    # 能过关的状态（倒着找）
    alive = None
    dead = 0
    dead_at = None
    if live and not capped:
        pred = [[] for _ in states]
        for i, out in enumerate(succ):
            for j in out:
                pred[j].append(i)
        alive = bytearray(len(states))
        todo = list(goals)
        for g in goals:
            alive[g] = 1
        while todo:
            j = todo.pop()
            for i in pred[j]:
                if not alive[i]:
                    alive[i] = 1
                    todo.append(i)
        dead = len(states) - sum(alive)
        if dead:
            i = next(i for i in range(len(states)) if not alive[i])
            f, w, lev = states[i]
            dead_at = (divmod(f, C), divmod(w, C), lev)
    got = (set(), set())
    for role in (0, 1):
        for item in touched_by[role]:
            si, touched = item
            if alive is None or alive[si]:
                got[role].update(touched)
    missing = {("fire", "water")[r]: sorted(divmod(p, C) for p in set(L.gems[r]) - got[r])
               for r in (0, 1) if set(L.gems[r]) - got[r]}
    return {"ok": best is not None, "steps": best and best[0], "switches": best and best[1],
            "gems_ok": not missing, "missing_gems": missing, "states": len(states), "dead": dead, "capped": capped,
            "dead_at": dead_at}
