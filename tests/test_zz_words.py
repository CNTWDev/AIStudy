"""单词模块（app/words.py，DESIGN 5.41）：单词包自动挂、每天的新词怎么挑、每个词会到哪一级、例句和句子补洞（复习页、游戏）。"""
import random
from datetime import timedelta

from fastapi.testclient import TestClient

from app import arena, auth, db, engine, prefetch, recall, words
from app.arena import matches, sources
from app.main import app
from tests.test_zz_mix import _setup


def _kid(c, monkeypatch, email, grade="G3", subj=None):
    kid = _setup(c, monkeypatch, email, grade=grade, subj=subj or {"subj_english": "eng-shanghai"})
    return dict(db.one("SELECT * FROM users WHERE id=?", kid))


def _new_cards(kid_id):
    return [c for c in db.q("SELECT * FROM cards WHERE user_id=? AND kind IN ('word','term')", kid_id)
            if (db.jload(c["extra"], {}) or {}).get("new")]


def test_packs_attach_by_grade_and_daily_new_words(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "wd1@x.com")
        att = {a["pack"]["id"]: a for a in words.attached(k["id"])}
        assert att["en-sh-g3"]["main"] and att["en-sh-g3"]["per_day"] == words.PER_DAY["primary"]   # 上海三年级：课本单词包当主包
        assert "en-sh-g1" in att and not att["en-sh-g1"]["main"]                  # 低年级的包：闪测和游戏里用，不每天加新词
        assert "en-core-g3" in att and not att["en-core-g3"]["main"]              # 核心词还挂着（以前的卡照常复习），不再每天加新词
        assert "en-sh-g4" not in att and "en-yl-g3" not in att                   # 没到的年级、别的教材不挂
        assert not {i for i in att if words.shelf(words.packs()[i]) in ("exam", "adult", "scene")}   # 考试包、成人包不自动挂

        db.run("DELETE FROM cards WHERE user_id=?", k["id"])
        assert words.daily_new(k["id"]) == 4
        new = _new_cards(k["id"])
        pos = sorted(next(i for i, w in enumerate(words.units(words.packs()["en-sh-g3"])[0]["words"]) if w["w"] == c["front"]) for c in new)
        assert pos == [0, 3, 6, 9]                         # 一组里隔着挑，挨着的（同一个话题）不在同一天
        assert all(c["due"] == db.today().isoformat() for c in new)
        assert words.daily_new(k["id"]) == 0               # 同一天不再加

        # 家长把每天调到 6 个：当天就补上 2 个；停掉这个包，不再加、闪测和游戏也不用它
        _login_parent(c)
        r = c.post(f"/parent/kids/{k['id']}/tracks", data={"kind": "words", "ref": "en-sh-g3", "daily_amount": "6"})
        assert "已安排" in r.text and "单词包" in c.get(f"/parent/kids/{k['id']}/plan").text
        words.daily_new(k["id"])                            # 存的时候已经按新安排排过今天的计划
        assert len(_new_cards(k["id"])) == 6
        c.post(f"/parent/kids/{k['id']}/tracks", data={"kind": "words", "ref": "en-sh-g3", "action": "stop"})
        assert "en-sh-g3" not in {wl["id"] for wl in words.lists(k["id"])}
        assert engine.Paused(k["id"]).card(new[0])          # 停掉的包留下的卡先不叫回来复习


def _login_parent(c):
    c.get("/logout")
    c.post("/login", data={"email": "mixp@x.com", "password": "secret1"})


def test_new_words_skip_known_and_wait_for_backlog(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "wd2@x.com")
        db.run("DELETE FROM cards WHERE user_id=?", k["id"])
        first = words.units(words.packs()["en-sh-g3"])[0]["words"]
        words.log_answer(k["id"], "en-sh-g3", first[0]["w"], True, "read", mode="probe")   # 热身里已经认出来的词
        words.daily_new(k["id"])
        fronts = {c["front"] for c in _new_cards(k["id"])}
        assert first[0]["w"] not in fronts and first[3]["w"] in fronts

        # 欠着要复习的词太多：先不加新词
        k2 = _kid(c, monkeypatch, "wd3@x.com")
        db.run("DELETE FROM cards WHERE user_id=?", k2["id"])
        for i in range(words.BACKLOG_STOP):
            cid = engine.add_card(k2["id"], "word", f"zz{i}", "测试", {}, due=db.today() - timedelta(days=1))
            db.run("UPDATE cards SET reviews=1 WHERE id=?", cid)
        assert words.daily_new(k2["id"]) == 0


