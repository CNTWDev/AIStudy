"""追根（app/trace.py）和旧知识穿插校正（app/explore.py）：卡住时当场找根、结论进今天的清单、家长能看到链；
摸底候选会确认「推断会」的点、优先今天要学的知识点用到的旧知识、家长页按年级看摸清了多少。"""
from fastapi.testclient import TestClient

from app import db, engine, explore, itemtypes, trace
from app.catalog import catalog
from app.main import app

KP = "PHY-IG-4.5.6-01"  # 前置链比较长的物理知识点（IGCSE，英文授课）


def _setup(c):
    if "创建网站管理员账号" in c.get("/login").text:
        c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
    else:
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
    c.post("/admin/users/create", data={"email": "trp@x.com", "password": "secret1", "name": "追爸", "role": "parent"})
    c.get("/logout")
    c.post("/login", data={"email": "trp@x.com", "password": "secret1"})
    c.post("/parent/kids/save", data={"name": "追娃", "email": "trk@x.com", "password": "secret1", "grade": "G8",
                                      "daily_minutes": "90", "subj_physics": "phy-cambridge"})
    c.get("/logout")
    c.post("/login", data={"email": "trk@x.com", "password": "secret1"})
    return db.one("SELECT id FROM users WHERE email='trk@x.com'")["id"]


def _good(it):
    return it["answer"] if it["type"] == "mcq" else (it["answer"][0] if isinstance(it["answer"], list) else it["answer"])


def _full(item_id):
    return engine._item_row_to_dict(db.one("SELECT * FROM items WHERE id=?", item_id))


def test_trace_flow():
    with TestClient(app) as c:
        kid = _setup(c)
        c.post("/api/plan/rebuild")
        items = c.get(f"/api/practice/{KP}?n=3").json()["items"]
        mcq = next(i for i in items if i["type"] == "mcq" and not i.get("probe"))
        assert "traps" not in mcq and "answer" not in mcq
        full = _full(mcq["id"])
        wrong = next(i for i in range(len(full["options"])) if i != full["answer"])
        # 出题时 AI 标的「选这个错项说明哪个前置没懂」
        hint = "PHY-IG-4.2.5-01"
        db.run("UPDATE items SET data=? WHERE id=?", db.jdump({**{k: v for k, v in full.items() if k in itemtypes.FIELDS},
                                                             "traps": {str(wrong): hint}}), mcq["id"])
        # 错一次：可能是粗心，不追；错第二次：可以追根
        r = c.post("/api/answer", json={"item_id": mcq["id"], "kp_id": KP, "answer": wrong}).json()
        assert not r["correct"] and not r.get("trace")
        r = c.post("/api/answer", json={"item_id": mcq["id"], "kp_id": KP, "answer": wrong}).json()
        assert r["trace"] and r["trace"]["kp"] == KP
        tid = c.post("/api/trace/start", json={"kp_id": KP, "item_id": mcq["id"], "answer": wrong}).json()["id"]
        assert trace.get(kid, tid)["hint_kp"] == hint
        assert c.post("/api/trace/start", json={"kp_id": KP}).json()["id"] == tid  # 同一天同一个点不重复开
        assert "找找根在哪" in c.get(f"/trace/{tid}").text
        # 第一步：错项直接指向的前置最可疑
        s = c.get(f"/api/trace/{tid}/next").json()
        assert not s["done"] and s["step"]["kp"] == hint and s["step"]["n"] == 1
        r = c.post(f"/api/trace/{tid}/answer", json={"item_id": s["item"]["id"], "kp": hint, "kind": "kp", "dont_know": True}).json()
        assert not r["correct"] and "result" not in r
        # 之后沿着「它」再往下追：下一步测的是 hint 的前置；答对就排除那一支
        below = {p["id"] for p, _ in catalog.ancestors(hint, depth=3)} | {x["kp"]["id"] for x in catalog.related(hint, ["uses"])}
        res = None
        for _ in range(5):
            s = c.get(f"/api/trace/{tid}/next").json()
            if s["done"]:
                res = s
                break
            assert s["step"]["kp"] in below, s["step"]
            it = _full(s["item"]["id"])
            r = c.post(f"/api/trace/{tid}/answer", json={"item_id": it["id"], "kp": s["step"]["kp"], "kind": "kp",
                                                         "answer": _good(it)}).json()
            assert r["correct"]
            if r.get("result"):
                res = r["result"]
                break
        assert res and res["result"] == "root" and res["root"]["kp"] == hint
        assert len(res["chain"]) <= trace.MAX_STEPS and res["chain"][0]["kp"] == hint
        assert res["next"][0]["url"] == f"/learn/{hint}?task=backfill"
        # 结论进今天的清单：没做的任务最前面先补根，再回来打；重排计划也不会丢
        todo = [t for t in engine.today_plan(kid)["plan"] if not t["done"]]
        assert todo[0]["kp"] == hint and todo[0]["type"] == "backfill" and todo[1]["kp"] == KP
        plan = [t for t in c.post("/api/plan/rebuild").json()["plan"] if not t["done"]]
        assert any(t.get("kp") == hint and t["type"] == "backfill" and t.get("trace") for t in plan)
        assert sum(1 for t in plan if t.get("kp") == hint and t["type"] == "backfill") == 1
        # 系统发现里能看到这条链（家长页）
        from app import insights
        found = insights.refresh(kid)
        assert any(f["kind"] == "trace" and f["kp_id"] == hint for f in found)
        c.get("/logout")
        c.post("/login", data={"email": "trp@x.com", "password": "secret1"})
        page = c.get("/parent").text
        assert "根在「" in page and "按年级看摸清了多少" in page
        c.get("/logout")
        c.post("/login", data={"email": "trk@x.com", "password": "secret1"})

        # 基础都没问题 → 是这个点本身没学会；「还不会」一次就可以追
        kp2 = "PHY-IG-6.2.3-01"
        it2 = c.get(f"/api/practice/{kp2}?n=3").json()["items"][0]
        r = c.post("/api/answer", json={"item_id": it2["id"], "kp_id": kp2, "dont_know": True}).json()
        assert r["trace"]
        tid2 = c.post("/api/trace/start", json={"kp_id": kp2, "item_id": it2["id"]}).json()["id"]
        res = None
        for _ in range(6):
            s = c.get(f"/api/trace/{tid2}/next").json()
            if s["done"]:
                res = s
                break
            it = _full(s["item"]["id"])
            r = c.post(f"/api/trace/{tid2}/answer", json={"item_id": it["id"], "kp": s["step"]["kp"], "kind": s["step"]["kind"],
                                                          "answer": _good(it)}).json()
            if r.get("result"):
                res = r["result"]
                break
        assert res["result"] == "self" and "本身" in res["message"]
        # 一天最多追两次
        kp3 = "PHY-IG-4.4-01"
        it3 = c.get(f"/api/practice/{kp3}?n=3").json()["items"][0]
        assert not c.post("/api/answer", json={"item_id": it3["id"], "kp_id": kp3, "dont_know": True}).json()["trace"]


