"""CEO 专项课（自编教材）：课程包、作者版讲义导入和学习页（docs/DESIGN.md §4.6）。"""
import json
import uuid

from fastapi.testclient import TestClient

from app import auth, bank, config, db, engine, itemtypes
from app.catalog import catalog
from app.main import app
from app.web import course_choices


def test_ceo_pack_and_seed_files():
    with TestClient(app):
        pack = catalog.packs["ceo-core"]
    assert pack.kind == "course" and pack.stages == ["CEO-1"] and "BeeJoy" in pack.copyright
    kps = [catalog.kps[k] for k in pack.kp_ids]
    assert kps[0]["id"] == "CEO-E-01"  # 先上开篇，原理库在后面
    assert {k["evidence"] for k in kps} <= {"研究共识", "经典案例", "经验之谈"}
    assert all(k.get("think") for k in kps if k["strand"] == "C")
    lessons = json.loads((config.SEED_DIR / "teach_ceo-core.json").read_text(encoding="utf-8"))["lessons"]
    assert {"CEO-E-01", "CEO-C-R-01", "CEO-C-Q-01"} <= {x["kp"] for x in lessons}
    assert all(x["kp"] in catalog.kps and x["steps"] and x["remember"] for x in lessons)
    items = json.loads((config.SEED_DIR / "items_ceo-core.json").read_text(encoding="utf-8"))
    for it in items:
        assert it["kp"][0] in catalog.kps
        if it["type"] == "mcq":
            assert len(it["options"]) == 4 and itemtypes.TYPES["mcq"].check(it, it["answer"])
        else:
            assert it["type"] == "short" and it["model"] and it["points"]
    assert len({it["answer"] for it in items if it["type"] == "mcq"}) == 4  # 正确选项不总在同一个位置


def test_author_lessons_replace_ai_lessons():
    with TestClient(app):
        kp = "CEO-M1-01"
        ai = bank.save_content("teach", {"steps": [{"title": "AI", "text": "AI 写的"}]}, kp, bank.kp_lang(kp))
        n = engine.load_seed_lessons(config.SEED_DIR)
        assert n == engine.load_seed_lessons(config.SEED_DIR)  # 幂等
        rows = db.q("SELECT id, origin FROM contents WHERE kind='teach' AND kp_id=? AND status='active'", kp)
        assert len(rows) == 1 and rows[0]["origin"] == "author" and rows[0]["id"] != ai
        # 管理员下架的作者讲义，重启后不自动重新上架
        db.run("UPDATE contents SET status='retired' WHERE id=?", rows[0]["id"])
        engine.load_seed_lessons(config.SEED_DIR)
        assert not db.one("SELECT id FROM contents WHERE kind='teach' AND kp_id=? AND status='active'", kp)


def test_ceo_learner_sees_case_lesson():
    with TestClient(app) as c:
        email = f"{uuid.uuid4().hex[:8]}@ceo.example"
        auth.create_user(email, "secret123", "老板", role="kid", grade="ADULT")
        c.post("/login", data={"email": email, "password": "secret123"})
        assert list(course_choices("", "ADULT"))[0] in ("CEO 专项课", "基金法律法规")  # 成人课排前面
        r = c.post("/me/courses", data={"name": "老板", "grade": "ADULT", "subj_ceo": "ceo-core"})
        assert r.status_code == 200
        page = c.get("/learn/CEO-C-R-01").text
        assert "难题" in page and 'id="thinktxt"' in page and "看看不同的想法" in page and "BeeJoy" in page
        assert "经验之谈" in page
        t = c.get("/api/teach/CEO-C-R-01").json()
        assert t["steps"][0]["title"].startswith("碰撞") and t["action"]
        assert "研究共识" in c.get("/learn/CEO-M1-02").text
