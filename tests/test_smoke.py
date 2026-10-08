"""端到端冒烟测试：python -m pytest -q（使用 mock 大模型和临时数据库）

默认用临时 SQLite。要在 PostgreSQL 上跑：
  TEST_DATABASE_URL=postgresql://用户:密码@127.0.0.1/空数据库 python -m pytest -q
"""
from fastapi.testclient import TestClient

from app.main import app


def test_full_flow(monkeypatch):
    with TestClient(app) as c:
        assert c.get("/healthz").json()["kps"] > 500
        r = c.get("/login")
        assert "创建网站管理员账号" in r.text
        # 第一个账号是网站管理员：不带孩子、不做题，只进管理后台
        r = c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        assert r.url.path == "/admin"
        assert c.get("/parent").status_code == 403
        assert c.get("/today").status_code == 403
        assert "管理员" in c.post("/admin/users/create", data={"email": "p@x.com", "password": "secret1",
                                                               "name": "爸爸", "role": "parent"}).text
        c.get("/logout")
        r = c.post("/login", data={"email": "p@x.com", "password": "secret1"})
        assert "家长页" in r.text and 'href="/admin"' not in r.text
        # 姐姐：IGCSE 物理 + 剑桥英语 + 统编语文；弟弟：上海英语 + 统编语文 + 上海数学
        c.post("/parent/kids/save", data={"name": "姐姐", "email": "a@x.com", "password": "secret1", "grade": "G8",
                                          "daily_minutes": "90", "subj_physics": "phy-cambridge",
                                          "subj_english": "eng-cambridge", "subj_chinese": "chn-tongbian"})
        c.post("/parent/kids/save", data={"name": "弟弟", "email": "b@x.com", "password": "secret1", "grade": "G3",
                                          "daily_minutes": "60", "subj_english": "eng-shanghai",
                                          "subj_chinese": "chn-tongbian", "subj_math": "math-shanghai"})
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
        assert len([i for i in items if not i.get("probe")]) == 3
        # 练习里穿插一道「以前学过的」摸底题（第 2 题），记在它自己的知识点上
        assert len(items) == 4 and items[1]["probe"]["kp"] != "ENG-VOC-01", items[1]

        # 复习卡片（错题 + 术语）
        due = c.get("/api/review/due").json()["cards"]
        # 卡片明天才到期，这里直接把它们设为今天
        from app import db, engine, streak
        monkeypatch.setattr(streak, "RULE_FROM", "2000-01-01")   # 不依赖新规则的生效日期
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
        # 学习时长自动计时：心跳累计秒数（一次最多 75 秒），不再手填
        assert c.post("/api/beat", json={"s": 40}).json()["minutes"] == 1
        assert c.post("/api/beat", json={"s": 9999}).json()["minutes"] == 2
        # 连续天数按保底算：只打卡、开着页面不算，做完第 1 节才算
        # （上面做过的练习、阅读可能已自动勾掉第 1 节的部分任务，先统一设为没做，最后再还原）
        kid_id = db.one("SELECT id FROM users WHERE email='a@x.com'")["id"]
        plan = engine.today_plan(kid_id)["plan"]
        first = [plan[i] for i in streak.sections(plan)[0]["tasks"]]
        for t in first:
            engine.mark_task(kid_id, t["id"], done=False)
        assert c.post("/api/checkin", json={"reflection": "学会了量筒读数", "minutes": 40}).json()["streak"] == 0
        for t in first:
            engine.mark_task(kid_id, t["id"])
        assert engine.streak(kid_id) == 1
        for t in first:   # 还原，后面的测试要用今天的清单
            engine.mark_task(kid_id, t["id"], done=bool(t.get("done")))
        assert db.one("SELECT minutes FROM days WHERE user_id=(SELECT id FROM users WHERE email='a@x.com')")["minutes"] == 2
        assert "今天学到的" in c.get("/today").text
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
        assert "正在查看" in r.text and 'id="askbtn"' not in r.text and 'id="checkin"' not in r.text
        # 家长只能查看和管理：做题、复习、阅读、提问都不行
        assert c.get("/review").status_code == 403
        assert c.get("/reading").status_code == 403
        assert c.post("/api/answer", json={"item_id": "x", "dont_know": True}).status_code == 403
        assert c.post("/api/ask", json={"question": "hi"}).status_code == 403
        assert c.post("/api/checkin", json={"minutes": 5}).status_code == 403
        assert c.post("/api/beat", json={"s": 30}).json()["minutes"] is None  # 家长查看不计时
        for path in ["/records", "/subjects", "/progress", "/papers", "/map/math-shanghai", "/learn/MATH-PRE-UNIT"]:
            assert c.get(path).status_code == 200, path
        assert 'id="start"' not in c.get("/learn/MATH-PRE-UNIT").text


def test_accounts():
    from app import auth, db
    with TestClient(app) as c:
        # 管理员（第一个账号）登录，生成邀请码
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
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
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        q_id = auth.by_email("q@x.com")["id"]
        c.post(f"/admin/users/{q_id}/status", data={"status": "disabled"})
        r = c.post(f"/admin/users/{q_id}/reset")
        link = r.text.split('value="')[1].split('"')[0] if "/reset/" in r.text else ""
        token = [x for x in r.text.split() if "/reset/" in x][0].split("/reset/")[1].split('"')[0]
        c.get("/logout")
        assert "停用" in c.post("/login", data={"email": "q@x.com", "password": "secret2"}).text
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
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
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        r = c.get("/admin")
        assert "朋友介绍，孩子四年级" in r.text
        s_id = auth.by_email("s@x.com")["id"]
        c.post(f"/admin/users/{s_id}/status", data={"status": "active", "back": "overview"})
        for tab in ["overview", "stats", "families", "invites", "tree", "log", "system"]:
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
        # 手动打勾只给阅读（可能读的是纸质书）；单词等任务要真做完才算
        words_t = next(t for t in plan if t["type"] == "words")
        assert c.post("/api/plan/task", json={"id": words_t["id"], "done": True}).status_code == 400
        read_t = next(t for t in plan if t["type"] == "read_en")
        assert c.post("/api/plan/task", json={"id": read_t["id"], "done": True}).json()["ok"]
        assert "在书上读完了" in c.get("/today").text

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


