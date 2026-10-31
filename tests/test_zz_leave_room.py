"""从正在玩的游戏回到大厅（app/static/play/shell.js leaveRoom、app/arena/rooms.py leave）：
孩子在玩，家里人开了房间等他。他得能停下这一局、让出座位回大厅；回乐园再点这款游戏进的是大厅，
不会又被拉回原来那一局。乐园首页「X 在等你」点进来直接加入那个房间。所有联网游戏（包括往上跳、左右飞）都走这一套。"""
import json
import pathlib
import re

from fastapi.testclient import TestClient

from app import arena, auth, db
from app.arena import rooms
from app.main import app

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"


def _kids(tag):
    pid = auth.create_user(f"lv-p-{tag}@x.com", "secret1", "家长", "parent")
    a = auth.create_user(f"lv-a-{tag}@x.com", "2580", "哥哥", "kid", parent_id=pid, grade="G5")
    b = auth.create_user(f"lv-b-{tag}@x.com", "2580", "爸爸号", "kid", parent_id=pid, grade="G5")
    for k in (a, b):
        arena.set_game_unlock(k, "free")
    return a, b


def _login(c, email):
    c.cookies.clear()
    assert c.post("/login", data={"email": email, "password": "2580"}).status_code in (200, 303)


def _info(page: str) -> dict:
    raw = re.search(r"data-info='([^']*)'", page).group(1)
    return json.loads(raw.replace("&#34;", '"').replace("&#39;", "'").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


def test_leaving_mid_game_frees_the_seat_for_every_multi_game():
    with TestClient(app) as c:
        for game in ("jump", "fly", "helix"):
            a, _ = _kids(game)
            _login(c, f"lv-a-{game}@x.com")
            dev = {"device": f"d-{game}"}
            rid = c.post("/api/rooms", json={**dev, "game": game, "kind": "family"}).json()["id"]
            play = c.post(f"/api/rooms/{rid}/start", json={**dev, "n": 1}).json()["play"]
            assert _info(c.get(f"/arena/{game}").text)["room"] == rid   # 刷新：回到这一局
            assert c.post(f"/api/rooms/{rid}/leave", json=dev).status_code == 200
            assert db.one("SELECT result FROM game_sessions WHERE id=?", play["session"])["result"] == "quit"
            assert rooms.my_room(auth.get_user(a), game) is None
            assert _info(c.get(f"/arena/{game}").text)["room"] is None   # 再点这款游戏：进大厅，不回原来那一局


def test_invite_joins_that_room_not_my_old_one():
    with TestClient(app) as c:
        a, b = _kids("inv")
        _login(c, "lv-a-inv@x.com")
        old = c.post("/api/rooms", json={"device": "da", "game": "jump", "kind": "family"}).json()["id"]
        c.post(f"/api/rooms/{old}/start", json={"device": "da", "n": 1})
        _login(c, "lv-b-inv@x.com")
        rid = c.post("/api/rooms", json={"device": "db", "game": "jump", "kind": "family"}).json()["id"]

        _login(c, "lv-a-inv@x.com")
        assert f'href="/arena/jump?join={rid}"' in c.get("/arena").text
        info = _info(c.get(f"/arena/jump?join={rid}").text)
        assert info["join"] == rid and info["room"] == old
        room = c.post(f"/api/rooms/{rid}/enter", json={"device": "da"}).json()
        assert {m["id"] for m in room["members"]} == {a, b}
        assert rooms.my_room(auth.get_user(a), "jump") == rid
        assert db.one("SELECT status FROM game_rooms WHERE id=?", old)["status"] == "closed"   # 原来的房间让出来了
        assert _info(c.get(f"/arena/jump?join={rid}").text)["join"] == 0   # 已经在这个房间：照常回房间


def test_shell_has_one_way_out():
    """外壳：暂停面板有「不玩了，回大厅」，左上角 ‹ 在联网游戏里先离开房间再走；三处都走同一个 leaveRoom。"""
    js = (ROOT / "static" / "play" / "shell.js").read_text(encoding="utf-8")
    assert "pl-quit" in js and "不玩了，回大厅" in js
    assert js.count("await leaveRoom()") == 1 and "leaveRoom(), new Promise" in js
    assert "joinRoom(data.join)" in js
