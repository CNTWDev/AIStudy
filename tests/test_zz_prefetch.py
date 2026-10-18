"""提前备题（app/prefetch.py）：早上按清单备题、混合练习不为一道题停下来等 AI、等题时的复习小卡。"""

from fastapi.testclient import TestClient

from app import db, engine, mix, prefetch
from app.catalog import catalog
from app.main import app

from test_zz_mix import _setup


def _calls(task="items"):
    return db.one("SELECT COUNT(*) AS n FROM llm_calls WHERE task=?", task)["n"]


def test_items_for_without_ai(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "pf1@x.com", grade="G3", subj={"subj_math": "math-shanghai"})
        kp = "MSH-NUM-17"
        before = _calls()
        got = engine.items_for(kid, kp, n=3, ai=False)
        assert _calls() == before            # 只看题库，不请 AI
        assert prefetch.ensure(kid, kp, len(got) + 3)  # 不够：请 AI 出
        assert _calls() > before
        assert len(engine.items_for(kid, kp, n=3, ai=False)) == 3
        assert not prefetch.ensure(kid, kp, 3)        # 够了：什么都不做


def test_morning_prefetch(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "pf2@x.com", grade="G3", subj={"subj_math": "math-shanghai"})
        monkeypatch.setattr(prefetch, "HOUR", 0)
        assert kid in [k["id"] for k in prefetch.kids()]          # 刚加进来的孩子也备
        monkeypatch.setattr(prefetch, "kids", lambda: [{"id": kid}])  # 别的测试留下的孩子不管
        db.run("DELETE FROM jobs WHERE name=?", prefetch.JOB)
        r = prefetch.run_daily()
        assert r["done"] and r["kids"] >= 1, r
        assert prefetch.run_daily()["skipped"]       # 一天一次
        q = "SELECT * FROM mix_sets WHERE user_id=? AND pack_id='math-shanghai' AND day=?"
        s = db.one(q, kid, db.today().isoformat())
        assert s and s["pos"] == 0                      # 题单建好了，还没开始做
        # 早上建好、还没开始做的那组不算「已经开始」：计划重排时题数照常按时间分
        plan = c.post("/api/plan/rebuild").json()["plan"]
        n = next(t for t in plan if t["type"] == "mix" and t["pack"] == "math-shanghai")["n"]
        # 孩子打开练习：题数跟今天的计划一致；题都是现成的，不用现场请 AI
        c.get("/practice/math-shanghai")
        s = db.one(q, kid, db.today().isoformat())
        assert len(db.jload(s["slots"], [])) == n
        before = _calls()
        got = c.get(f"/api/mix/{s['id']}/next?k_=4").json()["items"]
        assert got and _calls() == before


def test_mix_defers_missing(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "pf3@x.com", grade="G3", subj={"subj_math": "math-shanghai"})
        c.post("/api/plan/rebuild")
        s = mix.get_or_create(kid, "math-shanghai", 8)
        slots = db.jload(s["slots"], [])
        # 第一道换成一个题库里一道题都没有的知识点：不等它，先给后面的
        used = {x["kp"] for x in slots}
        empty = next(k["id"] for k in catalog.pack_kps("math-shanghai") if k["id"] not in used
                     and not engine.items_for(kid, k["id"], n=1, ai=False))
        for sl in slots[1:]:
            prefetch.ensure(kid, sl["kp"], 2, sl.get("purpose", "practice"), "G3", sl.get("uses"))
        slots[0] = {"kp": empty, "role": "past", "mode": "probe", "purpose": "practice"}
        db.run("UPDATE mix_sets SET slots=? WHERE id=?", db.jdump(slots), s["id"])
        kicked = []
        monkeypatch.setattr(prefetch, "kick", lambda *a, **k: kicked.append(a[1]))
        got = mix.next_items(kid, s["id"], "G3", k=2)
        assert got["items"] and all(i["kp_id"] != empty for i in got["items"])
        assert kicked == [empty]
        moved = db.jload(db.one("SELECT slots FROM mix_sets WHERE id=?", s["id"])["slots"], [])
        assert any(x["kp"] == empty and x.get("deferred") for x in moved)


def test_filler(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "pf4@x.com", grade="G3", subj={"subj_math": "math-shanghai"})
        assert c.get("/api/filler").json() == {}
        for w, m in (("apple", "苹果"), ("banana", "香蕉"), ("orange", "橙子"), ("grape", "葡萄")):
            engine.add_card(kid, "word", w, m, {}, None)
        db.run("UPDATE cards SET due=? WHERE user_id=?", db.today().isoformat(), kid)
        f = c.get("/api/filler").json()
        assert f["card"] and f["quiz"]["mode"] == "choice" and len(f["quiz"]["options"]) == 4
        assert "front" not in f and "answer" not in f["quiz"]
        f2 = c.get(f"/api/filler?skip={f['card']}").json()
        assert f2["card"] != f["card"]
        front = db.one("SELECT front FROM cards WHERE id=?", f["card"])["front"]
        r = c.post(f"/api/review/{f['card']}/answer", json={"answer": front, "mode": "choice"}).json()
        assert r["correct"]


def test_learn_page_kicks_prefetch(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "pf5@x.com", grade="G3", subj={"subj_math": "math-shanghai"})
        kp = "MSH-NUM-20"
        assert c.get(f"/learn/{kp}").status_code == 200
        assert len(engine.items_for(kid, kp, n=3, ai=False)) == 3   # 测试里后台出题是同步跑完的
        assert prefetch.has_teach(kp)
