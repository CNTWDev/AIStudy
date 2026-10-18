"""联网房间不只限一家人（app/arena/rooms.py）：公开房间、密码房间、昵称；泡泡堂、贪吃蛇 2～4 个人一起玩。"""
from fastapi.testclient import TestClient

from app import arena, auth, db
from app.arena import games, progress
from app.main import app


def _kid(tag, pid=None, name="孩子"):
    pid = pid or auth.create_user(f"or-p-{tag}@x.com", "secret1", "家长", "parent")
    k = auth.create_user(f"or-{tag}@x.com", "secret1", name, "kid", parent_id=pid, grade="G5")
    arena.set_game_unlock(k, "free")
    return pid, k


def _login(c, tag):
    c.cookies.clear()
    assert c.post("/login", data={"email": f"or-{tag}@x.com", "password": "secret1"}).status_code in (200, 303)


def _dev(tag):
    return {"device": f"d-{tag}"}


def _nick(c, nick):
    return c.post("/api/arena/nickname", json={"nickname": nick})


def test_nickname_rules():
    with TestClient(app):
        _, k = _kid("nk", name="王小明")
        u = auth.get_user(k)
        for bad in ("", "王小明", "小明同学", "x" * 11, "QQ12345678", "a@b"):
            try:
                auth.set_nickname(u, bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        assert auth.set_nickname(u, " 闪电  小队长 ") == "闪电 小队长"
        assert auth.public_name(auth.get_user(k)) == "闪电 小队长"
        assert auth.public_name({"id": 7, "nickname": ""}) == "小玩家7"


def test_public_and_code_rooms():
    with TestClient(app) as c:
        _kid("pa", name="姐姐甲")
        _kid("pb", name="弟弟乙")
        pc_parent, pc = _kid("pc", name="丙丙")
        auth.set_private(pc, True)

        # 没有兄弟姐妹也能开公开房间；先要起昵称
        _login(c, "pa")
        lob = c.get("/api/games/bubble/lobby").json()
        assert lob["siblings"] == [] and lob["public_ok"] and lob["nickname"] == ""
        r = c.post("/api/rooms", json={**_dev("pa"), "game": "bubble", "kind": "public"})
        assert r.status_code == 409 and r.json()["need_nick"]
        assert c.post("/api/rooms", json={**_dev("pa"), "game": "bubble", "kind": "family"}).status_code == 403   # 家里没别人
        assert _nick(c, "姐姐甲").status_code == 400
        assert _nick(c, "火箭").json()["nickname"] == "火箭"
        room = c.post("/api/rooms", json={**_dev("pa"), "game": "bubble", "kind": "public"}).json()
        rid = room["id"]
        assert room["kind"] == "public" and room["members"][0]["name"] == "火箭"

        # 别的家庭的孩子：大厅里看得到，只看到昵称；进来也要先起昵称
        _login(c, "pb")
        lob = c.get("/api/games/bubble/lobby").json()
        row = next(x for x in lob["rooms"] if x["id"] == rid)
        assert row["kind"] == "public" and row["host"]["name"] == "火箭" and row["count"] == 1 and row["max"] == 4
        assert "姐姐甲" not in c.get("/api/games/bubble/lobby").text
        r = c.post(f"/api/rooms/{rid}/enter", json=_dev("pb"))
        assert r.status_code == 409 and r.json()["need_nick"]
        _nick(c, "小飞侠")
        room = c.post(f"/api/rooms/{rid}/enter", json=_dev("pb")).json()
        assert {m["name"] for m in room["members"]} == {"火箭", "小飞侠"} and room["full"]
        assert "姐姐甲" not in c.get(f"/api/rooms/{rid}").text and "弟弟乙" not in c.get(f"/api/rooms/{rid}").text

        # 家长关了公开房间：大厅里看不到，也进不去
        _login(c, "pc")
        assert _nick(c, "云朵").status_code == 200
        assert rid not in [x["id"] for x in c.get("/api/games/bubble/lobby").json()["rooms"]]
        assert c.post(f"/api/rooms/{rid}/enter", json=_dev("pc")).status_code == 403
        assert c.post("/api/rooms", json={**_dev("pc"), "game": "bubble", "kind": "public"}).status_code == 403

        # 密码房间：大厅里不列出来，房间号 + 密码对了才能进
        room = c.post("/api/rooms", json={**_dev("pc"), "game": "snake", "kind": "code"}).json()
        cid, pin = room["id"], room["pin"]
        assert room["kind"] == "code" and len(pin) == 4 and pin.isdigit()
        _login(c, "pb")
        assert cid not in [x["id"] for x in c.get("/api/games/snake/lobby").json()["rooms"]]
        wrong = f"{(int(pin) + 1) % 10000:04d}"
        assert c.post(f"/api/rooms/{cid}/enter", json={**_dev("pb"), "pin": wrong}).status_code == 403
        room = c.post(f"/api/rooms/{cid}/enter", json={**_dev("pb"), "pin": pin}).json()
        assert len(room["members"]) == 2 and room["kind"] == "code"
        assert pc_parent


def test_four_players_and_no_joining_mid_game():
    with TestClient(app) as c:
        tags = ["f1", "f2", "f3", "f4", "f5"]
        ids = {}
        for i, t in enumerate(tags):
            ids[t] = _kid(t, name=f"甲乙{i}")[1]
            _login(c, t)
            _nick(c, f"昵称{i}")
        _login(c, "f1")
        rid = c.post("/api/rooms", json={**_dev("f1"), "game": "bubble", "kind": "public", "role": "red"}).json()["id"]
        for t in tags[1:4]:
            _login(c, t)
            room = c.post(f"/api/rooms/{rid}/enter", json=_dev(t)).json()
        assert [m["role"] for m in room["members"]] == ["red", "blue", "yellow", "green"]
        _login(c, "f5")
        assert c.post(f"/api/rooms/{rid}/enter", json=_dev("f5")).status_code == 409   # 满了

        _login(c, "f4")
        c.post(f"/api/rooms/{rid}/leave", json=_dev("f4"))
        _login(c, "f1")
        room = c.post(f"/api/rooms/{rid}/role", json=_dev("f1")).json()   # 换角色：三个人轮换手里的角色
        assert [m["role"] for m in room["members"]] == ["blue", "yellow", "red"]
        play = c.post(f"/api/rooms/{rid}/start", json={**_dev("f1"), "n": 1}).json()["play"]
        stage = play["stage"]
        assert stage["players"] == 3 and stage["safe"] == 3 and any("3" in row for row in stage["map"])
        assert stage["monsters"] > games.get("bubble").level(1)["monsters"]
        _login(c, "f5")   # 正在打的这一局不能进人
        r = c.post(f"/api/rooms/{rid}/enter", json=_dev("f5"))
        assert r.status_code == 409 and "打完" in r.json()["error"]
        assert rid not in [x["id"] for x in c.get("/api/games/bubble/lobby").json()["rooms"]]

        _login(c, "f1")
        lv = games.get("bubble").level(1)
        r = c.post(f"/api/rooms/{rid}/finish", json={**_dev("f1"), "session": play["session"], "result": "win",
                                                    "report": {"seconds": lv["par"] - 1, "deaths": 3}}).json()
        assert r["stars"] == 3 and r["checks"]["safe"]   # 3 个人：被困 3 次也算
        three = progress.team_key(ids[t] for t in tags[:3])
        assert set(r["stickers"]) == {str(ids[t]) for t in tags[:3]}
        assert db.one("SELECT team FROM game_sessions WHERE id=?", play["session"])["team"] == three
        assert progress.progress(games.get("bubble"), three)["upto"] == 2


def test_new_team_opens_levels_everyone_reached():
    """几个人第一次凑成一队：队里打得最远的人打到的关都能选（不用从第 1 关打起），每个人过的关都记在自己身上。"""
    g = games.get("snake")
    a, b, c = (_kid(f"rc{i}")[1] for i in range(3))
    for n in (1, 2, 3):
        progress.record(g, progress.team_key((a, b)), n, True, 1, 30)
    progress.record(g, progress.team_key((c,)), 1, True, 1, 30)
    progress.record(g, progress.team_key((b, c)), 2, True, 1, 30)
    assert progress.reach(g, a) == 3 and progress.reach(g, c) == 2
    assert progress.progress(g, progress.team_key((a, b, c)))["upto"] == 4   # 打得最远的打到第 3 关：开到第 4 关
    assert progress.progress(g, progress.team_key((a, c)))["upto"] == 4
    assert progress.progress(g, progress.team_key((c,)))["upto"] == 3       # 和别人一起过的关也算自己的


def test_levels_for_more_players():
    """泡泡堂 3、4 个人：每个出生点旁边有空地、怪兽离谁都够远、怪兽更多；贪吃蛇目标按人数加。"""
    bub = games.get("bubble")
    for lv in [bub.level(n) for n in range(1, bub.total + 1, 7)]:
        for k in (3, 4):
            s = bub.for_team(lv, k)
            g = s["map"]
            assert len(g) == len(lv["map"]) and all(len(r) == len(lv["map"][0]) for r in g)
            starts = [next((r, row.index(ch)) for r, row in enumerate(g) if ch in row) for ch in "1234"[:k]]
            mons = [(r, c) for r, row in enumerate(g) for c, x in enumerate(row) if x in "sbg"]
            for r, c in starts:
                assert sum(g[r + dr][c + dc] == "." for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0))) >= 2, (lv["n"], k)
                assert min(abs(rr - r) + abs(cc - c) for rr, cc in mons) >= 5, (lv["n"], k)
            assert s["monsters"] >= lv["monsters"] and s["players"] == k
        assert bub.for_team(lv, 4)["monsters"] >= bub.for_team(lv, 3)["monsters"]
        assert bub.for_team(lv, 2) is lv
    snk = games.get("snake")
    lv = snk.level(5)
    assert snk.for_team(lv, 4)["target"] == lv["target"] * 2 and snk.level(5)["target"] == lv["target"]


