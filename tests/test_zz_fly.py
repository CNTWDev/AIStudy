"""左右飞（app/arena/games/fly.py、static/play/fly.js）：关卡生成、每面墙都飞得过去（按前端的手感模拟）、几个人一起的星星、题都写成短选项（题墙）。"""
import random

from fastapi.testclient import TestClient

from app import arena, db
from app.arena import matches, sources
from app.arena.games.fly import SLOTS, gap_at
from app.main import app
from tests.test_zz_words import _kid

# 和 static/play/fly.js 一样的手感（屋子高 = 1）
RW, TS, BR, G, FLAP, VMAX = 0.68, 0.045, 0.032, 1.6, -0.62, 1.1
SL = (1 - 2 * TS) / SLOTS


def _fly(y, vy, target, half, cross, wind=0.0):
    """从一面墙飞到对面：每 0.1 秒决定扑不扑一下（孩子点屏幕的快慢），把能钻进口子的点法都找出来（深搜，同样的状态不重复搜），
    返回其中最靠口子正中间的那一种到墙时的 (高度, 速度)；怎么点都钻不进去返回 None。"""
    dt, step = 1 / 120, 12   # 一次决定管 12 小步 = 0.1 秒
    total = round((RW - 2 * BR) / (RW / cross) / dt)
    seen = set()

    ends = []

    def go(i, y, vy):
        if i >= total:
            if abs(y - target) < half - BR * 0.6:
                ends.append((y, vy))
            return
        key = (i, round(y, 2), round(vy, 1))
        if key in seen:
            return
        seen.add(key)
        for tap in (False, True):
            ny, nv = y, FLAP if tap else vy
            for _ in range(min(step, total - i)):
                nv = min(VMAX, nv + (G + wind) * dt)
                ny += nv * dt
                if ny - BR < TS * 0.8 or ny + BR > 1 - TS * 0.8:
                    break
            else:
                go(i + step, ny, nv)

    go(0, y, vy)
    return min(ends, key=lambda e: abs(e[0] - target) + 0.15 * max(0.0, -e[1]), default=None)   # 像孩子一样对准口子正中间，到墙时别正往上冲


def _gaps(row):
    """一面墙上所有能钻的口子（连着 2 格以上的空）：(从第几格起, 几格高)，大的在前。"""
    out, j = [], 0
    while j < len(row):
        k = j
        while k < len(row) and row[k] == ".":
            k += 1
        if k - j >= 2:
            out.append((j, k - j))
        j = max(k, j + 1)
    return sorted(out, key=lambda g: -g[1])


def test_fly_levels():
    g = arena.GAMES["fly"]
    assert list(arena.GAMES)[:4] == ["runner", "jump", "helix", "fly"] and g.players == (1, 4) and g.multi
    assert g.shape == "choices" and not g.freeze and g.record == "score" and g.total == 1000 and g.per_world == 6
    for n in list(range(1, 40)) + [100, 500, 1000]:
        lv = g.level(n)   # level() 里跑 check：每面墙 9 格、普通墙有口子
        rows = lv["rows"]
        gates = [i for i, r in enumerate(rows) if r == "q" * SLOTS]
        assert lv["walls"] == len(rows) and lv["gates"] == len(gates) >= 2
        assert 0 not in gates and 1 not in gates and len(rows) - 1 not in gates
        for r in rows:
            if r != "q" * SLOTS:
                start, size = gap_at(r)
                assert size >= 2 and r[0] == r[-1] == "s", r   # 口子够钻；两头贴着上下两排刺的格子总是刺
        w = (n - 1) // 6   # 新东西从它那个世界起才有
        assert (lv["slide"] > 0) == (w >= 1) and (lv["ball"] > 0) == (w >= 2) and (lv["blink"] > 0) == (w >= 3) and (lv["wind"] > 0) == (w >= 4)
    assert g.level(1000)["walls"] > g.level(1)["walls"] and g.level(1000)["cross"] < g.level(1)["cross"]
    assert sum(r.count("s") for r in g.level(1000)["rows"]) / g.level(1000)["walls"] > sum(r.count("s") for r in g.level(1)["rows"]) / g.level(1)["walls"]
    assert g.level(3) == g.level(3)   # 种子固定，每次一样
    assert g.title(7).startswith("糖果屋") and g.title(31).startswith("晴空花园 2")


def test_fly_every_wall_can_be_flown():
    """按前端的重力、扑一下的高度、横穿时间，一面墙一面墙飞过去：每个口子都钻得进去（题墙跳过：上下是软云）。"""
    g = arena.GAMES["fly"]
    for n in list(range(1, 31)) + list(range(31, 1001, 9)):
        lv = g.level(n)
        y, vy = 0.45, 0.0
        for i, r in enumerate(lv["rows"]):
            if r == "q" * SLOTS:
                y, vy = 0.55, 0.0
                continue
            outs = [o for start, size in _gaps(r)
                    if (o := _fly(y, vy, TS + (start + size / 2) * SL, size * SL / 2, lv["cross"], wind=lv["wind"] * (1 if i % 2 else -1)))]
            assert outs, (n, i, r)   # 哪个口子都行
            out = outs[0]
            y, vy = out   # 进了口子，没擦到口子两边的刺


def test_fly_stars_for_teams():
    g = arena.GAMES["fly"]
    lv = g.level(1)
    rep = g.clean(lv, {"seconds": 40, "deaths": 0, "score": 12, "gates": lv["gates"]})
    assert rep["gates"] == lv["gates"] and rep["score"] == 12
    assert g.checks(lv, rep, None) == {"clear": True, "safe": True, "gates": True}
    assert g.checks(lv, g.clean(lv, {"deaths": 1, "gates": "x"}), None) == {"clear": True, "safe": False, "gates": False}
    team = g.for_team(lv, 3)   # 三个人：倒下 2 次、题墙错 2 道以内都还有星
    assert team["team"] == 3 and "team" not in lv
    assert g.checks(team, {"deaths": 2, "gates": lv["gates"] * 3 - 2}, None) == {"clear": True, "safe": True, "gates": True}
    assert g.checks(team, {"deaths": 3, "gates": lv["gates"] * 3 - 3}, None) == {"clear": True, "safe": False, "gates": False}


def test_fly_questions_are_short_choices(monkeypatch):
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "fy1@x.com")
        arena.set_game_unlock(k["id"], "free")
        k = dict(db.one("SELECT * FROM users WHERE id=?", k["id"]))
        page = c.get("/arena/fly")
        assert page.status_code == 200 and "/static/play/fly.js" in page.text and "/static/play/fly.css" in page.text
        m = matches.start(k, "fly")
        for i in range(10):
            q = matches.question(k, m["match_id"], random.Random(i))["item"]
            assert q["widget"] == "choice" and 2 <= len(q["options"]) <= 4, q
            assert all(sources._width(o) <= sources.OPT_WIDTH for o in q["options"])
            res = matches.answer(k, m["match_id"], q["id"], "0")   # 撞到第一朵花
            assert res["answer"][:2] in ("A.", "B.", "C.", "D.")   # 前端从这里知道对的是哪朵
            assert res["correct"] or res["freeze_ms"] == 0
