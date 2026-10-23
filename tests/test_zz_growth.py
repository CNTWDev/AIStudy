"""积少成多：一共攒了多少、学会过只增不减、做完一项的「这一项 → 今天 → 一共」、里程碑、每天第一次打开的那句话（docs/DESIGN.md 5.51）。"""
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import auth, db, engine, evidence, growth
from app.catalog import catalog
from app.main import app

KP = "PHY-IG-4.5.6-01"


def _kid(c, grade="G8", courses=("phy-cambridge",)):
    email = f"{uuid.uuid4().hex[:8]}@grow.example"
    auth.create_user(email, "secret123", "攒攒", role="kid", grade=grade)
    c.post("/login", data={"email": email, "password": "secret123"})
    uid = db.one("SELECT id FROM users WHERE email=?", email)["id"]
    for p in courses:
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active) VALUES(?,?,?,1)", uid, p, catalog.packs[p].stages[0])
    return uid


def _answer(uid, n, at=None, mode="practice", correct=True):
    for _ in range(n):
        evidence.log(uid, "kp", KP, "answer", correct=correct, mode=mode, at=at)


def _ago(days):
    return (datetime.fromisoformat(db.now()) - timedelta(days=days)).isoformat(timespec="seconds")


def test_learned_only_grows():
    with TestClient(app) as c:
        uid = _kid(c)
        st = {"p": 0.95, "s": 10, "d": 5, "last": db.now(), "ev": evidence.empty_evidence(), "status": "mastered"}
        evidence._save(uid, KP, st, attempts=5, correct=5, source="practice", key="t")
        assert growth.totals(uid)["kps"] == 1
        evidence._save(uid, KP, {**st, "status": "weak", "p": 0.3}, attempts=6, correct=5, source="practice", key="t")
        evidence._save(uid, KP, st, attempts=7, correct=6, source="practice", key="t")
        assert growth.totals(uid)["kps"] == 1   # 又不稳了不减，重新学会不重复算
        got = growth.kps_between(uid, db.today().isoformat())
        assert got[0]["n"] == 1 and got[0]["name"] == catalog.kp(KP)["name"]


def test_milestones_once_and_silent_at_start():
    with TestClient(app) as c:
        uid = _kid(c)
        _answer(uid, 120)
        assert growth.milestones(uid, growth.totals(uid)) == []   # 上线那天：已经跨过的悄悄记下
        _answer(uid, 85)
        ms = growth.milestones(uid, growth.totals(uid))
        assert [(m["kind"], m["n"]) for m in ms] == [("answers", 200)]
        assert "第 200 道" in ms[0]["text"] and "攒" in ms[0]["text"]
        assert growth.milestones(uid, growth.totals(uid)) == []   # 只庆祝一次
        assert growth.next_mark("answers", 10000) == 15000 and growth.next_mark("kps", 9) == 10


def test_task_done_shows_item_today_total():
    with TestClient(app) as c:
        uid = _kid(c)
        _answer(uid, 30, at=_ago(3))
        plan = engine.today_plan(uid)["plan"]
        assert len(plan) >= 2
        engine.mark_task_by(uid, id=plan[0]["id"])
        _answer(uid, 7)
        _answer(uid, 4, mode="sprint")   # 两项之间去冲刺：今天、一共算，「这一项」不算
        r = c.post("/api/plan/task-done", json={"type": plan[1]["type"], "current": plan[1]["id"]}).json()
        g = r["flow"]["grow"]
        assert (g["item"], g["today"], g["total"]) == (7, 11, 41)
        assert c.get("/static/app.js").text.count("fs-grow") == 1


def test_hello_says_yesterday_and_never_blames():
    with TestClient(app) as c:
        uid = _kid(c, grade="G3")
        yday = (db.today() - timedelta(days=1)).isoformat()
        db.run("INSERT INTO days(user_id,day,plan,minutes) VALUES(?,?,?,?)", uid, yday, "[]", 30)
        _answer(uid, 46, at=yday + "T10:00:00" + db.now()[19:])
        tot = growth.totals(uid)
        h = growth.hello(uid, tot)
        assert "昨天你答了 46 道题" in h and "一共 46 道" in h
        assert "答" in growth.hello(uid, tot, teen=True) and "啦" not in growth.hello(uid, tot, teen=True)
        db.run("UPDATE days SET day=? WHERE user_id=? AND day=?", (db.today() - timedelta(days=4)).isoformat(), uid, yday)
        h = growth.hello(uid, tot)
        assert h.startswith("欢迎回来") and "没" not in h
        page = c.get("/today").text
        assert "一共 <b>46</b> 道" in page


def test_hello_near_milestone():
    with TestClient(app) as c:
        uid = _kid(c, grade="G3")
        yday = (db.today() - timedelta(days=1)).isoformat()
        db.run("INSERT INTO days(user_id,day,plan,minutes) VALUES(?,?,?,?)", uid, yday, "[]", 30)
        _answer(uid, 60, at=_ago(5))
        _answer(uid, 35, at=yday + "T10:00:00" + db.now()[19:])
        h = growth.hello(uid, growth.totals(uid))
        assert "再答 5 道就到 100 道" in h


def test_records_page_kid_board_and_parent_line():
    with TestClient(app) as c:
        uid = _kid(c, grade="G3")
        _answer(uid, 30, at=_ago(15))
        _answer(uid, 140)
        b = growth.board(uid, {"grow": {"icon": "🪐", "name": "颗星球"}})
        assert b["jars"]["full"] == 1 and b["jars"]["part_n"] == 70 and b["jars"]["icon"] == "🪐"
        assert b["weeks"][-1]["n"] == 170 and b["weeks"][-1]["now"]
        assert b["next"][0]["kind"] == "answers" and b["next"][0]["mark"] == 200
        page = c.get("/records").text
        assert "我的积累" in page and "装满了 <b>1</b> 罐蜜" in page and "正确率" not in page.split("我的积累")[1].split("bd-strip")[0]
        assert growth.line(uid).startswith("从第一天到现在：") and "答过 170 道题" in growth.line(uid)
