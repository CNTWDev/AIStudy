"""坚持和冲刺：小节怎么切、保底、补签卡、冲刺的倍数和冲刺分。"""
from datetime import timedelta

from fastapi.testclient import TestClient

from app import db, engine, sprint, streak
from app.main import app


def _plan(*mins, done=0):
    return [{"id": f"t{i}", "title": f"任务{i}", "minutes": m, "done": i < done} for i, m in enumerate(mins)]


def test_sections_and_base():
    secs = streak.sections(_plan(2, 4, 3, 15, 15, 5))
    assert [s["tasks"] for s in secs] == [[0, 1, 2], [3], [4, 5]]   # 一节最多 3 项；最后剩 5 分钟并进上一节
    assert [s["minutes"] for s in secs] == [9, 15, 20]
    assert streak.sections(_plan(15, 15, 15))[0]["tasks"] == [0]
    assert not streak.base_done(_plan(2, 4, 3, 15, done=2), 30)
    assert streak.base_done(_plan(2, 4, 3, 15, done=3), 0)            # 做完第 1 节就是保底
    assert streak.base_done([], 10) and not streak.base_done([], 9)  # 没有任务：学满 10 分钟


def _kid(email):
    with TestClient(app) as c:
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        c.post("/admin/users/create", data={"email": f"p-{email}", "password": "secret1", "name": "家长", "role": "parent"})
        c.get("/logout")
        c.post("/login", data={"email": f"p-{email}", "password": "secret1"})
        c.post("/parent/kids/save", data={"name": "小冲", "email": email, "password": "secret1", "grade": "G3",
                                          "daily_minutes": "60", "subj_math": "math-shanghai", "game_minutes": "20",
                                          "game_unlock": "free"})
    return dict(db.one("SELECT * FROM users WHERE email=?", email))


def _day(kid_id, d, plan, minutes=20):
    db.run("INSERT INTO days(user_id, day, plan, minutes) VALUES(?,?,?,?) ON CONFLICT(user_id, day) DO UPDATE SET plan=excluded.plan, minutes=excluded.minutes",
           kid_id, d.isoformat(), db.jdump(plan), minutes)


def test_streak_freeze_cards(monkeypatch):
    monkeypatch.setattr(streak, "RULE_FROM", "2000-01-01")   # 测试里所有日子都按保底算
    kid = _kid("fz@x.com")
    today = db.today()
    ok, half = _plan(5, 5, 5, done=3), _plan(5, 5, 5, done=1)
    for i in range(1, 8):                               # 前 7 天天天做完保底
        _day(kid["id"], today - timedelta(days=i), ok)
    r = streak.settle(kid["id"])                        # 满 7 天：得一张补签卡
    assert engine.streak(kid["id"]) == 7 and r == {"used": [], "earned": 1} and streak.cards(kid["id"]) == 1
    assert streak.settle(kid["id"])["earned"] == 0      # 不重复发
    # 只打卡、开着页面不算：昨天只做了 1 项（没做完第 1 节）
    _day(kid["id"], today - timedelta(days=1), half, minutes=40)
    assert engine.streak(kid["id"]) == 0
    r = streak.settle(kid["id"])                        # 今天打开时自动用卡补上，补的那天不加天数
    assert r["used"] == [(today - timedelta(days=1)).isoformat()]
    assert engine.streak(kid["id"]) == 6 and streak.cards(kid["id"]) == 0
    assert streak.settle(kid["id"])["used"] == []       # 不重复用
    assert any(d["frozen"] for d in engine.calendar(kid["id"], 1))
    _day(kid["id"], today, ok)                          # 今天做完保底：接着往上涨，又满 7 天再发一张
    assert engine.streak(kid["id"]) == 7
    assert streak.settle(kid["id"])["earned"] == 1
    # 断了两天、手里只有一张：不够补，不用卡
    db.run("DELETE FROM days WHERE user_id=?", kid["id"])
    db.run("DELETE FROM streak_freezes WHERE user_id=?", kid["id"])
    db.run("INSERT INTO streak_freezes(user_id, day, kind, created_at) VALUES(?,?,?,?)", kid["id"], "2000-01-01", "earned", db.now())
    for i in range(3, 6):
        _day(kid["id"], today - timedelta(days=i), ok)
    assert streak.settle(kid["id"])["used"] == [] and streak.cards(kid["id"]) == 1


