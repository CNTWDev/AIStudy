"""联网游戏（app/arena/：games/、rooms.py、sessions.py、bus.py）：关卡都能走通、房间只在一个家庭里、过关记队伍纪录和星星、
换设备和存档、实时消息只转给房间里的人。"""
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import arena, auth, db
from app.arena import games, progress, rooms, sessions
from app.main import app


# ------------------------------------------------------------------ 关卡

def test_levels_load():
    for gid in ("duo", "bubble", "snake"):
        g = games.get(gid)
        assert len(g.levels) >= 8, gid
        assert gid in arena.GAMES and g.multi and g.players == (2, 2) and g.freeze is False
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
    for lv in games.get("duo").levels:
        for role, door, gem in (("fire", "D", "f"), ("water", "E", "w")):
            got = _reach(lv, role)
            want = [(r, c) for r, row in enumerate(lv["map"]) for c, ch in enumerate(row) if ch in (door, gem)]
            assert all(p in got for p in want), (lv["name"], role, [p for p in want if p not in got])


def test_bubble_levels_starts_are_safe():
    """泡泡堂：出生点旁边留了空地（能放第一个泡泡再躲开），怪兽离出生点够远。"""
    for lv in games.get("bubble").levels:
        g = lv["map"]
        for ch in "12":
            r, c = next((r, row.index(ch)) for r, row in enumerate(g) if ch in row)
            free = [(r + dr, c + dc) for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)) if g[r + dr][c + dc] == "."]
            assert len(free) >= 2, (lv["name"], ch)
            mons = [(rr, cc) for rr, row in enumerate(g) for cc, x in enumerate(row) if x in "sbg"]
            assert min(abs(rr - r) + abs(cc - c) for rr, cc in mons) >= 6, (lv["name"], ch)


def test_star_rules():
    duo, bub, snk = games.get("duo"), games.get("bubble"), games.get("snake")
    lv = duo.level(1)
    assert duo.checks(lv, duo.clean(lv, {"seconds": lv["par"] - 1, "gems": 99}), None) == {"clear": True, "gems": True, "time": True}
    assert duo.clean(lv, {"seconds": 10, "gems": 99})["gems"] == lv["gems"]   # 不能报比地图上还多的宝石
    lv = bub.level(1)
    assert bub.checks(lv, bub.clean(lv, {"seconds": lv["par"] + 1, "deaths": 2}), None) == {"clear": True, "time": False, "safe": True}
    lv = snk.level(1)
    assert snk.checks(lv, snk.clean(lv, {"seconds": 10, "deaths": 2, "score": 70}), None)["safe"] is False


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


A, B = {"device": "da"}, {"device": "db"}


def _start(c, rid, n=1, dev=A):
    return c.post(f"/api/rooms/{rid}/start", json={**dev, "n": n}).json()["play"]


def _finish(c, rid, play, result="win", dev=A, **report):
    return c.post(f"/api/rooms/{rid}/finish", json={**dev, "session": play["session"], "result": result, "report": report})


