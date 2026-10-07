"""游戏乐园：快题答案正确、按水平出题确实公平、一局完整走通。"""
import random
import statistics

from fastapi.testclient import TestClient

from app import arena, db, itemtypes, quickgen
from app.catalog import catalog
from app.main import app


def _right_answer(it):
    a = it["answer"]
    return a[0] if isinstance(a, list) else a


def test_quickgen_answers_are_correct():
    """每个题族、每个级别：程序算出的答案能通过判分，错一点的答案不能通过。"""
    with TestClient(app):  # 加载教材
        rng = random.Random(7)
        for fam, (kp_id, _, _) in quickgen.FAMILIES.items():
            assert catalog.kp(kp_id), f"{fam} 挂的知识点 {kp_id} 不存在"
            for lv in quickgen.LEVELS:
                for _ in range(60):
                    it = quickgen.generate(fam, lv, rng)
                    assert it["q"] and it["id"].startswith(f"gen-{fam}-")
                    assert itemtypes.check(it, str(_right_answer(it))), (fam, lv, it)
                    if it["type"] == "num":
                        assert not itemtypes.check(it, str(float(it["answer"]) + 1)), (fam, lv, it)
                    if fam != "int_add" and it["type"] == "num":
                        assert float(it["answer"]) >= 0, (fam, lv, it)


def test_koujue():
    assert [quickgen.koujue(*x) for x in ((3, 2), (9, 8), (4, 5), (1, 1), (3, 3), (2, 5), (3, 4))] == ["二三得六", "八九七十二", "四五二十", "一一得一", "三三得九", "二五一十", "三四十二"]


def test_fraction_answers_accept_equivalent_forms():
    it = quickgen.generate("frac_same", 3, random.Random(1))
    for form in it["answer"]:
        assert itemtypes.check(it, form)


def test_adaptive_difficulty_gives_everyone_the_same_chance():
    """模拟三个真实水平差很多的孩子（都在一个题族 1–5 级覆盖的范围内）：按水平出题后，大家的答对率都接近目标（75%），
    经验值期望相同。超出范围的孩子（这个知识点最简单的一级也太难）由 arena.kp_pool 少出这个知识点、换别的知识点。"""
    target = arena.GAMES["stickman"]["target"]
    rng = random.Random(42)
    accs = []
    for true in (-0.8, 0.4, 2.0):           # 弱、中、强
        theta, n, right = 0.0, 0, []
        levels = {lv: (arena.prior_b(lv), 0) for lv in quickgen.LEVELS}
        for _ in range(300):
            lv = arena.pick_level(theta, levels, target, rng)
            b = levels[lv][0]
            ok = rng.random() < arena.sigmoid(true - b)
            theta, _ = arena.elo_step(theta, n, b, 1000, ok)   # 难度已校准好，只更新孩子
            n += 1
            right.append(ok)
        accs.append(sum(right[100:]) / len(right[100:]))
    for a in accs:
        assert abs(a - target) < 0.08, accs
    assert max(accs) - min(accs) < 0.1, accs


def test_xp_and_freeze_rules():
    rng = random.Random(3)
    xs = [arena.roll_xp(rng, 0)[0] for _ in range(4000)]
    assert 85 <= statistics.mean(xs) <= 115
    assert min(xs) >= 70 and max(xs) <= 300
    assert arena.roll_xp(random.Random(1), arena.DAILY_SOFT_CAP)[0] <= 150
    assert [arena.freeze_seconds(k, True) for k in (1, 2, 3, 4)] == [5, 8, 12, 12]
    assert arena.freeze_seconds(0, False) == 5


def _pending(mid):
    return db.jload(db.one("SELECT data FROM arena_matches WHERE id=?", mid)["data"], {})["pending"]["item"]