def test_goals():
    kid = _kid("goal@x.com")
    assert streak.goal(kid) == "all"
    st = streak.today_state(kid, _plan(5, 5, 5, 15, done=4), 30, 0)
    assert st["goal_reached"] and st["sprint_open"]
    assert streak.set_goal(kid["id"], "sprint") and not streak.set_goal(kid["id"], "nope")
    kid = dict(db.one("SELECT * FROM users WHERE id=?", kid["id"]))
    st = streak.today_state(kid, _plan(5, 5, 5, 15, done=4), 30, 20)
    assert not st["goal_reached"] and st["goal_pct"] == 70
    assert streak.today_state(kid, _plan(5, 5, 5, 15, done=4), 30, 50)["goal_reached"]


def _pending(run_id):
    return db.jload(db.one("SELECT data FROM sprint_runs WHERE id=?", run_id)["data"], {})["pending"]


def _right(it):
    a = it["answer"]
    return str(a[0] if isinstance(a, list) else a)


def test_sprint_multiplier_and_points():
    kid = _kid("sp@x.com")
    with TestClient(app) as c:
        c.post("/login", data={"email": "sp@x.com", "password": "secret1"})
        assert "冲刺" in c.get("/today").text
        r = c.post("/api/sprint/start")
        assert r.status_code == 403 and "任务" in r.json()["error"]     # 先做完今天的任务
        assert "先做完今天的任务" in c.get("/sprint").text
        for t in engine.today_plan(kid["id"])["plan"]:
            engine.mark_task(kid["id"], t["id"])
        assert "开始冲刺" in c.get("/sprint").text
        rid = c.post("/api/sprint/start").json()["run_id"]

        def ask():
            q = c.get(f"/api/sprint/{rid}/q").json()["item"]
            assert "answer" not in q
            return q, _pending(rid)["item"]

        def wait(sec):   # 假装这道题想了 sec 秒
            st = db.jload(db.one("SELECT data FROM sprint_runs WHERE id=?", rid)["data"], {})
            st["pending"]["issued"] -= sec
            db.run("UPDATE sprint_runs SET data=? WHERE id=?", db.jdump(st), rid)

        tiers, gains = [], []
        for _ in range(13):
            q, it = ask()
            wait(5)
            res = c.post(f"/api/sprint/{rid}/a", json={"item_id": q["id"], "answer": _right(it)}).json()
            assert res["correct"] and res["freeze_ms"] == 0
            tiers.append(res["tier"]); gains.append(res["gain"])
        # 连对 5 题升到 ×2，连对 12 题升到 ×3；每题得「答题时的倍数」分
        assert tiers == [1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 3, 3]
        assert gains == [1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 3] and res["points"] == 22
        # 想了一会儿答错：只降一档
        q, it = ask(); wait(6)
        res = c.post(f"/api/sprint/{rid}/a", json={"item_id": q["id"], "answer": "-987654"}).json()
        assert not res["correct"] and res["tier"] == 2 and res["tier_down"] and res["why"] == "wrong"
        # 「这题不会」：不降档、不加分
        q, it = ask()
        res = c.post(f"/api/sprint/{rid}/a", json={"item_id": q["id"], "dont_know": True}).json()
        assert res["dont_know"] and res["tier"] == 2 and res["gain"] == 0
        # 3 秒内答错（像在猜）：回到 ×1
        q, it = ask()
        res = c.post(f"/api/sprint/{rid}/a", json={"item_id": q["id"], "answer": "-987654"}).json()
        assert res["tier"] == 1 and res["why"] == "guess"
        # 冲刺分换 ⭐、乐园多玩几分钟；作答记进学习记录
        assert res["points_today"] == 22 and res["stars_today"] == 2
        assert db.one("SELECT COUNT(*) AS n FROM attempts WHERE user_id=? AND mode='sprint'", kid["id"])["n"] >= 1
        from app import arena
        assert arena.status(kid)["bonus_sprint"] == 1
        stars_before = engine.total_stars(kid["id"])
        end = c.post(f"/api/sprint/{rid}/end").json()
        assert end["points"] == 22 and end["best_tier"] == 3 and end["answered"] == 16
        assert c.post(f"/api/sprint/{rid}/end").status_code == 409
        assert engine.total_stars(kid["id"]) == stars_before
        assert sprint.best_day(kid["id"]) == 22
        # 目标可以改
        assert c.post("/api/goal", json={"goal": "super"}).json()["ok"]
        assert "超神" in c.get("/today").text
