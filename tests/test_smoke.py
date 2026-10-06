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
        assert "今天的任务" in r.text
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


def test_approval_and_admin(monkeypatch):
    from app import auth, config, db
    monkeypatch.setattr(config, "REGISTRATION", "approval")
    with TestClient(app) as c:
        # 没有邀请码：提交申请 → 待审批，不能登录
        r = c.post("/register", data={"email": "s@x.com", "password": "secret1", "name": "申请人", "note": "朋友介绍，孩子四年级"})
        assert "申请已提交" in r.text
        assert "等待管理员审批" in c.post("/login", data={"email": "s@x.com", "password": "secret1"}).text

        # 家长生成邀请链接 → 别人用它注册直接开通，记录邀请关系
        c.post("/login", data={"email": "q@x.com", "password": "newpass1"})
        r = c.post("/invite", data={"note": "同事"})
        code = r.text.split("邀请码：")[1][:14]
        assert "/login?invite=" + code in r.text
        c.get("/logout")
        assert code in c.get(f"/login?invite={code}").text
        r = c.post("/register", data={"email": "t@x.com", "password": "secret1", "name": "同事", "invite": code})
        assert "家长页" in r.text
        t = auth.by_email("t@x.com")
        assert t["invited_by"] == auth.by_email("q@x.com")["id"] and t["invite_code"] == code
        c.get("/logout")

        # 管理员：概览里能看到待审批，通过后可以登录
        c.post("/login", data={"email": "p@x.com", "password": "secret1"})
        r = c.get("/admin")
        assert "朋友介绍，孩子四年级" in r.text
        s_id = auth.by_email("s@x.com")["id"]
        c.post(f"/admin/users/{s_id}/status", data={"status": "active", "back": "overview"})
        for tab in ["overview", "families", "invites", "tree", "log", "system"]:
            assert c.get(f"/admin?tab={tab}").status_code == 200, tab
        fam = c.get("/admin?tab=families").text
        assert "姐姐" in fam and "连续" in fam
        assert "同事" in c.get("/admin?tab=tree").text
        assert "→ 同事" in c.get("/admin?tab=invites").text
        kid = db.one("SELECT id FROM users WHERE role='kid' ORDER BY id")["id"]
        assert c.get(f"/admin/kids/{kid}").status_code == 200
        c.get("/logout")
        assert "家长页" in c.post("/login", data={"email": "s@x.com", "password": "secret1"}).text
        assert c.get("/admin").status_code == 403


