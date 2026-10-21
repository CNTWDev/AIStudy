"""散户美股实战课（自编教材）和决策日志（docs/DESIGN.md §4.8）。"""
import json
import uuid

from fastapi.testclient import TestClient

from app import auth, config, db, itemtypes, journal
from app.catalog import catalog
from app.main import app


def test_invest_pack_and_seed_files():
    with TestClient(app):
        pack = catalog.packs["us-stock"]
        assert not [e for e in catalog.errors if "us-stock" in e or "ceo-core" in e]
    assert pack.kind == "course" and pack.audience == "adult" and "BeeJoy" in pack.copyright
    assert "不要推荐" in pack.prompt_note and "不是投资咨询" in pack.prompt_note  # 不荐股写进给 AI 的说明
    kps = [catalog.kps[k] for k in pack.kp_ids]
    assert kps[0]["id"] == "INV-E-01"
    assert all(k.get("think") for k in kps if k["strand"] == "C")
    assert {m["kp"] for m in pack.thinking_moves} <= set(pack.kp_ids)
    assert set(pack.journal["traps"]) <= set(pack.kp_ids) and pack.journal["review_days"]
    lessons = json.loads((config.SEED_DIR / "teach_us-stock.json").read_text(encoding="utf-8"))["lessons"]
    assert {x["kp"] for x in lessons} == set(pack.kp_ids)  # 每一课都有作者讲义
    assert all(x["steps"] and x["remember"] and x["action"] for x in lessons)
    items = json.loads((config.SEED_DIR / "items_us-stock.json").read_text(encoding="utf-8"))
    assert {it["kp"][0] for it in items} == set(pack.kp_ids)
    for it in items:
        if it["type"] == "mcq":
            assert len(it["options"]) == 4 and itemtypes.TYPES["mcq"].check(it, it["answer"])
        else:  # 投资决定没有标准答案：开放题都是思辨题
            assert it["type"] == "discuss" and len(it["perspectives"]) >= 3 and it["twists"]
            assert set(it["moves"]) <= {m["id"] for m in pack.thinking_moves}
    assert len({it["answer"] for it in items if it["type"] == "mcq"}) >= 3


def _adult(c, courses):
    email = f"{uuid.uuid4().hex[:8]}@inv.example"
    auth.create_user(email, "secret123", "学员", role="kid", grade="ADULT")
    c.post("/login", data={"email": email, "password": "secret123"})
    assert c.post("/me/courses", data={"name": "学员", "grade": "ADULT", **courses}).status_code == 200
    return db.one("SELECT id FROM users WHERE email=?", email)["id"]


def test_learner_sees_case_lesson():
    with TestClient(app) as c:
        _adult(c, {"subj_invest": "us-stock"})
        page = c.get("/learn/INV-C-01").text
        assert "难题" in page and 'id="thinktxt"' in page and "交易决策日志" in page
        assert c.get("/api/teach/INV-B-05").json()["steps"][0]["text"].startswith("1956")


def test_journal_write_review_and_patterns():
    with TestClient(app) as c:
        uid = _adult(c, {"subj_invest": "us-stock", "subj_ceo": "ceo-core"})
        assert {p.id for p in journal.packs(uid)} == {"us-stock", "ceo-core"}  # 通用：两门课都有日志
        page = c.get("/journal").text
        assert "我的优势" in page or "关键假设" in page or "我在赌什么" in page
        r = c.post("/journal", data={"pack": "us-stock", "subject": "买入 某公司", "size": "8%", "why": "收入加速",
                                     "exit": "跌破 85 美元", "confidence": "80", "days": "30"})
        assert r.status_code == 200 and "记下了" in r.text
        assert c.post("/journal", data={"pack": "us-stock", "subject": "空的", "confidence": "50", "days": "30"}).status_code == 400
        assert not journal.due(uid)
        # 三条七八成把握的，到期了
        for i in range(2):
            journal.add(uid, "us-stock", {"subject": f"买入 第{i}家", "why": "看好", "confidence": "80", "days": "30"})
        db.run("UPDATE journal SET review_on='2000-01-01' WHERE user_id=?", uid)
        due = journal.due(uid)
        assert len(due) == 3 and "3 个决定到了回看的时候" in c.get("/today").text
        e = due[0]["id"]
        assert "先别看结果" in c.get(f"/journal/{e}").text
        r = c.post(f"/journal/{e}/review", data={"judgment": "flawed", "outcome": "good", "traps": ["INV-P-05", "NOPE"],
                                                  "review": "追高，运气好"})
        assert r.status_code == 200 and "最容易让人把错的做法重复下去" in r.text
        assert db.jload(db.one("SELECT traps FROM journal WHERE id=?", e)["traps"], []) == ["INV-P-05"]  # 只认本课的陷阱
        assert c.post(f"/journal/{e}/review", data={"judgment": "sound", "outcome": "bad"}).status_code == 409
        for d in due[1:]:
            journal.review(uid, d["id"], {"judgment": "flawed", "outcome": "bad", "traps": ["INV-P-05"]})
        p = journal.patterns(uid)
        assert p["reviewed"] == 3 and [q["n"] for q in p["quadrants"]] == [0, 0, 1, 2]
        assert any("把握常常估高了" in f for f in p["found"]) and any("追高和从众" in f for f in p["found"])
        assert "复盘过 3 个决定" in c.get("/journal").text


def test_journal_is_private():
    with TestClient(app) as c:
        uid = _adult(c, {"subj_invest": "us-stock"})
        eid = journal.add(uid, "us-stock", {"subject": "买入 某指数", "why": "定投", "confidence": "50", "days": "90"})
        other = TestClient(app)
        _adult(other, {"subj_ceo": "ceo-core"})
        assert other.get(f"/journal/{eid}").status_code == 404   # 别人的日志打不开
        assert other.post(f"/journal/{eid}/review", data={"judgment": "sound", "outcome": "good"}).status_code in (403, 404)