def test_ladder_levels_from_answers(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "wd4@x.com")
        day1 = (db.today() - timedelta(days=3)).isoformat() + "T10:00:00"
        for tier, ok, at in (("read", True, day1), ("reverse", True, day1), ("reverse", True, None), ("spell", False, None)):
            from app import evidence
            evidence.log(k["id"], "word", "en-core-g3:cat", "answer", correct=ok, data={"tier": tier}, at=at)
        lad = words.ladder(k["id"])
        assert lad["cat"]["level"] == 3 and lad["cat"]["ok"] and lad["cat"]["wobbly"]   # 两天都想得起，最近一次拼错了
        st = words.stats(k["id"])
        assert st["known"] >= 1 and "cat" in st["wobbly"]
        assert c.get("/words/map").status_code == 200 and "我认识的词" in c.get("/words/map").text


def test_examples_and_cloze_in_review(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "wd5@x.com")
        db.run("DELETE FROM cards WHERE user_id=?", k["id"])
        monkeypatch.setattr(prefetch, "MODE", "inline")
        words.daily_new(k["id"])
        words.prepare(k["id"])                               # 后台写例句（测试里当场跑完，假 AI 照提示词里的词写）
        new = _new_cards(k["id"])
        b = words.band("G3")
        got = words.examples([x["front"] for x in new], b)
        assert set(got) == {x["front"].lower() for x in new}
        assert words._clean("cat", [{"en": "I like cats.", "zh": ""}, {"en": "A [[dog]] and a [[cat]].", "zh": ""}]) == []

        # 新词第一次是学习卡：先猜（不算对错）→ 揭晓；不当场考，隔开几个词以后回来认意思（check）
        card = db.one("SELECT * FROM cards WHERE id=?", new[0]["id"])
        q = recall.quiz(k["id"], card)
        assert q["mode"] == "learn" and q["example"] and "use" not in q and q["meaning"] in q["guess"]
        chk = q["check"]
        assert chk["mode"] == "meaning" and chk["q"] == card["front"] and q["meaning"] in chk["options"] and len(chk["options"]) == 4
        r = c.post(f"/api/review/{card['id']}/answer", json={"mode": "learn", "check": "meaning", "guess": "不对的意思",
                                                             "answer": q["meaning"]}).json()
        assert r["correct"] and r["pick"] == q["meaning"]
        ev = db.q("SELECT kind, data FROM events WHERE user_id=? AND target='word' AND target_id LIKE ?", k["id"], f"%:{card['front']}")
        assert {e["kind"] for e in ev} == {"guess", "answer"}
        assert any((db.jload(e["data"], {}) or {}).get("tier") == "read" for e in ev if e["kind"] == "answer")
        card = db.one("SELECT * FROM cards WHERE id=?", card["id"])
        assert card["reviews"] == 1 and card["due"] == (db.today() + timedelta(days=1)).isoformat()   # 第二天第一次复习
        q2 = recall.quiz(k["id"], card)
        assert q2["mode"] == "cloze" and "____" in q2["q"]          # 第二天换方向：放回句子里选英文
        r = c.post(f"/api/review/{card['id']}/answer", json={"mode": "cloze", "answer": card["front"], "q": q2["q"]}).json()
        assert r["correct"] and r["full"] and "[[" not in r["full"]
        assert db.one("SELECT due FROM cards WHERE id=?", card["id"])["due"] == (db.today() + timedelta(days=2)).isoformat()  # 1/2/4/7

        # 看过、这一轮没隔开（或出不了题）：今天不考，明天第一次考
        other = new[1]
        assert c.post(f"/api/review/{other['id']}/answer", json={"mode": "seen"}).json()["seen"]
        row = db.one("SELECT * FROM cards WHERE id=?", other["id"])
        assert row["due"] == (db.today() + timedelta(days=1)).isoformat() and recall.quiz(k["id"], row)["mode"] != "learn"
        assert not db.q("SELECT 1 FROM events WHERE user_id=? AND target='word' AND target_id LIKE ?", k["id"], f"%:{other['front']}")

        # 页面：单词任务是「几个新词」
        plan = c.post("/api/plan/rebuild").json()["plan"]
        t = next(x for x in plan if x["type"] == "words")
        assert "新词" in t["title"]