def test_daily_tasks_progress_and_tools():
    import io
    import zipfile

    from app import db
    with TestClient(app) as c:
        kid_b = db.one("SELECT id FROM users WHERE email='b@x.com'")["id"]
        # 家长给弟弟安排：西游记每天 1 回、英文分级读物、每天 5 个新词
        c.post("/login", data={"email": "p@x.com", "password": "secret1"})
        assert "西游记" in c.get(f"/parent/kids/{kid_b}/plan").text
        c.post(f"/parent/kids/{kid_b}/tracks", data={"kind": "read_zh", "ref": "zh-xiyouji", "daily_amount": "1", "daily_minutes": "20"})
        c.post(f"/parent/kids/{kid_b}/tracks", data={"kind": "read_en", "ref": "en-i-will-surprise-my-friend", "daily_amount": "1", "daily_minutes": "15"})
        r = c.post(f"/parent/kids/{kid_b}/tracks", data={"kind": "words", "ref": "en-core-g3", "daily_amount": "5"})
        assert "已安排" in r.text
        c.get("/logout")

        c.post("/login", data={"email": "b@x.com", "password": "secret1"})
        plan = c.post("/api/plan/rebuild").json()["plan"]
        types = [t["type"] for t in plan]
        assert types[0] == "progress"  # 还没设置学校进度 → 第一项提醒
        assert {"words", "read_zh", "read_en"} <= set(types), types
        assert "第 1 回" in c.get("/today").text or "第1回" in c.get("/today").text
        assert len(c.get("/api/review/due?group=words").json()["cards"]) >= 5

        # 更新学校进度：数学正在学两位数乘两位数，前面的乘法学过了
        assert "学校学到哪了" in c.get("/progress").text
        r = c.post("/progress/math-shanghai", data={"stage": "G3", "current": "MSH-NUM-20",
                                                   "taught": ["MSH-NUM-16", "MSH-NUM-17"]})
        assert "进度已更新" in r.text
        e = db.one("SELECT * FROM enrollments WHERE user_id=? AND pack_id='math-shanghai'", kid_b)
        assert e["progress_kp"] == "MSH-NUM-20"
        taught = {x["kp_id"] for x in db.q("SELECT kp_id FROM kp_taught WHERE user_id=?", kid_b)}
        assert taught == {"MSH-NUM-16", "MSH-NUM-17", "MSH-NUM-20"}
        plan = c.post("/api/plan/rebuild").json()["plan"]
        prog = [t for t in plan if t["type"] == "progress"]
        assert prog and prog[0]["done"]  # 做完的进度任务保留并打勾
        assert any(t["type"] == "sync" and "两位数乘两位数" in t["title"] for t in plan), plan
        # 再次只改学段：清空正在学，保留勾选
        c.post("/progress/math-shanghai", data={"stage": "G4"})
        assert db.one("SELECT stage FROM enrollments WHERE user_id=? AND pack_id='math-shanghai'", kid_b)["stage"] == "G4"
        c.post("/progress/math-shanghai", data={"stage": "G3", "current": "MSH-NUM-20", "taught": ["MSH-NUM-16"]})

        # 名著接着读：读完第 1 回 → 任务完成、进度 +1
        tid = db.one("SELECT id FROM tracks WHERE user_id=? AND kind='read_zh' AND active=1", kid_b)["id"]
        assert c.get(f"/track/{tid}").status_code == 200
        assert c.post(f"/api/track/{tid}/log", json={"to": 1, "minutes": 18, "summary": "石猴出世", "feeling": "😄"}).json()["ok"]
        assert db.one("SELECT position FROM tracks WHERE id=?", tid)["position"] == 1
        plan = c.post("/api/plan/rebuild").json()["plan"]
        assert [t for t in plan if t["type"] == "read_zh"][0]["done"]
        assert "石猴出世" in c.get(f"/day/{db.today().isoformat()}").text

        # 「这道题还不会」：不算错，进错题本
        items = c.get("/api/practice/MATH-PRE-UNIT?n=1").json()["items"]
        res = c.post("/api/answer", json={"item_id": items[0]["id"], "kp_id": "MATH-PRE-UNIT", "dont_know": True}).json()
        assert res["dont_know"] and "answer" in res
        assert db.one("SELECT COUNT(*) AS n FROM attempts WHERE user_id=? AND dont_know=1", kid_b)["n"] == 1
        assert db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='mistake'", kid_b)["n"] >= 1
        assert c.get("/review?group=mistakes").status_code == 200
        assert c.post("/api/checkin", json={"reflection": "读了西游记", "minutes": 30, "mood": "😄"}).json()["badges"]

        # 划词查词插件：生成连接码 → 用 Bearer 调接口 → 加入单词本 → 作废后不能用
        r = c.post("/tools/token", data={"kid": "0"})
        token = r.text.split('id="tok"')[1].split(">")[1].split("<")[0].strip()
        assert token.startswith("ais_") and "token=" not in str(r.url)
        c.get("/logout")
        with TestClient(app) as ext:
            h = {"Authorization": f"Bearer {token}"}
            assert ext.get("/ext/me").status_code == 401
            assert ext.get("/ext/me", headers=h).json()["name"] == "弟弟"
            look = ext.post("/ext/lookup", json={"text": "seed", "context": "A small seed fell."}, headers=h).json()
            assert look["lang"] == "en" and look["result"]["meaning"] and not look["saved"]
            assert ext.post("/ext/save", json={"word": "seed", "meaning": "种子"}, headers=h).json()["ok"]
            assert ext.post("/ext/lookup", json={"text": "seed"}, headers=h).json()["saved"]
            assert ext.post("/ext/lookup", json={"text": "苹果"}, headers=h).json()["lang"] == "zh"
            card = db.one("SELECT * FROM cards WHERE user_id=? AND front='seed'", kid_b)
            assert "插件" in card["extra"]

            # 家长页面：给孩子管理连接码、下载插件
            c.post("/login", data={"email": "p@x.com", "password": "secret1"})
            page = c.get(f"/tools?kid={kid_b}").text
            assert "弟弟" in page and "作废" in page
            th = db.one("SELECT token_hash FROM api_tokens WHERE user_id=?", kid_b)["token_hash"]
            c.post("/tools/token", data={"kid": str(kid_b), "action": "revoke", "token_hash": th})
            assert ext.get("/ext/me", headers=h).status_code == 401
            z = zipfile.ZipFile(io.BytesIO(c.get("/tools/extension.zip").content))
            assert "aistudy-extension/manifest.json" in z.namelist()
            assert "http" in z.read("aistudy-extension/config.js").decode()
            assert c.get(f"/parent/as/{kid_b}?next=/progress").url.path == "/progress"


