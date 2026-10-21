"""课程按人群分开：中小学的课只给中小学生，成人的课只给成人；按学习者的年级分，不按账号类型（docs/课程按人群分开-方案.md）。"""
from fastapi.testclient import TestClient

from app import auth, db, engine
from app.catalog import Catalog, catalog
from app.main import app


def _login(c, email, pw="secret1"):
    c.post("/logout")
    c.post("/login", data={"email": email, "password": pw})


def test_pack_audience_from_stages():
    with TestClient(app):
        _audience_checks()


def _audience_checks():
    assert catalog.packs["ceo-core"].audience == "adult" and catalog.packs["fund-law"].audience == "adult"
    assert catalog.packs["math-shanghai"].audience == "school" and catalog.packs["math-cn"].audience == "school"
    assert catalog.visible("ceo-core", "ADULT") and catalog.visible("ceo-core", "U1")
    assert not catalog.visible("ceo-core", "G3") and not catalog.visible("ceo-core", "")
    assert catalog.visible("math-shanghai", "G3") and not catalog.visible("math-shanghai", "ADULT")
    assert not catalog.visible("no-such-pack", "G3")
    ceo_kp = catalog.packs["ceo-core"].kp_ids[0]
    assert catalog.kp_visible(ceo_kp, "ADULT") and not catalog.kp_visible(ceo_kp, "G8")
    kids = {p.id for ps in catalog.by_subject("public", "G3").values() for p in ps}
    adults = {p.id for ps in catalog.by_subject("", "ADULT").values() for p in ps}
    assert "math-shanghai" in kids and "ceo-core" not in kids and "fund-law" not in kids
    assert {"ceo-core", "fund-law"} <= adults and not adults & kids and all(catalog.packs[p].audience == "adult" for p in adults)


def test_school_course_cannot_depend_on_adult_course(tmp_path):
    import shutil
    from app import config
    with TestClient(app):
        ceo_kp = catalog.packs["ceo-core"].kp_ids[0]
    d = tmp_path / "curricula"
    shutil.copytree(config.CURRICULA_DIR, d)
    import json
    p = d / "math-cambridge.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["kps"][0]["prereqs"] = [*data["kps"][0].get("prereqs", []), {"id": ceo_kp, "strength": "有帮助"}]
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    c = Catalog()
    c.load(d, register_meta=False)
    assert any("中小学的课不能把它当前置" in e for e in c.errors)


def test_kid_and_adult_see_only_their_courses():
    with TestClient(app) as c:
        if not auth.by_email("admin@x.com"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        pa = auth.create_user("au-p@x.com", "secret1", "爸爸", "parent")
        kid = auth.create_user("au-k@x.com", "2580", "小宝", "kid", parent_id=pa, grade="G3")
        _login(c, "au-p@x.com")

        # 家长给孩子选课：只列中小学的课；页面硬塞成人的课也不收
        edit = c.get(f"/parent/kids/{kid}/edit").text
        assert 'value="math-shanghai"' in edit and 'value="ceo-core"' not in edit and 'value="fund-law"' not in edit
        assert 'value="ceo-core"' not in c.get("/parent/kids/new").text
        c.post("/parent/kids/save", data={"id": str(kid), "email": "au-k@x.com", "name": "小宝", "grade": "G3",
                                          "daily_minutes": "40", "subj_math": "math-shanghai", "subj_ceo": "ceo-core",
                                          "game_minutes": "30", "game_unlock": "free"})
        assert {e["pack_id"] for e in db.q("SELECT pack_id FROM enrollments WHERE user_id=? AND active=1", kid)} == {"math-shanghai"}

        # 孩子打不开成人课：知识地图、学习页、讲解、练习都当没有
        _login(c, "au-k@x.com", "2580")
        ceo_kp = catalog.packs["ceo-core"].kp_ids[0]
        assert c.get("/map/ceo-core").status_code == 404
        assert c.get(f"/learn/{ceo_kp}").status_code == 404
        assert c.get(f"/api/teach/{ceo_kp}").status_code == 404
        assert c.get(f"/api/practice/{ceo_kp}").status_code == 404
        assert c.get("/diagnose/fund-law").status_code == 404
        assert c.get("/map/math-shanghai").status_code == 200

        # 自学者：注册就是成人，只看成人的课，年级只能选大学或「成人」；中小学的课打不开
        auth.create_user("au-s@x.com", "secret1", "自学", "kid", grade="ADULT")   # 自学者：没有家长，注册时就是「成人」
        _login(c, "au-s@x.com")
        me = auth.by_email("au-s@x.com")
        page = c.get("/me/courses").text
        assert 'value="ceo-core"' in page and 'value="math-shanghai"' not in page
        assert 'value="G3"' not in page and 'value="ADULT"' in page
        assert "自学账号是给成人的" in c.post("/me/courses", data={"name": "自学", "grade": "G8"}).text
        c.post("/me/courses", data={"name": "自学", "grade": "ADULT", "subj_ceo": "ceo-core", "subj_math": "math-shanghai"})
        assert {e["pack_id"] for e in db.q("SELECT pack_id FROM enrollments WHERE user_id=? AND active=1", me["id"])} == {"ceo-core"}
        assert c.get("/map/math-shanghai").status_code == 404
        assert c.get(f"/learn/{ceo_kp}").status_code == 200

        # 以前报过的、不是给这群人的课：启动时暂停，记录留着
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active,track) VALUES(?,?,?,1,?)", kid, "ceo-core", "CEO-1", "")
        assert engine.fit_courses() >= 1
        assert db.one("SELECT active FROM enrollments WHERE user_id=? AND pack_id='ceo-core'", kid)["active"] == 0
        assert engine.fit_courses() == 0