def test_run_hint_follows_word_level():
    assert words.run_hint(None, 0.5) == 0                          # 新词：整个词写出来照着拼
    assert words.run_hint(None, 0.9) == 1                          # 水平高的，没见过的词也不用照着拼
    assert words.run_hint({"level": 0, "ok": True}, 0.5) == 1      # 答对过：中文 + 首字母
    assert words.run_hint({"level": 4}, 0.5) == 2                  # 拼得出：只给中文和读音
    assert words.run_hint({"level": 4, "wobbly": True}, 0.5) == 1  # 最近一次错了：退一档


def test_runner_game(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "wd6@x.com")
        g = arena.GAMES["runner"]
        assert "cloze" not in arena.GAMES                          # 句子补洞不在乐园里了（补句子还是复习里的一种题）
        assert list(arena.GAMES)[0] == "runner" and g.ask == ("words", "run") and g.study and not g.freeze and g.total == 1000
        assert g.level(1)["words"] == 4 and g.level(1)["bars"] == 0 and g.level(1000)["words"] == 10
        assert g.level(1000)["speed"] > g.level(1)["speed"]
        assert g.checks(g.level(1), {"deaths": 0, "score": 3}, None) == {"clear": True, "safe": True, "clean": True}
        assert g.checks(g.level(1), {"deaths": 1, "score": 2}, None) == {"clear": True, "safe": False, "clean": False}
        arena.set_game_unlock(k["id"], "free")
        k = dict(db.one("SELECT * FROM users WHERE id=?", k["id"]))
        assert c.get("/arena/runner").status_code == 200

        # 出的是一个要拼的词：前端按 run 摆字母；摔倒答题时同一道题就是「把它拼出来」
        m = matches.start(k, "runner")
        q = matches.question(k, m["match_id"], random.Random(3))["item"]
        assert q["type"] == "fill" and q["keypad"] == "letters" and q["run"]["w"] and q["run"]["hint"] in (0, 1, 2)
        assert q["say"] == q["run"]["w"]
        res = matches.answer(k, m["match_id"], q["id"], q["run"]["w"])
        assert res["correct"] and res["word"] == q["run"]["w"]
        ev = db.one("SELECT data FROM events WHERE user_id=? AND target='word' ORDER BY id DESC LIMIT 1", k["id"])
        assert (db.jload(ev["data"], {}) or {}).get("tier") in words.RUN_HINTS

        # 撞错过字母：报撞到的那串，算没拼对，词进复习
        q = matches.question(k, m["match_id"], random.Random(4))["item"]
        res = matches.answer(k, m["match_id"], q["id"], q["run"]["w"] + "x")
        assert not res["correct"] and res["word"] == q["run"]["w"]
        assert db.one("SELECT 1 FROM cards WHERE user_id=? AND LOWER(front)=?", k["id"], q["run"]["w"].lower())


