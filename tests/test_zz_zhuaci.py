"""抓词（app/wordpick.py，DESIGN 5.58）：统一的出词（跟课本、往前补、往外、联想、学过的），一组一组来，
「我认识」验一题、认一认、往上推一级、用法卡和用法题、每天新词上限跟着「隔几天还记得多少」走、做完一组送一局跑酷。"""
import random
from datetime import timedelta

from fastapi.testclient import TestClient

from app import arena, db, engine, prefetch, recall, wordpick, words
from app.arena import matches, rules
from app.main import app
from tests.test_zz_mix import _setup


def _kid(c, monkeypatch, email, grade="G3", subj=None):
    kid = _setup(c, monkeypatch, email, grade=grade, subj=subj or {"subj_english": "eng-shanghai"})
    db.run("DELETE FROM cards WHERE user_id=?", kid)
    return dict(db.one("SELECT * FROM users WHERE id=?", kid))


def _card(kid_id, front):
    return db.one("SELECT * FROM cards WHERE user_id=? AND front=?", kid_id, front)


def _see(kid_id, ws, day=None):
    """这些词都见过了（答对过一次）。"""
    for w in ws:
        words.log_answer(kid_id, "en-sh-g3", w, True, "read", mode="probe")
    if day:
        db.run("UPDATE events SET created_at=? WHERE user_id=? AND target='word'", day, kid_id)


def test_word_lines(monkeypatch):
    """出词的几路：跟课本从学校正在上的单元起；学校教过的前几单元先认一认（往前补）；跟上了，一半往后学、一半往外；
    抓牢了的词带出它的一家人（联想）。"""
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "zc1@x.com")
        wl = words.packs()["en-sh-g3"]
        us = words.units(wl)
        monkeypatch.setattr(words, "school_unit", lambda uid, pack: 2 if pack["id"] == "en-sh-g3" else None)

        got = wordpick.new_words(k["id"], 4)
        assert {w["line"] for w in got} == {"book"} and {w["unit"] for w in got} == {2}   # 学校正在上第 3 单元：从它学起
        old = wordpick.old_words(k["id"], 30)
        early = {w["w"] for u in us[:2] for w in u["words"]}
        assert old and all(w["line"] == "back" for w in old) and old[0]["w"] in early   # 前两单元学校教过：先认一认，不当新词教
        assert {w["w"] for w in old[:len([x for x in early])]} <= early | {w["w"] for x in ("en-sh-g1", "en-sh-g2") for w in words.packs()[x]["words"]}

        # 跟上了（学校正在上的单元和它前面的都见过了）：一半往后学，一半往外（课本外的高频词）
        _see(k["id"], [w["w"] for u in us[:3] for w in u["words"]])
        assert wordpick.Pool(k["id"]).caught_up()
        got = wordpick.new_words(k["id"], 6)
        lines = [w["line"] for w in got]
        assert lines.count("book") == 3 and lines.count("out") == 3
        books = {w["w"].lower() for x in words.packs().values() if words.shelf(x) == "grade" and x.get("series") == "shanghai" for w in x["words"]}
        assert all(w["w"].lower() not in books and words.shelf(words.packs()[w["list"]]) in ("core", "exam") for w in got if w["line"] == "out")
        assert all(w["unit"] >= 3 for w in got if w["line"] == "book")

        # 联想：抓牢了的词，用法卡里的一家人当新词学（每天最多 1 个）
        root = us[0]["words"][0]["w"]
        cid = engine.add_card(k["id"], "word", root, "x", {"list": "en-sh-g3"}, due=db.today() + timedelta(days=3))
        db.run("UPDATE cards SET box=3, reviews=3 WHERE id=?", cid)
        db.run("DELETE FROM word_usage WHERE word=? AND band='primary'", root.lower())   # 前面的用例可能已经替它写过用法卡
        db.run("INSERT INTO word_usage(word, band, data, created_at) VALUES(?,?,?,?)", root.lower(), "primary",
               db.jdump({"family": [{"w": "zzrelated", "pos": "n.", "zh": "联想词"}]}), db.now())
        got = wordpick.new_words(k["id"], 4)
        rel = [w for w in got if w["line"] == "related"]
        assert len(rel) == 1 and rel[0]["w"] == "zzrelated" and rel[0]["root"] == root.lower()
        wordpick.add_new(k["id"], rel[0])
        assert not [w for w in wordpick.new_words(k["id"], 4) if w["line"] == "related"]   # 今天已经有一个了


