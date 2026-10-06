"""端到端冒烟测试：python -m pytest -q（使用 mock 大模型和临时数据库）

默认用临时 SQLite。要在 PostgreSQL 上跑：
  TEST_DATABASE_URL=postgresql://用户:密码@127.0.0.1/空数据库 python -m pytest -q
"""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{os.environ['DATA_DIR']}/test.db"
os.environ["LLM_CONFIG_FILE"] = os.path.join(os.environ["DATA_DIR"], "no-llm.toml")
os.environ["LLM_PROVIDER"] = "mock"
os.environ["SECRET_KEY"] = "test"
os.environ["REGISTRATION"] = "invite"

if os.environ.get("TEST_DATABASE_URL"):  # 测试库每次清空重建
    import psycopg
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as _c:
        _c.execute("DROP SCHEMA public CASCADE")
        _c.execute("CREATE SCHEMA public")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_full_flow():
    with TestClient(app) as c:
        assert c.get("/healthz").json()["kps"] > 500
        r = c.get("/login")
        assert "创建管理员账号" in r.text
        c.post("/register", data={"email": "p@x.com", "password": "secret1", "name": "爸爸"})
        assert "家长页" in c.get("/parent").text
        # 姐姐：IGCSE 物理 + 剑桥英语 + 统编语文；弟弟：上海英语 + 统编语文 + 上海数学
        c.post("/parent/kids/save", data={"name": "姐姐", "email": "a@x.com", "password": "secret1", "grade": "G8",
                                          "daily_minutes": "90", "pack_phy-cambridge": "on", "stage_phy-cambridge": "IGCSE",
                                          "pack_eng-cambridge": "on", "stage_eng-cambridge": "G8",
                                          "pack_chn-tongbian": "on", "stage_chn-tongbian": "G8"})
        c.post("/parent/kids/save", data={"name": "弟弟", "email": "b@x.com", "password": "secret1", "grade": "G3",
                                          "daily_minutes": "60", "pack_eng-shanghai": "on", "stage_eng-shanghai": "G3",
                                          "pack_chn-tongbian": "on", "stage_chn-tongbian": "G3",
                                          "pack_math-shanghai": "on", "stage_math-shanghai": "G3"})
        page = c.get("/parent").text
        assert "姐姐" in page and "弟弟" in page
        c.get("/logout")

        # 姐姐登录
        r = c.post("/login", data={"email": "a@x.com", "password": "secret1"})
        assert "今日计划" in r.text
        assert "摸底诊断" in r.text
        for path in ["/subjects", "/map/phy-cambridge", "/learn/PHY-IG-1.1-05", "/review", "/words", "/reading", "/records",
                     "/diagnose/phy-cambridge", "/words?kind=term", "/settings"]:
            assert c.get(path).status_code == 200, path

        # 诊断：全部答错，验证会往回追前置
        sid = c.post("/api/diag/start", json={"pack_id": "phy-cambridge"}).json()["sid"]
        depths = []
        for _ in range(30):
            q = c.get(f"/api/diag/{sid}/next").json()
            if q["done"]:
                break
            depths.append(q["depth"])
            c.post(f"/api/diag/{sid}/answer", json={"answer": "zzz", "self": "dont"})
        assert max(depths) >= 1, "做错后应该回溯到前置知识点"
        rep = c.get(f"/diagnose/report/{sid}")
        assert "断点" in rep.text
        plan = c.post("/api/plan/rebuild").json()["plan"]
        kinds = {t["type"] for t in plan}
        assert kinds & {"weak", "backfill"}, plan

        # 练习：种子题 + mock AI 出题
        items = c.get("/api/practice/MATH-PRE-UNIT?n=3").json()["items"]
        assert items and "answer" not in items[0]
        it = items[0]
        res = c.post("/api/answer", json={"item_id": it["id"], "kp_id": "MATH-PRE-UNIT", "answer": "0"}).json()
        assert "correct" in res
        items = c.get("/api/practice/ENG-VOC-01?n=3").json()["items"]  # 无种子题 → mock AI
        assert len(items) == 3

        # 复习卡片（错题 + 术语）
        due = c.get("/api/review/due").json()["cards"]
        # 卡片明天才到期，这里直接把它们设为今天
        from app import db
        db.run("UPDATE cards SET due=?", db.today().isoformat())
        due = c.get("/api/review/due").json()["cards"]
        assert due
        assert "box" in c.post(f"/api/review/{due[0]['id']}", json={"grade": "good"}).json()

        # 阅读：AI 生成 → 查词 → 收藏 → 造句 → 读完
        r = c.post("/reading/new", data={"mode": "ai", "lang": "en", "topic": "plants"})
        assert r.status_code == 200 and "A Small Seed" in r.text
        rid = int(str(r.url).rstrip("/").split("/")[-1])
        look = c.post("/api/lookup", json={"q": "seed", "context": "A small seed fell.", "lang": "en", "reading_id": rid}).json()
        assert look["meaning"]
        assert c.post("/api/cards", json={"kind": "word", "front": "seed", "back": "种子"}).json()["id"]
        fb = c.post("/api/sentence", json={"word": "seed", "meaning": "种子", "sentence": "I plant a seed.", "lang": "en"}).json()
        assert "ok" in fb
        assert c.post("/api/explain", json={"sentence": "A small seed fell on the ground.", "lang": "en"}).json()["meaning"]
        fin = c.post(f"/api/reading/{rid}/finish", json={"minutes": 5, "answers": {"0": "1"}}).json()
        assert fin["results"][0]["ok"] is True
        r = c.post("/reading/new", data={"mode": "paste", "lang": "zh", "title": "春", "body": "盼望着，盼望着。东风来了。"})
        assert "东风来了" in r.text
        assert c.post("/api/checkin", json={"reflection": "学会了量筒读数", "minutes": 40}).json()["streak"] == 1
        assert "学会了量筒读数" in c.get("/records").text
        assert "seed" in c.get("/words").text
        c.get("/logout")

        # 弟弟看不到姐姐的数据；家长可以看报告
        c.post("/login", data={"email": "b@x.com", "password": "secret1"})
        assert "学会了量筒读数" not in c.get("/records").text
        assert c.get("/parent").status_code == 403
        assert c.get("/map/math-shanghai").status_code == 200
        c.get("/logout")
        c.post("/login", data={"email": "p@x.com", "password": "secret1"})
        kid_ids = [r["id"] for r in db.q("SELECT id FROM users WHERE role='kid' ORDER BY id")]
        assert "学会了量筒读数" in c.get(f"/parent/kids/{kid_ids[0]}").text 
        r = c.get(f"/parent/as/{kid_ids[1]}")
        assert "正在以" in r.text


