"""beejoy 品牌：顶栏标志、网页图标、孩子自己选小伙伴形象。"""
from fastapi.testclient import TestClient

from app import brand, db
from app.main import app


def test_mascot_svg_ids_are_unique_per_use():
    a, b = brand.mascot_svg("buzzy"), brand.mascot_svg("buzzy")
    assert 'clip-path="url(#bB-' in a and a != b  # 同一页放两个，clipPath 不串
    assert "<style>" not in brand.mascot_svg("hive")  # 文件里给浏览器图标用的样式不能漏进页面
    assert set(brand.MASCOTS) == {"hive", "buzzy", "letter", "flyer", "drop"}


def test_kid_picks_mascot():
    with TestClient(app) as c:
        if c.get("/login").text.count("创建网站管理员账号"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        else:
            c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        c.post("/admin/users/create", data={"email": "bp@x.com", "password": "secret1", "name": "蜜蜂爸爸", "role": "parent"})
        c.get("/logout")
        c.post("/login", data={"email": "bp@x.com", "password": "secret1"})
        c.post("/parent/kids/save", data={"name": "小蜂", "email": "bk@x.com", "password": "secret1", "grade": "G4",
                                          "daily_minutes": "60", "subj_math": "math-shanghai"})
        c.get("/logout")

        c.post("/login", data={"email": "bk@x.com", "password": "secret1"})
        today = c.get("/today").text
        assert 'data-tone="kid"' in today and 'href="/static/brand/hive.svg"' in today  # 没选过：默认蜂巢宝宝
        assert "选一个你最喜欢的小伙伴" in today
        page = c.get("/settings").text
        assert "选一个陪你学习的小伙伴" in page and page.count('name="mascot"') == 5

        r = c.post("/settings/mascot", data={"mascot": "drop"})
        assert "蜜糖滴" in r.text
        kid = db.one("SELECT settings FROM users WHERE email='bk@x.com'")
        assert db.jload(kid["settings"], {})["mascot"] == "drop"
        today = c.get("/today").text
        assert 'href="/static/brand/drop.svg"' in today and "选一个你最喜欢的小伙伴" not in today
        assert "没有这个形象" in c.post("/settings/mascot", data={"mascot": "dragon"}).text
        for f in ("hive.svg", "wordmark.svg", "icon-180.png", "site.webmanifest"):
            assert c.get(f"/static/brand/{f}").status_code == 200
        c.get("/logout")

        # 家长：同一套颜色，成人口吻
        c.post("/login", data={"email": "bp@x.com", "password": "secret1"})
        assert 'data-tone="adult"' in c.get("/parent").text
        assert "我的图标" in c.get("/settings").text


def test_old_saved_site_name_falls_back_to_beejoy():
    """改名前后台保存过站点设置的网站，库里存着「AIStudy」，升级后要自动显示 beejoy。"""
    from app import sitecfg
    with TestClient(app):
        sitecfg.set_many({"site_name": "AIStudy"})
        assert sitecfg.get("site_name") == "beejoy"
        sitecfg.set_many({"site_name": "AI Study"})
        assert sitecfg.get("site_name") == "beejoy"
        sitecfg.set_many({"site_name": "我家学习本"})  # 自己起的名字照常生效
        assert sitecfg.get("site_name") == "我家学习本"
        sitecfg.set_many({"site_name": ""})
