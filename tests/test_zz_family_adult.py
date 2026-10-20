"""家里的成人成员：挂在家庭里，但学习自己安排（自己选课，记录只有本人能看）；家长只管账号；不上孩子的榜、不进公开房间。"""
from fastapi.testclient import TestClient

from app import auth, boards, db
from app.arena import rules
from app.main import app


def _login(c, email):
    c.post("/logout")
    c.post("/login", data={"email": email, "password": "secret1"})


def test_family_adult_manages_own_learning():
    with TestClient(app) as c:
        if not auth.by_email("admin@x.com"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        pa = auth.create_user("fa-p@x.com", "secret1", "爸爸", "parent")
        kid = auth.create_user("fa-k@x.com", "2580", "小宝", "kid", parent_id=pa, grade="G12")
        _login(c, "fa-p@x.com")

        # 家长添加一个成人成员：表单里没有学习安排（成人年级时隐藏、不提交），不替 TA 选课
        r = c.post("/parent/kids/save", data={"email": "fa-m@x.com", "name": "妈妈", "grade": "ADULT",
                                              "daily_minutes": "40", "password": "secret1"})
        assert r.status_code == 200
        mom = auth.by_email("fa-m@x.com")
        assert mom["parent_id"] == pa and auth.self_managed(mom) and not auth.is_self_learner(mom)
        assert not db.q("SELECT 1 FROM enrollments WHERE user_id=?", mom["id"])

        # 家长页：只看坚持，学习记录、今天清单都进不去；资料与密码能改
        page = c.get("/parent").text
        assert "妈妈" in page and "学习由 TA 自己安排" in page and f"/parent/as/{mom['id']}" not in page
        assert c.get(f"/parent/as/{mom['id']}").status_code == 403
        assert c.get(f"/parent/kids/{mom['id']}").status_code == 403
        assert c.get(f"/parent/kids/{mom['id']}/plan").status_code == 403
        edit = c.get(f"/parent/kids/{mom['id']}/edit").text
        assert "家里的成人成员" in edit and 'name="subj_' not in edit and 'name="daily_minutes"' not in edit

        # 本人登录：没课先去「我的课程」，能选成人课；年级不能改成孩子的
        _login(c, "fa-m@x.com")
        assert c.get("/today", follow_redirects=False).headers["location"] == "/me/courses"
        mine = c.get("/me/courses").text
        assert 'value="ceo-core"' in mine and 'name="game_minutes"' in mine and 'name="private"' not in mine
        r = c.post("/me/courses", data={"name": "妈妈", "grade": "ADULT", "daily_minutes": "30", "subj_ceo": "ceo-core",
                                        "game_minutes": "-1", "game_unlock": "free"})
        assert r.status_code == 200
        assert [e["pack_id"] for e in db.q("SELECT pack_id FROM enrollments WHERE user_id=? AND active=1", mom["id"])] == ["ceo-core"]
        assert "请家长在家长页改年级" in c.post("/me/courses", data={"name": "妈妈", "grade": "G5"}).text
        assert auth.get_user(mom["id"])["grade"] == "ADULT"
        assert "/me/courses" in c.get("/today").text

        # 家长改成人成员的资料：只动账号，不动 TA 的课程和每天时长
        _login(c, "fa-p@x.com")
        c.post("/parent/kids/save", data={"id": str(mom["id"]), "email": "fa-m@x.com", "name": "孩子妈妈", "grade": "ADULT"})
        mom = auth.get_user(mom["id"])
        assert mom["name"] == "孩子妈妈" and mom["daily_minutes"] == 30
        assert db.q("SELECT 1 FROM enrollments WHERE user_id=? AND active=1", mom["id"])

        # 孩子升到大学：自动改成自己安排，原来的课程留着
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active,track) VALUES(?,?,?,1,?)", kid, "math-shanghai", "G12", "")
        c.post("/parent/kids/save", data={"id": str(kid), "email": "fa-k@x.com", "name": "小宝", "grade": "U1"})
        assert auth.self_managed(auth.get_user(kid))
        assert db.q("SELECT 1 FROM enrollments WHERE user_id=? AND pack_id='math-shanghai' AND active=1", kid)

        # 成人不上孩子的榜、不进公开房间；还没成年的孩子照常
        mom, child = dict(mom), dict(auth.get_user(auth.create_user("fa-c@x.com", "2580", "二宝", "kid", parent_id=pa, grade="G3")))
        assert not boards.on_board(mom) and not rules.public_rooms(mom)
        assert boards.on_board(child) and rules.public_rooms(child)
        # 家里的成人成员默认能玩乐园（不限时）；没有家庭的成人自学者默认不玩
        assert rules.game_minutes(mom) == rules.UNLIMITED and rules.game_minutes(child) > 0
        assert rules.game_minutes({"grade": mom["grade"], "parent_id": None, "settings": None}) == 0