def test_paper_import_and_diagnosis():
    from app import db, papers
    with TestClient(app) as c:
        kid_b = db.one("SELECT id FROM users WHERE email='b@x.com'")["id"]
        c.post("/login", data={"email": "b@x.com", "password": "secret1"})
        assert "导入一份试卷" in c.get("/papers").text
        # 没有照片也没有文字 → 提示
        assert c.post("/api/papers", data={"pack_id": "math-shanghai"}).status_code == 400
        assert c.post("/api/papers", data={"pack_id": "nope", "text": "x" * 20}).status_code == 400
        assert c.post("/api/papers", data={"pack_id": "math-shanghai"},
                      files=[("photos", ("a.txt", b"hello", "text/plain"))]).status_code == 400
        jpg = b"\xff\xd8\xff\xe0" + b"0" * 100
        r = c.post("/api/papers", data={"pack_id": "math-shanghai", "title": "第三单元测验", "exam_date": "2026-10-01"},
                   files=[("photos", ("p1.jpg", jpg, "image/jpeg")), ("photos", ("p2.jpg", jpg, "image/jpeg"))])
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        p = db.one("SELECT * FROM papers WHERE id=?", pid)
        assert p["title"] == "第三单元测验" and db.jload(p["images"]) == ["1.jpg", "2.jpg"]
        rows = papers.rows(pid)
        assert len(rows) == 3
        assert rows[0]["kp_id"] and rows[2]["kp_id"] is None  # 不在候选列表里的知识点 → 未对应
        assert rows[0]["orig"] == "wrong" and rows[1]["orig"] == "right"
        # 原卷错题进错题本；今天多一项「试卷订正」
        assert db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='mistake' AND front LIKE '示例：1 m%'", kid_b)["n"] == 1
        plan = c.post("/api/plan/rebuild").json()["plan"]
        assert any(t["type"] == "paper" for t in plan), plan
        page = c.get(f"/papers/{pid}").text
        assert "第三单元测验" in page and "原卷 ✗" in page
        assert c.get(f"/papers/{pid}/img/1.jpg").content == jpg
        assert c.get(f"/papers/{pid}/img/9.jpg").status_code == 404

        # 在线重做：第 1 题选对、第 2 题还不会、第 3 题（简答）先看参考答案再自评
        a = c.post(f"/api/papers/{pid}/answer", json={"pi": rows[0]["id"], "answer": "1"}).json()
        assert a["correct"] and a["left"] == 2
        a = c.post(f"/api/papers/{pid}/answer", json={"pi": rows[1]["id"], "dont_know": True}).json()
        assert a["dont_know"] and a["answer"] == "5"
        assert c.post(f"/api/papers/{pid}/answer", json={"pi": rows[2]["id"], "answer": "我觉得"}).json()["reveal"]
        assert c.post(f"/api/papers/{pid}/answer", json={"pi": rows[2]["id"], "answer": "我觉得", "self": "no"}).json()["left"] == 0
        # 改知识点
        kp = papers.candidates("math-shanghai", "G3")[5]["id"]
        assert c.post(f"/api/papers/{pid}/kp", json={"pi": rows[2]["id"], "kp_id": kp}).json()["ok"]
        assert c.post(f"/api/papers/{pid}/kp", json={"pi": rows[2]["id"], "kp_id": "PHY-IG-1.1-02"}).status_code == 400

        assert c.post(f"/api/papers/{pid}/finish").json()["ok"]
        rep = c.get(f"/papers/{pid}/report").text
        assert "要补的知识点" in rep and "可能是粗心" in rep
        assert db.one("SELECT status FROM papers WHERE id=?", pid)["status"] == "done"
        plan = c.post("/api/plan/rebuild").json()["plan"]
        assert [t for t in plan if t["type"] == "paper"][0]["done"]
        assert "第三单元测验" in c.get("/records").text

        # 别人看不到；删除
        c.get("/logout")
        c.post("/login", data={"email": "a@x.com", "password": "secret1"})
        assert c.get(f"/papers/{pid}").status_code == 404
        assert c.get(f"/papers/{pid}/img/1.jpg").status_code == 404
        c.get("/logout")
        c.post("/login", data={"email": "b@x.com", "password": "secret1"})
        c.post(f"/papers/{pid}/delete")
        assert not db.one("SELECT id FROM papers WHERE id=?", pid)
        assert not (papers.PAPER_DIR / str(pid)).exists()
        # 粘贴文字也可以
        r = c.post("/api/papers", data={"pack_id": "math-shanghai", "text": "1. 1 m = ? cm\n2. 2 + 3 = ?"})
        assert r.status_code == 200 and db.one("SELECT source FROM papers WHERE id=?", r.json()["id"])["source"] == "text"