def test_adult_runner_has_words_and_no_game_clock(monkeypatch):
    with TestClient(app) as c:
        if not auth.by_email("admin@x.com"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        uid = auth.create_user("wd-run@x.com", "secret1", "自学", "kid", grade="ADULT")
        c.get("/logout")
        c.post("/login", data={"email": "wd-run@x.com", "password": "secret1"})
        att = {a["pack"]["id"]: a for a in words.attached(uid)}
        assert "en-scene-restaurant" in att and not att["en-scene-restaurant"]["main"]   # 还没挑包：先挂上场景词，不每天加新词
        u = dict(db.one("SELECT * FROM users WHERE id=?", uid))
        assert arena.game_minutes(u) == 0 and arena.free_for(u, arena.GAMES["runner"])   # 自学的成人不玩乐园，跑酷照样能跑
        assert not arena.free_for(u, arena.GAMES["race"])
        page = c.get("/arena/runner")
        assert page.status_code == 200 and "/words/map" in page.text
        assert "单词跑酷" in c.get("/words/map").text
        m = matches.start(u, "runner")
        assert m["unlimited"]
        q = matches.question(u, m["match_id"], random.Random(5))["item"]
        scene = {w["w"] for wl in words.packs().values() if wl.get("shelf") == "scene" or wl.get("starter") for w in wl["words"]}
        assert q["run"]["w"] in scene

        # 自己挑了包：只用挑的
        c.post("/words/packs", data={"ref": "en-core-g8-igcse", "daily_amount": "5"})
        assert "en-scene-restaurant" not in {a["pack"]["id"] for a in words.attached(uid)}


def test_adult_picks_own_packs(monkeypatch):
    with TestClient(app) as c:
        if not auth.by_email("admin@x.com"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        uid = auth.create_user("wd-adult@x.com", "secret1", "自学", "kid", grade="ADULT")
        c.get("/logout")
        c.post("/login", data={"email": "wd-adult@x.com", "password": "secret1"})
        assert not any(a["main"] for a in words.attached(uid))       # 成人不自动加新词
        assert "单词包书架" in c.get("/words/map").text and "加上" in c.get("/words/map").text
        c.post("/words/packs", data={"ref": "en-core-g8-igcse", "daily_amount": "6"})
        a = next(a for a in words.attached(uid) if a["pack"]["id"] == "en-core-g8-igcse")
        assert a["main"] and a["per_day"] == 6
        # 孩子的包由家长管
        _kid(c, monkeypatch, "wd7@x.com")
        assert c.post("/words/packs", data={"ref": "en-core-g3"}).status_code == 403


def test_textbook_packs_follow_school_unit(monkeypatch):
    """课本单词包：江苏七年级选译林版，主包是译林七年级；新词先出学校正在上的那一单元；单词页、单词包页、书架。"""
    from app import schoolyear
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "wd-yl@x.com", grade="G7", subj={"subj_english": "eng-yilin"})
        att = {a["pack"]["id"]: a for a in words.attached(k["id"])}
        assert att["en-yl-g7"]["main"] and not att["en-yl-g6"]["main"] and "en-sh-g7" not in att and "en-sh-junior" not in att
        wl = words.packs()["en-yl-g7"]
        e = next(e for e in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", k["id"]) if e["pack_id"] == "eng-yilin")
        est = schoolyear.estimate(dict(db.one("SELECT * FROM users WHERE id=?", k["id"])), e)
        i = words.school_unit(k["id"], wl)
        if est["current"]:   # 上课期间：对上同一册同一单元
            assert wl["units"][i]["unit"] == est["current"]["name"] and wl["units"][i]["book"] == est["current"]["book"]
            db.run("DELETE FROM cards WHERE user_id=?", k["id"])
            words.daily_new(k["id"])
            unit_words = set(wl["units"][i]["words"])
            assert {c["front"] for c in _new_cards(k["id"]) if (db.jload(c["extra"], {}) or {}).get("list") == "en-yl-g7"} <= unit_words
        else:
            assert i is None

        c.get("/logout")
        c.post("/login", data={"email": "wd-yl@x.com", "password": "2580"})
        home = c.get("/words/map").text
        assert "单词包书架" in home and "译林版 七年级英语单词包" in home and "上海 一年级" not in home   # 孩子只看自己这套课本
        page = c.get("/words/pack/en-yl-g7").text
        assert "上册 Unit 1 This is me!" in page and "greet" in page
        assert c.get("/words/pack/nope").status_code == 404


def test_word_pack_content():
    """单词包内容：每个词都有中文；组里的词都在包里；课标词表的数对得上；课本包一个年级一包。"""
    ps = words.packs()
    for wl in ps.values():
        assert all(w.get("zh") for w in wl["words"]), wl["id"]
        names = {w["w"] for w in wl["words"]}
        assert len(names) == len(wl["words"]), wl["id"]
        for u in wl.get("units") or []:
            assert set(u["words"]) <= names, (wl["id"], u["title"])
        assert words.shelf(wl) in words.SHELVES
    assert len(ps["en-std-primary"]["words"]) == 505 and len(ps["en-exam-zhongkao"]["words"]) >= 1590
    assert {f"en-yl-g{g}" for g in range(3, 10)} <= set(ps) and {f"en-sh-g{g}" for g in range(1, 7)} <= set(ps)
    assert {"en-hs-g10", "en-hs-g11", "en-hs-g12", "en-bsl-1", "en-ngsl-1"} <= set(ps)


def test_adult_shelves_and_parent_groups(monkeypatch):
    with TestClient(app) as c:
        uid = auth.create_user("wd-shelf@x.com", "secret1", "自学", "kid", grade="ADULT")
        ids = [sh["id"] for sh in words.shelves(uid)]
        assert ids[:3] == ["adult", "scene", "exam"] and "grade" in ids
        c.get("/logout")
        c.post("/login", data={"email": "wd-shelf@x.com", "password": "secret1"})
        c.post("/words/packs", data={"ref": "en-bsl-1", "daily_amount": "5", "back": "pack"})
        assert next(a for a in words.attached(uid) if a["pack"]["id"] == "en-bsl-1")["main"]
        assert "商务英语核心词（上）" in c.get("/words/map").text
        groups = dict(words.grouped())
        assert words.SHELVES["exam"] in groups and words.SHELVES["grade"] in groups
