"""联网游戏（app/arena/mp/）：关卡都能走通、房间只在一个家庭里、过关记队伍纪录和星星、实时消息只转给房间里的人。"""
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import arena, auth, db
from app.arena import mp
from app.arena.mp import rooms
from app.main import app


# ------------------------------------------------------------------ 关卡

def test_levels_load():
    for gid in ("duo", "bubble", "snake"):
        g = mp.get(gid)
        assert len(g.levels) >= 8, gid
        assert gid in arena.GAMES and arena.GAMES[gid]["multi"] and arena.GAMES[gid]["freeze"] is False
        assert [lv["n"] for lv in g.levels] == list(range(1, len(g.levels) + 1))
        assert g.stars[0][0] == "clear" and len(g.stars) == 3


SOLID = set("#FWG")


def _reach(lv, role):
    """双人闯关：不管机关（门当作开着、桥当作有、升降台两头连通），这个角色能走到哪些格子。
    跳：最多往上 2 格、往旁边 3 格；不能站在会害死自己的池子上。"""
    g = lv["map"]
    R, C = len(g), len(g[0])
    kill = "WG" if role == "fire" else "FG"
    floors, lifts = set(), []
    for o in lv.get("objects", []):
        if o["k"] == "bridge":
            floors |= {(o["y"], c) for c in range(o["x"], o["x"] + o["w"])}
        if o["k"] == "lift":
            lifts.append(o)
            floors |= {(row, c) for c in range(o["x"], o["x"] + o["w"]) for row in (o["y"], o["to"])}

    def solid(r, c):
        return r < 0 or c < 0 or r >= R or c >= C or g[r][c] in SOLID

    def floor(r, c):
        return solid(r + 1, c) or (r + 1, c) in floors

    def ok(r, c):
        return not solid(r, c) and floor(r, c) and not (r + 1 < R and g[r + 1][c] in kill and (r + 1, c) not in floors)

    def fall(r, c):
        while not solid(r, c):
            if floor(r, c):
                return (r, c) if ok(r, c) else None
            r += 1
        return None

    start = next((r, c) for r in range(R) for c in range(C) if g[r][c] == ("1" if role == "fire" else "2"))
    seen, todo = {start}, [start]
    while todo:
        r, c = todo.pop()
        nxt = []
        for d in (-1, 1):
            if not solid(r, c + d):
                nxt.append(fall(r, c + d))
            for k in (1, 2):
                if any(solid(r - i, c) for i in range(1, k + 1)):
                    break
                for i in range(1, 4):
                    if solid(r - k, c + d * i):
                        break
                    nxt.append(fall(r - k, c + d * i))
        for o in lifts:
            if o["x"] <= c < o["x"] + o["w"] and r in (o["y"] - 1, o["to"] - 1):
                nxt.append((o["to"] - 1 if r == o["y"] - 1 else o["y"] - 1, c))
        for p in nxt:
            if p and p not in seen:
                seen.add(p)
                todo.append(p)
    return seen


def test_duo_levels_reachable():
    """每一关：火苗能走到自己的门和所有火宝石，水滴也一样（机关要两个人配合，这里只查地形有没有画错）。"""
    for lv in mp.get("duo").levels:
        for role, door, gem in (("fire", "D", "f"), ("water", "E", "w")):
            got = _reach(lv, role)
            want = [(r, c) for r, row in enumerate(lv["map"]) for c, ch in enumerate(row) if ch in (door, gem)]
            assert all(p in got for p in want), (lv["name"], role, [p for p in want if p not in got])


def test_bubble_levels_starts_are_safe():
    """泡泡堂：出生点旁边留了空地（能放第一个泡泡再躲开），怪兽离出生点够远。"""
    for lv in mp.get("bubble").levels:
        g = lv["map"]
        for ch in "12":
            r, c = next((r, row.index(ch)) for r, row in enumerate(g) if ch in row)
            free = [(r + dr, c + dc) for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)) if g[r + dr][c + dc] == "."]
            assert len(free) >= 2, (lv["name"], ch)
            mons = [(rr, cc) for rr, row in enumerate(g) for cc, x in enumerate(row) if x in "sbg"]
            assert min(abs(rr - r) + abs(cc - c) for rr, cc in mons) >= 6, (lv["name"], ch)