def test_practice_draws_from_all_past_words(monkeypatch):
    """游戏、快练里的单词：过去学过的词（最久没见的先）也常出，不只是到期的和词表里的。做完一组送的那一局只出那一组的词。"""
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "zc2@x.com")
        old = []
        for i, w in enumerate(("apple", "banana", "orange")):
            cid = engine.add_card(k["id"], "word", w, "水果", {"list": "en-core-g3"}, due=db.today() + timedelta(days=5))
            db.run("UPDATE cards SET reviews=2, box=2 WHERE id=?", cid)
            old.append(db.one("SELECT * FROM cards WHERE id=?", cid))
        words.log_answer(k["id"], "en-core-g3", "apple", True, "read")
        words.log_answer(k["id"], "en-core-g3", "banana", True, "read")
        lst = [{"w": "zzz", "zh": "某词"}]
        seen = {wordpick.practice(k["id"], random.Random(i), lst, old, {})["w"] for i in range(40)}
        assert "orange" in seen and "zzz" in seen        # 从没在记录里出现过的旧词（最久没见）会被拿出来
        st = {"word_set": [{"w": "cat", "zh": "猫"}, {"w": "dog", "zh": "狗"}]}
        got = [wordpick.practice(k["id"], random.Random(i), lst, old, st)["w"] for i in range(4)]
        assert sorted(got) == ["cat", "cat", "dog", "dog"]   # 只出这一组的词，轮着来

        # 游戏里答对只算认识（见过），不升级：升级靠学的地方
        for day in (3, 0):
            words.log_answer(k["id"], "en-core-g3", "kite", True, "spell", mode="game")
            db.run("UPDATE events SET created_at=? WHERE id=(SELECT MAX(id) FROM events WHERE user_id=?)",
                   (db.today() - timedelta(days=day)).isoformat() + "T09:00:00", k["id"])
        assert words.ladder(k["id"])["kite"] == {"level": 0, "ok": True, "wobbly": False}


