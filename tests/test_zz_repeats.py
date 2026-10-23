"""做对过的题不再出、错题换变式、「还不会」的承接、成人课学过的课换成回访（docs/DESIGN.md 5.46）。"""
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app import auth, bank, db, engine, insights, itemtypes, lessons, llm, plan, recall
from app.catalog import catalog
from app.main import app

KP = "PHY-IG-4.5.6-01"


def _kid(c, grade="G8", courses=("phy-cambridge",)):
    email = f"{uuid.uuid4().hex[:8]}@rep.example"
    auth.create_user(email, "secret123", "练习者", role="kid", grade=grade)
    c.post("/login", data={"email": email, "password": "secret123"})
    uid = db.one("SELECT id FROM users WHERE email=?", email)["id"]
    for p in courses:
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active) VALUES(?,?,?,1)", uid, p, catalog.packs[p].stages[0])
    return uid


def _right(it):
    a = it.get("answer")
    return a[0] if isinstance(a, list) else a


def _gen(calls):
    def gen(kp, pack, grade, n=3, **kw):  # 每次出的都是新题（mock 出的题每次一样）
        calls.append(kw.get("avoid"))
        return [{"type": "num", "q": f"A current of {len(calls)}.{j} A flows for 10 s. Charge in C? #{uuid.uuid4().hex[:6]}",
                 "answer": round((len(calls) + j / 10) * 10, 2), "difficulty": 2, "explain": "Q = It。代入数字。所以答案是 It。"}
                for j in range(n)]
    return gen


def test_correct_items_never_come_back(monkeypatch):
    with TestClient(app) as c:
        kid = _kid(c)
        calls = []
        monkeypatch.setattr(llm, "generate_items", _gen(calls))
        first = engine.items_for(kid, KP, n=3)
        for it in first:
            engine.record_attempt(kid, it, KP, "practice", True)
        done = {it["id"] for it in first}
        for r in bank.candidates(kid, KP):  # 题库里这个点别的题也都做对过
            if r["id"] not in done:
                engine.record_attempt(kid, bank.row_to_item(r), KP, "practice", True)
                done.add(r["id"])
        # 热身 / 往回摸底：不请 AI 时只给没做过的，没有就不给（以前会拿做对过的老题凑）
        assert engine.items_for(kid, KP, n=1, purpose="diagnose", ai=False) == []
        # 请 AI：出新题，新题里带着「别出成这样」的已有题目开头
        n0 = len(calls)
        got = engine.items_for(kid, KP, n=3)
        assert len(calls) > n0 and calls[-1]
        assert got and not ({it["id"] for it in got} & done)
        # AI 出不来：做对过的题也不拿来凑数
        monkeypatch.setattr(llm, "generate_items", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("down")))
        for it in got:
            engine.record_attempt(kid, it, KP, "practice", True)
        assert engine.items_for(kid, KP, n=3) == []
        # 知识点复习卡：这个点没有没做过的题，今天先不出（后台去出新题）
        cid = engine.add_card(kid, "kp", "电荷", "Q=It", {}, KP, due=db.today())
        assert recall.quiz(kid, db.one("SELECT * FROM cards WHERE id=?", cid))["mode"] == "later"
        assert not any(x["id"] == cid for x in c.get("/api/review/due").json()["cards"])
        # 没配置 AI 的部署：出不了新题，只好用做对过的
        monkeypatch.setattr(llm, "enabled", lambda: False)
        assert engine.items_for(kid, KP, n=1)


