"""思辨题：没有标准答案的题型（app/discuss.py，docs/DESIGN.md §4.7）。"""
import json
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app import auth, db, discuss, engine, itemtypes, llm
from app.catalog import catalog
from app.main import app

KP = "CEO-C-R-01"
ITEM = {"type": "discuss", "q": "AI 能写初级程序员八成的代码，研发总监建议明年不招应届生。你会怎么判断、怎么做？",
        "perspectives": [{"view": "停招，省下的钱投到 AI", "holds_when": "现金紧、业务稳定", "risk": "三五年后中层断档"},
                         {"view": "少招，重新设计新人的成长路径", "holds_when": "还要靠人才梯队", "risk": "培养成本高"},
                         {"view": "照常招，用 AI 让新人更快上手", "holds_when": "业务在扩张", "risk": "人效没有提升"}],
        "moves": ["CEO-T-01", "CEO-T-05"], "twists": ["如果公司只有 30 人、现金只够半年呢？", "如果对手已经全面停招了呢？"]}


def _item_id():
    iid = "Q-T-DSC-" + uuid.uuid4().hex[:6]
    data = {k: v for k, v in ITEM.items() if k != "type"}
    db.run("INSERT INTO items(id,kp_id,kp_ids,type,difficulty,data,source,verified,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
           iid, KP, db.jdump([KP]), "discuss", 3, db.jdump(data), "seed", 1, db.now())
    db.run("INSERT INTO item_kps(item_id,kp_id) VALUES(?,?)", iid, KP)
    return iid


def _login(c):
    email = f"{uuid.uuid4().hex[:8]}@think.example"
    auth.create_user(email, "secret123", "老板", role="kid", grade="ADULT")
    c.post("/login", data={"email": email, "password": "secret123"})
    return db.one("SELECT id FROM users WHERE email=?", email)["id"]


def test_discuss_type_is_open_and_hides_perspectives():
    t = itemtypes.TYPES["discuss"]
    assert t.judge == "open" and t.widget == "discuss" and not t.self_rated
    it = itemtypes.normalize({**ITEM})
    assert it["type"] == "discuss" and len(it["perspectives"]) == 3
    pub = itemtypes.public(it)
    assert "perspectives" not in pub and "twists" not in pub and pub["judge"] == "open"
    assert itemtypes.normalize({"type": "discuss", "q": "只有一种看法", "perspectives": [{"view": "a"}]})["type"] == "short"
    assert "discuss" not in itemtypes.schema_text() and "discuss" in itemtypes.schema_text(discuss=True)
    with TestClient(app):
        assert [m["id"] for m in catalog.packs["ceo-core"].thinking_moves][0] == "CEO-T-01"


def test_discussion_no_verdict_then_revisit_with_twist():
    with TestClient(app) as c:
        uid = _login(c)
        iid = _item_id()
        base = {"item_id": iid, "kp_id": KP, "request_id": uuid.uuid4().hex}
        assert c.post("/api/answer", json={**base, "answer": "嗯"}).status_code == 400
        r1 = c.post("/api/answer", json={**base, "answer": "不该停招，90 分会变成行业底线"}).json()
        assert r1["discuss"] and r1["ai"] and r1["challenge"] and r1["can_continue"] and "correct" not in r1
        assert [m["id"] for m in r1["moves_now"]] == ["CEO-T-05"]
        r2 = c.post("/api/answer", json={**base, "answer": "小公司是不是不一样？", "discussion_id": r1["discussion_id"]}).json()
        assert r2["turn"] == 2 and r2["discussion_id"] == r1["discussion_id"]
        end = c.post("/api/answer", json={**base, "answer": "", "discussion_id": r1["discussion_id"],
                                          "conclusion": "先建能力再定人数", "action": "挑一个新人岗位试点"}).json()
        assert end["done"] and end["correct"] is None and end["shift"]
        assert [p["near"] for p in end["perspectives"]] == [False, True, False]
        assert {m["id"]: m["how"] for m in end["moves"]} == {"CEO-T-05": "self", "CEO-T-01": "prompted"}
        assert end["next"]["twist"] == ITEM["twists"][0]
        assert end["next"]["due"] == (db.today() + timedelta(days=3)).isoformat()
        # 不判对错：不写作答记录、不进错题本
        assert not db.one("SELECT id FROM attempts WHERE user_id=? AND item_id=?", uid, iid)
        assert not db.one("SELECT id FROM cards WHERE user_id=? AND kind='mistake'", uid)
        # 想过的题不再当新题出
        assert iid not in [i["id"] for i in engine.items_for(uid, KP, n=5, ai=False, fallback=False)]
        # 到期：换一个条件再问
        assert discuss.due(uid) == []
        db.run("UPDATE discussions SET next_due=? WHERE id=?", db.today().isoformat(), end["discussion_id"])
        d = discuss.due(uid)[0]
        assert d["twist"] == ITEM["twists"][0]
        assert "这次换个条件" in c.get("/thinking").text
        assert json.dumps(ITEM["twists"][0]) in c.get(f"/thinking/{d['id']}").text  # 题卡上写着这次的条件
        a = c.post("/api/answer", json={"item_id": iid, "kp_id": KP, "answer": "现金只够半年就先停招", "rethink": d["id"]}).json()
        assert db.one("SELECT twist FROM discussions WHERE id=?", a["discussion_id"])["twist"] == ITEM["twists"][0]
        e2 = c.post("/api/answer", json={"item_id": iid, "kp_id": KP, "answer": "", "discussion_id": a["discussion_id"],
                                         "conclusion": "现金决定顺序"}).json()
        assert e2["next"]["twist"] == ITEM["twists"][1]
        assert db.one("SELECT next_due FROM discussions WHERE id=?", end["discussion_id"])["next_due"] == ""
        prof = discuss.profile(uid)[0]
        assert prof["discussions"] == 2 and {m["id"]: m["self"] for m in prof["moves"]}["CEO-T-05"] == 2
        assert "我的思考方式" in c.get("/thinking").text