def test_set_flow_known_check_push_and_reward(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "zc3@x.com")
        monkeypatch.setattr(words, "school_unit", lambda uid, pack: 1 if pack["id"] == "en-sh-g3" else None)
        assert c.get("/review?group=words", follow_redirects=False).headers["location"] == "/words/go"
        assert "抓词" in c.get("/words/go").text
        home = c.get("/words/map").text
        assert "来一组" in home and "/words/go" in home

        r = c.post("/api/words/set", json={}).json()
        cards = {x["front"]: x for x in r["cards"]}
        roles = [x["role"] for x in r["cards"]]
        assert roles.count("due") == words.PER_DAY["primary"]        # 今天的新词（每日任务的）先来
        assert len(r["cards"]) == wordpick.SET_SIZE and roles.count("new") + roles.count("check") >= 1
        learn = next(x for x in r["cards"] if x["quiz"]["mode"] == "learn")
        assert learn["quiz"]["known"]["mode"] in ("spell", "choice")      # 「我认识」：马上验一道更难的

        # 「我认识」拼对了：不用学，7 天后再来；拼错：照常学（不排期）
        ok = c.post(f"/api/review/{learn['id']}/answer", json={"mode": "spell", "known": 1, "answer": learn["front"]}).json()
        assert ok["correct"] and ok["known"]
        row = _card(k["id"], learn["front"])
        assert row["box"] == 3 and row["due"] == (db.today() + timedelta(days=recall.KNOWN_DAYS)).isoformat()
        other = next(x for x in r["cards"] if x["quiz"]["mode"] == "learn" and x["id"] != learn["id"])
        bad = c.post(f"/api/review/{other['id']}/answer", json={"mode": "spell", "known": 1, "answer": "zzz"}).json()
        assert not bad["correct"] and bad["known"] is False and _card(k["id"], other["front"])["reviews"] == 0

        # 认一认（往前补）：认出来算认识（认得）；没认出来变成要学的新词
        checks = [x for x in r["cards"] if x["role"] == "check"]
        if checks:
            ch = checks[0]
            assert ch["quiz"]["mode"] == "meaning" and ch["quiz"]["probe"]
            res = c.post(f"/api/review/{ch['id']}/answer", json={"mode": "meaning", "answer": "不对"}).json()
            assert not res["correct"] and res["learn_next"]
            row = _card(k["id"], ch["front"])
            assert recall.quiz(k["id"], row)["mode"] == "learn"

        # 做完：小结 + 送一局跑酷（不算游戏时间，只出这一组的词）
        done = c.post(f"/api/words/set/{r['id']}/done", json={}).json()
        assert learn["front"] in done["known"] and other["front"] in done["wobbly"]
        assert done["known_total"] >= done["known_before"] and done["reward"] and done["more"]
        assert c.get(f"/arena/runner?reward={r['id']}").status_code == 200
        rules.set_game_minutes(k["id"], 0)                             # 家长设了今天不玩游戏：送的这一局照样能跑
        kk = dict(db.one("SELECT * FROM users WHERE id=?", k["id"]))
        m = matches.start(kk, "runner", reward=r["id"])
        assert m["unlimited"]
        row = db.one("SELECT mode, data FROM arena_matches WHERE id=?", m["match_id"])
        assert row["mode"] == "reward"
        set_words = {w["w"] for w in (db.jload(row["data"], {}) or {})["word_set"]}
        q = matches.question(kk, m["match_id"], random.Random(1))["item"]
        assert q["run"]["w"] in set_words
        db.run("UPDATE arena_matches SET ended_at=?, seconds=300 WHERE id=?", db.now(), m["match_id"])
        assert rules.seconds_today(k["id"]) == 0                      # 不算游戏时间
        assert not wordpick.reward_ok(k["id"], r["id"])                # 一组只送一局
        try:
            matches.start(kk, "runner", reward=r["id"])
            raise AssertionError("用过的奖励不能再开")
        except arena.ArenaError:
            pass

        # 再来一组：上一组没认出来的词，这次是学习卡
        r2 = c.post("/api/words/set", json={}).json()
        assert r2["id"] != r["id"] and r2["cards"]
        if checks:
            assert any(x["front"] == checks[0]["front"] and x["quiz"]["mode"] == "learn" for x in r2["cards"])