def test_site_settings_and_insights():
    from app import db, engine, sitecfg
    from app.catalog import catalog
    with TestClient(app) as c:
        # 管理员在后台改站点设置，立即生效
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        r = c.post("/admin/settings", data={"site_name": "我家学习本", "assistant_name": "小星", "assistant_icon": "⭐",
                                            "registration": "invite", "parent_invite_limit": "2"})
        assert "已保存" in r.text and "我家学习本" in r.text
        assert sitecfg.registration() == "invite" and sitecfg.parent_invite_limit() == 2
        assert c.post("/admin/settings", data={"registration": "bad"}).status_code == 400
        st = c.get("/admin?tab=stats").text
        assert "近 14 天" in st and "弟弟" in st
        c.get("/logout")

        kid_b = db.one("SELECT id FROM users WHERE email='b@x.com'")["id"]
        # 制造一些信号：两个薄弱点共用一个前置、同一知识点两次「还不会」、同一个词查了两次
        root = next(k for k in catalog.packs["math-shanghai"].kp_ids
                    if len([s for s in catalog.successors(k) if s["pack"] == "math-shanghai"]) >= 2
                    and engine.get_mastery(kid_b).get(k, {}).get("status") != "mastered")
        for s2 in [s for s in catalog.successors(root) if s["pack"] == "math-shanghai"][:2]:
            engine.set_mastery(kid_b, s2["id"], 0.2, "weak", "test")
        c.post("/login", data={"email": "b@x.com", "password": "secret1"})
        assert "问小星" in c.get("/today").text and "我家学习本" in c.get("/today").text
        item = c.get("/api/practice/MATH-PRE-UNIT?n=1").json()["items"][0]
        for _ in range(2):
            c.post("/api/answer", json={"item_id": item["id"], "kp_id": "MATH-PRE-UNIT", "dont_know": True})
            c.post("/api/lookup", json={"q": "glacier", "context": "A glacier moves.", "lang": "en"})
        # 做题用时记下来
        c.post("/api/answer", json={"item_id": item["id"], "kp_id": "MATH-PRE-UNIT", "answer": "1", "ms": 42000})
        assert db.one("SELECT ms FROM attempts WHERE user_id=? ORDER BY id DESC", kid_b)["ms"] == 42000
        plan = c.post("/api/plan/rebuild").json()["plan"]
        kinds = {r["kind"] for r in db.q("SELECT kind FROM insights WHERE user_id=? AND resolved_at IS NULL", kid_b)}
        assert {"root", "dont_know", "lookup_repeat"} <= kinds, kinds
        assert db.one("SELECT id FROM cards WHERE user_id=? AND LOWER(front)='glacier'", kid_b)  # 自动加入单词本
        assert any(t.get("auto") and t["why"].startswith("🔍") for t in plan), plan
        today = c.get("/today").text
        assert "小星发现" in today and "根源可能在" in today
        c.get("/logout")
        # 家长页能看到系统发现
        c.post("/login", data={"email": "p@x.com", "password": "secret1"})
        assert "系统发现" in c.get("/parent").text
        assert "系统发现" in c.get(f"/parent/kids/{kid_b}").text
        c.get("/logout")
        sitecfg.set_many({"site_name": "", "assistant_name": "", "assistant_icon": "", "registration": "", "parent_invite_limit": ""})