def test_rooms_and_finish():
    with TestClient(app) as c:
        _, a, b = _family("r")
        lone = auth.create_user("mp-lone@x.com", "secret1", "独生", "kid", parent_id=auth.create_user("mp-lp@x.com", "secret1", "家长", "parent"))
        _login(c, "mp-lone@x.com")
        assert c.get("/arena/duo").status_code == 200
        assert c.get("/api/games/duo/lobby").json()["siblings"] == []      # 页面据此提示「家里还没有别的小伙伴」
        assert c.post("/api/rooms", json={"game": "duo"}).status_code == 403
        assert lone

        _login(c, "mp-a-r@x.com")
        assert c.get("/arena/duo").status_code == 200
        room = c.post("/api/rooms", json={**A, "game": "duo", "role": "water"}).json()
        assert room["host"] and room["me"]["role"] == "water" and room["progress"] is None and not room["full"]
        rid = room["id"]
        assert c.post(f"/api/rooms/{rid}/start", json={**A, "n": 1}).status_code == 409   # 人还没到齐

        _login(c, "mp-b-r@x.com")
        lob = c.get("/api/games/duo/lobby").json()
        assert [r["id"] for r in lob["rooms"]] == [rid] and lob["rooms"][0]["host"]["name"] == "姐姐"
        assert "姐姐" in c.get("/arena").text   # 乐园首页：「姐姐在等你」
        room = c.post(f"/api/rooms/{rid}/enter", json=B).json()
        assert not room["host"] and room["me"]["role"] == "fire" and len(room["members"]) == 2 and room["full"]
        assert room["progress"]["upto"] == 1
        assert c.post(f"/api/rooms/{rid}/level", json={**B, "n": 2}).status_code == 403          # 只有开房间的人选关
        assert c.post(f"/api/rooms/{rid}/start", json={**B, "n": 1}).status_code == 403

        _login(c, "mp-a-r@x.com")
        assert c.post(f"/api/rooms/{rid}/level", json={**A, "n": 2}).status_code == 403          # 第 2 关还没解锁
        play = _start(c, rid)
        assert _finish(c, rid, play, seconds=2).status_code == 400   # 太快，不算
        par = games.get("duo").level(1)["par"]
        r = _finish(c, rid, play, seconds=par + 5, gems=1, deaths=1).json()
        assert r["stars"] == 1 and r["checks"] == {"clear": True, "gems": False, "time": False}
        assert r["next"] == 2 and r["progress"]["upto"] == 2 and r["new_best"]
        assert {s["key"] for s in r["stickers"][str(a)]} >= {"mp1"} and "mp_clean" not in {s["key"] for s in r["stickers"][str(a)]}
        assert {s["key"] for s in r["stickers"][str(b)]} >= {"mp1"}
        play = _start(c, rid)
        r2 = _finish(c, rid, play, seconds=par - 5, gems=99).json()
        assert r2["stars"] == 3 and r2["new_best"] and r2["prev_best"] == par + 5
        prog = progress.progress(games.get("duo"), progress.team_key((b, a)))   # 谁先谁后都是同一队
        assert prog["levels"][0]["stars"] == 3 and prog["levels"][0]["best"] == par - 5 and prog["upto"] == 2
        assert len(prog["worlds"]) == 2   # 8 关只有两个世界有关卡
        # 没过关也记一局，不扣星星
        play = _start(c, rid, 2)
        assert _finish(c, rid, play, "lose", seconds=40).json()["stars"] == 0
        assert db.one("SELECT result, team FROM game_sessions WHERE id=?", play["session"])["result"] == "lose"
        assert c.post(f"/api/rooms/{rid}/level", json={**A, "n": 2}).json()["level"] == 2
        # 换角色：两个人互换
        room = c.post(f"/api/rooms/{rid}/role", json=A).json()
        assert room["me"]["role"] == "fire" and next(m for m in room["members"] if m["id"] == b)["role"] == "water"

        # 倒下答题：答错不冷冻（马上能拿下一道题）；答题对局挂在这一局下面
        play = _start(c, rid, 2)
        m = c.post("/api/arena/start", json={"game": "duo", "session": play["session"]}).json()
        q = c.get(f"/api/arena/{m['match_id']}/q").json()["item"]
        res = c.post(f"/api/arena/{m['match_id']}/a", json={"item_id": q["id"], "answer": "-99999"}).json()
        assert res["correct"] is False and res["freeze_ms"] == 0
        assert c.get(f"/api/arena/{m['match_id']}/q").status_code == 200

        # 别的家庭进不来，也不能把对局挂到别人的局上
        _family("x")
        _login(c, "mp-a-x@x.com")
        assert c.post(f"/api/rooms/{rid}/enter", json=A).status_code == 403
        assert c.get(f"/api/rooms/{rid}").status_code == 403
        assert c.post("/api/arena/start", json={"game": "duo", "session": play["session"]}).status_code == 410

        # 房主走了：弟弟接任房主；弟弟也走了，房间关掉
        _login(c, "mp-a-r@x.com")
        c.post(f"/api/rooms/{rid}/leave", json=A)
        assert db.one("SELECT host_id FROM game_rooms WHERE id=?", rid)["host_id"] == b
        assert db.one("SELECT ended_at FROM game_sessions WHERE id=?", play["session"])["ended_at"]   # 正在玩的这一局算退出
        _login(c, "mp-b-r@x.com")
        assert c.get(f"/api/rooms/{rid}").json()["host"]
        c.post(f"/api/rooms/{rid}/leave", json=B)
        assert db.one("SELECT status FROM game_rooms WHERE id=?", rid)["status"] == "closed"


