"""双人闯关的关卡检查：模拟两个人轮流走、跳、踩按钮、推拉杆、坐升降台，确认能一起走到门口、每颗宝石都拿得到。

按前端（app/static/play/duo.js）的手感估：一跳能上 2 格、往前 3 格；角色占一格。
保守估计：走的那个人自己踩着的按钮不算（离开就松开了），所以这里说能过，游戏里一定能过。
solve 返回最少几步（一步 = 一个人从站着的地方走 / 跳到另一个站得住的地方）和要换几次人，用来排难度。
"""
from collections import deque

SOLID = set("#FWG")
KILL = {"fire": set("WG"), "water": set("FG")}
ROLES = ("fire", "water")


class Level:
    def __init__(self, lv: dict):
        self.g = [list(r) for r in lv["map"]]
        self.R, self.C = len(self.g), len(self.g[0])
        self.start, self.door, self.gems = {}, {}, {"fire": set(), "water": set()}
        for r, row in enumerate(self.g):
            for c, ch in enumerate(row):
                if ch in "12":
                    self.start["fire" if ch == "1" else "water"] = (r, c)
                if ch in "DE":
                    self.door["fire" if ch == "D" else "water"] = (r, c)
                if ch in "fw":
                    self.gems["fire" if ch == "f" else "water"].add((r, c))
        objs = lv.get("objects", [])
        self.btns = [((o["y"], o["x"]), o["id"]) for o in objs if o["k"] == "btn"]
        self.levers = [((o["y"], o["x"]), o["id"]) for o in objs if o["k"] == "lever"]
        self.gates = [o for o in objs if o["k"] == "gate"]
        self.bridges = [o for o in objs if o["k"] == "bridge"]
        self.lifts = [o for o in objs if o["k"] == "lift"]
        self._reach = {}

    # ---- 机关的样子：哪些格子挡路 / 能站
    @staticmethod
    def on(ids: frozenset, oid: str) -> bool:
        return any(i in ids for i in str(oid).split("|"))

    def blocks(self, ids: frozenset) -> set:
        out = set()
        for o in self.gates:
            if self.on(ids, o["id"]) == bool(o.get("inv")):    # 关着
                out |= {(o["y"] + i, o["x"] + j) for i in range(o["h"]) for j in range(o["w"])}
        for o in self.bridges:
            if self.on(ids, o["id"]):
                out |= {(o["y"], o["x"] + j) for j in range(o["w"])}
        for o in self.lifts:
            row = o["to"] if self.on(ids, o["id"]) else o["y"]
            out |= {(row, o["x"] + j) for j in range(o["w"])}
        return out

    def active(self, pos: dict, levers: frozenset, skip=None) -> frozenset:
        ids = set(levers)
        for role, p in pos.items():
            if role != skip:
                ids |= {i for b, i in self.btns if b == p}
        return frozenset(ids)

    # ---- 一个人在机关不动时能到哪些地方（站得住、不会死），顺带经过了哪些格子
    def reach(self, role: str, start: tuple, ids: frozenset):
        key = (role, start, ids)
        if key in self._reach:
            return self._reach[key]
        blk = self.blocks(ids)
        R, C, g = self.R, self.C, self.g

        def solid(r, c):
            return r < 0 or c < 0 or r >= R or c >= C or g[r][c] in SOLID or (r, c) in blk

        def stand(r, c):   # 能站在 (r, c)：脚下挡得住，不是会烫死 / 淹死的池子
            if solid(r, c) or not solid(r + 1, c):
                return False
            return (r + 1, c) in blk or g[r + 1][c] not in KILL[role] if r + 1 < R else True

        def fall(r, c, path):
            while not solid(r + 1, c):
                r += 1
                path.append((r, c))
                if r >= R - 1:
                    return None
            return (r, c) if stand(r, c) else None

        seen, touched, todo = {start}, {start}, [start]
        while todo:
            r, c = todo.pop()
            for d in (-1, 1):
                moves = []
                if not solid(r, c + d):
                    moves.append([(r, c + d)])
                for k in (1, 2):
                    if any(solid(r - i, c) for i in range(1, k + 1)):
                        break
                    up = [(r - i, c) for i in range(1, k + 1)]
                    for i in range(1, 4):
                        if solid(r - k, c + d * i):
                            break
                        moves.append(up + [(r - k, c + d * j) for j in range(1, i + 1)])
                for path in moves:
                    end = fall(*path[-1], path)
                    touched.update(path)
                    if end and end not in seen:
                        seen.add(end)
                        todo.append(end)
        self._reach[key] = (seen, touched)
        return seen, touched

    # ---- 机关变了以后：站在升降台上的跟着走，桥没了就掉下去
    def settle(self, pos: dict, old: frozenset, new: frozenset):
        if old == new:
            return pos
        out = dict(pos)
        for role, (r, c) in pos.items():
            for o in self.lifts:
                if o["x"] <= c < o["x"] + o["w"]:
                    was = o["to"] if self.on(old, o["id"]) else o["y"]
                    now = o["to"] if self.on(new, o["id"]) else o["y"]
                    if r == was - 1 and was != now:
                        out[role] = (now - 1, c)
        blk = self.blocks(new)
        for role, (r, c) in out.items():   # 脚下空了：往下掉
            while r + 1 < self.R and self.g[r + 1][c] not in SOLID and (r + 1, c) not in blk:
                r += 1
            if r + 1 >= self.R or ((r + 1, c) not in blk and self.g[r + 1][c] in KILL[role]):
                return None
            out[role] = (r, c)
        return out


