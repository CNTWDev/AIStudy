"""只能在这里学的课（自编课、考证）：先上课再做题，不摸底；没上过的课不出题；「考我一下」跳过（docs/没学就先考-讨论稿.md v2）。"""
import uuid

from fastapi.testclient import TestClient

from app import auth, db, engine, mix, plan
from app.arena import sources
from app.main import app


def _learner(c):
    email = f"{uuid.uuid4().hex[:8]}@self.example"
    auth.create_user(email, "secret123", "学员", role="kid", grade="ADULT")
    c.post("/login", data={"email": email, "password": "secret123"})
    uid = db.one("SELECT id FROM users WHERE email=?", email)["id"]
    db.run("INSERT INTO enrollments(user_id,pack_id,stage,active,track) VALUES(?,?,?,1,?)", uid, "psy-core", "PSY-1", "")
    return uid


def test_first_day_is_a_lesson_not_a_test():
    with TestClient(app) as c:
        uid = _learner(c)
        tasks = plan.build_plan(uid)
        assert not any(t["type"] == "diagnose" and t.get("pack") == "psy-core" for t in tasks)   # 不摸底
        assert not any(t["type"] == "mix" and t.get("pack") == "psy-core" for t in tasks)        # 一课没上，没有能练的
        lesson = [t for t in tasks if t["type"] == "lesson" and t.get("pack") == "psy-core"]
        assert lesson and lesson[0]["kp"] == "PSY-O-01" and lesson[0]["url"] == "/learn/PSY-O-01?task=lesson" and lesson[0]["title"].startswith("新的一课")
        assert "PSY-O-03" in engine.unlearned(uid)


def test_only_learned_lessons_are_practised():
    with TestClient(app) as c:
        uid = _learner(c)
        # 以前摸底蒙对过一道没上过的课：不能拿来「换个问法」，快练也不出
        engine.set_mastery(uid, "PSY-O-03", 0.8, "learning", "diagnose")
        assert c.post("/api/learn/done", json={"kp_id": "PSY-O-01", "summary": "先看证据"}).json()["ok"]
        slots = mix.build(uid, "psy-core", 10)
        assert slots and {s["kp"] for s in slots} == {"PSY-O-01"}
        assert {s["role"] for s in slots} >= {"new"} and "preview" not in {s["role"] for s in slots}
        assert any(t["type"] == "mix" and t.get("pack") == "psy-core" for t in plan.build_plan(uid))
        kid = dict(db.one("SELECT * FROM users WHERE id=?", uid))
        kps = sources.BankSource()._kps(sources.Ctx(kid))
        assert "PSY-O-03" not in {k for v in kps.values() for k in v}
        nxt = [t["kp"] for t in plan.build_plan(uid) if t["type"] == "lesson"]
        assert "PSY-O-01" not in nxt and nxt   # 下一课接着上


def test_test_out_skips_a_lesson():
    with TestClient(app) as c:
        uid = _learner(c)
        page = c.get("/learn/PSY-O-02").text
        assert "考我一下" in page
        items = c.get("/api/testout/PSY-O-02").json()["items"]
        assert len(items) == 2
        assert not c.post("/api/testout/done", json={"kp_id": "PSY-O-02", "item_ids": [i["id"] for i in items]}).json()["ok"]
        for it in items:
            ans = db.jload(db.one("SELECT data FROM items WHERE id=?", it["id"])["data"])["answer"]
            assert c.post("/api/answer", json={"item_id": it["id"], "kp_id": "PSY-O-02", "answer": ans}).json()["correct"]
        assert c.post("/api/testout/done", json={"kp_id": "PSY-O-02", "item_ids": [i["id"] for i in items]}).json()["ok"]
        assert "PSY-O-02" not in engine.unlearned(uid)
        assert "考我一下" not in c.get("/learn/PSY-O-02").text
        assert c.get("/api/testout/PSY-O-02").status_code == 409


def test_school_courses_unchanged():
    with TestClient(app) as c:
        email = f"{uuid.uuid4().hex[:8]}@kid.example"
        auth.create_user(email, "secret123", "孩子", role="kid", grade="G8")
        uid = db.one("SELECT id FROM users WHERE email=?", email)["id"]
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active,track) VALUES(?,?,?,1,?)", uid, "math-shanghai", "G8", "")
        assert engine.unlearned(uid) == set()
        assert any(t["type"] == "diagnose" for t in plan.build_plan(uid))


def test_lessons_learned_before_the_record_are_kept():
    with TestClient(app) as c:
        uid = _learner(c)
        engine.add_card(uid, "kp", "后见之明", "先写下预测", {}, "PSY-O-01")
        day = db.today().isoformat()
        db.run("INSERT INTO days(user_id,day,plan) VALUES(?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET plan=excluded.plan",
               uid, day, db.jdump([{"type": "check", "kp": "PSY-O-02", "done": True}, {"type": "weak", "kp": "PSY-O-04", "done": False}]))
        assert engine.backfill_lessons() >= 2
        hold = engine.unlearned(uid)
        assert "PSY-O-01" not in hold and "PSY-O-02" not in hold and "PSY-O-04" in hold
        before = db.one("SELECT COUNT(*) AS n FROM lessons WHERE user_id=?", uid)["n"]
        engine.backfill_lessons()   # 这门课已经有记录了：不再补
        assert db.one("SELECT COUNT(*) AS n FROM lessons WHERE user_id=?", uid)["n"] == before
