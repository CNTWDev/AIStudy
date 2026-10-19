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
        # 等导入时小伙伴「翻书」（DESIGN 5.34）：名字用配置的小伙伴名，不再是一行「正在准备」小字
        assert "window.AI = {name: " in page and "seek($('#ctx'), 'story'" in page and "正在准备" not in page

        # 导入带引子问题；没有引子问题的老版本换成新的一次
        kp = catalog.kp("MATH-PRE-UNIT")
        db.run("UPDATE contents SET status='retired' WHERE kind='context' AND kp_id=?", kp["id"])
        old = bank.save_content("context", {"story": "老故事"}, kp["id"], "", "G6")
        ctx = c.get("/api/context/MATH-PRE-UNIT").json()
        assert ctx["question"] and ctx["content_id"] != old
        assert db.one("SELECT status FROM contents WHERE id=?", old)["status"] == "retired"
        assert c.get("/api/context/MATH-PRE-UNIT").json()["content_id"] == ctx["content_id"]  # 之后大家共用这一份
        # 导入配一张故事条，讲解的例题配图：存的是画图说明，给页面时画成 SVG
        assert ctx["fig"]["kind"] == "story" and "<svg" in ctx["fig"]["svg"]
        stored = db.jload(db.one("SELECT body FROM contents WHERE id=?", ctx["content_id"])["body"], {})
        assert stored["fig"]["kind"] == "story" and "svg" not in stored["fig"] and stored["v"] == bank.KP_CONTENT_V["context"]
        t = c.get("/api/teach/MATH-PRE-UNIT").json()
        assert "<svg" in t["example"]["fig"]["svg"]
        # 画不出来的图去掉，文字照用
        assert "fig" not in bank._keep_figs({"steps": [{"title": "a", "text": "b", "fig": {"kind": "story", "beats": []}}]})["steps"][0]
        # 人写的讲解（教材作者）不因为版本号换掉
        kp2 = catalog.kp("MATH-PRE-UNIT")
        db.run("UPDATE contents SET status='retired' WHERE kind='teach' AND kp_id=?", kp2["id"])
        mine = bank.save_content("teach", {"steps": [{"title": "作者", "text": "人写的"}]}, kp2["id"], "", "G6", origin="author")
        assert c.get("/api/teach/MATH-PRE-UNIT").json()["content_id"] == mine
