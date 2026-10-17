"""错题重做提交要快：变式题在打开错题时就在后台备好；题库里已有能当变式题的，提交时不再请 AI 现场出题。"""
from fastapi.testclient import TestClient

from app import bank, db, engine, llm, recall
from app.main import app

KP = "PHY-IG-4.5.6-01"


def _setup(c):
    if "创建网站管理员账号" in c.get("/login").text:
        c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
    else:
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
    c.post("/admin/users/create", data={"email": "rvp@x.com", "password": "secret1", "name": "重爸", "role": "parent"})
    c.get("/logout")
    c.post("/login", data={"email": "rvp@x.com", "password": "secret1"})
    c.post("/parent/kids/save", data={"name": "重娃", "email": "rvk@x.com", "password": "secret1", "grade": "G8",
                                      "daily_minutes": "90", "subj_physics": "phy-cambridge"})
    c.get("/logout")
    c.post("/login", data={"email": "rvk@x.com", "password": "secret1"})
    return db.one("SELECT id FROM users WHERE email='rvk@x.com'")["id"]


def _good(it):
    return it["answer"] if it["type"] == "mcq" else (it["answer"][0] if isinstance(it["answer"], list) else it["answer"])


def _card(kid, it):
    cid = engine.add_card(kid, "mistake", it["q"], "答案", {"item_id": it["id"], "my_answer": "x"}, KP, due=db.today())
    ex = db.jload(db.one("SELECT extra FROM cards WHERE id=?", cid)["extra"])
    db.run("UPDATE cards SET extra=? WHERE id=?", db.jdump({**ex, "streak": 1, "streak_day": "2000-01-01"}), cid)
    return cid


def test_redo_answer_does_not_wait_for_ai(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c)
        it = next(i for i in engine.items_for(kid, KP, n=5) if i["type"] in ("mcq", "fill", "num"))
        it = engine._item_row_to_dict(db.one("SELECT * FROM items WHERE id=?", it["id"]))
        # 题库里别的题孩子都做对过（不再是「新题」），以前提交时会因此请 AI 再出 3 道
        for r in bank.candidates(kid, KP):
            if r["id"] != it["id"]:
                engine.record_attempt(kid, bank.row_to_item(r), KP, "practice", True)
        cid = _card(kid, it)
        calls = []
        real = llm.generate_items

        def gen(*a, **k):  # mock 出的题每次都一样，加个编号当作新题
            calls.append(1)
            return [{**x, "q": f"{x['q']} #{len(calls)}-{j}"} for j, x in enumerate(real(*a, **k))]
        monkeypatch.setattr(llm, "generate_items", gen)
        # 题库里已经有这个知识点的别的题：打开、提交都不请 AI
        cards = c.get("/api/review/due?group=mistakes").json()["cards"]
        assert any(x["id"] == cid for x in cards)
        res = c.post(f"/api/review/{cid}/answer", json={"item_id": it["id"], "answer": _good(it)}).json()
        assert res.get("variant_next"), res
        assert not calls

        # 题库里没有别的题：打开错题时后台备题，提交时直接用
        db.run("DELETE FROM cards WHERE id=?", cid)
        retired = [r["id"] for r in bank.candidates(kid, KP) if r["id"] != it["id"]]
        for oid in retired:
            db.run("UPDATE items SET status='retired' WHERE id=?", oid)
        cid = _card(kid, it)
        card = db.one("SELECT * FROM cards WHERE id=?", cid)
        assert recall._variant(kid, card, recall._extra(card)) is None
        c.get("/api/review/due?group=mistakes")
        t = recall._prep.get(cid)
        if t:
            t.join(timeout=30)
        assert calls  # 后台出了题
        n = len(calls)
        res = c.post(f"/api/review/{cid}/answer", json={"item_id": it["id"], "answer": _good(it)}).json()
        assert res.get("variant_next"), res
        assert len(calls) == n  # 提交时没有再出题
        for oid in retired:  # 别影响后面的测试
            db.run("UPDATE items SET status='active' WHERE id=?", oid)
