"""每天的计划和学习页用同一条规则：计划里排的知识点，学习者一定打得开（catalog.kp_visible）。
成人以前在中小学的课里留下的作答记录，不会再被「自动发现」成「先打基础」排进今天的计划；
排好以后才变的（改了年级、升级前排的），打开今天页就按现在的情况重排。"""
from fastapi.testclient import TestClient

from app import auth, db, engine, insights
from app.catalog import catalog
from app.main import app


def _login(c, email, pw="secret1"):
    c.post("/logout")
    c.post("/login", data={"email": email, "password": pw})


def _shared_root(pack_id: str):
    """这门课里被两个知识点都当「必须」前置的一个点：它们都做错，自动发现就会说「根」在它。"""
    users: dict[str, list] = {}
    for k in catalog.packs[pack_id].kp_ids:
        for p, _ in catalog.ancestors(k, depth=1):
            users.setdefault(p["id"], []).append(k)
    root, kids = next((r, ks) for r, ks in users.items() if len(ks) >= 2)
    return root, kids[:2]


def test_plan_never_points_at_a_kp_the_learner_cannot_open():
    with TestClient(app) as c:
        uid = auth.create_user("pa-adult@x.com", "secret1", "自学", "kid", grade="ADULT")
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active,track) VALUES(?,?,?,1,?)", uid, "ceo-core", "CEO-1", "")
        root, wrong = _shared_root("math-cambridge")
        assert not catalog.kp_visible(root, "ADULT") and root in catalog.closed_to("ADULT")
        now = db.now()
        for k in wrong:   # 以前（比如还是孩子的年级时）在中小学的课里做错过
            for _ in range(2):
                db.run("INSERT INTO attempts(user_id,item_id,kp_id,mode,correct,answer,created_at) VALUES(?,?,?,?,?,?,?)",
                       uid, None, k, "practice", 0, "x", now)

        # 自动发现不再把打不开的点当「根」；排出来的计划里也没有
        assert root not in {f.get("kp_id") for f in insights.detect(uid)}
        plan = engine.today_plan(uid, rebuild=True)["plan"]
        assert all(catalog.kp_visible(t["kp"], "ADULT") for t in plan if t.get("kp"))

        # 修之前排好的旧计划：打开今天页就重排，打不开的那项不见了，做完的照样留着
        stale = [{"id": "t0", "type": "warmup", "title": "热身 3 题", "minutes": 4, "url": "/warmup", "done": True},
                 {"id": "t1", "type": "backfill", "kp": root, "title": f"先打基础：{catalog.kp(root)['name']}",
                  "minutes": 10, "url": f"/learn/{root}?task=backfill", "done": False}]
        db.run("UPDATE days SET plan=? WHERE user_id=? AND day=?", db.jdump(stale), uid, db.today().isoformat())
        plan = engine.today_plan(uid)["plan"]
        assert root not in {t.get("kp") for t in plan}
        assert any(t["title"] == "热身 3 题" and t["done"] for t in plan)

        # 学习页同一条规则：打不开的点，计划里也不会有
        _login(c, "pa-adult@x.com")
        assert c.get(f"/learn/{root}").status_code == 404
        assert root not in c.get("/").text