def test_trace_language_step():
    """英文授课的物理：卡住的题没有中文翻译时，先用中文问一次；中文会 → 根在术语，术语卡今天复习。"""
    with TestClient(app) as c:
        c.post("/login", data={"email": "trk@x.com", "password": "secret1"})
        kid = db.one("SELECT id FROM users WHERE email='trk@x.com'")["id"]
        db.run("DELETE FROM traces WHERE user_id=?", kid)  # 换一天的样子：今天还没追过
        kp = "PHY-IG-4.4-01"
        orig = engine.save_items(kp, [{"type": "num", "q": "A wire has R = 2 Ω and I = 3 A. Find V.", "answer": 6, "difficulty": 2}])[0]
        zh = engine.save_items(kp, [{"type": "num", "q": "A wire has R = 4 Ω and I = 2 A. Find V.", "zh": "电阻 4 Ω、电流 2 A，电压是多少伏？",
                                     "answer": 8, "difficulty": 2}])[0]
        tid = trace.start(kid, kp, orig["id"])
        s = c.get(f"/api/trace/{tid}/next").json()
        assert s["step"]["kind"] == "lang" and s["item"]["q"].startswith("电阻") and s["item"]["id"] == zh["id"]
        r = c.post(f"/api/trace/{tid}/answer", json={"item_id": zh["id"], "kind": "lang", "answer": "8"}).json()
        assert r["correct"] and r["result"]["result"] == "lang"
        assert any(t["type"] == "words" and "术语过关" in t["title"] for t in engine.today_plan(kid)["plan"])


def test_calibration_candidates():
    """穿插校正：推断会的前置也要确认；今天要学的知识点用到的旧知识排前面；覆盖度按学段分开。"""
    with TestClient(app):
        kid = db.one("SELECT id FROM users WHERE email='trk@x.com'")["id"]
        pre = "PHY-IG-4.5.1-01"
        db.run("DELETE FROM probes WHERE user_id=? AND kp_id=?", kid, pre)
        engine.set_mastery(kid, pre, 0.65, "learning", "inferred")
        cands = explore.candidates(kid, near_kp=KP)
        hit = next(x for x in cands if x["kp"] == pre)
        assert "确认" in hit["reason"]
        # 今天清单里有某个知识点：它用到的、没测过的旧知识带着「今天要学的」理由出现
        plan = engine.today_plan(kid)["plan"]
        plan.insert(0, {"id": "tx", "type": "sync", "kp": KP, "title": "x", "minutes": 5, "done": False})
        db.run("UPDATE days SET plan=? WHERE user_id=? AND day=?", db.jdump(plan), kid, db.today().isoformat())
        reasons = [x["reason"] for x in explore.candidates(kid)]
        assert any("今天要学的" in r for r in reasons)
        cov = explore.coverage(kid)
        assert cov and cov[0]["stages"] and sum(g["total"] for g in cov[0]["stages"]) == cov[0]["total"]