def test_parent_sets_nickname_and_public_rooms():
    with TestClient(app) as c:
        c.cookies.clear()
        pid = auth.create_user("or-pp@x.com", "secret1", "家长", "parent")
        k = auth.create_user("or-pk@x.com", "secret1", "小可", "kid", parent_id=pid, grade="G3")
        c.post("/login", data={"email": "or-pp@x.com", "password": "secret1"})
        assert "公开房间" in c.get(f"/parent/kids/{k}/edit").text and arena.public_rooms(dict(auth.get_user(k)))
        base = {"id": str(k), "name": "小可", "email": "or-pk@x.com", "grade": "G3", "daily_minutes": "60", "game_minutes": "20"}
        assert c.post("/parent/kids/save", data={**base, "nickname": "小可爱"}).status_code == 400   # 带了真名
        c.post("/parent/kids/save", data={**base, "nickname": "星星", "private": "1"})
        u = auth.get_user(k)
        assert u["nickname"] == "星星" and not arena.public_rooms(dict(u))
        c.post("/parent/kids/save", data={**base, "nickname": "星星"})
        assert arena.public_rooms(dict(auth.get_user(k)))
        # 以前单独关过公开房间的：算打开了隐私模式
        db.run("UPDATE users SET settings=? WHERE id=?", db.jdump({"public_rooms": False}), k)
        assert auth.is_private(dict(auth.get_user(k)))
