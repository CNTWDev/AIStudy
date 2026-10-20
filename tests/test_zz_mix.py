"""学校进度按校历推演（app/schoolyear.py）、地基（app/ladder.py）、每门课一组混合练习（app/mix.py）、
融合题的证据、快练进主线（app/sprint.py drill）。"""
import time
from datetime import date

from fastapi.testclient import TestClient

from app import bank, db, engine, explore, ladder, learning, mix, schoolyear, sprint
from app.catalog import catalog, stage_rank
from app.main import app

DAY = date(2026, 10, 17)


def _setup(c, monkeypatch, email="mixk@x.com", grade="G8", subj=None):
    monkeypatch.setattr(schoolyear, "today", lambda: DAY)
    if "创建网站管理员账号" in c.get("/login").text:
        c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
    else:
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
    c.post("/admin/users/create", data={"email": "mixp@x.com", "password": "secret1", "name": "混爸", "role": "parent"})
    c.get("/logout")
    c.post("/login", data={"email": "mixp@x.com", "password": "secret1"})
    c.post("/parent/kids/save", data={"name": "混娃", "email": email, "password": "2580", "grade": grade,
                                      "daily_minutes": "90", **(subj or {"subj_physics": "phy-cambridge"})})
    c.get("/logout")
    c.post("/login", data={"email": email, "password": "2580"})
    return db.one("SELECT id FROM users WHERE email=?", email)["id"]


def test_calendar_estimate():
    with TestClient(app):
        e = {"pack_id": "math-shanghai", "stage": "G3", "track": None}
        u = {"grade": "G3"}
        est = schoolyear.estimate(u, e, date(2026, 10, 17))
        assert est["phase"] == "term" and est["current"]["key"] == "二、用一位数乘"
        # 寒假：上册学完，下册还没开始，不推新课
        est = schoolyear.estimate(u, e, date(2027, 2, 1))
        assert est["phase"] == "vacation" and est["current"] is None
        assert all(ch.get("rank") != 2 for ch in est["chapters"][:est["idx"]])
        # 第二学期往下册走；暑假：这一学段都学完了
        assert schoolyear.estimate(u, e, date(2027, 4, 20))["current"]["rank"] == 2
        est = schoolyear.estimate(u, e, date(2027, 8, 10))
        assert est["idx"] == len(est["chapters"]) and est["current"] is None
        # 两年的课程（IGCSE）：第二年的同一天比第一年往后
        ig = {"pack_id": "phy-cambridge", "stage": "IGCSE", "track": None}
        y1 = schoolyear.estimate({"grade": "G9"}, ig, date(2027, 3, 1))
        y2 = schoolyear.estimate({"grade": "G10"}, ig, date(2027, 3, 1))
        assert (y2["idx"], y2["within"]) > (y1["idx"], y1["within"])
        # 没有校历的课（职业资格考试）不推演
        assert schoolyear.estimate({"grade": "ADULT"}, {"pack_id": "fund-law", "stage": "FUND-1", "track": None}) is None


def test_ladder_foundations():
    with TestClient(app):
        sis = [{"pack_id": "phy-cambridge", "stage": "IGCSE", "track": None}]
        found = {f["pack_id"]: f for f in ladder.foundations({"grade": "G8"}, sis)}
        # 物理要用到科学和数学：没报的用基础教材，到孩子的年级为止
        assert found["math-cn"]["upto"] == 8 and found["sci-shanghai"]["upto"] == 8
        rng = ladder.range_of(found["math-cn"], ladder.enrolled_concepts(sis))
        assert rng and all(stage_rank(catalog.kps[k]["stage"]) < 8 for k in rng)
        # 小学生报的都是从一年级开始的教材：没有额外的地基
        bro = [{"pack_id": p, "stage": "G3", "track": None} for p in ("math-shanghai", "sci-shanghai", "eng-shanghai")]
        assert ladder.foundations({"grade": "G3"}, bro) == []


def test_foundation_only_when_relevant(monkeypatch):
    """地基不铺开摸：只有和正在学的有关、或答错后往回追到的，才拿来测。"""
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "mixrel@x.com")
        c.post("/api/plan/rebuild")
        found = set().union(*[ladder.range_of(f, ladder.enrolled_concepts(explore._enrolls(kid)))
                              for f in ladder.foundations(dict(db.one("SELECT * FROM users WHERE id=?", kid)), explore._enrolls(kid))])
        assert found
        cands = [x["kp"] for x in explore.candidates(kid)]
        assert not [k for k in cands if k in found and not x_rel(k, kid)], "地基里和正在学的无关的不该被铺开摸底"
        # 答错以后沿前置往回追：排进队列的地基知识点会出来
        k = sorted(found)[0]
        db.run("INSERT INTO probes(user_id,kind,kp_id,reason,from_kp,depth,priority,created_at) VALUES(?,'kp',?,?,?,1,9,?)",
               kid, k, "测试：往回追", "", db.now())
        assert k in [x["kp"] for x in explore.candidates(kid)]


def x_rel(k, kid):
    near = set()
    for e in explore._enrolls(kid):
        if e["progress_kp"]:
            near |= {p["id"] for p, _ in explore._near(e["progress_kp"])}
    for t in explore._today_kps(kid):
        near |= {p["id"] for p, _ in explore._near(t)}
    return k in near