def test_devices():
    """同一个账号两台设备：原来那台在玩就先问，点了才换；换走的那台再改房间、存档都会被告知；刷新（同一台设备）直接回来。"""
    with TestClient(app) as c:
        _, a, b = _family("d")
        _login(c, "mp-a-d@x.com")
        rid = c.post("/api/rooms", json={**A, "game": "snake"}).json()["id"]
        cookie_a = dict(c.cookies)
        c2 = TestClient(app)
        _login(c2, "mp-b-d@x.com")
        c2.post(f"/api/rooms/{rid}/enter", json=B)
        c.cookies.update(cookie_a)
        play = _start(c, rid)
        assert c.post(f"/api/rooms/{rid}/save", json={**A, "session": play["session"], "el": 5, "me": {"len": 9}}).json()["ok"]

        pad = {"device": "ipad"}
        r = c.post(f"/api/rooms/{rid}/enter", json=pad).json()   # 原来那台正在玩：先问
        assert r["elsewhere"] == 1
        assert c.post(f"/api/rooms/{rid}/enter", json=A).json().get("elsewhere") is None   # 刷新：同一台设备，直接回来
        r = c.post(f"/api/rooms/{rid}/take", json=pad).json()   # 点了「在这台设备上接着玩」
        assert r["play"]["resume"]["me"] == {"len": 9}
        old = c.post(f"/api/rooms/{rid}/save", json={**A, "session": play["session"], "el": 7})
        assert old.status_code == 409 and old.json()["moved"]    # 原来那台：已经换到另一台设备上了
        assert c.post(f"/api/rooms/{rid}/level", json={**A, "n": 1}).status_code == 409
        assert c.post(f"/api/rooms/{rid}/save", json={**pad, "session": play["session"], "el": 8}).json()["ok"]
        # 原来那台没在玩（太久没存档）：新设备直接换过来，不用问
        db.run("UPDATE room_members SET seen_at=? WHERE room_id=? AND user_id=?", "2000-01-01T00:00:00", rid, a)
        assert c.post(f"/api/rooms/{rid}/enter", json=A).json().get("elsewhere") is None
        assert c.post(f"/api/rooms/{rid}/save", json={**pad, "session": play["session"], "el": 9}).status_code == 409
        # 存档太大：不收
        assert c.post(f"/api/rooms/{rid}/save", content=b'{"x": "' + b"y" * 20000 + b'"}', headers={"Content-Type": "application/json"}).status_code == 413


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
        rid = c.post("/api/rooms", json={**A, "game": "snake"}).json()["id"]
        cookie_a = dict(c.cookies)
        c2 = TestClient(app)
        _login(c2, "mp-b-w@x.com")
        c2.post(f"/api/rooms/{rid}/enter", json=B)
        c3 = TestClient(app)
        _family("w2")
        _login(c3, "mp-a-w2@x.com")
        with pytest.raises(WebSocketDisconnect) as e:
            with c3.websocket_connect(f"/ws/room/{rid}") as ws:
                ws.receive_text()
        assert e.value.code == 4403

        c.cookies.update(cookie_a)
        with c.websocket_connect(f"/ws/room/{rid}") as wa:
            with c2.websocket_connect(f"/ws/room/{rid}") as wb:
                hello = json.loads(wa.receive_text())
                assert hello == {"t": "hello", "from": b}
                wb.send_text("x" * 7000)                                   # 太长：丢掉
                wb.send_text(json.dumps({"t": "s", "x": 10, "from": 999}))   # from 由服务器盖，改不了
                assert _recv(wa) == {"t": "s", "x": 10, "from": b}
                wa.send_text(json.dumps({"t": "rescue", "to": b}))          # 泡泡堂救人：照样转发
                assert _recv(wb) == {"t": "rescue", "to": b, "from": a}
            assert _recv(wa) == {"t": "bye", "from": b}
        assert db.one("SELECT seen_at FROM game_rooms WHERE id=?", rid)["seen_at"]