def test_star_rules():
    duo, bub, snk = mp.get("duo"), mp.get("bubble"), mp.get("snake")
    lv = duo.level(1)
    assert duo.checks(lv, duo.clean(lv, {"seconds": lv["par"] - 1, "gems": 99})) == {"clear": True, "gems": True, "time": True}
    assert duo.clean(lv, {"seconds": 10, "gems": 99})["gems"] == lv["gems"]   # 不能报比地图上还多的宝石
    lv = bub.level(1)
    assert bub.checks(lv, bub.clean(lv, {"seconds": lv["par"] + 1, "deaths": 2})) == {"clear": True, "time": False, "safe": True}
    lv = snk.level(1)
    assert snk.checks(lv, snk.clean(lv, {"seconds": 10, "deaths": 2, "score": 70}))["safe"] is False


# ------------------------------------------------------------------ 房间和过关

def _family(tag):
    pid = auth.create_user(f"mp-p-{tag}@x.com", "secret1", "家长", "parent")
    a = auth.create_user(f"mp-a-{tag}@x.com", "secret1", "姐姐", "kid", parent_id=pid, grade="G8")
    b = auth.create_user(f"mp-b-{tag}@x.com", "secret1", "弟弟", "kid", parent_id=pid, grade="G3")
    for k in (a, b):
        arena.set_game_unlock(k, "free")
    return pid, a, b


def _login(c, email):
    c.cookies.clear()
    assert c.post("/login", data={"email": email, "password": "secret1"}).status_code in (200, 303)


def test_rooms_and_finish():
    with TestClient(app) as c:
        _, a, b = _family("r")
        lone = auth.create_user("mp-lone@x.com", "secret1", "独生", "kid", parent_id=auth.create_user("mp-lp@x.com", "secret1", "家长", "parent"))
        _login(c, "mp-lone@x.com")
        assert c.get("/arena/duo").status_code == 200
        assert c.get("/api/mp/duo/lobby").json()["siblings"] == []      # 页面据此提示「家里还没有别的小伙伴」
        assert c.post("/api/mp/rooms", json={"game": "duo"}).status_code == 403
        assert lone

        _login(c, "mp-a-r@x.com")
        assert c.get("/arena/duo").status_code == 200
        room = c.post("/api/mp/rooms", json={"game": "duo", "role": "water"}).json()
        assert room["host"] and room["me"]["role"] == "water" and room["progress"] is None
        rid = room["id"]

        _login(c, "mp-b-r@x.com")
        lob = c.get("/api/mp/duo/lobby").json()
        assert [r["id"] for r in lob["rooms"]] == [rid] and lob["rooms"][0]["host"]["name"] == "姐姐"
        assert "姐姐" in c.get("/arena").text   # 乐园首页：「姐姐在等你」
        room = c.post(f"/api/mp/rooms/{rid}/join").json()
        assert not room["host"] and room["me"]["role"] == "fire" and len(room["members"]) == 2
        assert room["progress"]["upto"] == 1
        assert c.post(f"/api/mp/rooms/{rid}/level", json={"n": 2}).status_code == 403          # 只有开房间的人选关
        assert c.post(f"/api/mp/rooms/{rid}/finish", json={"n": 1, "report": {"seconds": 20}}).status_code == 403

        _login(c, "mp-a-r@x.com")
        assert c.post(f"/api/mp/rooms/{rid}/level", json={"n": 2}).status_code == 403          # 第 2 关还没解锁
        assert c.post(f"/api/mp/rooms/{rid}/finish", json={"n": 1, "report": {"seconds": 2}}).status_code == 400   # 太快，不算
        par = mp.get("duo").level(1)["par"]
        r = c.post(f"/api/mp/rooms/{rid}/finish", json={"n": 1, "report": {"seconds": par + 5, "gems": 1, "deaths": 1}}).json()
        assert r["stars"] == 1 and r["checks"] == {"clear": True, "gems": False, "time": False}
        assert r["next"] == 2 and r["progress"]["upto"] == 2 and r["new_best"]
        assert {s["key"] for s in r["stickers"][str(a)]} >= {"mp1"} and "mp_clean" not in {s["key"] for s in r["stickers"][str(a)]}
        assert {s["key"] for s in r["stickers"][str(b)]} >= {"mp1"}
        r2 = c.post(f"/api/mp/rooms/{rid}/finish", json={"n": 1, "report": {"seconds": par - 5, "gems": 99}}).json()
        assert r2["stars"] == 3 and r2["new_best"] and r2["prev_best"] == par + 5
        prog = rooms.progress("duo", b, a)   # 谁先谁后都是同一队
        assert prog["levels"][0]["stars"] == 3 and prog["levels"][0]["best"] == par - 5 and prog["upto"] == 2
        assert c.post(f"/api/mp/rooms/{rid}/level", json={"n": 2}).json()["level"] == 2
        # 换角色
        assert c.post(f"/api/mp/rooms/{rid}/role", json={"role": "fire"}).json()["me"]["role"] == "fire"

        # 倒下答题：答错不冷冻（马上能拿下一道题）
        m = c.post("/api/arena/start", json={"game": "duo"}).json()
        q = c.get(f"/api/arena/{m['match_id']}/q").json()["item"]
        res = c.post(f"/api/arena/{m['match_id']}/a", json={"item_id": q["id"], "answer": "-99999"}).json()
        assert res["correct"] is False and res["freeze_ms"] == 0
        assert c.get(f"/api/arena/{m['match_id']}/q").status_code == 200

        # 别的家庭进不来
        _family("x")
        _login(c, "mp-a-x@x.com")
        assert c.post(f"/api/mp/rooms/{rid}/join").status_code == 403
        assert c.get(f"/api/mp/rooms/{rid}").status_code == 403


