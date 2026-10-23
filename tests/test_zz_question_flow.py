"""一次快练 / 一局游戏里的出题顺序：出过的不再出，答错的隔几道再考一次（只一次）；单词换着考法；高年级不出低龄口算。"""
import random

from fastapi.testclient import TestClient

from app import db, itemtypes, sprint
from app.arena import sources
from app.catalog import catalog
from app.main import app
from tests.test_zz_mix import _setup


def _kid(c, monkeypatch, email, grade, subj):
    kid = _setup(c, monkeypatch, email, grade=grade, subj=subj)
    return dict(db.one("SELECT * FROM users WHERE id=?", kid))


def _right(it):
    a = it["answer"]
    return a[0] if isinstance(a, list) else a


def test_drill_no_repeats_and_one_retry(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "qf1@x.com", "G3", {"subj_math": "math-shanghai", "subj_english": "eng-shanghai"})
        run = sprint.start(k, drill=True)
        rng = random.Random(7)
        served, wrong_at = [], {}
        for i in range(40):
            q = sprint.question(k, run["run_id"], rng)["item"]
            it = db.jload(db.one("SELECT data FROM sprint_runs WHERE id=?", run["run_id"])["data"])["pending"]["item"]
            served.append((sources.key(it), bool(it.get("retry"))))
            ok = i % 5 != 0          # 每 5 道错 1 道
            sprint.answer(k, run["run_id"], q["id"], _right(it) if ok else "zzz")
            if not ok and not it.get("retry"):
                wrong_at[sources.key(it)] = i
        fresh = [key for key, retry in served if not retry]
        assert len(fresh) == len(set(fresh))                        # 新题一道都不重复
        retries = [(i, key) for i, (key, retry) in enumerate(served) if retry]
        assert retries and len({key for _, key in retries}) == len(retries)   # 每道错题只再考一次
        for i, key in retries:
            assert i - wrong_at[key] == sources.RETRY_GAP + 1      # 中间隔了 4 道别的题


def test_word_formats_and_spelling(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "qf2@x.com", "G4", {"subj_english": "eng-shanghai"})
        ctx = sources.Ctx(k)
        ctx["mode"] = "sprint"
        rng = random.Random(3)
        fmts = {}
        for _ in range(120):
            it = sources.SOURCES["words"].pick(k, ctx, 0.85, rng)
            fmts.setdefault(it["fmt"], it)
        assert {"read", "reverse", "listen", "spell"} <= set(fmts)
        sp = fmts["spell"]
        assert sp["type"] == "fill" and sp["family"].endswith(":spell")
        assert set(sp["word"].lower()) <= set(sp["letters"]) and len(sp["letters"]) <= 11
        assert itemtypes.check(sp, sp["word"].upper()) and not itemtypes.check(sp, sp["word"] + "x")
        assert sources.public(sp)["letters"] == sp["letters"] and "answer" not in sources.public(sp)
        assert sp["word"] not in fmts["listen"]["q"]                 # 听音：题面上不出现这个词
        ctx["mode"] = "game"                                         # 游戏里不出听音（可能关着声音）
        assert all(sources.SOURCES["words"].pick(k, ctx, 0.85, rng)["fmt"] != "listen" for _ in range(60))


def test_no_far_behind_math(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "qf3@x.com", "G8", {"subj_physics": "phy-cambridge"})
        kps = {kp for kp, _ in sources.SOURCES["math"].pool(sources.Ctx(k), 0.8)}
        assert "MSH-NUM-02" not in kps and "MSH-NUM-10" not in kps   # 初二不出 10 以内加减、乘法口诀
        assert "PHY-IG-1.3-01" in kps
        db.run("INSERT INTO mastery(user_id, kp_id, score, attempts, correct, status, source, updated_at) VALUES(?,?,?,?,?,?,?,?)",
               k["id"], "MSH-NUM-10", 0.3, 4, 1, "weak", "practice", db.now())
        assert "MSH-NUM-10" in {kp for kp, _ in sources.SOURCES["math"].pool(sources.Ctx(k), 0.8)}   # 查出薄弱了就出


def test_one_ability_for_practice_and_games(monkeypatch):
    """每日练习里的作答也更新游戏、快练用的水平 θ；自己判的简答题不算；刚看过答案再答的那一道不算。"""
    from app import ability, bank, engine
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "qf4@x.com", "G8", {"subj_physics": "phy-cambridge"})
        kp = "PHY-IG-1.3-01"
        it = bank.save_items(kp, [{"type": "mcq", "q": "Weight is measured in?", "options": ["N", "kg", "m", "s"], "answer": 0}])[0]
        assert ability.scale_of(it) == ("bank", 3)
        assert ability.stored_ability(k["id"], kp) is None
        engine.record_attempt(k["id"], it, kp, "practice", True, "0")
        t1, n1 = ability.stored_ability(k["id"], kp)
        assert n1 == 1
        engine.record_attempt(k["id"], it, kp, "practice", False, "1")
        t2, n2 = ability.stored_ability(k["id"], kp)
        assert n2 == 2 and t2 < t1                                    # 练习里答错，游戏里的题跟着变容易
        engine.record_attempt(k["id"], it, kp, "practice", True, "0", calibrate=False)
        assert ability.stored_ability(k["id"], kp)[1] == 2
        short = {"id": "x-short", "type": "short", "q": "Explain weight.", "answer": "..."}
        assert ability.scale_of(short) is None
        # 游戏 / 快练出题时直接用这个 θ
        assert sources.kp_ability(sources.Ctx(k), kp)[0] == t2