def test_discussion_without_ai_goes_straight_to_conclusion(monkeypatch):
    with TestClient(app) as c:
        _login(c)
        iid = _item_id()
        monkeypatch.setattr(llm, "enabled", lambda: False)
        r = c.post("/api/answer", json={"item_id": iid, "kp_id": KP, "answer": "先试点再说"}).json()
        assert r["discuss"] and not r["ai"] and not r["can_continue"]
        end = c.post("/api/answer", json={"item_id": iid, "kp_id": KP, "answer": "", "discussion_id": r["discussion_id"],
                                          "conclusion": "先找一个岗位试点"}).json()
        assert end["done"] and len(end["perspectives"]) == 3 and not end["shift"]


def test_judged_places_skip_discuss_items():
    """摸底、找根源、热身、复习卡片要判对错：不拿思辨题。"""
    with TestClient(app):
        uid = auth.create_user(f"{uuid.uuid4().hex[:8]}@think.example", "secret123", "老板", role="kid", grade="ADULT")
        uid = uid if isinstance(uid, int) else db.one("SELECT MAX(id) AS id FROM users")["id"]
        iid = _item_id()
        assert iid not in [i["id"] for i in engine.items_for(uid, KP, n=20, purpose="diagnose", ai=False)]
        from app import recall
        assert (recall._kp_item(uid, KP) or {}).get("type") != "discuss"


def test_old_mistake_card_on_a_discuss_item():
    """改成思辨题以前判错进了错题本的题：重做时照思辨题的方式想，想完卡就拿掉；启动时这类卡直接清掉。"""
    with TestClient(app) as c:
        uid = _login(c)
        iid = _item_id()
        cid = engine.add_card(uid, "mistake", ITEM["q"], "旧的参考答案", {"item_id": iid, "my_answer": "旧答案"}, KP)
        r = c.post(f"/api/review/{cid}/answer", json={"item_id": iid, "answer": "不该停招，先建能力"}).json()
        assert r["discuss"] and "correct" not in r and db.one("SELECT id FROM cards WHERE id=?", cid)
        end = c.post(f"/api/review/{cid}/answer", json={"item_id": iid, "answer": "", "discussion_id": r["discussion_id"],
                                                        "conclusion": "先拿两个岗位试点"}).json()
        assert end["done"] and not db.one("SELECT id FROM cards WHERE id=?", cid)
        assert not db.one("SELECT id FROM attempts WHERE user_id=? AND item_id=?", uid, iid)
        cid2 = engine.add_card(uid, "mistake", ITEM["q"] + " ", "旧的参考答案", {"item_id": iid}, KP)
        it = engine._item_row_to_dict(db.one("SELECT * FROM items WHERE id=?", iid))
        engine.record_attempt(uid, {**it, "type": "short"}, KP, "practice", False, "旧答案")  # 改成思辨题以前判的错
        assert db.one("SELECT status FROM mastery WHERE user_id=? AND kp_id=?", uid, KP)
        assert engine.retire_open_mistakes() >= 1 and not db.one("SELECT id FROM cards WHERE id=?", cid2)
        assert not db.one("SELECT id FROM events WHERE user_id=? AND item_id=?", uid, iid)
        assert not db.one("SELECT id FROM attempts WHERE user_id=? AND item_id=?", uid, iid)
        assert not db.one("SELECT status FROM mastery WHERE user_id=? AND kp_id=?", uid, KP)