def test_account_management():
    from app import auth, db
    with TestClient(app) as c:
        kid_b = db.one("SELECT id FROM users WHERE email='b@x.com'")["id"]
        parent = db.one("SELECT id FROM users WHERE email='p@x.com'")["id"]
        # 家长：改孩子资料、换教材（只选版本，学段跟年级走）、重置密码
        c.post("/login", data={"email": "p@x.com", "password": "secret1"})
        r = c.get(f"/parent/kids/{kid_b}/edit")
        assert "登录与安全" in r.text and "stage_" not in r.text and 'name="subj_math"' in r.text
        c.post("/parent/kids/save", data={"id": kid_b, "name": "弟弟", "email": "b2@x.com", "grade": "G4", "daily_minutes": "60",
                                          "subj_english": "eng-shanghai", "subj_math": "math-shanghai", "subj_chinese": ""})
        assert db.one("SELECT email FROM users WHERE id=?", kid_b)["email"] == "b2@x.com"
        st = {r["pack_id"]: r["stage"] for r in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", kid_b)}
        assert st == {"eng-shanghai": "G4", "math-shanghai": "G4"}, st
        r = c.post(f"/parent/kids/{kid_b}/account", data={"action": "password", "password": ""})
        temp = r.text.split("新密码：<b")[1].split(">")[1].split("<")[0]
        assert len(temp) == 9 and "只显示这一次" in r.text
        r = c.post(f"/parent/kids/{kid_b}/account", data={"action": "password", "password": "kidpass1"})
        assert "新密码就是你刚才输入的" in r.text and "只显示这一次" not in r.text
        other = db.insert("INSERT INTO users(email,pw_hash,name,role,status,created_at) VALUES('z@x.com','x','z','kid','active',?)", db.now())
        assert c.post(f"/parent/kids/{other}/account", data={"action": "unlock"}).status_code == 404  # 不是自己的孩子
        c.get("/logout")
        assert c.post("/login", data={"email": "b2@x.com", "password": "kidpass1"}).url.path == "/today"
        c.get("/logout")
        # 管理员：账号详情页，改家长邮箱、给临时密码（对方登录后必须先改密码）、解锁、退出所有设备
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        r = c.get(f"/admin/users/{parent}")
        assert "登录与安全" in r.text and "最近的账号记录" in r.text
        assert f'/admin/users/{parent}' in c.get("/admin?tab=families").text
        c.post(f"/admin/users/{parent}/profile", data={"name": "爸爸", "email": "p2@x.com"})
        assert auth.get_user(parent)["email"] == "p2@x.com"
        db.run("UPDATE users SET locked_until=? WHERE id=?", "2999-01-01", parent)
        assert "解除锁定" in c.get(f"/admin/users/{parent}").text
        c.post(f"/admin/users/{parent}/account", data={"action": "unlock"})
        assert auth.get_user(parent)["locked_until"] is None
        r = c.post(f"/admin/users/{parent}/account", data={"action": "password", "password": "", "force": "1"})
        temp = r.text.split("新密码：<b")[1].split(">")[1].split("<")[0]
        assert auth.get_user(parent)["must_change_pw"] == 1
        c.get("/logout")
        r = c.post("/login", data={"email": "p2@x.com", "password": temp})
        assert r.url.path == "/settings" and "临时密码" in r.text
        assert c.get("/parent").url.path == "/settings"
        c.post("/password", data={"old": temp, "new": "secret1"})
        assert c.get("/parent").url.path == "/parent" and auth.get_user(parent)["must_change_pw"] == 0
        c.get("/logout")
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        assert "退出登录" in c.post(f"/admin/users/{parent}/account", data={"action": "signout"}).text
        assert not auth.sessions_of(parent)
        assert c.get(f"/admin/users/{db.one('SELECT id FROM users WHERE email=?', 'admin@x.com')['id']}").url.path == "/settings"
        c.get("/logout")


def test_explore_warmup_and_selection(monkeypatch):
    from app import db, explore, webpage
    with TestClient(app) as c:
        kid = db.one("SELECT id FROM users WHERE email='a@x.com'")["id"]
        c.post("/login", data={"email": "a@x.com", "password": "secret1"})
        # 每天的清单最前面有「热身」：混着以前学过的知识点和旧单词
        plan = c.post("/api/plan/rebuild").json()["plan"]
        todo = [t for t in plan if not t["done"]]  # 今天已经做完的旧任务会留在清单最前面
        assert any(t["type"] == "warmup" for t in todo[:2]), plan
        cov0 = {x["pack"].id: x["known"] for x in explore.coverage(kid)}
        items = c.get("/api/warmup").json()["items"]
        words = [i for i in items if i.get("word")]
        kps = [i for i in items if i.get("probe")]
        assert words and kps and "answer" not in kps[0]
        # 旧单词：不认识 → 自动进单词复习；估算词汇量
        w = words[0]
        r = c.post("/api/warmup/word", json={"word": w["word"]["w"], "list": w["word"]["list"], "dont_know": True}).json()
        assert not r["correct"] and r["added"]
        assert db.one("SELECT id FROM cards WHERE user_id=? AND front=?", kid, w["word"]["w"])
        for x in explore.pick_words(kid, 12):
            c.post("/api/warmup/word", json={"word": x["word"]["w"], "list": x["word"]["list"], "choice": x["options"][0]})
        assert explore.word_stats(kid)["estimate"]
        # 旧知识点答错：沿必须前置往回追（排进队列）；答对：点亮 + 推断前置
        p = kps[0]
        r = c.post("/api/answer", json={"item_id": p["id"], "kp_id": p["probe"]["kp"], "mode": "probe", "dont_know": True,
                                        "probe": {"depth": 0}}).json()
        assert r["dont_know"]
        from app.catalog import catalog
        reqs = catalog.prereqs(p["probe"]["kp"], required_only=True)
        if reqs:
            assert db.one("SELECT id FROM probes WHERE user_id=? AND status='queued'", kid)
        m = explore.engine.get_mastery(kid)
        unk = lambda k: m.get(k, {}).get("status") in (None, "unknown")  # noqa: E731
        rng = [k for e in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", kid) for k in explore.past_range(kid, e)]
        target = next(k for k in rng if unk(k) and any(unk(p["id"]) for p, _ in catalog.ancestors(k, depth=2)))
        tpack = catalog.kps[target]["pack"]
        it = explore.engine.items_for(kid, target, n=1, purpose="diagnose", grade="G8")[0]
        good = it["answer"] if it["type"] == "mcq" else (it["answer"][0] if isinstance(it["answer"], list) else it["answer"])
        r = c.post("/api/answer", json={"item_id": it["id"], "kp_id": target, "mode": "probe", "answer": good, "probe": {}}).json()
        assert r["correct"] and r["probe"]["inferred"] >= 1
        # 摸底答对一题只是一条证据：概率升高，但要隔天换题再对才算掌握（不点亮）
        mrow = db.one("SELECT status, score FROM mastery WHERE user_id=? AND kp_id=?", kid, target)
        assert mrow["status"] == "learning" and mrow["score"] > 0.6 and not r.get("lit")
        cov1 = {x["pack"].id: x["known"] for x in explore.coverage(kid)}
        assert cov1[tpack] > cov0.get(tpack, 0)
        today = c.get("/today").text
        assert "我的学习地图" in today and "前方" in today and "核心英语词" in today
        assert "data-kid" in today and 'id="qlbtn"' in today
        # 知识背景（mock AI）
        assert c.get(f"/api/context/{target}").json()["story"]
        # 划词：查第二次 / 做题时查 → 自动进复习；翻译；手动加入
        from app import llm
        monkeypatch.setattr(llm, "lookup", lambda q, ctx, lang, grade, user_id=None: {"word": q, "meaning": "意思：" + q})
        assert not c.post("/api/lookup", json={"q": "glimmer", "lang": "en", "auto": True}).json()["auto_added"]
        r = c.post("/api/lookup", json={"q": "glimmer", "lang": "en", "auto": True}).json()
        assert r["auto_added"] and r["saved"]
        r = c.post("/api/lookup", json={"q": "drizzle", "lang": "en", "auto": True, "item_id": it["id"]}).json()
        assert r["auto_added"] == "做题时查的"
        r = c.post("/api/translate", json={"text": "The ball rolls down the slope because of gravity."}).json()
        assert r["meaning"] and r["auto_added"] == ""
        r = c.post("/api/collect", json={"text": "net force", "context": "x"}).json()
        assert r["id"] and db.one("SELECT kind FROM cards WHERE id=?", r["id"])["kind"] == "phrase"
        # 贴链接读网页：内网地址拒绝；正常网页抽正文放进阅读器
        r = c.post("/reading/url", data={"url": "http://127.0.0.1/admin"})
        assert r.status_code == 400 and "内网" in r.text
        html = "<html><head><title>Bees | News</title></head><body><nav>Home</nav><article><h1>Bees</h1>" + \
               "".join(f"<p>Bees visit many flowers every day and help plants grow fruit number {i}.</p>" for i in range(5)) + "</article></body></html>"
        monkeypatch.setattr(webpage, "fetch", lambda u: ("https://example.org/bees", html))
        r = c.post("/reading/url", data={"url": "https://example.org/bees"})
        assert r.url.path.startswith("/reading/") and "Bees visit" in r.text and "example.org" in r.text and "Home" not in r.text
        c.get("/logout")
        # 家长页能看到摸清了多少
        c.post("/login", data={"email": "p2@x.com", "password": "secret1"})
        assert "以前学过的内容已摸清" in c.get("/parent").text
        c.get("/logout")


def test_self_records():
    """跟自己比：PB、「上周的我」、专注、每周进步卡。正确率不到 80% 不算纪录。"""
    from datetime import timedelta

    from app import db, records
    with TestClient(app) as c:
        kid = db.one("SELECT id FROM users WHERE email='b2@x.com'")["id"]
        c.post("/login", data={"email": "b2@x.com", "password": "kidpass1"})
        r = c.get("/api/records/words").json()
        assert r["pb"]["runs"] == 0 and r["ghost"] is None
        assert c.get("/api/records/nope").status_code == 404
        # 上周的我：上周做了两组单词复习，每道答对的题 4 秒
        today = db.today()
        lastwk = today - timedelta(days=today.weekday() + 3)
        for d in (lastwk, today - timedelta(days=1)):
            db.insert("INSERT INTO runs(user_id,kind,day,n_items,n_right,ms_active,ms_total,best_combo,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", kid, "words", d.isoformat(), 10, 10, 40000, 50000, 4, db.now())
        db.run("INSERT INTO days(user_id,day,plan,minutes) VALUES(?,?,?,?)", kid, lastwk.isoformat(), "[]", 20)
        r = c.get("/api/records/words").json()
        assert r["ghost"] == 4000 and r["pb"]["right"] == 10 and r["pb"]["speed"] == 4000
        # 第一次做阅读：只立纪录，不算破纪录
        assert c.post("/api/run", json={"kind": "read", "n_items": 3, "n_right": 3, "ms_active": 400000}).json()["first"]
        # 乱答得快：正确率不到 80%，不算速度和答对纪录
        r = c.post("/api/run", json={"kind": "words", "n_items": 20, "n_right": 12, "ms_active": 12000, "best_combo": 2}).json()
        assert not r["pbs"]
        # 认真又快：破答对、连对、速度三项
        r = c.post("/api/run", json={"kind": "words", "n_items": 12, "n_right": 12, "ms_active": 36000, "ms_total": 1,
                                     "best_combo": 12}).json()
        assert {p["key"] for p in r["pbs"]} == {"right", "combo", "speed"}, r
        assert r["pb"]["speed"] == 3000
        # 专注 6 分钟以上：破最长专注（阅读那次是 6 分 40 秒）
        r = c.post("/api/run", json={"kind": "practice", "n_items": 3, "n_right": 1, "ms_active": 500000}).json()
        assert [p["key"] for p in r["pbs"]] == ["focus"]
        # 乱填的数字会被收住
        r = c.post("/api/run", json={"kind": "hack", "n_items": 5, "n_right": 99, "ms_active": -5}).json()
        row = db.one("SELECT * FROM runs WHERE user_id=? ORDER BY id DESC", kid)
        assert row["kind"] == "practice" and row["n_right"] == 5 and row["ms_active"] == 0
        # 每周进步卡和「我的纪录」
        wk = records.weekly(kid)
        assert wk and any("学习了" in x for x in wk["lines"])
        page = c.get("/today").text
        assert "上周进步卡" in page and "我的纪录" in page and "每题最快 3.0 秒" in page
        for path in ("/warmup", "/review?group=words"):
            assert c.get(path).status_code == 200
        # 家长只能看，不能替孩子记成绩；家长页能看到进步卡
        c.get("/logout")
        c.post("/login", data={"email": "p2@x.com", "password": "secret1"})
        c.get(f"/parent/as/{kid}")
        assert c.post("/api/run", json={"kind": "words", "n_items": 1, "n_right": 1}).status_code == 403
        assert "上周进步" in c.get("/parent").text


def test_curricula_layers_tracks_and_bridges():
    """教材分层：学段配置、教材方向、学校模板、跨教材关联（同一概念互认、跨学科背景、阅读话题）。"""
    from app import db, engine
    from app.catalog import catalog, stage_rank
    assert not catalog.errors, catalog.errors
    assert stage_rank("MYP3") == 8 and stage_rank("DP1") == 11  # IB 学段在同一根学年轴上
    eng = catalog.packs["eng-cambridge"]
    assert eng.track("") == "0511" and eng.track("bogus") == "0511"
    ids_0511, ids_0500 = set(catalog.ids_for("eng-cambridge", "0511")), set(catalog.ids_for("eng-cambridge", "0500"))
    assert "ENG-REA-12" in ids_0511 - ids_0500 and "ENG-WRI-22" in ids_0500 - ids_0511
    assert len(catalog.ids_for("phy-cambridge", "core")) < len(catalog.ids_for("phy-cambridge", "extended"))
    # 关联层：各领域的关联文件都加载了，物理英文术语能连到中文的同一内容
    assert len(catalog.concepts) >= 200 and {"uses", "language", "context"} <= {ln["type"] for ln in catalog.links}
    assert any(ln["type"] == "language" and ln["from"].startswith("PHY-IG") for ln in catalog.links)
    assert catalog.packs["hist-shanghai"].subject == "history" and catalog.preset("sh-public-middle")["packs"]["chemistry"] == "chem-shanghai"
    with TestClient(app) as c:
        c.post("/login", data={"email": "p2@x.com", "password": "secret1"})
        kid = db.one("SELECT id FROM users WHERE email='a@x.com'")
        page = c.get(f"/parent/kids/{kid['id']}/edit").text
        assert "学校类型" in page and "国际学校 · 剑桥 IGCSE 路线" in page and 'name="track_eng-cambridge"' in page
        # 选国际学校模板 + 英语 0500 方向
        form = {"id": str(kid["id"]), "name": "姐姐", "email": "a@x.com", "grade": "G8", "daily_minutes": "90",
                "preset": "intl-cambridge", "subj_english": "eng-cambridge", "track_eng-cambridge": "0500",
                "subj_physics": "phy-cambridge", "track_phy-cambridge": "core", "subj_chinese": "chn-tongbian"}
        c.post("/parent/kids/save", data=form)
        u = db.one("SELECT preset, school_type FROM users WHERE id=?", kid["id"])
        assert u["preset"] == "intl-cambridge" and u["school_type"] == "international"
        assert engine.enroll_track(kid["id"], "eng-cambridge") == "0500"
        assert engine.enroll_track(kid["id"], "phy-cambridge") == "core"
        assert engine.pack_summary(kid["id"], "phy-cambridge")["total"] == len(catalog.ids_for("phy-cambridge", "core"))
        # 0500 方向的地图里有定向写作、没有 ESL 的笔记补全
        c.get(f"/parent/as/{kid['id']}")
        m = c.get("/map/eng-cambridge").text
        assert catalog.kp("ENG-WRI-22")["name"] in m and catalog.kp("ENG-REA-12")["name"] not in m
        form["track_eng-cambridge"] = "0511"
        c.post("/parent/kids/save", data=form)
        c.get("/logout")
        # 同一概念互认：学会沪科版「密度」→ 剑桥物理的密度推断为「学习中」
        c.post("/login", data={"email": "a@x.com", "password": "secret1"})
        assert not db.one("SELECT 1 AS x FROM mastery WHERE user_id=? AND kp_id='PHY-IG-1.4-01' AND status!='unknown'", kid["id"])
        engine.record_attempt(kid["id"], None, "PSH-MECH-14", "probe", True)
        mrow = db.one("SELECT status, source FROM mastery WHERE user_id=? AND kp_id='PHY-IG-1.4-01'", kid["id"])
        assert mrow["status"] in ("learning", "mastered")
        # 学习页显示跨学科联系，讲解提示里带上孩子已学的相关内容
        page = c.get("/learn/PHY-IG-1.4-01").text
        assert "和别的学科连起来" in page and catalog.kp("PSH-MECH-14")["name"] in page
        # 讲解存进题库大家共用；孩子已学的相关内容现场拼在前面
        t = c.get("/api/teach/PHY-IG-1.4-01").json()
        assert t["steps"] and any(b["name"] == catalog.kp("PSH-MECH-14")["name"] for b in t["bridge"])
        # 英语阅读可以选「别的课学过的」话题
        assert any(t["kp"]["id"] in ("PSH-MECH-14", "PHY-IG-1.4-01") for t in engine.cross_topics(kid["id"], "en"))
        assert "课学过的内容" in c.get("/reading").text


def test_content_bank():
    """题库沉淀：生成的题、讲解、背景、短文都存下来，第二次直接用库里的；做题统计；标记有问题；后台题库页和导出。"""
    from app import bank, db
    calls = []
    from app.llm import tasks
    real = tasks.ask_json

    def spy(task, *a, **kw):
        calls.append(task)
        out = real(task, *a, **kw)
        if task == "passage":  # 模拟模型每次写的短文都不一样
            out = {**out, "body": out["body"] + f" ({len(calls)})"}
        return out
    tasks.ask_json = spy
    try:
        with TestClient(app) as c:
            kp = "PHY-IG-1.3-01"
            c.post("/login", data={"email": "a@x.com", "password": "secret1"})
            items = c.get(f"/api/practice/{kp}?n=3").json()["items"]
            items = [i for i in items if not i.get("probe")]  # 去掉穿插的旧知识点题
            assert items and "items" in calls
            row = db.one("SELECT * FROM items WHERE id=?", items[0]["id"])
            assert row["lang"] == "en" and row["grade"] == "G8" and row["purpose"] and row["qhash"]
            assert db.jload(row["gen_meta"])["prompt_v"] >= 1
            assert db.one("SELECT 1 AS x FROM item_kps WHERE item_id=? AND kp_id=?", items[0]["id"], kp)
            # 作答更新题目统计
            c.post("/api/answer", json={"item_id": items[0]["id"], "kp_id": kp, "answer": "zzz", "ms": 4000})
            row2 = db.one("SELECT * FROM items WHERE id=?", items[0]["id"])
            assert row2["n_attempts"] == row["n_attempts"] + 1 and row2["total_ms"] == row["total_ms"] + 4000
            # 讲解、背景：第二次不再调用 AI
            for path in (f"/api/teach/{kp}", f"/api/context/{kp}"):
                a1, a2 = c.get(path).json(), c.get(path).json()
                assert a1["content_id"] == a2["content_id"]
            assert calls.count("teach") == 1 and calls.count("context") == 1
            # 阅读短文：存下来，同话题换个孩子直接用
            c.post("/reading/new", data={"mode": "ai", "lang": "en", "topic": "seeds"})
            assert calls.count("passage") == 1
            cid = db.one("SELECT content_id FROM readings WHERE user_id=(SELECT id FROM users WHERE email='a@x.com') ORDER BY id DESC LIMIT 1")["content_id"]
            assert cid and db.one("SELECT kind FROM contents WHERE id=?", cid)["kind"] == "passage"
            c.get("/logout")

            # 另一个孩子（同年级）做同一知识点：直接用库里的题，不再出题
            c.post("/login", data={"email": "p2@x.com", "password": "secret1"})
            c.post("/parent/kids/save", data={"name": "同学", "email": "c@x.com", "password": "secret1", "grade": "G8",
                                              "daily_minutes": "60", "subj_physics": "phy-cambridge", "subj_english": "eng-cambridge"})
            c.get("/logout")
            c.post("/login", data={"email": "c@x.com", "password": "secret1"})
            n_items = calls.count("items")
            from app import engine
            again = engine.items_for(db.one("SELECT id FROM users WHERE email='c@x.com'")["id"], kp, n=3, grade="G8")
            assert {i["id"] for i in again} & {i["id"] for i in items} and calls.count("items") == n_items
            c.post("/reading/new", data={"mode": "ai", "lang": "en", "topic": "seeds"})
            assert calls.count("passage") == 1
            assert db.one("SELECT uses FROM contents WHERE id=?", cid)["uses"] >= 2
            # 标记有问题：这个孩子不再看到；第二个人标记后暂停使用
            bad = items[0]["id"]
            assert c.post("/api/flag", json={"id": bad, "reason": "wrong"}).json()["status"] == "active"
            assert c.post("/api/flag", json={"id": bad, "reason": "nope"}).status_code == 400
            assert bad not in {i["id"] for i in c.get(f"/api/practice/{kp}?n=5").json()["items"]}
            c.get("/logout")
            c.post("/login", data={"email": "a@x.com", "password": "secret1"})
            assert c.post("/api/flag", json={"id": bad, "reason": "unclear"}).json()["status"] == "review"
            c.get("/logout")

            # 管理后台：题库页能看到被标记的题，可以恢复；导出不含试卷原题
            c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
            page = c.get("/admin?tab=bank").text
            assert "被标记有问题" in page and "答案好像不对" in page and "各教材的题目覆盖" in page
            c.post(f"/admin/bank/item/{bad}/status", data={"status": "active"})
            assert db.one("SELECT status, n_flags FROM items WHERE id=?", bad) == {"status": "active", "n_flags": 0}
            dump = c.get("/admin/bank/export.json").json()
            assert any(i["id"] == bad for i in dump["items"]) and not any(i["source"] == "paper" for i in dump["items"])
            assert {x["kind"] for x in dump["contents"]} >= {"teach", "context", "passage"}
            c.get("/logout")
    finally:
        tasks.ask_json = real
    # 去重：同样的题再存一次不会多出一条
    n = db.one("SELECT COUNT(*) AS n FROM items")["n"]
    full = bank.row_to_item(db.one("SELECT * FROM items WHERE id=?", items[0]["id"]))
    assert bank.save_items("PHY-IG-1.3-01", [full])[0]["id"] == full["id"]
    assert db.one("SELECT COUNT(*) AS n FROM items")["n"] == n


def test_evidence_model():
    """掌握判定：BKT + 遗忘模型 + 交叉验证（换题、隔天、换题型）。"""
    from datetime import timedelta
    from app import db, engine, evidence, explore
    kid = db.one("SELECT id FROM users WHERE email='a@x.com'")["id"]
    kp = "PHY-IG-2.1-01"
    mcq = lambda i: {"id": f"T-M{i}", "type": "mcq", "options": ["a", "b", "c", "d"]}  # noqa: E731
    fill = lambda i: {"id": f"T-F{i}", "type": "fill"}  # noqa: E731
    row = lambda: db.one("SELECT * FROM mastery WHERE user_id=? AND kp_id=?", kid, kp)  # noqa: E731
    # 一道四选一答对：概率只升一点，不算掌握
    assert engine.update_mastery(kid, kp, True, item=mcq(1)) == "learning"
    p1 = row()["score"]
    assert p1 < 0.75
    # 同一天再答对两道不同题型：概率很高，但没有隔天的证据，还不算掌握
    engine.update_mastery(kid, kp, True, item=fill(1))
    assert engine.update_mastery(kid, kp, True, item=fill(2)) == "learning" and row()["score"] >= 0.85
    assert "隔天再答对一次" in evidence.missing(kid, dict(row()))
    # 当天反复答对，记忆稳定性几乎不涨（间隔效应）
    s_same_day = row()["stability"]
    assert s_same_day < 4
    # 模拟两天后：还记得的概率下降；这时答对 → 掌握，稳定性明显变大
    ev = db.jload(row()["evidence"])
    ev["days"] = [(db.today() - timedelta(days=2)).isoformat()]
    two_days_ago = (db.today() - timedelta(days=2)).isoformat() + "T08:00:00"
    db.run("UPDATE mastery SET last_ev=?, evidence=? WHERE user_id=? AND kp_id=?", two_days_ago, db.jdump(ev), kid, kp)
    assert evidence.profile(kid).memory.recall(2, s_same_day) < 1
    assert engine.update_mastery(kid, kp, True, item=fill(3)) == "mastered"
    assert row()["stability"] > s_same_day * 1.5
    # 掌握后粗心错一次：概率下降，但不会直接变「薄弱」
    assert engine.update_mastery(kid, kp, False, item=fill(4)) != "weak"
    # 不同的题错两次、概率很低，才算薄弱
    kp2 = "PHY-IG-2.1-02"
    engine.update_mastery(kid, kp2, False, item=fill(5))
    assert db.one("SELECT status FROM mastery WHERE user_id=? AND kp_id=?", kid, kp2)["status"] == "learning"
    assert engine.update_mastery(kid, kp2, False, item=fill(6)) == "weak"
    # 遗忘模型：学会过、隔了很久的，排进复查
    db.run("UPDATE mastery SET last_ev=? WHERE user_id=? AND kp_id=?", "2020-01-01T08:00:00", kid, kp)
    assert any(m["kp_id"] == kp for m in evidence.due_checks(kid, engine.get_mastery(kid), limit=50))
    # 复习卡片：按稳定性排期，记得的越久间隔越长
    cid = engine.add_card(kid, "word", "evidence-test", "测试")
    r1 = engine.review_card(kid, cid, "good")
    db.run("UPDATE cards SET last_review=? WHERE id=?", (db.today() - timedelta(days=3)).isoformat() + "T08:00:00", cid)
    r2 = engine.review_card(kid, cid, "good")
    assert r2["due"] > r1["due"]
    # 单词：几天前四选一认出来的词，换成「看中文写英文」复查；写不出来，就不算认识
    from app.content import content
    lst = explore.word_lists(kid)[0]
    w = lst["words"][0]
    old = (db.today() - timedelta(days=5)).isoformat() + "T08:00:00"
    db.run("INSERT INTO probes(user_id,kind,kp_id,ref,reason,status,result,created_at,done_at) VALUES(?,'word',?,?,?,'done',1,?,?)",
           kid, lst["id"], w["w"], lst["title"], old, old)
    known0 = next(x for x in explore.word_stats(kid)["lists"] if x["id"] == lst["id"])["known"]
    picked = explore.pick_words(kid, 2)
    rc = [x for x in picked if x["word"].get("recheck")]
    assert rc and rc[0]["type"] == "fill" and content.word_lists[lst["id"]]
    res = explore.answer_word(kid, rc[0]["word"]["w"], rc[0]["word"]["list"], "zzz", recheck=True)
    assert not res["correct"] and res["answer"] == rc[0]["word"]["w"]
    known1 = next(x for x in explore.word_stats(kid)["lists"] if x["id"] == rc[0]["word"]["list"])["known"]
    assert known1 < known0 or rc[0]["word"]["list"] != lst["id"]


def test_event_log_and_methods():
    """学习事件表是唯一的原始记录：掌握状态能按事件重放；换学习方式会按新方式重算，计划也跟着变。"""
    from app import db, engine, evidence, plan
    from app.methods import methods
    from app.main import set_method
    kid = db.one("SELECT id FROM users WHERE email='a@x.com'")["id"]
    n_ev = db.one("SELECT COUNT(*) AS n FROM events WHERE user_id=?", kid)["n"]
    assert n_ev > 0
    kinds = {r["target"] for r in db.q("SELECT DISTINCT target FROM events WHERE user_id=?", kid)}
    assert {"kp", "card", "word"} <= kinds  # 做题、复习卡片、单词摸底都在事件表里
    # 重放：和实时更新算出来的一样（同一个方式）
    before = {k: (m["status"], round(m["score"], 3)) for k, m in engine.get_mastery(kid).items()}
    db.run("UPDATE mastery SET model='' WHERE user_id=?", kid)
    assert evidence.ensure_current() >= 1
    after = {k: (m["status"], round(m["score"], 3)) for k, m in engine.get_mastery(kid).items()}
    kp2 = "PHY-IG-2.1-02"
    assert after[kp2] == before[kp2]
    assert all(m["model"] == evidence.profile(kid).key for m in engine.get_mastery(kid).values())
    # 推断（没有作答）不会直接变成「掌握」
    assert engine.set_mastery(kid, "PHY-IG-2.1-03", 0.95, "mastered", "import") == "learning"
    # 换成「先学牢再往前」：掌握标准更严，按全部记录重算；计划里不再有预习
    assert methods.get("mastery").mastery.master_p > methods.get("balanced").mastery.master_p
    old_key = evidence.profile(kid).key
    assert set_method(kid, "mastery")
    assert evidence.profile(kid).id == "mastery" and evidence.profile(kid).key != old_key
    assert all(m["model"] == evidence.profile(kid).key for m in engine.get_mastery(kid).values())
    assert not [t for t in plan.build_plan(kid) if t["type"] == "preview"]
    assert set_method(kid, "balanced")
    assert not set_method(kid, "no-such-method")


def test_adult_exam_course():
    """自学者（成人备考基金从业）：自己注册、自己选课和定考试日期；没有乐园、不先摸底；
    按剩余天数排新考点，最后两周冲刺高频考点。家长给家人开的成人账号也能在表单里直接填考试日期。"""
    from datetime import timedelta

    from app import db, engine
    from app.catalog import catalog
    with TestClient(app) as c:
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        r = c.post("/admin/invites/create", data={"note": "自学", "max_uses": "1", "days": "7"})
        code = r.text.split("新邀请码：")[1][:14]
        c.get("/logout")

        # 注册时选「自己学」：没有家长，默认成人，先去「我的课程」选课
        r = c.post("/register", data={"email": "mom@x.com", "password": "secret1", "name": "妈妈", "invite": code, "who": "self"})
        assert r.url.path == "/me/courses" and "我的课程" in r.text and 'name="email"' not in r.text and "game_minutes" not in r.text
        mom = db.one("SELECT * FROM users WHERE email='mom@x.com'")
        assert mom["role"] == "kid" and mom["parent_id"] is None and mom["grade"] == "ADULT"
        assert c.get("/today").url.path == "/me/courses"  # 还没选课
        assert c.get("/parent").status_code == 403
        assert c.post("/me/courses", data={"name": "妈妈", "grade": "ADULT", "subj_fund_law": "fund-law",
                                           "exam_fund-law": "2026-02-30"}).status_code == 400
        r = c.post("/me/courses", data={"name": "妈妈", "grade": "ADULT", "daily_minutes": "45", "subj_fund_law": "fund-law",
                                        "exam_fund-law": ""})
        assert r.url.path == "/today"
        assert db.one("SELECT stage FROM enrollments WHERE user_id=?", mom["id"])["stage"] == "FUND-1"
        assert c.post("/settings/profile", data={"name": "妈妈", "email": "mom@x.com"}).url.path == "/settings"
        assert "我的课程" in c.get("/settings").text
        c.get("/logout")

        # 管理后台：自学者单独列出
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        page = c.get("/admin?tab=families").text
        assert "自学者" in page and "mom@x.com" in page
        assert c.get("/admin?tab=tree").status_code == 200 and "自学者" in c.get(f"/admin/users/{mom['id']}").text
        # 家长给家人开的成人账号：考证课的考试日期直接在表单里填
        c.post("/admin/users/create", data={"email": "fp@x.com", "password": "secret1", "name": "家长", "role": "parent"})
        c.get("/logout")
        c.post("/login", data={"email": "fp@x.com", "password": "secret1"})
        assert 'name="exam_fund-law"' in c.get("/parent/kids/new").text
        day = (db.today() + timedelta(days=90)).isoformat()
        c.post("/parent/kids/save", data={"name": "爸爸", "email": "dad@x.com", "password": "secret1", "grade": "ADULT",
                                          "subj_fund_law": "fund-law", "exam_fund-law": day})
        dad = db.one("SELECT id FROM users WHERE email='dad@x.com'")["id"]
        assert db.one("SELECT exam_date FROM enrollments WHERE user_id=?", dad)["exam_date"] == day
        c.get("/logout")

        c.post("/login", data={"email": "mom@x.com", "password": "secret1"})
        page = c.get("/today").text
        assert 'href="/arena"' not in page and "学习进度和考试日期" in page
        plan = c.post("/api/plan/rebuild").json()["plan"]
        types = [t["type"] for t in plan]
        assert plan[0]["title"].startswith("设定考试日期") and "diagnose" not in types and "exam" not in types, types
        assert "考试日期" in c.get("/progress").text
        assert c.post("/progress/fund-law", data={"exam_date": "2026-13-40"}).status_code == 400

        # 离考试 60 天：103 个考点要在 46 天里学完 → 每天 3 个，按大纲顺序
        day = (db.today() + timedelta(days=60)).isoformat()
        c.post("/progress/fund-law", data={"exam_date": day})
        plan = c.post("/api/plan/rebuild").json()["plan"]
        exam = [t for t in plan if t["type"] == "exam"]
        assert [t["kp"] for t in exam] == catalog.ids_for("fund-law")[:3], plan
        assert exam[0]["title"].startswith("按考期学") and "?task=preview" in exam[0]["url"]
        assert all(t["done"] for t in plan if t["title"].startswith("设定考试日期"))  # 设好日期就自动打勾
        assert "还有 <b>60</b> 天" in c.get("/today").text
        assert c.get(exam[0]["url"]).status_code == 200
        assert c.get(f"/api/practice/{exam[0]['kp']}?n=3&purpose=preview").status_code == 200

        # 离考试一周：不再学新内容，冲刺还没掌握的高频考点（大纲要求「掌握」的）
        c.post("/progress/fund-law", data={"exam_date": (db.today() + timedelta(days=7)).isoformat()})
        exam = [t for t in c.post("/api/plan/rebuild").json()["plan"] if t["type"] == "exam"]
        assert exam and all(t["title"].startswith("考前过一遍") and catalog.kps[t["kp"]]["hot"] for t in exam)
        e = db.one("SELECT * FROM enrollments WHERE user_id=? AND pack_id='fund-law'", mom["id"])
        assert engine.exam_view(mom["id"], e)["phase"] == "sprint"


def test_bank_papers_admin_import_and_mock():
    """公共卷库：只有管理员能导入；草稿不出给学习者；逐题核对后发布，练习时真题优先并标出来源；
    学习者做整卷模拟考（复用试卷流程），卷库的题不随题库导出。"""
    from app import bank, bankpapers, db, engine
    with TestClient(app) as c:
        kid = db.one("SELECT * FROM users WHERE email='mom@x.com'")
        e = db.one("SELECT * FROM enrollments WHERE user_id=? AND pack_id='fund-law'", kid["id"])
        c.post("/login", data={"email": "mom@x.com", "password": "secret1"})
        assert c.post("/admin/bank-papers", data={"pack_id": "fund-law", "text": "x" * 20}).status_code == 403
        assert c.post("/mocks/1/start").status_code == 404
        c.get("/logout")

        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        assert "导入一份卷子" in c.get("/admin?tab=papers").text
        assert c.post("/admin/bank-papers", data={"pack_id": "fund-law"}).status_code == 400
        jpg = b"\xff\xd8\xff\xe0" + b"0" * 100
        r = c.post("/admin/bank-papers", data={"pack_id": "fund-law", "stage": e["stage"], "kind": "school", "title": "期末卷",
                                               "year": "2024", "region": "上海徐汇", "org": "某某小学", "exam": "期末", "minutes": "60"},
                   files=[("photos", ("p1.jpg", jpg, "image/jpeg"))])
        assert r.status_code == 200, r.text
        bp = r.json()["id"]
        rows = bankpapers.rows(bp)
        assert len(rows) == 3 and all(r["item"]["source"] == "bank" for r in rows)
        assert db.one("SELECT status FROM items WHERE id=?", rows[0]["item_id"])["status"] == "draft"
        assert c.get(f"/admin/bank-papers/{bp}/img/1.jpg").content == jpg
        page = c.get(f"/admin/bank-papers/{bp}").text
        assert "待核对" in page and "2024 上海徐汇 某某小学 期末 · 名校卷" in page
        kp = rows[0]["item"]["kp_id"]
        assert kp
        # 草稿：练习里不出现
        assert all(r["id"] not in {x["item_id"] for x in rows} for r in bank.candidates(kid["id"], kp))

        # 逐题核对：改答案（选择题写字母）、删掉认错的题；答案不合格会退成简答题并提示
        c.post(f"/admin/bank-papers/{bp}/items/{rows[0]['id']}", data={
            "label": "1", "points": "3", "type": "mcq", "q": "1 m = ? cm", "options": "10\n100\n1000\n0.1",
            "answer": "b", "explain": "1 米是 100 厘米", "kp_id": kp})
        it = bank.row_to_item(db.one("SELECT * FROM items WHERE id=?", rows[0]["item_id"]))
        assert it["answer"] == 1 and it["q"] == "1 m = ? cm" and it["type"] == "mcq"
        r = c.post(f"/admin/bank-papers/{bp}/items/{rows[1]['id']}", data={"type": "mcq", "q": "2+3", "options": "4\n5", "answer": "Z"})
        assert "简答题" in r.text
        c.post(f"/admin/bank-papers/{bp}/items/{rows[2]['id']}", data={"delete": "1"})
        assert len(bankpapers.rows(bp)) == 2 and not db.one("SELECT 1 AS ok FROM items WHERE id=?", rows[2]["item_id"])

        # 发布：进公共题库，带来源标签；下架 / 删除的规则
        assert "已发布" in c.post(f"/admin/bank-papers/{bp}/status", data={"action": "publish"}).text
        it = bank.row_to_item(db.one("SELECT * FROM items WHERE id=?", rows[0]["item_id"]))
        assert it["src"] == "2024 上海徐汇 某某小学 期末 · 名校卷"
        assert "不能删" in c.post(f"/admin/bank-papers/{bp}/status", data={"action": "delete"}).text
        assert all(x["source"] != "bank" for x in bank.export()["items"])
        c.get("/logout")

        # 学习者：练习时卷库的题优先；整卷模拟考
        c.post("/login", data={"email": "mom@x.com", "password": "secret1"})
        got = engine.items_for(kid["id"], kp, n=1)
        assert got[0]["id"] == rows[0]["item_id"] and got[0]["src"]
        plan = c.post("/api/plan/rebuild").json()["plan"]  # 离考试一周：冲刺期排一套整卷模拟考
        assert any(t["title"] == "基金法律法规整卷模拟考" and t["url"] == "/mocks?pack=fund-law" for t in plan), plan
        page = c.get("/mocks").text
        assert "期末卷" in page and "名校卷" in page
        r = c.post(f"/mocks/{bp}/start")
        assert r.url.path.startswith("/papers/")
        pid = int(r.url.path.split("/")[2])
        assert c.post(f"/mocks/{bp}/start").url.path == f"/papers/{pid}"  # 没做完的接着做
        page = c.get(f"/papers/{pid}").text
        assert "模拟考" in page and "mclock" in page and 'class="kpsel"' not in page
        plan = c.post("/api/plan/rebuild").json()["plan"]
        assert any(t["title"].startswith("模拟考：") for t in plan), plan
        prs = db.q("SELECT * FROM paper_items WHERE paper_id=? ORDER BY seq", pid)
        assert c.post(f"/api/papers/{pid}/kp", json={"pi": prs[0]["id"], "kp_id": kp}).status_code == 400
        assert c.post(f"/api/papers/{pid}/answer", json={"pi": prs[0]["id"], "answer": "1"}).json()["correct"]
        c.post(f"/api/papers/{pid}/answer", json={"pi": prs[1]["id"], "dont_know": True})
        assert c.post(f"/api/papers/{pid}/finish").json()["ok"]
        assert "再做一次" in c.get("/mocks").text
        assert db.one("SELECT n_attempts FROM items WHERE id=?", rows[0]["item_id"])["n_attempts"] >= 1
        c.get("/logout")

        # 下架后练习里不再出现
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        c.post(f"/admin/bank-papers/{bp}/status", data={"action": "retire"})
        assert rows[0]["item_id"] not in {r["id"] for r in bank.candidates(kid["id"], kp)}
        assert "已下架" in c.get("/admin?tab=papers").text
        c.get("/logout")


def test_bank_pipeline(monkeypatch):
    """题库流水线：家里的卷子改编入库（先待校对）、AI 独立校对答案、同型题分组、按作答校准难度、出题小帮手贴纸；
    多个进程只跑一份。"""
    from app import bank, bankflow, db, engine
    from app.arena import awards
    from app.catalog import catalog
    with TestClient(app) as c:
        mom = db.one("SELECT * FROM users WHERE email='mom@x.com'")
        c.post("/login", data={"email": "mom@x.com", "password": "secret1"})
        assert 'name="shared"' in c.get("/papers").text
        r = c.post("/api/papers", data={"pack_id": "fund-law", "title": "单元测验", "text": "第一题……" * 5})
        pid = r.json()["id"]
        assert db.one("SELECT shared FROM papers WHERE id=?", pid)["shared"] == 1  # 默认同意分享
        r = c.post("/api/papers", data={"pack_id": "fund-law", "title": "不分享", "text": "第一题……" * 5, "share_choice": "1"})
        assert db.one("SELECT shared FROM papers WHERE id=?", r.json()["id"])["shared"] == 0
        c.get("/logout")

        # 改编入库 + 校对：AI 独立做出来和参考答案一样 → 进公共题库（别的测试里的卷子先设成不分享）
        db.run("UPDATE papers SET shared=0 WHERE user_id<>?", mom["id"])
        monkeypatch.setattr(bankflow.llm, "solve_item", lambda it, pack, grade: {"answer": it.get("answer"), "ok": True})
        res = bankflow.run_once(force=True)
        assert res["variants"]["made"] == 1, res  # 两道题改编出来一样的，只存一份
        v = db.one("SELECT * FROM items WHERE source='variant'")
        assert v["status"] == "active" and v["verified"] == 1 and v["contributor_id"] == mom["id"] and v["variant_of"]
        src = db.one("SELECT p.shared FROM items i JOIN paper_items pi ON pi.item_id=i.id JOIN papers p ON p.id=pi.paper_id "
                     "WHERE i.id=?", v["variant_of"])
        assert src["shared"] == 1
        assert bankflow.run_once(force=True)["variants"]["tried"] == 0  # 改编过的不再改编

        # 校对没过：暂停使用，进管理后台「被标记有问题」并写明原因
        kp = catalog.ids_for("fund-law")[0]
        bad = bank.save_items(kp, [{"type": "num", "q": "基金份额 100 份，每份净值 1.5 元，资产净值多少元？", "answer": 150}])[0]
        monkeypatch.setattr(bankflow.llm, "solve_item", lambda it, pack, grade: {"answer": 15, "ok": True})
        bankflow.verify_batch()
        row = db.one("SELECT * FROM items WHERE id=?", bad["id"])
        assert row["status"] == "review" and row["verified"] == -1 and "对不上" in row["verify_note"]
        weird = bank.save_items(kp, [{"type": "fill", "q": "基金的____", "answer": ["x"]}])[0]
        monkeypatch.setattr(bankflow.llm, "solve_item", lambda it, pack, grade: {"answer": "", "ok": False, "problem": "条件不够"})
        bankflow.verify_batch()
        assert "条件不够" in db.one("SELECT verify_note FROM items WHERE id=?", weird["id"])["verify_note"]

        # 同型题：换了数字的同一道题归一组；一次不出同组两道，做过一道后另一道不算新题
        a, b2, other = bank.save_items(kp, [
            {"type": "num", "q": "某基金持有 300 万元股票，占净值 30%，基金净值多少万元？", "answer": 1000},
            {"type": "num", "q": "某基金持有 450 万元股票，占净值 15%，基金净值多少万元？", "answer": 3000},
            {"type": "mcq", "q": "下列哪项属于基金托管人的职责？", "options": ["选股", "保管基金资产", "销售", "投研"], "answer": 1}])
        na = db.one("SELECT near_key FROM items WHERE id=?", a["id"])["near_key"]
        assert na == db.one("SELECT near_key FROM items WHERE id=?", b2["id"])["near_key"]
        assert na != db.one("SELECT near_key FROM items WHERE id=?", other["id"])["near_key"]
        db.run("UPDATE items SET verified=1 WHERE kp_id=?", kp)
        got = [x["id"] for x in engine.items_for(mom["id"], kp, n=20)]
        assert not (a["id"] in got and b2["id"] in got)
        engine.record_attempt(mom["id"], a, kp, "practice", False, "1")
        got = [x["id"] for x in engine.items_for(mom["id"], kp, n=20)]
        done = {r["item_id"] for r in db.q("SELECT item_id FROM attempts WHERE user_id=?", mom["id"])}
        fresh = [x for x in got if x not in done and x != b2["id"]]
        assert b2["id"] in got and a["id"] not in got  # 做错的组：先出换了数字的那道，不是原题
        assert got.index(b2["id"]) == len(fresh)  # 排在真正没见过的题后面、重做的题前面

        # 难度校准：要看是谁做的
        users = [db.insert("INSERT INTO users(email,name,role,grade,created_at,pw_hash) VALUES(?,?,?,?,?,?)",
                           f"cal{i}@x.com", f"cal{i}", "kid", "ADULT", db.now(), "x") for i in range(4)]
        for u in users:
            for _ in range(3):
                db.run("INSERT INTO attempts(user_id,item_id,kp_id,mode,correct,answer,dont_know,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       u, other["id"], kp, "practice", 0, "0", 0, db.now())
                db.run("INSERT INTO attempts(user_id,item_id,kp_id,mode,correct,answer,dont_know,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       u, v["id"], kp, "practice", 1, "2", 0, db.now())
        assert bankflow.calibrate() >= 2
        lv_hard = db.one("SELECT level FROM items WHERE id=?", other["id"])["level"]
        lv_easy = db.one("SELECT level FROM items WHERE id=?", v["id"])["level"]
        assert lv_hard > lv_easy and 1 <= lv_easy and lv_hard <= 5
        # 别的同学练了 12 次改编题 → 出题小帮手
        assert bankflow.contributed(mom["id"]) == {"items": 1, "uses": 12}
        assert "helper" in awards.learning_keys(mom["id"])
        c.post("/login", data={"email": "mom@x.com", "password": "secret1"})
        assert "改编出 1 道新题" in c.get("/papers").text
        c.get("/logout")

        # 多个进程只跑一份；管理后台能看到、能手动跑
        db.run("UPDATE jobs SET locked_until=? WHERE name='bank'", "9999")
        assert bankflow.run_once()["skipped"]
        db.run("UPDATE jobs SET locked_until='' WHERE name='bank'")
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        page = c.get("/admin?tab=bank").text
        assert "题库流水线" in page and "AI 校对" in page
        assert "跑完了" in c.post("/admin/bank/maintain").text
