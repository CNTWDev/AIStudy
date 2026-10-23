"""向下跳（app/arena/games/helix.py、static/play/helix.js）：关卡生成、题都写成短选项（题门）、几个人一起的星星。"""
import random

from fastapi.testclient import TestClient

from app import arena, db, itemtypes, quickgen
from app.arena import matches, sources
from app.arena.games.helix import SECTORS
from app.main import app
from tests.test_zz_words import _kid


def test_helix_levels():
    g = arena.GAMES["helix"]
    assert list(arena.GAMES)[:2] == ["runner", "helix"] and g.players == (1, 4) and g.multi and g.shape == "choices" and not g.freeze
    assert g.total == 1000
    for n in list(range(1, 40)) + [100, 500, 1000]:
        lv = g.level(n)   # level() 里跑 check：每层 12 格、普通层有洞也有落脚的地方
        rows = lv["rows"]
        gates = [i for i, r in enumerate(rows) if r == "q" * SECTORS]
        assert lv["gates"] == len(gates) >= 2 and 0 not in gates and len(rows) - 1 not in gates
        for i in gates:   # 题门前后一层没有刺：从题门掉下来不会马上撞刺
            assert "s" not in rows[i - 1] and "s" not in rows[i + 1]
        if n <= 6:        # 第一个世界：洞正下方不放刺，直直掉下去不会死；没有会转的层、闪的刺
            assert lv["spin"] == 0 and lv["blink"] == 0
            for a, b in zip(rows, rows[1:]):
                assert not any(x == "g" and y == "s" for x, y in zip(a, b))
    assert len(g.level(1000)["rows"]) > len(g.level(1)["rows"])
    assert g.level(1000)["rows"][5].count("s") > g.level(1)["rows"][5].count("s") or g.level(1000)["spin"] > 0
    assert g.level(3) == g.level(3)   # 种子固定，每次一样


def test_helix_stars_for_teams():
    g = arena.GAMES["helix"]
    lv = g.level(1)
    assert g.checks(lv, {"deaths": 0, "score": lv["gates"]}, None) == {"clear": True, "safe": True, "gates": True}
    assert g.checks(lv, {"deaths": 1, "score": lv["gates"] - 1}, None) == {"clear": True, "safe": False, "gates": False}
    team = g.for_team(lv, 3)   # 三个人：倒下 2 次、题门错 2 道以内都还有星
    assert team["team"] == 3 and "team" not in lv
    assert g.checks(team, {"deaths": 2, "score": lv["gates"] * 3 - 2}, None) == {"clear": True, "safe": True, "gates": True}
    assert g.checks(team, {"deaths": 3, "score": lv["gates"] * 3 - 3}, None) == {"clear": True, "safe": False, "gates": False}


def test_as_choices():
    with TestClient(app):  # 建好库（校准表）
        rng = random.Random(5)
        for fam in quickgen.FAMILIES:
            for lv in quickgen.LEVELS:
                it = quickgen.generate(fam, lv, rng)
                ch = sources.as_choices({**it, "theta": 0.0}, rng)
                if ch is None:   # 写不成选项的（题太长）：换别的题
                    assert sources._width(it["q"]) > sources.Q_WIDTH or it["type"] not in ("num", "fill"), it
                    continue
                assert ch["type"] == "mcq" and 2 <= len(ch["options"]) <= 4 and len(set(ch["options"])) == len(ch["options"])
                assert all(sources._width(o) <= sources.OPT_WIDTH for o in ch["options"])
                right = ch["options"][ch["answer"]]
                assert itemtypes.check(it, right), (fam, lv, it, ch)                      # 对的选项就是原来的答案
                assert not any(itemtypes.check(it, o) for i, o in enumerate(ch["options"]) if i != ch["answer"]), (it, ch)   # 错的选项都不对
                assert itemtypes.check(ch, str(ch["answer"])) and ch["family"] == f"{fam}:pick" and ch["kp_id"] == it["kp_id"]
        long = {"type": "mcq", "q": "x", "options": ["很长很长很长很长的一个选项", "b", "c"], "answer": 0}
        assert sources.as_choices(long, rng) is None
        ok = sources.as_choices({"type": "mcq", "q": "x", "options": ["很长很长很长很长的一个选项", "a", "b"], "answer": 1}, rng)
        assert ok and sorted(ok["options"]) == ["a", "b"] and ok["options"][ok["answer"]] == "a"
        assert sources.as_choices({"type": "fill", "q": "spell", "answer": ["cat"], "keypad": "letters"}, rng) is None


def test_helix_questions_are_short_choices(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "hx1@x.com")
        arena.set_game_unlock(k["id"], "free")
        k = dict(db.one("SELECT * FROM users WHERE id=?", k["id"]))
        assert c.get("/arena/helix").status_code == 200
        m = matches.start(k, "helix")
        for i in range(12):
            q = matches.question(k, m["match_id"], random.Random(i))["item"]
            assert q["widget"] == "choice" and 2 <= len(q["options"]) <= 4, q
            assert all(sources._width(o) <= sources.OPT_WIDTH for o in q["options"])
            res = matches.answer(k, m["match_id"], q["id"], "0")   # 掉进第一个洞
            assert res["answer"][:2] in ("A.", "B.", "C.", "D.")   # 前端从这里知道对的是第几个洞
            assert res["correct"] or res["freeze_ms"] == 0
