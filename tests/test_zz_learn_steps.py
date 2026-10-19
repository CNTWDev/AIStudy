"""知识点学习页一步一张卡（docs/DESIGN.md 5.32）：先导入为什么学，一次只显示一张卡；导入带引子问题，老版本换新。"""
from fastapi.testclient import TestClient

from app import auth, bank, db
from app.catalog import catalog
from app.main import app


def test_learn_page_is_step_cards():
    with TestClient(app) as c:
        if not auth.by_email("admin@x.com"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        pa = auth.create_user("ls-p@x.com", "secret1", "家长", "parent")
        auth.create_user("ls-k@x.com", "secret1", "小宝", "kid", parent_id=pa, grade="G6")
        c.post("/logout")
        c.post("/login", data={"email": "ls-k@x.com", "password": "secret1"})
        page = c.get("/learn/MATH-PRE-UNIT").text
        assert page.index('data-step="why"') < page.index('data-step="see"') < page.index('data-step="method"')
        # 一次只显示一张：第一张是导入，其余的先藏着
        assert page.count('<section class="card lcard"') == 5 and page.count('" hidden>') >= 4
        assert 'id="guess"' in page and '<details class="kp-around">' in page

        # 导入带引子问题；没有引子问题的老版本换成新的一次
        kp = catalog.kp("MATH-PRE-UNIT")
        db.run("UPDATE contents SET status='retired' WHERE kind='context' AND kp_id=?", kp["id"])
        old = bank.save_content("context", {"story": "老故事"}, kp["id"], "", "G6")
        ctx = c.get("/api/context/MATH-PRE-UNIT").json()
        assert ctx["question"] and ctx["content_id"] != old
        assert db.one("SELECT status FROM contents WHERE id=?", old)["status"] == "retired"
        assert c.get("/api/context/MATH-PRE-UNIT").json()["content_id"] == ctx["content_id"]