def test_due_reviews_come_first(monkeypatch):
    """快忘了的知识点在快练和游戏里多出；今天该复习的生词卡在快练里答了就算复习过。"""
    from datetime import timedelta
    from app import engine
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "qf5@x.com", "G4", {"subj_english": "eng-shanghai"})
        ctx = sources.Ctx(k)
        assert ctx.due == set()   # 新孩子没有快忘了的
        ctx["due"] = {"MSH-NUM-17"}
        assert sources._status_weight(ctx, "MSH-NUM-17", True) == sources.DUE_BOOST * sources._status_weight(ctx, "MSH-NUM-18", True)

        cid = engine.add_card(k["id"], "word", "giraffe", "长颈鹿", {"list": "x"}, due=db.today() - timedelta(days=1))
        ctx = sources.Ctx(k)
        ctx["mode"] = "game"
        rng = random.Random(2)
        it = next(x for x in (sources.SOURCES["words"].pick(k, ctx, 0.85, rng) for _ in range(60)) if x.get("card"))
        assert it["card"] == cid and it["word"] == "giraffe"
        it["src"] = "words"
        sources.settle(k, {}, it, it["word"] if it["type"] == "fill" else it["answer"], False, 4000, mode="sprint", weight=0.75)
        card = db.one("SELECT due, reviews FROM cards WHERE id=?", cid)
        assert card["reviews"] == 1 and str(card["due"]) > db.today().isoformat()   # 答对了：排到以后


def test_same_item_not_again_anywhere(monkeypatch):
    """练习、游戏、快练取题都走 engine.items_for：做过的题不再给（不请 AI 时只给没做过的，由后台去出新题）。"""
    from app import bank, engine, llm
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "qf6@x.com", "G8", {"subj_physics": "phy-cambridge"})
        kp = "PHY-IG-1.4-01"
        a, b = bank.save_items(kp, [{"type": "mcq", "q": "Density unit?", "options": ["kg/m³", "N", "J", "W"], "answer": 0},
                                     {"type": "num", "q": "m=10 g, V=5 cm³. ρ = ? g/cm³", "answer": 2}])
        monkeypatch.setattr(llm, "enabled", lambda: True)
        got = engine.items_for(k["id"], kp, n=5, purpose="quick", ai=False)
        assert {x["id"] for x in got} == {a["id"], b["id"]}
        engine.record_attempt(k["id"], a, kp, "practice", True, "0")
        assert [x["id"] for x in engine.items_for(k["id"], kp, n=5, purpose="quick", ai=False)] == [b["id"]]
        engine.record_attempt(k["id"], b, kp, "game", False, "1")
        assert engine.items_for(k["id"], kp, n=5, purpose="quick", ai=False) == []      # 都做过了：等后台出新题
        old = engine.items_for(k["id"], kp, n=5, purpose="quick", ai=False, fallback=True)
        assert [x["id"] for x in old] == [b["id"]]       # 实在出不了新题：只拿做错过的凑，做对过的不再出（DESIGN 5.46）
        long = bank.save_items(kp, [{"type": "mcq", "q": "x" * 120, "options": ["a", "b", "c", "d"], "answer": 0}])[0]
        assert long["id"] not in {x["id"] for x in engine.items_for(k["id"], kp, n=5, purpose="quick", ai=False)}   # 快答只要短题


def test_bank_source_covers_wide_and_asks_for_new(monkeypatch):
    """游戏和快练的题铺到所有在学科目（英语语法也在内，写作、听说这种答不快的不出）；没有新题就让后台出，这次换一道。"""
    from app import prefetch
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "qf7@x.com", "G8", {"subj_physics": "phy-cambridge", "subj_english": "eng-cambridge"})
        src = sources.SOURCES["bank"]
        cats = src._kps(sources.Ctx(k))
        allk = [kp for ks in cats.values() for kp in ks]
        packs = {catalog.kps[kp]["pack"] for kp in allk}
        assert {"phy-cambridge", "eng-cambridge"} <= packs
        assert any(catalog.kps[kp].get("strand") == "GRA" for kp in allk)
        assert not any(catalog.kps[kp].get("strand") in ("WRI", "LIS") for kp in allk if catalog.kps[kp]["pack"] == "eng-cambridge")
        asked = []
        monkeypatch.setattr(prefetch, "quick", lambda uid, kp, grade="": asked.append(kp))
        monkeypatch.setattr(sources.engine, "items_for", lambda *a, **kw: [])   # 题库里一道新的快答题都没有
        assert src.pick(k, sources.Ctx(k), 0.8, random.Random(1)) is None and len(asked) == sources.KP_TRIES
        it = sources.pick(k, 0.8, random.Random(1))           # 题库题一时出不了：换别的题源，照样有题
        assert it and it["src"] in ("math", "words")


def test_quick_generation_budget(monkeypatch):
    from app import prefetch
    calls = []
    monkeypatch.setattr(prefetch, "kick", lambda *a, **kw: calls.append(a[1]))
    for i in range(prefetch.QUICK_PER_DAY + 5):
        prefetch.quick(99, f"KP-{i}")
    assert len(calls) == prefetch.QUICK_PER_DAY


def test_words_known_recently_wait(monkeypatch):
    from app import evidence
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "qf8@x.com", "G4", {"subj_english": "eng-shanghai"})
        evidence.log(k["id"], "word", "eng-core-g3:apple", "answer", correct=True, mode="probe", fmt="choice")
        assert "apple" in sources.SOURCES["words"]._known(sources.Ctx(k))
