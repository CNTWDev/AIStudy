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


def _ceo_learner(c):
    email = f"{uuid.uuid4().hex[:8]}@ceo.example"
    uid = auth.create_user(email, "secret123", "老板", role="kid", grade="ADULT")
    c.post("/login", data={"email": email, "password": "secret123"})
    return uid if isinstance(uid, int) else db.one("SELECT id FROM users WHERE email=?", email)["id"]


def _short_item():
    items = json.loads((config.SEED_DIR / "items_ceo-core.json").read_text(encoding="utf-8"))
    it = next(x for x in items if x["type"] == "short")
    return it["id"], it["kp"][0]


def test_open_answer_ai_review_and_followups():
    """开放题：AI 点评 → 追问两次 → 最后一轮收尾，按结论记掌握；不再让学员自评。"""
    with TestClient(app) as c:
        uid = _ceo_learner(c)
        iid, kp = _short_item()
        row = engine._item_row_to_dict(db.one("SELECT * FROM items WHERE id=?", iid))
        from app.web import _public_item
        assert _public_item(row)["coach"] is True
        base = {"item_id": iid, "kp_id": kp, "request_id": uuid.uuid4().hex}
        assert c.post("/api/answer", json={**base, "answer": "嗯"}).status_code == 400  # 没写想法不点评
        r1 = c.post("/api/answer", json={**base, "answer": "先砍成本，保住现金"}).json()
        assert r1["coach"] and not r1["done"] and r1["followup"] and r1["verdict"] == "partial"
        assert "reveal" not in r1 and "correct" not in r1 and len(r1["checks"]) == len(row["points"])
        r2 = c.post("/api/answer", json={**base, "answer": "最好的人会先走", "review_id": r1["review_id"]}).json()
        assert r2["round"] == 2 and not r2["done"] and r2["followup"]
        r3 = c.post("/api/answer", json={**base, "answer": "所以要先和关键的人谈", "review_id": r1["review_id"]}).json()
        assert r3["round"] == 3 and r3["done"] and not r3["followup"] and r3["correct"] is False and r3["answer"] == row["model"]
        a = db.one("SELECT answer, correct FROM attempts WHERE user_id=? AND item_id=? ORDER BY id DESC", uid, iid)
        assert a["correct"] == 0 and "最好的人会先走" in a["answer"]
        # 同一次作答重发，拿到的是同一份结果；结束了的点评不能再接着答
        assert c.post("/api/answer", json={**base, "answer": "所以要先和关键的人谈", "review_id": r1["review_id"]}).json() == r3
        r4 = c.post("/api/answer", json={**base, "request_id": uuid.uuid4().hex, "answer": "再答一次", "review_id": r1["review_id"]})
        assert r4.status_code == 409


def test_open_answer_good_on_first_round(monkeypatch):
    from app import coach, evidence
    with TestClient(app) as c:
        uid = _ceo_learner(c)
        iid, kp = _short_item()
        monkeypatch.setattr(coach.llm, "review_open", lambda *a, **k: {
            "verdict": "good", "points": [{"hit": True, "note": "说到了"}] * 4, "feedback": "想得很完整", "followup": "还要问吗"})
        seen = []
        real = evidence.record
        monkeypatch.setattr(evidence, "record", lambda *a, **k: seen.append(k) or real(*a, **k))
        r = c.post("/api/answer", json={"item_id": iid, "kp_id": kp, "answer": "完整的想法写在这里"}).json()
        assert r["done"] and r["correct"] is True and not r["followup"] and r["status_label"]
        assert seen[-1]["weight"] == 1.0 and seen[-1]["item"]["judge"] == "ai"  # AI 点评过的，不按自评的轻分量算
        assert db.one("SELECT correct FROM attempts WHERE user_id=? AND item_id=?", uid, iid)["correct"] == 1


def test_open_answer_falls_back_to_self_rating(monkeypatch):
    from app import coach
    with TestClient(app) as c:
        _ceo_learner(c)
        iid, kp = _short_item()

        def boom(*a, **k):
            raise coach.llm.LLMError("down")
        monkeypatch.setattr(coach.llm, "review_open", boom)
        r = c.post("/api/answer", json={"item_id": iid, "kp_id": kp, "answer": "我的想法是这样"}).json()
        assert r["reveal"] and r["answer"]  # AI 出错：退回对照参考答案自评
        assert c.post("/api/answer", json={"item_id": iid, "kp_id": kp, "answer": "我的想法是这样", "self": "ok"}).json()["correct"]
