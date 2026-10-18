"""游戏乐园：快题答案正确、按水平出题确实公平、一局完整走通、解锁规则、贴纸和角色、多种题源。"""
import random
import statistics

from fastapi.testclient import TestClient

from app import arena, db, itemtypes, quickgen
from app.arena import awards, sources
from app.catalog import catalog
from app.main import app


def _right_answer(it):
    a = it["answer"]
    return a[0] if isinstance(a, list) else a


def test_quickgen_answers_are_correct():
    """每个题族、每个级别：程序算出的答案能通过判分，错一点的答案不能通过。"""
    with TestClient(app):  # 加载教材
        rng = random.Random(7)
        for fam, (kp_id, _, _) in quickgen.FAMILIES.items():
            assert catalog.kp(kp_id), f"{fam} 挂的知识点 {kp_id} 不存在"
            for lv in quickgen.LEVELS:
                for _ in range(60):
                    it = quickgen.generate(fam, lv, rng)
                    assert it["q"] and it["id"].startswith(f"gen-{fam}-")
                    assert itemtypes.check(it, str(_right_answer(it))), (fam, lv, it)
                    if it["type"] == "num":
                        assert not itemtypes.check(it, str(float(it["answer"]) + 1)), (fam, lv, it)
                    if fam != "int_add" and it["type"] == "num":
                        assert float(it["answer"]) >= 0, (fam, lv, it)


def test_koujue():
    assert [quickgen.koujue(*x) for x in ((3, 2), (9, 8), (4, 5), (1, 1), (3, 3), (2, 5), (3, 4))] == ["二三得六", "八九七十二", "四五二十", "一一得一", "三三得九", "二五一十", "三四十二"]


def test_fraction_answers_accept_equivalent_forms():
    it = quickgen.generate("frac_same", 3, random.Random(1))
    for form in it["answer"]:
        assert itemtypes.check(it, form)


def test_adaptive_difficulty_gives_everyone_the_same_chance():
    """模拟三个真实水平差很多的孩子（都在一个题族 1–5 级覆盖的范围内）：按水平出题后，大家的答对率都接近目标（75%），
    经验值期望相同。超出范围的孩子（这个知识点最简单的一级也太难）由 arena.kp_pool 少出这个知识点、换别的知识点。"""
    target = arena.GAMES["stickman"].target
    rng = random.Random(42)
    accs = []
    for true in (-0.8, 0.4, 2.0):           # 弱、中、强
        theta, n, right = 0.0, 0, []
        levels = {lv: (arena.prior_b(lv), 0) for lv in quickgen.LEVELS}
        for _ in range(300):
            lv = arena.pick_level(theta, levels, target, rng)
            b = levels[lv][0]
            ok = rng.random() < arena.sigmoid(true - b)
            theta, _ = arena.elo_step(theta, n, b, 1000, ok)   # 难度已校准好，只更新孩子
            n += 1
            right.append(ok)
        accs.append(sum(right[100:]) / len(right[100:]))
    for a in accs:
        assert abs(a - target) < 0.08, accs
    assert max(accs) - min(accs) < 0.1, accs


def test_xp_and_freeze_rules():
    rng = random.Random(3)
    xs = [arena.roll_xp(rng, 0)[0] for _ in range(4000)]
    assert 85 <= statistics.mean(xs) <= 115
    assert min(xs) >= 70 and max(xs) <= 300
    assert arena.roll_xp(random.Random(1), arena.DAILY_SOFT_CAP)[0] <= 150
    assert [arena.freeze_seconds(k, True) for k in (1, 2, 3, 4)] == [5, 8, 12, 12]
    assert arena.freeze_seconds(0, False) == 5


def _pending(mid):
    return db.jload(db.one("SELECT data FROM arena_matches WHERE id=?", mid)["data"], {})["pending"]["item"]