def test_mix_set_flow(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "mixflow@x.com", grade="G3",
                     subj={"subj_math": "math-shanghai", "subj_english": "eng-shanghai"})
        plan = c.post("/api/plan/rebuild").json()["plan"]
        mixes = [t for t in plan if t["type"] == "mix"]
        assert mixes and all(t["url"].startswith("/practice/") and t["n"] >= mix.MIN_ITEMS for t in mixes)
        assert "drill" in [t["type"] for t in plan]
        assert db.one("SELECT progress_kp FROM enrollments WHERE user_id=? AND pack_id='math-shanghai'", kid)["progress_kp"]
        slots = mix.build(kid, "math-shanghai", 15)
        assert len(slots) <= 15 and slots[0]["role"] == "new"
        assert all(a["kp"] != b["kp"] for a, b in zip(slots, slots[1:]) if a["role"] != "preview" and b["role"] != "preview")
        roles = mix.summary(slots)
        assert roles["new"] >= 3 and roles["past"] + roles["recent"] + roles["cross"] >= 1, roles

        page = c.get("/practice/math-shanghai")
        assert page.status_code == 200 and "数学练习" in page.text
        sid = db.one("SELECT id FROM mix_sets WHERE user_id=? AND pack_id='math-shanghai'", kid)["id"]
        got = c.get(f"/api/mix/{sid}/next?k_=3").json()
        assert got["items"] and all("answer" not in i and i["mix"]["role"] for i in got["items"])
        it = got["items"][0]
        full = engine._item_row_to_dict(db.one("SELECT * FROM items WHERE id=?", it["id"]))
        wrong = (full["answer"] + 1) % len(full["options"]) if full["type"] == "mcq" else "zzz"
        r = c.post("/api/answer", json={"item_id": it["id"], "kp_id": it["mix"]["kp"], "mode": it["mix"]["mode"],
                                        "request_id": it["request_id"], "answer": wrong}).json()
        assert not r["correct"]
        t = c.post(f"/api/mix/{sid}/tick", json={"kp": it["mix"]["kp"], "role": it["mix"]["role"], "correct": False}).json()
        assert t["again"]   # 错了：过几题换一道同类的再来
        # 同一组里不重复出同一道题
        seen = {i["id"] for i in got["items"]}
        more = c.get(f"/api/mix/{sid}/next?k_=6").json()["items"]
        assert not seen & {i["id"] for i in more}
        slots = db.jload(db.one("SELECT slots FROM mix_sets WHERE id=?", sid)["slots"])
        assert [x for x in slots if x["role"] == "again" and x["kp"] == it["mix"]["kp"]]
        r = c.post(f"/api/mix/{sid}/done").json()
        assert r["answered"] == 1 and r["flow"]["total"]
        assert any(t["done"] for t in engine.plan_of_today(kid) if t["type"] == "mix" and t["pack"] == "math-shanghai")


def test_combine_item_evidence(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "mixcomb@x.com", grade="G3", subj={"subj_math": "math-shanghai"})
        main, old = "MSH-NUM-20", "MSH-NUM-17"
        it = bank.save_items(main, [{"type": "num", "q": "测试融合题：两位数乘两位数，要先会乘一位数 23×14=?", "answer": 322,
                                      "kp_ids": [old], "difficulty": 2}], purpose="combine")[0]
        before = (db.one("SELECT attempts FROM mastery WHERE user_id=? AND kp_id=?", kid, old) or {}).get("attempts") or 0
        r = learning.submit(kid, {"item_id": it["id"], "kp_id": main, "answer": "322", "mode": "practice"})
        assert r["correct"]
        assert db.one("SELECT attempts FROM mastery WHERE user_id=? AND kp_id=?", kid, old)["attempts"] == before + 1
        # 做错：不扣旧知识点的分，排进「往回追」
        it2 = bank.save_items(main, [{"type": "num", "q": "测试融合题二：31×12=?", "answer": 372, "kp_ids": [old]}], purpose="combine")[0]
        # 题库里有没做过的融合题：按 combine 取到它（做过的不再出）
        assert engine.items_for(kid, main, n=1, purpose="combine", uses=[old])[0]["id"] == it2["id"]
        learning.submit(kid, {"item_id": it2["id"], "kp_id": main, "answer": "1", "mode": "practice"})
        assert db.one("SELECT id FROM probes WHERE user_id=? AND kp_id=? AND status='queued'", kid, old)


def test_drill_in_main_line(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "mixdrill@x.com", grade="G3", subj={"subj_math": "math-shanghai"})
        c.post("/api/plan/rebuild")
        assert "快练" in c.get("/sprint?drill=1").text
        k = dict(db.one("SELECT * FROM users WHERE id=?", kid))
        run = sprint.start(k, drill=True)   # 不用等今天的任务做完
        assert run["drill_s"] == sprint.DRILL_S
        t0 = time.time()
        monkeypatch.setattr(sprint.time, "time", lambda: t0 + sprint.DRILL_S + 5)
        st = db.jload(db.one("SELECT data FROM sprint_runs WHERE id=?", run["run_id"])["data"])
        db.run("UPDATE sprint_runs SET data=?, answered=? WHERE id=?", db.jdump({**st, "t0": t0}), sprint.DRILL_MIN_N, run["run_id"])
        q = sprint.question(k, run["run_id"])["item"]
        r = sprint.answer(k, run["run_id"], q["id"], None, dont_know=True)
        assert r["drill_done"] and r["drill_done"]["total"]
        assert any(t["done"] for t in engine.plan_of_today(kid) if t["type"] == "drill")
