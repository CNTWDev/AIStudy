"""管理后台：家庭列表 → 家庭页；学习账号换家庭、改为自学者、自学者挂到家庭（记录跟着账号走，要输名字确认）。"""
from fastapi.testclient import TestClient

from app import auth, db
from app.main import app


def _admin(c):
    if not auth.by_email("admin@x.com"):
        c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
    c.post("/login", data={"email": "admin@x.com", "password": "secret1"})


def test_family_pages_and_moves():
    with TestClient(app) as c:
        _admin(c)
        pa = auth.create_user("af-pa@x.com", "secret1", "甲家长", "parent")
        pb = auth.create_user("af-pb@x.com", "secret1", "乙家长", "parent")
        kid = auth.create_user("af-k@x.com", "secret1", "小甲", "kid", parent_id=pa, grade="G4")
        solo = auth.create_user("af-s@x.com", "secret1", "自学者甲", "kid", grade="U1")
        db.run("INSERT INTO days(user_id, day, plan, minutes, checked_in) VALUES(?,?,?,?,?)", kid, db.today().isoformat(), "[]", 20, 1)

        fam = c.get("/admin?tab=families&q=小甲").text
        assert f"/admin/families/{pa}" in fam and f"/admin/families/{pb}" not in fam and "1/1" in fam
        page = c.get(f"/admin/families/{pa}").text
        assert "小甲" in page and "家长定的规矩" in page and "学习报告" in page
        assert c.get(f"/admin/families/{kid}").status_code == 404
        assert "自学者甲" in c.get("/admin?tab=selfs").text
        assert "所属家庭" in c.get(f"/admin/users/{kid}").text and "所属家庭" not in c.get(f"/admin/users/{pa}").text

        # 名字没填对：不动
        r = c.post(f"/admin/users/{kid}/family", data={"to": str(pb), "confirm": "不对"})
        assert "确认名字没填对" in r.text and auth.get_user(kid)["parent_id"] == pa
        # 换到乙家：记录还在，乙家长能看到，甲家长看不到
        r = c.post(f"/admin/users/{kid}/family", data={"to": str(pb), "confirm": "小甲"})
        assert "已把 小甲 调整为" in r.text and auth.get_user(kid)["parent_id"] == pb
        assert db.one("SELECT minutes FROM days WHERE user_id=?", kid)["minutes"] == 20
        assert "小甲" in c.get(f"/admin/families/{pb}").text and "小甲" not in c.get(f"/admin/families/{pa}").text
        assert db.one("SELECT detail FROM auth_events WHERE event='admin_move_learner' ORDER BY id DESC")["detail"].startswith("af-pa@x.com 家 → af-pb@x.com 家")
        # 改为自学者；自学者挂到甲家
        c.post(f"/admin/users/{kid}/family", data={"to": "self", "confirm": "小甲"})
        assert auth.is_self_learner(auth.get_user(kid))
        c.post(f"/admin/users/{solo}/family", data={"to": str(pa), "confirm": "自学者甲"})
        assert auth.get_user(solo)["parent_id"] == pa
        # 只能挂到家长账号下；家长账号不能被移动
        assert "没有这个家庭" in c.post(f"/admin/users/{solo}/family", data={"to": str(kid), "confirm": "自学者甲"}).text
        assert c.post(f"/admin/users/{pa}/family", data={"to": "self", "confirm": "甲家长"}).status_code == 404
        # 旧链接：邀请关系并进了邀请码
        assert "邀请关系" in c.get("/admin?tab=tree").text
        for path in ("/admin/library", "/admin/news", "/admin/operations", "/admin/curricula"):
            assert 'class="adminnav"' in c.get(path).text, path
