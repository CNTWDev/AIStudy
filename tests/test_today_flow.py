"""一路做下去：统一入口、做完接下一项、页面顶上的进度条；每日阅读复用当天的文章；题目的统一展示；错题回来。"""
from fastapi.testclient import TestClient

from app import db, engine, itemtypes, plan, recall
from app.main import app


def _kid(c, email, **extra):
    c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
    c.post("/admin/users/create", data={"email": f"p-{email}", "password": "secret1", "name": "家长", "role": "parent"})
    c.get("/logout")
    c.post("/login", data={"email": f"p-{email}", "password": "secret1"})
    c.post("/parent/kids/save", data={"name": "小流", "email": email, "password": "secret1", "grade": "G3",
                                      "daily_minutes": "60", "subj_math": "math-shanghai", "subj_english": "eng-shanghai", **extra})
    c.get("/logout")
    c.post("/login", data={"email": email, "password": "secret1"})
    return dict(db.one("SELECT * FROM users WHERE email=?", email))


def _set_plan(kid_id, tasks):
    db.run("INSERT INTO days(user_id, day, plan) VALUES(?,?,?) ON CONFLICT(user_id, day) DO UPDATE SET plan=excluded.plan",
           kid_id, db.today().isoformat(), db.jdump(tasks))


def test_item_view():
    it = {"type": "mcq", "q": "1+1=?", "options": ["1", "2", "3"], "answer": 1, "explain": "一加一等于二"}
    v = itemtypes.view(it, "2")
    assert [o["right"] for o in v["options"]] == [False, True, False]
    assert [o["mine"] for o in v["options"]] == [False, False, True]
    assert v["answer"] == "B. 2" and v["mine"] == "C. 3" and not v["mine_right"] and v["explain"]
    q = itemtypes.view(it, with_answer=False)  # 不带答案：出题用
    assert "answer" not in q and not any(o["right"] for o in q["options"])
    f = itemtypes.view({"type": "fill", "q": "苹果的英文", "answer": ["apple"]}, "aple")
    assert f["answer"] == "apple" and f["mine"] == "aple" and f["options"] == []
    assert itemtypes.reveal(it)["answer_index"] == 1