def test_push_and_use_items(monkeypatch):
    """往上推一级：按这个词现在到哪一级出更难的题，答对不动排期；拼得出以后考用法题（选搭配、句型、找错词）。"""
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "zc4@x.com")
        cid = engine.add_card(k["id"], "word", "garden", "花园", {"list": "en-core-g3"}, due=db.today() + timedelta(days=4))
        db.run("UPDATE cards SET reviews=2, box=2 WHERE id=?", cid)
        day = (db.today() - timedelta(days=3)).isoformat() + "T10:00:00"
        for t in ("read", "listen"):
            words.log_answer(k["id"], "en-core-g3", "garden", True, t)
        db.run("UPDATE events SET created_at=? WHERE user_id=? AND target='word'", day, k["id"])
        words.log_answer(k["id"], "en-core-g3", "garden", True, "listen")
        assert words.level_of(k["id"], "garden") == 2
        card = _card(k["id"], "garden")
        q = recall.push_quiz(k["id"], card)
        assert q["mode"] == "spell" and q["push"]
        r = c.post(f"/api/review/{cid}/answer", json={"mode": "spell", "answer": "garden", "push": 1}).json()
        assert r["correct"] and _card(k["id"], "garden")["due"] == card["due"]   # 对了不动排期
        r = c.post(f"/api/review/{cid}/answer", json={"mode": "spell", "answer": "gardn", "push": 1}).json()
        assert not r["correct"] and _card(k["id"], "garden")["due"] == (db.today() + timedelta(days=1)).isoformat()

        # 用法卡：AI 写一次（测试里是假 AI），学习卡上按学段给；用法题答对记成「用得对」那一级
        monkeypatch.setattr(prefetch, "MODE", "inline")
        db.run("DELETE FROM word_usage WHERE word IN ('garden', 'apple')")
        assert words.write_usage([{"w": "garden", "zh": "花园"}], "primary") == 1
        v = words.usage_view("garden", "primary", "G3")
        assert v["phrases"] and not v["family"] and "icon" not in v   # 小学：少给；三年级不给图标
        assert words.usage_view("apple", "primary", "G1") is None
        words.write_usage([{"w": "apple", "zh": "苹果"}], "primary")
        assert words.usage_view("apple", "primary", "G1")["icon"] == "🍎"   # 一二年级的具体名词有图标
        db.run("UPDATE cards SET box=4, due=? WHERE id=?", db.today().isoformat(), cid)
        q = recall.quiz(k["id"], _card(k["id"], "garden"))
        assert q["mode"] == "use" and "___" in q["q"] and "a" in q["options"]
        r = c.post(f"/api/review/{cid}/answer", json={"mode": "use", "q": q["q"], "answer": "a"}).json()
        assert r["correct"] and r["pick"] == "a"
        ev = db.one("SELECT data FROM events WHERE user_id=? AND target='word' ORDER BY id DESC LIMIT 1", k["id"])
        assert (db.jload(ev["data"], {}) or {}).get("tier") == "use"

        # 反复忘的词：补一句记忆钩子，答错时给
        db.run("UPDATE cards SET lapses=3 WHERE id=?", cid)
        assert [w["w"] for w in words.wanted_hooks(k["id"])] == ["garden"]
        assert words.write_hooks(words.wanted_hooks(k["id"]), "primary") == 1
        db.run("UPDATE cards SET box=1, due=? WHERE id=?", db.today().isoformat(), cid)
        r = c.post(f"/api/review/{cid}/answer", json={"mode": "choice", "answer": "zzz"}).json()
        assert not r["correct"] and r["hook"]


def test_daily_cap_follows_keep_rate(monkeypatch):
    """每天新词有上限（含每日任务的）；隔几天再见到的词记得少，上限降一档，记得牢就升一档。到了上限，一组里不再加新词。"""
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "zc5@x.com")
        assert wordpick.cap(k["id"]) == wordpick.CAP["primary"]
        old = (db.today() - timedelta(days=10)).isoformat() + "T09:00:00"
        now = (db.today() - timedelta(days=1)).isoformat() + "T09:00:00"
        for i in range(12):
            words.log_answer(k["id"], "x", f"w{i}", True, "read")
            db.run("UPDATE events SET created_at=? WHERE id=(SELECT MAX(id) FROM events WHERE user_id=?)", old, k["id"])
            words.log_answer(k["id"], "x", f"w{i}", i < 6, "read")      # 隔了 9 天，一半忘了
            db.run("UPDATE events SET created_at=? WHERE id=(SELECT MAX(id) FROM events WHERE user_id=?)", now, k["id"])
        rate, n = wordpick.keep_rate(k["id"])
        assert n == 12 and rate == 0.5
        assert wordpick.cap(k["id"]) == wordpick.CAP["primary"] - wordpick.CAP_STEP

        monkeypatch.setattr(wordpick, "cap", lambda uid, p=None: 4)
        r = c.post("/api/words/set", json={}).json()
        assert wordpick.new_today(k["id"]) == 4 and not [x for x in r["cards"] if x["role"] == "new"]   # 每日任务的 4 个就到上限了
        assert wordpick.room_today(k["id"]) == 0