def solve(lv: dict, limit: int = 400_000) -> dict:
    """{ok, steps, switches, gems_ok, missing_gems}：steps / switches 是最少几步、最少换几次人（找不到是 None）。"""
    L = Level(lv)
    start = (L.start["fire"], L.start["water"], frozenset(), None)
    seen = {start[:3]: (0, 0)}
    q = deque([(start, 0, 0)])
    got = {"fire": set(), "water": set()}
    best = None
    while q and len(seen) < limit:
        (f, w, lev, last), steps, sw = q.popleft()
        pos = {"fire": f, "water": w}
        if pos["fire"] == L.door["fire"] and pos["water"] == L.door["water"]:
            best = best or (steps, sw)
            continue
        for role in ROLES:
            ids = L.active(pos, lev, skip=role)
            ends, touched = L.reach(role, pos[role], ids)
            got[role] |= touched & L.gems[role]
            for p in ends:
                if p == pos[role]:
                    continue
                for lv2 in {lev} | {lev ^ {i} for b, i in L.levers if b == p}:
                    lv2 = frozenset(lv2)
                    np = dict(pos, **{role: p})
                    np = L.settle(np, L.active(pos, lev), L.active(np, lv2))
                    if not np:
                        continue
                    key = (np["fire"], np["water"], lv2)
                    if key in seen:
                        continue
                    seen[key] = 1
                    q.append(((np["fire"], np["water"], lv2, role), steps + 1, sw + (last is not None and last != role)))
            # 不动、只推拉杆（站在拉杆上）
            for b, i in L.levers:
                if b == pos[role]:
                    lv2 = frozenset(lev ^ {i})
                    np = L.settle(pos, L.active(pos, lev), L.active(pos, lv2))
                    if np and (np["fire"], np["water"], lv2) not in seen:
                        seen[(np["fire"], np["water"], lv2)] = 1
                        q.append(((np["fire"], np["water"], lv2, role), steps + 1, sw))
    missing = {r: sorted(L.gems[r] - got[r]) for r in ROLES if L.gems[r] - got[r]}
    return {"ok": best is not None, "steps": best and best[0], "switches": best and best[1],
            "gems_ok": not missing, "missing_gems": missing, "states": len(seen)}