def test_go_and_flow():
    with TestClient(app) as c:
        kid = _kid(c, "flow@x.com")
        _set_plan(kid["id"], [
            {"id": "t0", "type": "warmup", "title": "热身 4 题", "minutes": 4, "url": "/warmup", "done": False},
            {"id": "t1", "type": "mistakes", "title": "错题重做 2 道", "minutes": 4, "url": "/review?group=mistakes", "done": False},
            {"id": "t2", "type": "read_en", "lang": "en", "title": "英文阅读 15 分钟", "minutes": 15,
             "url": "/reading?lang=en", "done": False}])  # 升级前排的清单：阅读入口会自动换成 /reading/today
        r = c.get("/go", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/warmup"
        assert c.get("/go?after=t0", follow_redirects=False).headers["location"] == "/review?group=mistakes"
        page = c.get("/warmup").text
        assert 'class="flowbar' in page and "第 1 项" in page and "/go?after=t0" in page
        # 做完一项：告诉前端下一项是什么，不用回首页
        fl = c.post("/api/plan/task-done", json={"type": "warmup"}).json()["flow"]
        assert fl["just"] and fl["done"] == 1 and fl["total"] == 3 and fl["next"]["id"] == "t1" and fl["next"]["n"] == 2
        assert c.get("/go", follow_redirects=False).headers["location"] == "/review?group=mistakes"
        # 跳过的那项：后面都做完了再回头找它
        assert engine.next_task(engine.plan_of_today(kid["id"]), "t2")["id"] == "t1"
        assert "/reading/today?lang=en" in c.get("/go?after=t1", follow_redirects=False).headers["location"]
        assert "/go" in c.get("/today").text

        # 每日阅读：今天还没读 → 写文章的工具；写过 → 直接打开那篇，不再生成第二篇
        r = c.get("/reading/today?lang=en")
        assert r.status_code == 200 and "今天的阅读" in r.text
        r = c.post("/reading/new", data={"mode": "ai", "lang": "en"}, follow_redirects=False)
        rid = int(r.headers["location"].split("/")[-1].split("?")[0])
        n = db.one("SELECT COUNT(*) AS n FROM readings WHERE user_id=?", kid["id"])["n"]
        r = c.post("/reading/new", data={"mode": "ai", "lang": "en"}, follow_redirects=False)
        assert r.headers["location"] == f"/reading/{rid}?dup=1"
        assert db.one("SELECT COUNT(*) AS n FROM readings WHERE user_id=?", kid["id"])["n"] == n
        assert c.get("/reading/today?lang=en", follow_redirects=False).headers["location"] == f"/reading/{rid}"
        page = c.get(f"/reading/{rid}").text
        assert "flowbar" in page and "英文阅读" in page
        qs = db.jload(db.one("SELECT questions FROM readings WHERE id=?", rid)["questions"], [])
        res = c.post(f"/api/reading/{rid}/finish", json={"minutes": 3, "answers": {"0": "0"}}).json()
        assert len(res["results"]) == len(qs) and (not qs or res["results"][0]["view"]["options"])
        assert res["flow"] and res["flow"]["just"] and res["flow"]["next"]["id"] == "t1"
        # 再进来：直接看到上次的答案和解析
        page = c.get("/reading/today?lang=en").text
        assert "这篇读完了" in page and ("qc-ans" in page or not qs)
        assert db.one("SELECT answers FROM readings WHERE id=?", rid)["answers"]
        r = c.post("/reading/new", data={"mode": "ai", "lang": "en"}, follow_redirects=False)  # 读完了还想读：可以再写一篇
        assert int(r.headers["location"].split("/")[-1]) != rid


def test_plan_order_and_mistakes():
    with TestClient(app) as c:
        kid = _kid(c, "order@x.com")
        it = db.one("SELECT * FROM items WHERE kp_id='MATH-PRE-UNIT' LIMIT 1")
        item = engine._item_row_to_dict(it)
        # 游戏、冲刺里认真想了还错的题，也进错题本；3 秒内手快答错的不进
        engine.record_attempt(kid["id"], item, "MATH-PRE-UNIT", "sprint", False, "9", ms=1200)
        assert not db.one("SELECT id FROM cards WHERE user_id=? AND kind='mistake'", kid["id"])
        engine.record_attempt(kid["id"], item, "MATH-PRE-UNIT", "game", False, "9", ms=8000)
        cid = db.one("SELECT id FROM cards WHERE user_id=? AND kind='mistake'", kid["id"])["id"]
        db.run("UPDATE cards SET due=? WHERE id=?", db.today().isoformat(), cid)
        engine.add_card(kid["id"], "word", "apple", "苹果", due=db.today())
        p = plan.build_plan(kid["id"])
        types = [t["type"] for t in p]
        # 主线：热身 → 错题（旧账先清）→ 单词 → 学新的 / 补弱的 → 阅读收尾
        assert "mistakes" in types and types.index("mistakes") < types.index("words")
        assert types[-1] == "read_en" and p[-1]["url"] == "/reading/today?lang=en"
        # 错题本按原题结构显示：题干、选项、正确答案、我选的、解析
        page = c.get("/words?kind=mistake").text
        assert 'class="qc"' in page and "qc-ans" in page and "今天要重做" in page
        q = recall.quiz(kid["id"], db.one("SELECT * FROM cards WHERE id=?", cid))
        assert q["mode"] == "item" and "answer" not in q["item"]
        res = c.post("/api/answer", json={"item_id": item["id"], "kp_id": "MATH-PRE-UNIT", "answer": "zzz"}).json()
        assert "explain" in res and res["answer"]
        if item["type"] == "mcq":
            assert res["answer_index"] == item["answer"]