def test_match_flow():
    with TestClient(app) as c:
        if c.get("/login").text.count("创建网站管理员账号"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        else:
            c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        c.post("/admin/users/create", data={"email": "gp@x.com", "password": "secret1", "name": "游戏爸爸", "role": "parent"})
        c.get("/logout")
        c.post("/login", data={"email": "gp@x.com", "password": "secret1"})
        c.post("/parent/kids/save", data={"name": "小火柴", "email": "gk@x.com", "password": "secret1", "grade": "G3",
                                          "daily_minutes": "60", "subj_math": "math-shanghai", "game_minutes": "20"})
        kid = db.one("SELECT * FROM users WHERE email='gk@x.com'")
        assert arena.game_minutes(dict(kid)) == 20
        assert c.get("/arena").status_code in (400, 403)  # 家长不能替孩子玩
        c.get("/logout")

        c.post("/login", data={"email": "gk@x.com", "password": "secret1"})
        assert "乐园" in c.get("/today").text
        assert "火柴人大战" in c.get("/arena").text
        assert c.get("/arena/stickman").status_code == 200
        mid = c.post("/api/arena/start", json={"game": "stickman"}).json()["match_id"]

        q = c.get(f"/api/arena/{mid}/q").json()["item"]
        assert "answer" not in q and q["q"]
        it = _pending(mid)
        # 只出不超过当前学段（三年级）的知识点
        assert catalog.kps[it["kp_id"]]["stage"] in ("G1", "G2", "G3")
        # 关掉再打开还是同一道题，不能换掉难题
        assert c.get(f"/api/arena/{mid}/q").json()["item"]["id"] == q["id"]

        # 秒答答错：冷冻，冷冻中不能拿新题
        r = c.post(f"/api/arena/{mid}/a", json={"item_id": q["id"], "answer": "-99999"}).json()
        assert r["correct"] is False and r["freeze_ms"] == 5000 and r["xp"] == 0
        r2 = c.get(f"/api/arena/{mid}/q")
        assert r2.status_code == 423 and r2.json()["freeze_ms"] > 0
        st = db.jload(db.one("SELECT data FROM arena_matches WHERE id=?", mid)["data"], {})
        st["freeze_until"] = 0
        db.run("UPDATE arena_matches SET data=? WHERE id=?", db.jdump(st), mid)

        # 答对：得经验值，记进账本和学习记录
        q = c.get(f"/api/arena/{mid}/q").json()["item"]
        it = _pending(mid)
        r = c.post(f"/api/arena/{mid}/a", json={"item_id": q["id"], "answer": str(_right_answer(it))}).json()
        assert r["correct"] and r["xp"] >= 70 and r["xp_total"] == r["xp"]
        assert db.one("SELECT COUNT(*) AS n FROM attempts WHERE user_id=? AND mode='game'", kid["id"])["n"] == 2
        assert db.one("SELECT COUNT(*) AS n FROM arena_ability WHERE user_id=?", kid["id"])["n"] >= 1
        # 旧题不能再交
        assert c.post(f"/api/arena/{mid}/a", json={"item_id": q["id"], "answer": "1"}).status_code == 409

        end = c.post(f"/api/arena/{mid}/end", json={"result": "win", "stats": {"zero_energy_s": 25}}).json()
        assert end["answered"] == 2 and end["right"] == 1 and end["accuracy"] == 50
        assert any("能量是 0" in t for t in end["tips"])
        assert c.post(f"/api/arena/{mid}/end", json={"result": "win"}).status_code == 409
        c.get("/logout")

        # 家长能看到游戏情况、把游戏关掉
        c.post("/login", data={"email": "gp@x.com", "password": "secret1"})
        assert "乐园本周" in c.get("/parent").text
        c.post("/parent/kids/save", data={"id": kid["id"], "name": "小火柴", "email": "gk@x.com", "grade": "G3",
                                          "daily_minutes": "60", "subj_math": "math-shanghai", "game_minutes": "0"})
        c.get("/logout")
        c.post("/login", data={"email": "gk@x.com", "password": "secret1"})
        r = c.post("/api/arena/start", json={"game": "stickman"})
        assert r.status_code == 403 and "家长" in r.json()["error"]
        c.get("/logout")

        # 管理后台的公平看板
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        page = c.get("/admin?tab=arena").text
        assert "游戏公平看板" in page and "实际答对率" in page
