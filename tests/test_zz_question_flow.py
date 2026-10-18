"""一次快练 / 一局游戏里的出题顺序：出过的不再出，答错的隔几道再考一次（只一次）；单词换着考法；高年级不出低龄口算。"""
import random

from fastapi.testclient import TestClient

from app import db, itemtypes, sprint
from app.arena import sources
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