def test_save_and_resume():
    """存档：刷新 / 换设备后接着玩。每个人存自己的，房主再存世界；结束了就清掉；太久没人就不接着玩。"""
    with TestClient(app) as c:
        _, a, b = _family("s")
        _login(c, "mp-a-s@x.com")
        rid = c.post("/api/rooms", json={**A, "game": "snake"}).json()["id"]
        cookie_a = dict(c.cookies)
        c2 = TestClient(app)
        _login(c2, "mp-b-s@x.com")
        c2.post(f"/api/rooms/{rid}/enter", json=B)
        c.cookies.update(cookie_a)
        play = _start(c, rid)
        m = c2.post("/api/arena/start", json={"game": "snake", "session": play["session"]}).json()["match_id"]
        assert c2.get(f"/api/rooms/{rid}").json()["play"]["resume"] is None   # 还没存过档
        c.post(f"/api/rooms/{rid}/save", json={**A, "session": play["session"], "el": 12.5, "me": {"len": 20}, "w": {"f": [[1, 2, 3, 1, 0]]}, "deaths": 1})
        c2.post(f"/api/rooms/{rid}/save", json={**B, "session": play["session"], "el": 13, "me": {"len": 33, "alive": 0}, "need": 2, "deaths": 4,
                                                "match": m, "w": {"cheat": 1}})   # 队友的世界不算
        assert c2.post(f"/api/rooms/{rid}/save", json={**B, "session": 99999, "el": 1}).status_code == 410   # 别的局：不收
        p = c2.get(f"/api/rooms/{rid}").json()["play"]
        rs = p["resume"]
        assert p["n"] == 1 and p["seed"] == play["seed"] and rs["el"] == 13 and rs["me"] == {"len": 33, "alive": 0}
        assert rs["need"] == 2 and rs["deaths"] == 4 and rs["peer_deaths"] == 1 and rs["match"] == m
        assert rs["world"] == {"f": [[1, 2, 3, 1, 0]]}
        pa = c.get(f"/api/rooms/{rid}").json()["play"]["resume"]
        assert pa["me"] == {"len": 20} and pa["match"] is None    # 开房间的人没有开着的答题对局
        # 对局结束了就不再接着用
        c2.post(f"/api/arena/{m}/end", json={"result": "quit", "stats": {}})
        assert c2.get(f"/api/rooms/{rid}").json()["play"]["resume"]["match"] is None
        # 只有房主能说「这一局不玩了」
        c2.post(f"/api/rooms/{rid}/save", json={**B, "session": play["session"], "end": 1})
        assert c.get(f"/api/rooms/{rid}").json()["play"]
        # 过关：清掉
        _finish(c, rid, play, seconds=30, score=99)
        assert c.get(f"/api/rooms/{rid}").json()["play"] is None
        assert db.one("SELECT state FROM game_sessions WHERE id=?", play["session"])["state"] is None
        # 太久没人：不接着玩（记成退出）
        play = _start(c, rid, 1)
        alive, sessions.ALIVE_S = sessions.ALIVE_S, -1
        try:
            assert c.get(f"/api/rooms/{rid}").json()["play"] is None
        finally:
            sessions.ALIVE_S = alive
        assert db.one("SELECT result FROM game_sessions WHERE id=?", play["session"])["result"] == "quit"
        play = _start(c, rid, 1)
        c.post(f"/api/rooms/{rid}/save", json={**A, "session": play["session"], "end": 1})
        assert c.get(f"/api/rooms/{rid}").json()["play"] is None