def test_ask_tutor():
    from app import db
    with TestClient(app) as c:
        kid_b = db.one("SELECT id FROM users WHERE email='b@x.com'")["id"]
        c.post("/login", data={"email": "b@x.com", "password": "secret1"})
        assert 'id="askbtn"' in c.get("/today").text
        assert 'id="askbtn"' not in c.get("/diagnose/math-shanghai").text  # 诊断时不能问
        item = c.get("/api/practice/MATH-PRE-UNIT?n=1").json()["items"][0]
        r = c.post("/api/ask", json={"question": "这题怎么做？", "ctx": {"path": "/learn/MATH-PRE-UNIT", "title": "单位换算",
                                                                     "item_id": item["id"], "text": item["q"]}}).json()
        assert r["reply"] and r["thread_id"]
        th = db.one("SELECT * FROM ask_threads WHERE id=?", r["thread_id"])
        assert th["user_id"] == kid_b and th["item_id"] == item["id"] and th["kp_id"] == "MATH-PRE-UNIT"
        assert "题目：" in th["context"]
        r2 = c.post("/api/ask", json={"question": "还是不懂", "thread_id": r["thread_id"]}).json()
        assert r2["thread_id"] == r["thread_id"]
        assert len(c.get(f"/api/ask/{r['thread_id']}").json()["messages"]) == 4
        assert c.post("/api/ask", json={"question": " "}).status_code == 400
        assert "还是不懂" in c.get("/records").text  # 家长和孩子都能在记录里看到问过什么
        c.get("/logout")
        # 别的孩子看不到这段对话，也不能接着问
        c.post("/login", data={"email": "a@x.com", "password": "secret1"})
        assert c.get(f"/api/ask/{r['thread_id']}").status_code == 404
        r3 = c.post("/api/ask", json={"question": "hi", "thread_id": r["thread_id"]}).json()
        assert r3["thread_id"] != r["thread_id"]


def test_assistant_name(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "ASSISTANT_NAME", "小星")
    from app import main
    monkeypatch.setitem(main.templates.env.globals, "ASSISTANT", "小星")
    with TestClient(app) as c:
        c.post("/login", data={"email": "b@x.com", "password": "secret1"})
        assert "问小星" in c.get("/today").text