def test_match_flow():
    with TestClient(app) as c:
        if c.get("/login").text.count("创建网站管理员账号"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        else:
            c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        c.post("/admin/users/create", data={"email": "gp@x.com", "password": "secret1", "name": "游戏爸爸", "role": "parent"})
        c.get("/logout")
        c.post("/login", data={"email": "gp@x.com", "password": "secret1"})
        c.post("/parent/kids/save", data={"name": "小火柴", "email": "gk@x.com", "password": "secret1", "grade": "G3",
                                          "daily_minutes": "60", "subj_math": "math-shanghai", "game_minutes": "20"})
        kid = db.one("SELECT * FROM users WHERE email='gk@x.com'")
        assert arena.game_minutes(dict(kid)) == 20
        assert c.get("/arena").status_code in (400, 403)  # 家长不能替孩子玩
        c.get("/logout")

        c.post("/login", data={"email": "gk@x.com", "password": "secret1"})
        assert "乐园" in c.get("/today").text   # 生成今天的任务
        hub = c.get("/arena").text
        assert "火柴人大战" in hub and "闪电赛跑" in hub and "星星守卫" in hub and "贴纸墙" in hub
        for g in arena.GAMES:
            assert c.get(f"/arena/{g}").status_code == 200
        assert c.get("/arena/nope").status_code == 404
        # 默认「做完今天一半的任务后才能玩」
        r = c.post("/api/arena/start", json={"game": "stickman"})
        assert r.status_code == 403 and "任务" in r.json()["error"]
        st = arena.status(dict(kid))
        assert st["need"] == (st["total"] + 1) // 2 and not st["unlocked"]
        arena.set_game_unlock(kid["id"], "free")
        start = c.post("/api/arena/start", json={"game": "stickman"}).json()
        mid = start["match_id"]
        assert start["avatar"] == "mint"

        q = c.get(f"/api/arena/{mid}/q").json()["item"]
        assert "answer" not in q and q["q"]
        it = _pending(mid)
        # 只出不超过当前学段（三年级）的知识点
        assert catalog.kps[it["kp_id"]]["stage"] in ("G1", "G2", "G3")
        # 关掉再打开还是同一道题，不能换掉难题
        assert c.get(f"/api/arena/{mid}/q").json()["item"]["id"] == q["id"]

        # 秒答答错：冷冻，冷冻中不能拿新题
        r = c.post(f"/api/arena/{mid}/a", json={"item_id": q["id"], "answer": "-99999"}).json()
        assert r["correct"] is False and r["freeze_ms"] == 5000 and r["xp"] == 0
        r2 = c.get(f"/api/arena/{mid}/q")
        assert r2.status_code == 423 and r2.json()["freeze_ms"] > 0
        st = db.jload(db.one("SELECT data FROM arena_matches WHERE id=?", mid)["data"], {})
        st["freeze_until"] = 0
        db.run("UPDATE arena_matches SET data=? WHERE id=?", db.jdump(st), mid)

        # 答对：得经验值，记进账本和学习记录
        q = c.get(f"/api/arena/{mid}/q").json()["item"]
        it = _pending(mid)
        r = c.post(f"/api/arena/{mid}/a", json={"item_id": q["id"], "answer": str(_right_answer(it))}).json()
        assert r["correct"] and r["xp"] >= 70
        assert db.one("SELECT COUNT(*) AS n FROM attempts WHERE user_id=? AND mode='game'", kid["id"])["n"] == 2
        assert db.one("SELECT COUNT(*) AS n FROM arena_ability WHERE user_id=?", kid["id"])["n"] >= 1
        # 旧题不能再交
        assert c.post(f"/api/arena/{mid}/a", json={"item_id": q["id"], "answer": "1"}).status_code == 409

        end = c.post(f"/api/arena/{mid}/end", json={"result": "win", "stats": {"zero_energy_s": 25}}).json()
        assert end["answered"] == 2 and end["right"] == 1 and end["accuracy"] == 50
        assert end["focus"] is None  # 不到 3 道题，不算专注指数
        assert any("能量是 0" in t for t in end["tips"])
        # 第一次赢电脑：得到「你好乐园」「第一场胜利」，解锁棒球帽角色
        keys = {x["key"] for x in end["stickers"]}
        assert {"hello", "first_win"} <= keys and end["level"]["level"] >= 1
        assert c.post("/api/arena/avatar", json={"avatar": "ninja"}).status_code == 400   # 还没解锁
        assert c.post("/api/arena/avatar", json={"avatar": "cap"}).json()["ok"]
        assert awards.avatar(dict(db.one("SELECT * FROM users WHERE id=?", kid["id"]))) == "cap"
        assert "第一场胜利" in c.get("/arena").text
        assert c.post(f"/api/arena/{mid}/end", json={"result": "win"}).status_code == 409
        c.get("/logout")

        # 家长能看到游戏情况、把游戏关掉
        c.post("/login", data={"email": "gp@x.com", "password": "secret1"})
        assert "乐园本周" in c.get("/parent").text
        c.post("/parent/kids/save", data={"id": kid["id"], "name": "小火柴", "email": "gk@x.com", "grade": "G3",
                                          "daily_minutes": "60", "subj_math": "math-shanghai", "game_minutes": "0"})
        c.get("/logout")
        c.post("/login", data={"email": "gk@x.com", "password": "secret1"})
        r = c.post("/api/arena/start", json={"game": "stickman"})
        assert r.status_code == 403 and "家长" in r.json()["error"]
        # 不能玩的时候：游戏卡片点不进去，直接打开游戏页面回到乐园首页（那里写着为什么）
        hub = c.get("/arena").text
        assert 'href="/arena/stickman"' not in hub and "data-locked" in hub and "家长设置了暂时不玩游戏" in hub
        r = c.get("/arena/stickman", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/arena?locked=1"
        assert "ah-lock shake" in c.get("/arena?locked=1").text
        c.get("/logout")

        # 管理后台的公平看板
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        page = c.get("/admin?tab=arena").text
        assert "游戏公平看板" in page and "实际答对率" in page


def test_unlock_rules_and_bonus(monkeypatch):
    from app.arena import rules
    kid = {"id": 999001, "game_minutes": 20, "game_unlock": "half"}
    monkeypatch.setattr(rules, "game_unlock", lambda k: k["game_unlock"])
    monkeypatch.setattr(rules, "game_minutes", lambda k: k["game_minutes"])
    monkeypatch.setattr(rules, "seconds_today", lambda kid_id, m=None: 0)
    monkeypatch.setattr(rules.explore, "lit_today", lambda kid_id: [1, 2, 3, 4, 5])
    for (done, total, rule), (need, minutes) in {
            (0, 0, "half"): (0, 20 + 6),          # 今天没有任务：直接能玩
            (1, 5, "half"): (2, 26), (3, 5, "half"): (0, 26),
            (4, 5, "all"): (1, 26), (5, 5, "all"): (0, 36),   # 全做完 +10
            (0, 5, "free"): (0, 26)}.items():
        monkeypatch.setattr(rules, "_tasks_today", lambda kid_id, d=done, t=total: (d, t))
        st = rules.status(dict(kid, game_unlock=rule))
        assert (st["need"], st["minutes"]) == (need, minutes), (done, total, rule, st)
        assert bool(rules.locked_reason(st)) == bool(need)
    st = rules.status(dict(kid, game_minutes=0))
    assert "家长" in rules.locked_reason(st)


def test_levels_and_avatars_are_deterministic():
    assert [awards.level_of(n)["level"] for n in (0, 9, 10, 29, 30, 60)] == [1, 1, 2, 2, 3, 4]
    assert {a for a, (_, need) in awards.AVATARS.items() if not need} == {"mint", "sunny", "berry", "sky"}
    for a, (_, need) in awards.AVATARS.items():
        assert not need or need in awards.STICKERS, a


def test_word_and_bank_sources(monkeypatch):
    """英语单词题：四个选项、答案在选项里、判分对；学英语的孩子游戏里会出到单词题，答错记进生词本。"""
    with TestClient(app) as c:
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        c.post("/admin/users/create", data={"email": "wp@x.com", "password": "secret1", "name": "单词妈妈", "role": "parent"})
        c.get("/logout")
        c.post("/login", data={"email": "wp@x.com", "password": "secret1"})
        subj = catalog.packs["eng-cambridge"].subject
        c.post("/parent/kids/save", data={"name": "小单词", "email": "wk@x.com", "password": "secret1", "grade": "G8",
                                          "daily_minutes": "60", f"subj_{subj}": "eng-cambridge", "game_minutes": "20",
                                          "game_unlock": "free"})
        kid = dict(db.one("SELECT * FROM users WHERE email='wk@x.com'"))
        assert arena.game_unlock(kid) == "free"
        ws = sources.weights(kid)
        assert "words" in ws and "math" in ws
        rng = random.Random(1)
        assert {sources.pick(kid, 0.75, rng)["src"] for _ in range(40)} >= {"words", "math"}   # 按自己的科目混着出
        src, ctx, rng = sources.SOURCES["words"], sources.Ctx(kid), random.Random(5)
        for _ in range(30):
            it = src.pick(kid, ctx, 0.8, rng)
            if it["type"] == "fill":   # 拼写：给一盘字母
                assert it["fmt"] == "spell" and itemtypes.check(it, it["word"]) and not itemtypes.check(it, "zzz")
            else:
                assert it["type"] == "mcq" and len(set(it["options"])) == 4 and 0 <= it["answer"] < 4
                assert itemtypes.check(it, str(it["answer"])) and not itemtypes.check(it, str((it["answer"] + 1) % 4))
            assert it["dim"].startswith(("vocab:", "terms:")) and 0 < it["p"] < 1
            pub = sources.public(it)
            assert "answer" not in pub and pub["q"]
        c.get("/logout")

        c.post("/login", data={"email": "wk@x.com", "password": "secret1"})
        monkeypatch.setattr(sources, "weights", lambda kid, ctx=None: {"words": 1.0})   # 下面这局只出单词题
        mid = c.post("/api/arena/start", json={"game": "race"}).json()["match_id"]
        q = c.get(f"/api/arena/{mid}/q").json()["item"]
        it = _pending(mid)
        assert it["src"] == "words" and (it["type"] == "fill" or len(q["options"]) == 4)
        wrong = "zzz" if it["type"] == "fill" else str((it["answer"] + 1) % 4)
        assert c.post(f"/api/arena/{mid}/a", json={"item_id": q["id"], "answer": wrong}).json()["correct"] is False
        assert db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=?", kid["id"])["n"] >= 1   # 答错的词进生词本
        end = c.post(f"/api/arena/{mid}/end", json={"result": "lose", "stats": {"finish_s": 80}}).json()
        assert end["answered"] == 1


def test_focus_index():
    """专注指数 = 实际答对 ÷ 预期答对：预期一样时，答对多的（更投入的）分数高；3 秒内答错算猜。"""
    from app.arena import matches
    uid = db.one("SELECT id FROM users WHERE role='kid' ORDER BY id LIMIT 1")["id"]
    mid = db.insert("INSERT INTO arena_matches(user_id, game, started_at) VALUES(?,?,?)", uid, "stickman", db.now())
    for ok, ms in ((1, 6000), (1, 5000), (1, 7000), (1, 4000), (0, 1200), (0, 900)):
        db.run("INSERT INTO arena_answers(user_id,match_id,kp_id,family,level,p_pred,correct,ms,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               uid, mid, "MSH-NUM-10", "mult", 3, 0.5, ok, ms, db.now())
    f = matches.focus(mid)
    assert f["index"] == 133 and f["extra"] == 1 and f["guesses"] == 2


def test_unlimited_game_time():
    """家长选「不限时」：不封顶、不倒计时、不显示还剩几分钟；解锁规则照常。"""
    with TestClient(app) as c:
        if c.get("/login").text.count("创建网站管理员账号"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        else:
            c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        c.post("/admin/users/create", data={"email": "up@x.com", "password": "secret1", "name": "不限爸爸", "role": "parent"})
        c.get("/logout")
        c.post("/login", data={"email": "up@x.com", "password": "secret1"})
        assert "不限时" in c.get("/parent/kids/new").text
        c.post("/parent/kids/save", data={"name": "小不限", "email": "uk@x.com", "password": "secret1", "grade": "G3",
                                          "daily_minutes": "60", "subj_math": "math-shanghai", "game_minutes": "-1"})
        kid = dict(db.one("SELECT * FROM users WHERE email='uk@x.com'"))
        assert arena.game_minutes(kid) == arena.UNLIMITED
        assert 'value="-1" selected' in c.get(f"/parent/kids/{kid['id']}/edit").text
        st = arena.status(kid)
        assert st["unlimited"] and st["minutes"] is None and not st["bonus_all"] and not st["can_bonus_all"]
        assert st["need"] == (st["total"] + 1) // 2      # 解锁规则还是「做完一半」
        arena.set_game_unlock(kid["id"], "free")
        # 玩了很久也不会用完
        db.insert("INSERT INTO arena_matches(user_id, game, mode, started_at, ended_at, seconds, data) VALUES(?,?,?,?,?,?,?)",
                  kid["id"], "stickman", "solo", db.now(), db.now(), 5 * 3600, "{}")
        kid = dict(db.one("SELECT * FROM users WHERE id=?", kid["id"]))
        st = arena.status(kid)
        assert st["unlocked"] and st["left"] > 0 and arena.locked_reason(st) == ""
        c.get("/logout")
        c.post("/login", data={"email": "uk@x.com", "password": "secret1"})
        hub = c.get("/arena").text
        assert "今天不限时" in hub and "今天还能玩" not in hub
        assert "今天不限时" in c.get("/today").text
        start = c.post("/api/arena/start", json={"game": "stickman"}).json()
        assert start["unlimited"] and start["seconds_left"] > 0
        # 改回有限时长，照常封顶
        arena.set_game_minutes(kid["id"], 25)
        assert arena.game_minutes(dict(db.one("SELECT * FROM users WHERE id=?", kid["id"]))) in (20, 30)
        c.get("/logout")
