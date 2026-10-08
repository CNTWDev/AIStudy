"""火柴人闯关：关卡越往后越难、过关才开下一关、星星、打通世界解锁装备、贴纸。"""
from fastapi.testclient import TestClient

from app import arena, db
from app.arena import levels
from app.main import app


def _right(it):
    a = it["answer"]
    return str(a[0] if isinstance(a, list) else a)


def test_stage_params_get_harder():
    ps = [levels.stage("stickman", n)["params"] for n in range(1, 31)]
    for key in ("ai_dmg", "ai_speed", "ai_aggr", "ai_dodge", "ai_guard"):
        assert all(a[key] <= b[key] for a, b in zip(ps, ps[1:])), key
    normal = [p["ai_hp"] for i, p in enumerate(ps) if (i + 1) % 6]
    assert normal == sorted(normal) and normal[0] < normal[-1]
    bosses = [n for n in range(1, 31) if levels.stage("stickman", n)["boss"]]
    assert bosses == [6, 12, 18, 24, 30] and all(ps[n - 1]["boss"]["pips"] >= 3 for n in bosses)
    assert ps[0]["regen"] and ps[1]["regen"] and not ps[2]["regen"]          # 前两关能量会慢慢回，之后只靠答题
    assert ps[2]["start_energy"] < ps[0]["start_energy"]
    assert levels.stage("stickman", 7, 1)["params"]["shop"] == ["stick", "shield"]
    assert levels.stage("stickman", 30, 5)["params"]["super"]
    assert levels.stars_of("win", 95, 60) == 3 and levels.stars_of("win", 80, 60) == 2 and levels.stars_of("win", None, 10) == 1
    assert levels.stars_of("lose", 120, 100) == 0


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
        assert "第 1 关" in c.get("/arena").text and "开始！" in c.get("/arena/stickman").text

        # 还没打过第 1 关，不能直接打第 3 关；不选就是能打的最高一关
        r = c.post("/api/arena/start", json={"game": "stickman", "stage": 3})
        assert r.status_code == 403 and "第 1 关" in r.json()["error"]
        s = c.post("/api/arena/start", json={"game": "stickman", "src": "math"}).json()
        assert s["stage"]["n"] == 1 and s["stage"]["params"]["ai_hp"] == 70
        mid = s["match_id"]
        for _ in range(3):   # 答对 3 题：专注指数够了
            q = c.get(f"/api/arena/{mid}/q").json()["item"]
            it = db.jload(db.one("SELECT data FROM arena_matches WHERE id=?", mid)["data"], {})["pending"]["item"]
            assert c.post(f"/api/arena/{mid}/a", json={"item_id": q["id"], "answer": _right(it)}).json()["correct"]
        end = c.post(f"/api/arena/{mid}/end", json={"result": "win", "stats": {"hp_left": 30}}).json()
        sg = end["stage"]
        assert sg["n"] == 1 and sg["stars"] == 2 and sg["checks"] == {"win": True, "focus": True, "hp": False}
        assert sg["next"] == 2 and sg["first_clear"] and sg["new_best"]

        # 输了不扣星星，也不算过关
        mid = c.post("/api/arena/start", json={"game": "stickman", "stage": 2}).json()["match_id"]
        sg = c.post(f"/api/arena/{mid}/end", json={"result": "lose", "stats": {"hp_left": 0}}).json()["stage"]
        assert sg["stars"] == 0 and sg["next"] is None
        assert c.post("/api/arena/start", json={"game": "stickman", "stage": 3}).status_code == 403
        # 回去刷第 1 关：星星只记最好的一次
        mid = c.post("/api/arena/start", json={"game": "stickman", "stage": 1}).json()["match_id"]
        sg = c.post(f"/api/arena/{mid}/end", json={"result": "win", "stats": {"hp_left": 80}}).json()["stage"]
        assert sg["stars"] == 2 and sg["best"] == 2 and not sg["new_best"] and not sg["first_clear"]   # 没答题：没有专注星

        # 打通第 1 个世界（第 6 关大怪兽）：解锁护盾，得贴纸，商店里能买护盾了
        for n in range(2, 6):
            db.run("INSERT INTO arena_levels(user_id, game, level, stars, plays, wins, cleared_at, updated_at) VALUES(?,?,?,?,?,?,?,?) "
                   "ON CONFLICT(user_id, game, level) DO UPDATE SET cleared_at=excluded.cleared_at",
                   kid["id"], "stickman", n, 1, 1, 1, db.now(), db.now())
        s = c.post("/api/arena/start", json={"game": "stickman"}).json()
        assert s["stage"]["n"] == 6 and s["stage"]["boss"] and s["stage"]["params"]["boss"]
        assert "shield" not in s["stage"]["params"]["shop"]
        end = c.post(f"/api/arena/{s['match_id']}/end", json={"result": "win", "stats": {"hp_left": 60}}).json()
        assert end["stage"]["unlocked"]["id"] == "shield" and end["stage"]["next"] == 7
        assert "sm_w1" in {x["key"] for x in end["stickers"]}
        s = c.post("/api/arena/start", json={"game": "stickman"}).json()
        assert s["stage"]["n"] == 7 and "shield" in s["stage"]["params"]["shop"]
        p = levels.progress(kid["id"], "stickman")
        assert p["upto"] == 7 and p["worlds_done"] == 1 and p["worlds"][0]["done"] and p["cleared"] == 6

        # 别的游戏没有关卡
        r = c.post("/api/arena/start", json={"game": "race"}).json()
        assert r["stage"] is None
        assert arena.GAMES["stickman"]["name"] == "火柴人大战"
