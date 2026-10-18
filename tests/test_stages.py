"""火柴人闯关（单机也走房间）：关卡越往后越难、过关才开下一关、星星、打通世界解锁装备、贴纸、每一局都有记录。"""
from fastapi.testclient import TestClient

from app import arena, db
from app.arena import games, progress
from app.main import app

DEV = {"device": "d1"}


def _right(it):
    a = it["answer"]
    return str(a[0] if isinstance(a, list) else a)


def test_stage_params_get_harder():
    sm = games.get("stickman")
    ps = [sm.params(n, 0) for n in range(1, 31)]
    for key in ("ai_dmg", "ai_speed", "ai_aggr", "ai_dodge", "ai_guard"):
        assert all(a[key] <= b[key] for a, b in zip(ps, ps[1:])), key
    normal = [p["ai_hp"] for i, p in enumerate(ps) if (i + 1) % 6]
    assert normal == sorted(normal) and normal[0] < normal[-1]
    bosses = [lv["n"] for lv in sm.levels if lv["boss"]]
    assert bosses == [6, 12, 18, 24, 30] and all(ps[n - 1]["boss"]["pips"] >= 3 for n in bosses)
    assert ps[0]["regen"] and ps[1]["regen"] and not ps[2]["regen"]          # 前两关能量会慢慢回，之后只靠答题
    assert ps[2]["start_energy"] < ps[0]["start_energy"]
    assert sm.params(7, 1)["shop"] == ["stick", "shield"] and sm.params(30, 5)["super"]
    lv = sm.level(1)
    rep = sm.clean(lv, {"hp_left": 60})
    assert sm.checks(lv, rep, 95) == {"clear": True, "focus": True, "hp": True}
    assert sm.checks(lv, sm.clean(lv, {"hp_left": 10}), None) == {"clear": True, "focus": False, "hp": False}


def _play(c, rid, n=None, answers=0):
    """开一局（房主开局）+ 我的答题对局，答对几题。返回 (这一局, 对局 id)。"""
    room = c.post(f"/api/rooms/{rid}/start", json={**DEV, "n": n}).json()
    play = room["play"]
    mid = c.post("/api/arena/start", json={"game": "stickman", "src": "math", "session": play["session"]}).json()["match_id"]
    for _ in range(answers):
        q = c.get(f"/api/arena/{mid}/q").json()["item"]
        it = db.jload(db.one("SELECT data FROM arena_matches WHERE id=?", mid)["data"], {})["pending"]["item"]
        assert c.post(f"/api/arena/{mid}/a", json={"item_id": q["id"], "answer": _right(it)}).json()["correct"]
    return play, mid


def _finish(c, rid, play, result, **report):
    report.setdefault("seconds", 30)
    return c.post(f"/api/rooms/{rid}/finish", json={**DEV, "session": play["session"], "result": result, "report": report}).json()


