"""翻花田（app/arena/games/mines.py、static/play/mines.js）：每关都不用猜（求解器只靠推理和题格翻得完）、第一下一定翻开一大片、
题格在安全的格子上、每个世界的新东西、几个人一起每人一块花田、星星，题格出「选一个」的题。"""
import random

from fastapi.testclient import TestClient

from app import arena, db
from app.arena import matches
from app.arena.games.mines import graph, numbers, solve, variant, weights
from app.main import app
from tests.test_zz_words import _kid

SAMPLE = list(range(1, 37)) + list(range(37, 1001, 37)) + [1000]


def _first(lv):
    """第一下一口气翻开的那片（和前端 reveal 一样：数字 0 接着往外翻，题格不翻）。"""
    adj, w, num, qs = graph(lv), weights(lv), numbers(lv), set(lv["qs"])
    seen, todo = set(), [lv["start"]]
    while todo:
        x = todo.pop()
        if x in seen:
            continue
        seen.add(x)
        if num[x] == 0:
            todo.extend(y for y in adj[x] if y not in w and y not in qs)
    return seen


def test_mines_levels():
    g = arena.GAMES["mines"]
    assert list(arena.GAMES)[:6] == ["runner", "jump", "helix", "fly", "link", "mines"] and g.players == (1, 4) and g.multi
    assert g.shape == "choices" and not g.freeze and g.record == "seconds" and g.total == 1000 and g.per_world == 6
    for n in SAMPLE:
        lv = g.level(n)   # level() 里跑 check
        adj, w = graph(lv), weights(lv)
        assert solve(lv), n                                               # 不用猜：只靠推理和题格翻得完
        assert numbers(lv)[lv["start"]] == 0 and lv["start"] not in w     # 第一下安全，周围一圈都没有刺猬
        first = _first(lv)
        assert 6 <= len(first) <= len(adj) * 0.5, n                       # 一下翻开一大片，又不是一大半
        assert all(q in adj and q not in w and q not in first for q in lv["qs"]) and len(set(lv["qs"])) == len(lv["qs"])
        assert all(h in adj and h not in w and h not in lv["qs"] for h in lv["honey"])
        assert lv["cols"] <= 10 and lv["rows"] <= 12                     # 手机竖屏放得下
        wd = (n - 1) // 6 % 5
        first_round = n <= 30
        assert (lv["kind"] == "hex") == (wd == 2), n                      # 黄昏谷：六边形
        if first_round:
            assert bool(lv["holes"]) == (wd >= 1), n                      # 糖果园起：花田变形状
            assert any(v == 2 for v in w.values()) <= (wd == 3) and lv["big"] == (wd == 3), n   # 星空岛：大刺猬算 2 只
            assert bool(lv["tunnels"]) == (wd == 4), n                    # 火山口：地道
        for a, b in lv["tunnels"]:
            assert b in adj[a] and a in adj[b]
    assert g.level(1)["rows"] == 6 and len(g.level(1)["qs"]) == 1 and len(g.level(1000)["qs"]) == 4
    assert g.level(9) == g.level(9)   # 种子固定，每次一样
    assert g.title(7).startswith("糖果园") and g.title(31).startswith("晴空花园 2")


def test_mines_solver_refuses_guessing():
    """二选一只能蒙的花田：求解器翻不完（这样的关不出）；那格换成题格就翻得完。"""
    lv = {"kind": "sq", "rows": 2, "cols": 4, "holes": [], "tunnels": [], "mines": {"3": 1}, "start": 0, "qs": [], "big": False}
    assert not solve(lv)
    assert solve({**lv, "qs": [7]})
    assert solve({**lv, "mines": {}, "qs": []})


def test_mines_bad_level_is_refused():
    g = arena.GAMES["mines"]
    lv = dict(g.level(3))
    lv["qs"] = [int(next(iter(lv["mines"])))]   # 题格放在刺猬上
    try:
        g.check(lv, "x")
        raise AssertionError("题格在刺猬上也通过了")
    except ValueError:
        pass


def test_mines_each_player_gets_own_field():
    """几个人一起：每人一块花田（同一关翻一翻、转半圈），一样大、一样难，刺猬的位置不一样，抄不了。"""
    g = arena.GAMES["mines"]
    assert "seats" not in g.for_team(g.level(9), 1)
    for n in (1, 7, 13, 19, 25, 100, 1000):
        lv = g.level(n)
        team = g.for_team(lv, 4)
        seats = team["seats"]
        assert list(seats) == list(g.roles) and seats["sun"]["mines"] == lv["mines"]
        layouts = set()
        for role, st in seats.items():
            v = {**lv, **st}
            assert solve(v), (n, role)
            assert len(graph(v)) == len(graph(lv)) and sorted(weights(v).values()) == sorted(weights(lv).values())
            assert numbers(v)[v["start"]] == 0 and len(v["qs"]) == len(lv["qs"])
            layouts.add(tuple(sorted(weights(v))))
        assert len(layouts) == (2 if lv["kind"] == "hex" else 4), n
    for k in range(4):
        assert variant(g.level(13), k)["rows"] == g.level(13)["rows"]


def test_mines_stars_for_teams():
    g = arena.GAMES["mines"]
    lv = g.level(5)
    assert g.checks(lv, g.clean(lv, {"seconds": 40, "deaths": 0, "gates": 1}), None) == {"clear": True, "safe": True, "gates": True}
    assert g.checks(lv, g.clean(lv, {"deaths": 1, "gates": 0}), None) == {"clear": True, "safe": False, "gates": False}
    team = g.for_team(lv, 3)
    assert g.checks(team, {"deaths": 2, "gates": 1}, None) == {"clear": True, "safe": True, "gates": True}
    assert g.checks(team, {"deaths": 3, "gates": 0}, None) == {"clear": True, "safe": False, "gates": False}


def test_mines_page_and_gate_questions(monkeypatch):
    """游戏页带上 mines.js / mines.css；题格的题是「选一个」，答对、答错都记下来。"""
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "mn1@x.com")
        arena.set_game_unlock(k["id"], "free")
        k = dict(db.one("SELECT * FROM users WHERE id=?", k["id"]))
        page = c.get("/arena/mines")
        assert page.status_code == 200 and "/static/play/mines.js" in page.text and "/static/play/mines.css" in page.text
        m = matches.start(k, "mines")
        for i in range(4):
            q = matches.question(k, m["match_id"], random.Random(i), gate=f"mines1-{i}")["item"]
            assert 2 <= len(q["options"]) <= 4, q
            res = matches.answer(k, m["match_id"], q["id"], "0")
            assert "correct" in res and res.get("freeze_ms", 0) == 0