def test_parent_sees_word_week(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "zc6@x.com")
        words.daily_new(k["id"])
        s = wordpick.parent_summary(k["id"])
        assert s["week_new"] == words.PER_DAY["primary"] and s["cap"] == wordpick.CAP["primary"]
        c.get("/logout")
        c.post("/login", data={"email": "mixp@x.com", "password": "secret1"})
        assert "单词这一周" in c.get("/parent").text


def test_daily_task_set_goes_on_to_next_task(monkeypatch):
    """今天清单里的「单词」和自己来抓词是同一个页面、两个场合：从清单来（wordpick.TASK_URL），一组只出今天要做的词，
    做完一组接着下一组，都做完了这一项打勾、弹出下一项；不出「再来一组」、不送跑酷（那是抓词自己的）。"""
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "zc7@x.com")
        monkeypatch.setattr(words, "school_unit", lambda uid, pack: 1 if pack["id"] == "en-sh-g3" else None)
        db.run("DELETE FROM days WHERE user_id=?", k["id"])
        plan = engine.today_plan(k["id"])["plan"]
        task = next(t for t in plan if t["type"] == "words")
        assert task["url"] == wordpick.TASK_URL
        page = c.get(wordpick.TASK_URL).text
        assert "DAY = SET &amp;&amp; true" in page or "DAY = SET && true" in page
        assert "DAY = SET && false" in c.get("/words/go").text or "DAY = SET &amp;&amp; false" in c.get("/words/go").text

        # 只出到期的（今天清单上写的那些），不添新词、认一认、往上推
        due = engine.due_cards(k["id"], 300, "words")
        r = c.post("/api/words/set", json={"task": True}).json()
        assert r["cards"] and {x["role"] for x in r["cards"]} == {"due"} and len(r["cards"]) == min(len(due), wordpick.SET_SIZE)
        # 还有今天要做的：这一项不打勾
        db.run("UPDATE cards SET due=? WHERE id IN (%s)" % ",".join("?" * len(r["cards"])),
               (db.today() + timedelta(days=1)).isoformat(), *[x["id"] for x in r["cards"]])
        done = c.post(f"/api/words/set/{r['id']}/done", json={}).json()
        left = done["due_left"]
        assert left == len(due) - len(r["cards"])
        if left:
            assert not next(t for t in engine.plan_of_today(k["id"]) if t["type"] == "words").get("done")
            db.run("UPDATE cards SET due=? WHERE user_id=? AND kind IN ('word','term')", (db.today() + timedelta(days=1)).isoformat(), k["id"])
            r = c.post("/api/words/set", json={"task": True}).json()
            assert r["id"] is None and not r["cards"]
        else:
            assert next(t for t in engine.plan_of_today(k["id"]) if t["type"] == "words").get("done")
        # 都做完了：和别的任务一样，弹出下一项
        flow = c.post("/api/plan/task-done", json={"type": "words"}).json()["flow"]
        assert flow["just"] and (flow["next"] or flow["all_done"])
        assert not flow["next"] or flow["next"]["id"] != task["id"]

        # 升级前排好的清单（入口还是 /words/go）：读出来就带上「在今天的清单里」
        row = db.one("SELECT plan FROM days WHERE user_id=? AND day=?", k["id"], db.today().isoformat())
        old = [{**t, "url": "/words/go"} if t["type"] == "words" else t for t in db.jload(row["plan"], [])]
        db.run("UPDATE days SET plan=? WHERE user_id=? AND day=?", db.jdump(old), k["id"], db.today().isoformat())
        assert next(t for t in engine.plan_of_today(k["id"]) if t["type"] == "words")["url"] == wordpick.TASK_URL
