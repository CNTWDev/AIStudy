"""往上跳（app/arena/games/jump.py、static/play/jump.js）：关卡只靠靠得住的板一定上得去、题板的云带里没有板、难度往上涨、几个人一起的星星、题都是短选项。"""
import random

import pytest
from fastapi.testclient import TestClient

from app import arena, db
from app.arena import matches, sources
from app.arena.games.jump import BAND, GAP_MAX, SOLID, TUNED
from app.main import app
from tests.test_zz_words import _kid


def _climb(lv):
    """一个最笨的小模拟：从地面开始，每次只跳到「一跳够得着」的靠得住的板（或题板的软云、软云上踩对后弹到的云带上面）。"""
    gates, y = set(lv["gates"]), 0.0
    steps = sorted({p[1] for p in lv["plats"] if p[2] in SOLID} | gates | {lv["height"]})
    for s in steps:
        reach = BAND + 0.02 if y in gates else GAP_MAX
        if s - y > reach + 1e-6:
            return y
        y = s
    return y


def test_jump_levels():
    g = arena.GAMES["jump"]
    assert list(arena.GAMES)[:3] == ["runner", "jump", "helix"] and g.players == (1, 4) and g.multi and g.shape == "choices" and not g.freeze
    assert g.total == 1000
    for n in list(range(1, 50)) + [100, 300, 500, 1000]:
        lv = g.level(n)   # level() 里跑 check：板子格式、云带里没板、靠得住的板一块接一块
        assert _climb(lv) == lv["height"], n
        assert len(lv["gates"]) >= 2 and all(0 < y < lv["height"] - BAND for y in lv["gates"])
        for y in lv["gates"]:   # 云带里没有板子、没有蜂蜜：只有踩对的弹簧过得去
            assert not any(y + 0.02 < p[1] < y + BAND - 0.04 for p in lv["plats"])
            assert not any(y < h[1] < y + BAND - 0.04 for h in lv["honey"])
        kinds = {p[2] for p in lv["plats"]}
        if n <= 6:            # 第一个世界：普通板 + 弹簧，镜头最慢
            assert kinds <= {"n", "s"} and not lv["wind"]
        if n <= 12:
            assert "x" not in kinds and "b" not in kinds and (lv["height"], lv["scroll"]) == (TUNED[n - 1][0], TUNED[n - 1][6])
    assert "m" in {p[2] for p in g.level(8)["plats"]} and "x" in {p[2] for p in g.level(14)["plats"]}
    assert "b" in {p[2] for p in g.level(20)["plats"]} and g.level(30)["wind"]
    first, last = g.level(1), g.level(1000)
    assert last["height"] > first["height"] and last["scroll"] > first["scroll"] and last["width"] < first["width"]
    assert g.level(3) == g.level(3)   # 种子固定，每次一样
    assert g.title(7) == "糖果塔 · 草坡" and g.level(7)["name"] == g.title(7)


def test_jump_check_catches_bad_levels():
    g = arena.GAMES["jump"]
    lv = g.level(1)
    with pytest.raises(ValueError, match="跳不上去"):
        g.check({**lv, "plats": [p for p in lv["plats"] if not 2 < p[1] < 2.5]}, "x")
    with pytest.raises(ValueError, match="云带"):
        g.check({**lv, "plats": lv["plats"] + [[0.5, lv["gates"][0] + 0.3, "x"]]}, "x")


def test_jump_stars_for_teams():
    g = arena.GAMES["jump"]
    lv = g.level(1)
    n = len(lv["gates"])
    assert g.checks(lv, {"deaths": 0, "score": n}, None) == {"clear": True, "safe": True, "gates": True}
    assert g.checks(lv, {"deaths": 1, "score": n - 1}, None) == {"clear": True, "safe": False, "gates": False}
    team = g.for_team(lv, 3)
    assert team["team"] == 3 and "team" not in lv
    assert g.checks(team, {"deaths": 2, "score": n * 3 - 2}, None) == {"clear": True, "safe": True, "gates": True}
    assert g.checks(team, {"deaths": 3, "score": n * 3 - 3}, None) == {"clear": True, "safe": False, "gates": False}


def test_jump_page_and_short_choices(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "jp1@x.com")
        arena.set_game_unlock(k["id"], "free")
        k = dict(db.one("SELECT * FROM users WHERE id=?", k["id"]))
        page = c.get("/arena/jump")
        assert page.status_code == 200 and "/static/play/jump.js" in page.text and "/static/play/jump.css" in page.text
        m = matches.start(k, "jump")
        for i in range(8):   # 答案写在板子上的牌子里：都是 2～4 个短选项
            q = matches.question(k, m["match_id"], random.Random(i))["item"]
            assert q["widget"] == "choice" and 2 <= len(q["options"]) <= 4, q
            assert all(sources._width(o) <= sources.OPT_WIDTH for o in q["options"])
            res = matches.answer(k, m["match_id"], q["id"], "0")   # 落在第一块板上
            assert res["answer"][:2] in ("A.", "B.", "C.", "D.")   # 前端从这里知道对的是哪一块