def test_accounts():
    from app import auth, db
    with TestClient(app) as c:
        # 管理员（第一个账号）登录，生成邀请码
        c.post("/login", data={"email": "p@x.com", "password": "secret1"})
        assert c.get("/admin").status_code == 200
        r = c.post("/admin/invites/create", data={"note": "表姐家", "max_uses": "1", "days": "7"})
        code = r.text.split("新邀请码：")[1][:14]
        c.get("/logout")

        # 没有邀请码不能注册；有邀请码可以，且只能用一次
        r = c.post("/register", data={"email": "q@x.com", "password": "secret1", "name": "表姐", "invite": "BAD"})
        assert "邀请码不对" in r.text
        r = c.post("/register", data={"email": "q@x.com", "password": "secret1", "name": "表姐", "invite": code})
        assert "家长页" in r.text
        assert c.get("/admin").status_code == 403
        c.get("/logout")
        r = c.post("/register", data={"email": "r@x.com", "password": "secret1", "name": "x", "invite": code})
        assert "邀请码不对" in r.text

        # 连续输错 5 次被锁定，正确密码也进不去；命令行可解锁
        for _ in range(5):
            assert "邮箱或密码不对" in c.post("/login", data={"email": "q@x.com", "password": "wrong"}).text
        assert "锁定" in c.post("/login", data={"email": "q@x.com", "password": "secret1"}).text
        from app import cli
        assert cli.main(["unlock", "q@x.com"]) == 0
        assert "家长页" in c.post("/login", data={"email": "q@x.com", "password": "secret1"}).text

        # 两个设备登录同一账号；改密码后另一个设备被踢下线
        with TestClient(app) as c2:
            c2.post("/login", data={"email": "q@x.com", "password": "secret1"})
            assert c2.get("/parent").status_code == 200
            assert len(auth.sessions_of(auth.by_email("q@x.com")["id"])) == 2
            r = c.post("/password", data={"old": "secret1", "new": "secret2"})
            assert "密码已修改" in r.text
            assert c.get("/parent").status_code == 200
            assert c2.get("/parent").url.path == "/login"
        c.get("/logout")
        assert c.get("/parent").url.path == "/login"

        # 管理员停用账号 → 立刻无法使用；生成重设密码链接 → 重设后可登录
        c.post("/login", data={"email": "p@x.com", "password": "secret1"})
        q_id = auth.by_email("q@x.com")["id"]
        c.post(f"/admin/users/{q_id}/status", data={"status": "disabled"})
        r = c.post(f"/admin/users/{q_id}/reset")
        link = r.text.split('value="')[1].split('"')[0] if "/reset/" in r.text else ""
        token = [x for x in r.text.split() if "/reset/" in x][0].split("/reset/")[1].split('"')[0]
        c.get("/logout")
        assert "停用" in c.post("/login", data={"email": "q@x.com", "password": "secret2"}).text
        c.post("/login", data={"email": "p@x.com", "password": "secret1"})
        c.post(f"/admin/users/{q_id}/status", data={"status": "active"})
        c.get("/logout")
        assert "两次输入" in c.post(f"/reset/{token}", data={"password": "newpass1", "password2": "x"}).text
        assert "密码已重设" in c.post(f"/reset/{token}", data={"password": "newpass1", "password2": "newpass1"}).text
        assert "无效" in c.get(f"/reset/{token}").text
        assert "家长页" in c.post("/login", data={"email": "q@x.com", "password": "newpass1"}).text
        assert db.one("SELECT COUNT(*) AS n FROM auth_events WHERE event='login_fail'")["n"] >= 5
        assert link
