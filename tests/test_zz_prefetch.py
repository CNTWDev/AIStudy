"""提前备题（app/prefetch.py）：早上按清单备题、混合练习不为一道题停下来等 AI、等题时的复习小卡。"""

from fastapi.testclient import TestClient

from app import bank, db, engine, mix, prefetch
from app.catalog import catalog
from app.main import app

import pytest

from test_zz_mix import _setup


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    monkeypatch.setattr(prefetch, "MODE", "inline")   # 后台备题在当前线程跑完，结果确定


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


def test_prepare_next_only(monkeypatch):
    """只备下一项，不整天一次备好；做完一项后备再下一项。"""
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "pf2@x.com", grade="G3", subj={"subj_math": "math-shanghai", "subj_english": "eng-shanghai"})
        plan = c.post("/api/plan/rebuild").json()["plan"]
        got = []
        real = prefetch.prepare_task
        monkeypatch.setattr(prefetch, "prepare_task", lambda uid, t: (got.append(t["id"]), real(uid, t)))
        assert c.get("/today").status_code == 200
        first = engine.next_task(engine.plan_of_today(kid))
        assert got == [first["id"]]                   # 打开今天页：只备第一项没做的
        mixes = [t for t in plan if t["type"] == "mix"]
        # 做完前一项：flow_state 给出下一项时就备它
        got.clear()
        nxt = engine.flow_state(kid, mixes[0]["id"])["next"]
        assert got == [nxt["id"]]
        # 下一项是混合练习时：题单建好了，前几道题库里就有，打开不用现场请 AI
        t = next(x for x in engine.plan_of_today(kid) if x["id"] == mixes[-1]["id"])
        prefetch.prepare_task(kid, t)
        s = db.one("SELECT * FROM mix_sets WHERE user_id=? AND pack_id=? AND day=?", kid, t["pack"], db.today().isoformat())
        assert s and s["pos"] == 0
        for sl in db.jload(s["slots"], [])[:prefetch.LOOKAHEAD]:
            assert engine.items_for(kid, sl["kp"], n=1, purpose=sl.get("purpose", "practice"), uses=sl.get("uses"), ai=False)
        before = _calls()
        assert c.get(f"/api/mix/{s['id']}/next?k_=2").json()["items"]
        assert _calls() >= before   # 取题后会在后台（测试里同步）备后面几道


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
        assert kicked[0] == empty            # 没题的那道先交给后台；后面几道也顺带往前备
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
        # 「为什么学」的导入也一起写好：一进学习页先要的就是它（DESIGN 5.34）；AI 写的老版本不算有
        assert prefetch.has_kp_content("context", kp)
        db.run("UPDATE contents SET status='retired' WHERE kind='context' AND kp_id=?", kp)
        bank.save_content("context", {"story": "老故事"}, kp, "", "G3")
        assert not prefetch.has_kp_content("context", kp)
