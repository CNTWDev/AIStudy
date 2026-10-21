"""心理学课（自编教材）、证据牌、作者写的导入、思辨题的安全规则（docs/DESIGN.md §4.6、§4.7.1、§4.9）。"""
import json
import uuid

from fastapi.testclient import TestClient

from app import auth, config, db, engine, llm, safety
from app.catalog import EVIDENCE, RESEARCH_EVIDENCE, catalog
from app.main import app


def _seed(name):
    return json.loads((config.SEED_DIR / name).read_text(encoding="utf-8"))


def _login(c):
    email = f"{uuid.uuid4().hex[:8]}@psy.example"
    auth.create_user(email, "secret123", "学员", role="kid", grade="ADULT")
    c.post("/login", data={"email": email, "password": "secret123"})
    return db.one("SELECT id FROM users WHERE email=?", email)["id"]


def test_psy_pack_and_seed_files():
    with TestClient(app):
        pack = catalog.packs["psy-core"]
    assert pack.kind == "course" and pack.audience == "adult" and "BeeJoy" in pack.copyright
    assert [m["id"] for m in pack.thinking_moves] == [f"PSY-O-0{i}" for i in range(1, 6)]
    assert pack.discuss_action == "这周我想试的一件事" and set(pack.journal["traps"]) == {f"PSY-D-0{i}" for i in range(1, 9)}
    kps = [catalog.kps[k] for k in pack.kp_ids]
    for k in kps:
        assert k.get("evidence") in (*EVIDENCE, None)
        if k.get("evidence") in RESEARCH_EVIDENCE:
            assert k["sources"] and k.get("evidence_note") or k["id"] == "PSY-D-06"
    assert {k["evidence"] for k in kps if k["strand"] != "R"} >= {"稳", "要小心", "站不住"}  # 三档都讲到
    assert all(k.get("think") for k in kps if k["strand"] == "R")
    lessons = {x["kp"]: x for x in _seed("teach_psy-core.json")["lessons"]}
    assert set(lessons) == set(pack.kp_ids)
    for k in kps:
        x = lessons[k["id"]]
        assert x["steps"] and x["remember"] and x["action"]   # 每课都有一周小实验
        if k["strand"] != "R":
            assert x["context"]["story"] and x["context"]["question"]
    items = _seed("items_psy-core.json")
    moves = {m["id"] for m in pack.thinking_moves}
    for kid in pack.kp_ids:
        mine = [it for it in items if it["kp"] == [kid]]
        if catalog.kps[kid]["strand"] == "R":
            assert {it["type"] for it in mine} == {"discuss"}
        else:  # 第一道是「先猜一猜」的选择题，最后一道是思辨题
            assert mine[0]["type"] == "mcq" and mine[0]["difficulty"] == 1 and mine[-1]["type"] == "discuss"
    for it in items:
        if it["type"] == "discuss":
            assert len(it["perspectives"]) == 3 and set(it["moves"]) <= moves and it["twists"]
        else:
            assert len(it["options"]) == 4 and 0 <= it["answer"] < 4
    assert len({it["answer"] for it in items if it["type"] == "mcq"}) == 4


def test_shared_concepts_link_the_adult_courses():
    with TestClient(app):
        same = {k for k in catalog.kps if catalog.concept_of.get(k) == "psy.loss-aversion"}
        assert {"PSY-D-04", "CEO-M1-02"} <= same
        assert catalog.concept_of.get("PSY-D-07") == catalog.concept_of.get("CEO-M6-05") == "psy.sunk-cost"


def test_every_pack_uses_the_one_evidence_scale():
    with TestClient(app):
        for k in catalog.kps.values():
            assert k.get("evidence") in (*EVIDENCE, None), k["id"]
        assert catalog.kps["CEO-M1-02"]["evidence"] == "要小心" and catalog.kps["CEO-M7-02"]["evidence"] == "基本原理"


def test_author_context_shown_without_ai(monkeypatch):
    with TestClient(app) as c:
        engine.load_seed_lessons(config.SEED_DIR)
        assert engine.load_seed_lessons(config.SEED_DIR) == engine.load_seed_lessons(config.SEED_DIR)  # 幂等
        rows = db.q("SELECT origin FROM contents WHERE kind='context' AND kp_id='PSY-H-07' AND status='active'")
        assert [r["origin"] for r in rows] == ["author"]
        _login(c)
        c.post("/me/courses", data={"name": "学员", "grade": "ADULT", "subj_psy": "psy-core"})
        monkeypatch.setattr(llm, "enabled", lambda: False)
        ctx = c.get("/api/context/PSY-H-07").json()
        assert "意志力像电池" in ctx["story"] and ctx["question"]
        page = c.get("/learn/PSY-H-07").text
        assert "🔴 站不住" in page and "证据牌" in page and "强版本没有得到支持" in page
        assert c.get("/api/teach/PSY-H-07").json()["action"]


def test_safety_words():
    assert safety.crisis("我最近真的不想活了")
    assert safety.crisis("有时候觉得活着没意思，我想结束自己")
    assert safety.crisis("I want to die")
    assert not safety.crisis("这篇文章讨论的是自杀率和社会支持的关系")
    assert not safety.crisis("这个方案要是失败，公司就活不下去")
    assert not safety.crisis("")


def test_discussion_stops_and_points_to_help(monkeypatch):
    with TestClient(app) as c:
        uid = _login(c)
        iid, kp = "Q-PSY-R-01-1", "PSY-R-01"
        r = c.post("/api/answer", json={"item_id": iid, "kp_id": kp, "answer": "我已经不想活了，什么都坚持不下来"}).json()
        assert r["care"]["lines"] and "12356" in r["care"]["lines"][0] and not r["can_continue"] and not r["done"]
        assert not db.one("SELECT id FROM discussions WHERE user_id=?", uid)   # 这段话不存
        # AI 觉得学员有危险：同样停下
        monkeypatch.setattr(llm, "discuss_turn", lambda *a, **k: {"care": True})
        r = c.post("/api/answer", json={"item_id": iid, "kp_id": kp, "answer": "我最近每天都很绝望"}).json()
        assert r["care"] and not db.one("SELECT id FROM discussions WHERE user_id=?", uid)
        # 正常的话照常讨论；收尾那一栏按课程问
        monkeypatch.undo()
        r = c.post("/api/answer", json={"item_id": iid, "kp_id": kp, "answer": "我觉得是计划没定好时间"}).json()
        assert r["discuss"] and not r.get("care") and r["action_label"] == "这周我想试的一件事"
        end = c.post("/api/answer", json={"item_id": iid, "kp_id": kp, "answer": "", "discussion_id": r["discussion_id"],
                                          "conclusion": "先改环境", "action": "跑鞋放门口"}).json()
        assert end["done"] and end["action_label"] == "这周我想试的一件事"
        ceo = c.post("/api/answer", json={"item_id": "Q-CEO-M1-02-3", "kp_id": "CEO-M1-02", "answer": "先讲清楚为什么调"}).json()
        assert ceo["action_label"] == "回到公司，我会做的一件事"
