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
        assert att["en-core-g3"]["main"] and att["en-core-g3"]["per_day"] == words.PER_DAY["primary"]
        assert "en-core-g1-2" in att and not att["en-core-g1-2"]["main"]          # 低年级的包：闪测和游戏里用，不每天加新词
        assert "en-core-g4-5" not in att                                         # 没到的年级不挂

        db.run("DELETE FROM cards WHERE user_id=?", k["id"])
        assert words.daily_new(k["id"]) == 4
        new = _new_cards(k["id"])
        pos = sorted(next(i for i, w in enumerate(words.units(words.packs()["en-core-g3"])[0]["words"]) if w["w"] == c["front"]) for c in new)
        assert pos == [0, 3, 6, 9]                         # 一组里隔着挑，挨着的（同一个话题）不在同一天
        assert all(c["due"] == db.today().isoformat() for c in new)
        assert words.daily_new(k["id"]) == 0               # 同一天不再加

        # 家长把每天调到 6 个：当天就补上 2 个；停掉这个包，不再加、闪测和游戏也不用它
        _login_parent(c)
        r = c.post(f"/parent/kids/{k['id']}/tracks", data={"kind": "words", "ref": "en-core-g3", "daily_amount": "6"})
        assert "已安排" in r.text and "单词包" in c.get(f"/parent/kids/{k['id']}/plan").text
        words.daily_new(k["id"])                            # 存的时候已经按新安排排过今天的计划
        assert len(_new_cards(k["id"])) == 6
        c.post(f"/parent/kids/{k['id']}/tracks", data={"kind": "words", "ref": "en-core-g3", "action": "stop"})
        assert "en-core-g3" not in {wl["id"] for wl in words.lists(k["id"])}
        assert engine.Paused(k["id"]).card(new[0])          # 停掉的包留下的卡先不叫回来复习


def _login_parent(c):
    c.get("/logout")
    c.post("/login", data={"email": "mixp@x.com", "password": "secret1"})


def test_new_words_skip_known_and_wait_for_backlog(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "wd2@x.com")
        db.run("DELETE FROM cards WHERE user_id=?", k["id"])
        first = words.units(words.packs()["en-core-g3"])[0]["words"]
        words.log_answer(k["id"], "en-core-g3", first[0]["w"], True, "read", mode="probe")   # 热身里已经认出来的词
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

        # 新词第一次是学习卡：先猜（不算对错）→ 揭晓 → 补洞用一次
        card = db.one("SELECT * FROM cards WHERE id=?", new[0]["id"])
        q = recall.quiz(k["id"], card)
        assert q["mode"] == "learn" and q["example"] and q["use"]["mode"] == "cloze" and "____" in q["use"]["q"]
        assert card["front"] in q["use"]["options"] and q["meaning"] in q["guess"]
        r = c.post(f"/api/review/{card['id']}/answer", json={"mode": "learn", "use": "cloze", "guess": "不对的意思",
                                                             "answer": card["front"], "q": q["use"]["q"]}).json()
        assert r["correct"] and r["full"] and "[[" not in r["full"]
        ev = db.q("SELECT kind, data FROM events WHERE user_id=? AND target='word' AND target_id LIKE ?", k["id"], f"%:{card['front']}")
        assert {e["kind"] for e in ev} == {"guess", "answer"}
        assert any((db.jload(e["data"], {}) or {}).get("tier") == "cloze" for e in ev if e["kind"] == "answer")
        card = db.one("SELECT * FROM cards WHERE id=?", card["id"])
        assert card["reviews"] == 1 and recall.quiz(k["id"], card)["mode"] == "cloze"   # 复习过一轮：放回句子里选

        # 页面：单词任务是「几个新词」
        plan = c.post("/api/plan/rebuild").json()["plan"]
        t = next(x for x in plan if x["type"] == "words")
        assert "新词" in t["title"]


def test_cloze_game(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "wd6@x.com")
        g = arena.GAMES["cloze"]
        assert g.ask == ("words", "cloze") and not g.freeze and g.total == 1000
        assert g.level(1)["holes"] == 6 and g.level(1000)["holes"] == 10
        assert g.checks(g.level(1), {"score": 5}, 95) == {"clear": True, "focus": True, "streak": True}
        assert c.get("/arena/cloze").status_code == 200

        # 还没有例句：先出看中文选英文（不让孩子等），后台去写；写好了就出句子补洞
        arena.set_game_unlock(k["id"], "free")
        k = dict(db.one("SELECT * FROM users WHERE id=?", k["id"]))
        m = matches.start(k, "cloze")
        it = sources.next_item(k, {}, 0.8, random.Random(1), ask=g.ask)
        assert it["src"] == "words"
        monkeypatch.setattr(prefetch, "MODE", "inline")
        for lid in ("en-core-g1-2", "en-core-g3"):
            ws = [{"w": w["w"], "zh": w["zh"], "pos": w.get("pos", "")} for w in words.packs()[lid]["words"]]
            for i in range(0, len(ws), 50):
                words.write_examples(ws[i:i + 50], words.band("G3"))
        q = matches.question(k, m["match_id"], random.Random(2))["item"]
        assert q["cloze"] and "____" in q["q"] and q["say"] == ""     # 先读出整句就把答案说出来了
        pend = db.jload(db.one("SELECT data FROM arena_matches WHERE id=?", m["match_id"])["data"])["pending"]["item"]
        res = matches.answer(k, m["match_id"], q["id"], pend["answer"] if pend["type"] == "mcq" else pend["answer"][0])
        assert res["correct"] and res["word"] == pend["word"] and res["full"]
        assert db.one("SELECT 1 FROM events WHERE user_id=? AND target='word' AND data LIKE '%cloze%'", k["id"])


def test_adult_picks_own_packs(monkeypatch):
    with TestClient(app) as c:
        if not auth.by_email("admin@x.com"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        uid = auth.create_user("wd-adult@x.com", "secret1", "自学", "kid", grade="ADULT")
        c.get("/logout")
        c.post("/login", data={"email": "wd-adult@x.com", "password": "secret1"})
        assert not any(a["main"] for a in words.attached(uid))       # 成人不自动加新词
        assert "加一个单词包" in c.get("/words/map").text
        c.post("/words/packs", data={"ref": "en-core-g8-igcse", "daily_amount": "6"})
        a = next(a for a in words.attached(uid) if a["pack"]["id"] == "en-core-g8-igcse")
        assert a["main"] and a["per_day"] == 6
        # 孩子的包由家长管
        _kid(c, monkeypatch, "wd7@x.com")
        assert c.post("/words/packs", data={"ref": "en-core-g3"}).status_code == 403
