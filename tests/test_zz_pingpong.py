"""乒乓球对战（app/arena/games/pingpong.py）：登记、让球（按年级，再按最近谁赢得多自动调）、输一局要答对 1 道题的存档、一场的结果。"""
from fastapi.testclient import TestClient

from app import arena, auth, db
from app.arena import games
from app.main import app

A, B = {"device": "pa"}, {"device": "pb"}


def _login(c, email):
    c.cookies.clear()
    pw = "2580" if auth.uses_pin(auth.by_email(email)) else "secret1"   # 孩子用 4 位数字
    assert c.post("/login", data={"email": email, "password": pw}).status_code in (200, 303)


def test_registered():
    g = games.get("pingpong")
    assert g.multi and g.players == (2, 2) and not g.total and g.freeze is False and "pingpong" in arena.GAMES
    rep = g.clean({}, {"winner": "7", "won": 9, "lost": -1, "seconds": 30})
    assert rep["winner"] == 7 and rep["won"] == 2 and rep["lost"] == 0


def test_match_and_handicap():
    with TestClient(app) as c:
        pid = auth.create_user("pp-p@x.com", "secret1", "家长", "parent")
        big = auth.create_user("pp-a@x.com", "2580", "姐姐", "kid", parent_id=pid, grade="G8")
        small = auth.create_user("pp-b@x.com", "2580", "弟弟", "kid", parent_id=pid, grade="G3")
        for k in (big, small):
            arena.set_game_unlock(k, "free")
        _login(c, "pp-a@x.com")
        assert c.get("/arena/pingpong").status_code == 200
        rid = c.post("/api/rooms", json={**A, "game": "pingpong"}).json()["id"]
        cookie = dict(c.cookies)
        c2 = TestClient(app)
        _login(c2, "pp-b@x.com")
        room = c2.post(f"/api/rooms/{rid}/enter", json=B).json()
        assert room["full"] and room["progress"] is None
        c.cookies.update(cookie)

        def start():
            return c.post(f"/api/rooms/{rid}/start", json={**A, "n": 1}).json()["play"]

        play = start()
        st = play["stage"]
        assert st["points"] == 7 and st["cap"] == 10 and st["to_win"] == 2
        assert st["assist"] == {str(big): 0.0, str(small): 0.8}   # 差 5 个年级：弟弟让球

        # 输了一局：要答对 1 道题（和所有游戏一样），存档记得住（刷新回来还在答题）
        c2.post(f"/api/rooms/{rid}/save", json={**B, "session": play["session"], "el": 40, "need": 1, "me": {"y": 70}})
        assert c2.get(f"/api/rooms/{rid}").json()["play"]["resume"]["need"] == 1

        # 弟弟赢了：记下赢的人和局分；下一场弟弟的让球少一点
        r = c.post(f"/api/rooms/{rid}/finish", json={**A, "session": play["session"], "result": "win",
                                                     "report": {"seconds": 300, "winner": small, "won": 2, "lost": 1, "score": 30}})
        assert r.status_code == 200 and r.json()["stars"] == 0 and r.json()["n"] == 0
        data = db.jload(db.one("SELECT data FROM game_sessions WHERE id=?", play["session"])["data"], {})
        assert data["winner"] == small and data["won"] == 2 and data["lost"] == 1
        st = start()["stage"]
        assert st["assist"] == {str(big): 0.1, str(small): 0.7}
        # 姐姐连赢三场：弟弟的让球回来，还更多一点（最多 1）
        for _ in range(3):
            play = c.get(f"/api/rooms/{rid}").json()["play"]
            c.post(f"/api/rooms/{rid}/finish", json={**A, "session": play["session"], "result": "win",
                                                     "report": {"seconds": 200, "winner": big, "won": 2, "lost": 0}})
            start()
        assert c.get(f"/api/rooms/{rid}").json()["play"]["stage"]["assist"] == {str(big): 0.0, str(small): 1.0}