def _recv(ws):
    """下一条不是 hello 的消息（PostgreSQL 转发是异步的：先进来的人的 hello 可能晚到，后进来的人也会收到，前端当作「对方在」）。"""
    while True:
        m = json.loads(ws.receive_text())
        if m["t"] != "hello":
            return m


def test_ws_relay():
    """实时消息：转给房间里的另一个人（盖上发送人），不转回给自己；不在房间里的人连不上；太长的消息丢掉。"""
    with TestClient(app) as c:
        _, a, b = _family("w")
        _login(c, "mp-a-w@x.com")
        rid = c.post("/api/mp/rooms", json={"game": "snake"}).json()["id"]
        cookie_a = dict(c.cookies)
        c2 = TestClient(app)
        _login(c2, "mp-b-w@x.com")
        c2.post(f"/api/mp/rooms/{rid}/join")
        c3 = TestClient(app)
        _family("w2")
        _login(c3, "mp-a-w2@x.com")
        with pytest.raises(WebSocketDisconnect) as e:
            with c3.websocket_connect(f"/ws/mp/{rid}") as ws:
                ws.receive_text()
        assert e.value.code == 4403

        c.cookies.update(cookie_a)
        with c.websocket_connect(f"/ws/mp/{rid}") as wa:
            with c2.websocket_connect(f"/ws/mp/{rid}") as wb:
                hello = json.loads(wa.receive_text())
                assert hello == {"t": "hello", "from": b}
                wb.send_text("x" * 3000)                                   # 太长：丢掉
                wb.send_text(json.dumps({"t": "s", "x": 10, "from": 999}))   # from 由服务器盖，改不了
                assert _recv(wa) == {"t": "s", "x": 10, "from": b}
                wa.send_text(json.dumps({"t": "gem", "k": "1,2"}))
                assert _recv(wb) == {"t": "gem", "k": "1,2", "from": a}
            assert _recv(wa) == {"t": "bye", "from": b}
        assert db.one("SELECT seen_at FROM mp_rooms WHERE id=?", rid)["seen_at"]
