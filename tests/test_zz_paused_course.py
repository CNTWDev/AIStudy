"""退掉的课：错题、术语卡、复查都暂停（engine.Paused），记录留着；选回来接着复习。"""
from fastapi.testclient import TestClient

from app import db, engine, explore, insights
from app.arena import sources
from app.catalog import catalog
from app.main import app


def _login(c, email):
    c.get("/logout")
    c.post("/login", data={"email": email, "password": "secret1"})


def test_dropped_course_pauses_cards_and_resumes():
    with TestClient(app) as c:
        if "创建网站管理员账号" in c.get("/login").text:
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        else:
            _login(c, "admin@x.com")
        c.post("/admin/users/create", data={"email": "pausep@x.com", "password": "secret1", "name": "停爸", "role": "parent"})
        _login(c, "pausep@x.com")
        base = {"name": "停娃", "email": "pausek@x.com", "password": "secret1", "grade": "G8", "daily_minutes": "90",
                "subj_math": "math-shanghai", "subj_english": "eng-cambridge"}
        c.post("/parent/kids/save", data={**base, "subj_physics": "phy-cambridge"})
        kid = dict(db.one("SELECT * FROM users WHERE email='pausek@x.com'"))
        phy = {k for k in catalog.packs["phy-cambridge"].kp_ids if not catalog.equivalents(k)}   # 只有物理里有的
        for k in sorted(phy)[:4]:
            its = engine.items_for(kid["id"], k, n=1, purpose="practice", grade="G8")
            for _ in range(3):
                engine.record_attempt(kid["id"], its[0] if its else None, k, "practice", False, answer="x", ms=8000)
        db.run("UPDATE cards SET due=? WHERE user_id=?", db.today().isoformat(), kid["id"])
        is_phy = lambda r: (r["kp_id"] or "") in phy  # noqa: E731
        assert any(is_phy(r) for r in engine.due_cards(kid["id"], 300, "mistakes"))
        assert any(is_phy(r) for r in engine.due_cards(kid["id"], 300, "words"))   # IGCSE 物理术语卡

        c.post("/parent/kids/save", data={**base, "id": str(kid["id"])})   # 家长把物理去掉
        assert not [r for r in engine.due_cards(kid["id"], 300) if is_phy(r)]
        plan = db.jload(db.one("SELECT plan FROM days WHERE user_id=? AND day=?", kid["id"], db.today().isoformat())["plan"], [])
        assert not [t for t in plan if t["type"] == "mistakes"]
        assert not [t for t in plan if (t.get("kp") or "") in phy]
        assert not [f for f in insights.detect(kid["id"]) if (f.get("kp_id") or "") in phy]
        assert not [x for x in explore.candidates(kid["id"]) if x["kp"] in phy]
        ctx = sources.Ctx(kid)
        assert not [w for w in sources.WordSource()._my_cards(ctx) if is_phy(w)]   # 游戏、快练里的单词题
        # 记录还在：错题本里收在「已经不学的课」里
        _login(c, "pausek@x.com")
        page = c.get("/words?kind=mistake").text
        assert "已经不学的课" in page and "暂停" in page
        assert db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='mistake'", kid["id"])["n"] >= 1

        _login(c, "pausep@x.com")
        c.post("/parent/kids/save", data={**base, "id": str(kid["id"]), "subj_physics": "phy-cambridge"})   # 选回来
        assert any(is_phy(r) for r in engine.due_cards(kid["id"], 300, "mistakes"))


def test_same_concept_in_another_textbook_stays():
    """换了教材：同一个概念在新教材里也有，旧教材的卡片照常复习。"""
    with TestClient(app):
        rows = [{"pack_id": "phy-cambridge", "active": 0}, {"pack_id": "phy-shanghai", "active": 1}]
        shared = next(k for k in catalog.packs["phy-cambridge"].kp_ids
                      if any(catalog.kp_pack[e["id"]] == "phy-shanghai" for e in catalog.equivalents(k)))
        uid = db.insert("INSERT INTO users(email,name,role,grade,pw_hash,created_at) VALUES(?,?,?,?,?,?)",
                        "pausesw@x.com", "换", "kid", "G8", "x", db.now())
        for r in rows:
            db.run("INSERT INTO enrollments(user_id,pack_id,stage,active) VALUES(?,?,?,?)", uid, r["pack_id"],
                   catalog.default_stage(r["pack_id"], "G8"), r["active"])
        p = engine.Paused(uid)
        assert shared not in p.kps
        assert p.kps and p.kps <= set(catalog.packs["phy-cambridge"].kp_ids)