def test_stage_flow():
    with TestClient(app) as c:
        if c.get("/login").text.count("创建网站管理员账号"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        else:
            c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        c.post("/admin/users/create", data={"email": "lp@x.com", "password": "secret1", "name": "家长", "role": "parent"})
        c.get("/logout")
        c.post("/login", data={"email": "lp@x.com", "password": "secret1"})
        c.post("/parent/kids/save", data={"name": "小闯", "email": "lk@x.com", "password": "secret1", "grade": "G3",
                                          "daily_minutes": "60", "subj_math": "math-shanghai", "game_minutes": "60",
                                          "game_unlock": "free"})
        c.get("/logout")
        c.post("/login", data={"email": "lk@x.com", "password": "secret1"})
        kid = dict(db.one("SELECT * FROM users WHERE email='lk@x.com'"))
        assert "第 1 关" in c.get("/arena").text and c.get("/arena/stickman").status_code == 200

        # 单机：每款游戏一个自己的房间，再打开还是它
        room = c.post("/api/rooms", json={**DEV, "game": "stickman"}).json()
        rid = room["id"]
        assert room["host"] and len(room["members"]) == 1 and room["progress"]["upto"] == 1 and room["play"] is None
        assert c.post("/api/rooms", json={**DEV, "game": "stickman"}).json()["id"] == rid

        # 还没打过第 1 关，不能直接打第 3 关
        r = c.post(f"/api/rooms/{rid}/start", json={**DEV, "n": 3})
        assert r.status_code == 403 and "第 1 关" in r.json()["error"]
        play, mid = _play(c, rid, 1, answers=3)   # 答对 3 题：专注值够了
        assert play["n"] == 1 and play["stage"]["params"]["ai_hp"] == 70 and play["seed"]
        assert db.one("SELECT session_id FROM arena_matches WHERE id=?", mid)["session_id"] == play["session"]
        res = _finish(c, rid, play, "win", hp_left=30)
        assert res["n"] == 1 and res["stars"] == 2 and res["checks"] == {"clear": True, "focus": True, "hp": False}
        assert res["next"] == 2 and res["first_clear"] and res["new_best"]
        end = c.post(f"/api/arena/{mid}/end", json={"result": "win", "stats": {"hp_left": 30}}).json()
        assert "first_win" in {s["key"] for s in end["stickers"]}
        s = db.one("SELECT * FROM game_sessions WHERE id=?", play["session"])
        assert s["result"] == "win" and s["stars"] == 2 and s["team"] == str(kid["id"]) and s["state"] is None and s["seconds"] > 0
        assert _finish(c, rid, play, "win").get("error")   # 一局只报一次

        # 输了不扣星星，也不算过关
        play, _ = _play(c, rid, 2)
        res = _finish(c, rid, play, "lose", hp_left=0)
        assert res["stars"] == 0 and res["next"] is None
        assert c.post(f"/api/rooms/{rid}/start", json={**DEV, "n": 3}).status_code == 403
        # 回去刷第 1 关：星星只记最好的一次
        play, _ = _play(c, rid, 1)
        res = _finish(c, rid, play, "win", hp_left=80)
        assert res["stars"] == 2 and res["best_stars"] == 2 and not res["new_best"] and not res["first_clear"]   # 没答题：没有专注星

        # 打通第 1 个世界（第 6 关大怪兽）：解锁护盾，得贴纸，商店里能买护盾了
        for n in range(2, 6):
            db.run("INSERT INTO game_runs(game, team, level, stars, plays, wins, cleared_at, updated_at) VALUES(?,?,?,?,?,?,?,?) "
                   "ON CONFLICT(game, team, level) DO UPDATE SET cleared_at=excluded.cleared_at",
                   "stickman", str(kid["id"]), n, 1, 1, 1, db.now(), db.now())
        play, _ = _play(c, rid, 6)
        assert play["stage"]["boss"] and play["stage"]["params"]["boss"] and "shield" not in play["stage"]["params"]["shop"]
        res = _finish(c, rid, play, "win", hp_left=60)
        assert res["unlocked"]["id"] == "shield" and res["next"] == 7
        assert "sm_w1" in {x["key"] for x in res["stickers"][str(kid["id"])]}
        assert c.get(f"/api/rooms/{rid}").json()["level"] == 7
        play, _ = _play(c, rid, 7)
        assert "shield" in play["stage"]["params"]["shop"]
        p = progress.progress(games.get("stickman"), str(kid["id"]))
        assert p["upto"] == 7 and p["worlds_done"] == 1 and p["worlds"][0]["done"] and p["cleared"] == 6

        # 存档：刷新后接着打（这一局还开着）
        c.post(f"/api/rooms/{rid}/save", json={**DEV, "session": play["session"], "el": 20, "me": {"tLeft": 99}})
        again = c.post("/api/rooms", json={**DEV, "game": "stickman"}).json()
        assert again["play"]["session"] == play["session"] and again["play"]["resume"]["me"] == {"tLeft": 99}

        # 别的游戏没有关卡
        room = c.post("/api/rooms", json={**DEV, "game": "race"}).json()
        assert room["progress"] is None
        play = c.post(f"/api/rooms/{room['id']}/start", json=DEV).json()["play"]
        assert play["n"] == 0 and play["stage"] == {}
        assert _finish(c, room["id"], play, "win")["stars"] == 0
        assert arena.GAMES["stickman"].name == "火柴人大战"