def test_dont_know_walks_through_and_twin(monkeypatch):
    with TestClient(app) as c:
        kid = _kid(c)
        calls = []
        monkeypatch.setattr(llm, "generate_items", _gen(calls))
        it = next(i for i in engine.items_for(kid, KP, n=3) if i["type"] in ("mcq", "num", "fill"))
        pub = c.get(f"/api/practice/{KP}?n=1").json()["items"][0]
        assert pub.get("hint") or pub.get("nudge")  # 点「还不会」先给的那一句：这类题的路子
        res = c.post("/api/answer", json={"item_id": it["id"], "kp_id": KP, "dont_know": True}).json()
        assert res["dont_know"] and isinstance(res["steps"], list)
        assert itemtypes.walk({"explain": "第一步。第二步；第三步。第四步。第五步。"}) and \
            len(itemtypes.walk({"explain": "一。二。三。四。五。六。七。"})) <= itemtypes.WALK_MAX
        card = db.one("SELECT * FROM cards WHERE user_id=? AND kind='mistake'", kid)
        ex = recall._extra(card)
        assert ex.get("dont_know")
        # 趁热试一道一样的：不是原题、没做过
        tw = c.get(f"/api/twin?item_id={it['id']}&kp_id={KP}").json()["item"]
        assert tw and tw["id"] != it["id"] and "answer" not in tw
        r = c.post("/api/answer", json={"item_id": tw["id"], "kp_id": KP, "mode": "helped", "answer": "zzz"}).json()
        assert r["correct"] is False
        assert db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='mistake'", kid)["n"] == 1  # 刚讲完做错不另进错题本
        # 第二天：不做原题（答案昨天已经看过了），直接换变式题
        db.run("UPDATE cards SET due=? WHERE id=?", db.today().isoformat(), card["id"])
        card = db.one("SELECT * FROM cards WHERE id=?", card["id"])
        q = recall.quiz(kid, card)
        if q["mode"] == "later":
            recall._ensure_variant(kid, card)
            q = recall.quiz(kid, card)
        assert q["mode"] == "item" and q["variant"] and q["item"]["id"] not in recall._group_ids(it["id"])


def test_two_dont_knows_in_a_week_mean_relearn(monkeypatch):
    with TestClient(app) as c:
        kid = _kid(c)
        monkeypatch.setattr(llm, "generate_items", _gen([]))
        for it in engine.items_for(kid, KP, n=2):
            engine.record_attempt(kid, it, KP, "practice", False, dont_know=True)
        tasks = insights.plan_tasks(insights.refresh(kid))["weak"]
        t = next(t for t in tasks if t["kp"] == KP)
        assert t["title"].startswith("从头学一遍")


def test_adult_lesson_revisit_and_rest():
    with TestClient(app) as c:
        kid = _kid(c, grade="ADULT", courses=["ceo-core"])
        kp = "CEO-M5-01"
        page = c.get(f"/learn/{kp}").text
        assert 'data-step="why"' in page and 'data-step="recall"' not in page  # 第一次：完整的一课
        assert c.post("/api/learn/done", json={"kp_id": kp, "summary": "先说结论"}).json()["ok"]
        row = lessons.learned(kid)[kp]
        assert row["n"] == 1 and kp in lessons.resting(kid)
        assert all(t.get("kp") != kp for t in plan.build_plan(kid))  # 刚学完：这几天不再排这一课
        # 隔几天回来：回访（先回想 → 新案例 → 用到自己身上），不再把讲解看一遍
        old = (db.today() - timedelta(days=lessons.REST_DAYS[0])).isoformat()
        db.run("UPDATE lessons SET first_done=?, last_done=? WHERE user_id=? AND kp_id=?", old, old, kid, kp)
        assert kp not in lessons.resting(kid)
        page = c.get(f"/learn/{kp}").text
        assert 'data-step="recall"' in page and 'data-step="why"' not in page and "先说结论" in page
        assert 'data-step="why"' in c.get(f"/learn/{kp}?full=1").text   # 想从头看也可以
        assert c.post("/api/learn/recall", json={"kp_id": kp, "ok": True}).json()["ok"]
        c.post("/api/learn/done", json={"kp_id": kp})
        row = lessons.learned(kid)[kp]
        assert row["n"] == 2 and lessons.next_on(row) == db.today() + timedelta(days=lessons.REST_DAYS[1])
