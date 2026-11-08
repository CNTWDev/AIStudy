"""一笔连花路（app/arena/games/link.py、static/play/link.js）：关卡都走得满（每个点正好一次）、每个世界的新东西、
几个人一起的星星、排序题（拼单词的字母、按得数排的算式）和交答案。"""
import random

from fastapi.testclient import TestClient

from app import arena, db
from app.arena import matches, sources
from app.arena.games.link import graph, is_path, variant
from app.main import app
from tests.test_zz_game_questions import _user
from tests.test_zz_words import _kid


def test_link_levels():
    g = arena.GAMES["link"]
    assert list(arena.GAMES)[:5] == ["runner", "jump", "helix", "fly", "link"] and g.players == (1, 4) and g.multi
    assert g.shape == "sequence" and not g.freeze and g.record == "seconds" and g.total == 1000 and g.per_world == 6
    for n in list(range(1, 37)) + list(range(37, 1001, 23)) + [1000]:
        lv = g.level(n)   # level() 里跑 check：path 走满整张图
        adj = graph(lv)
        assert is_path(adj, lv["path"]), n
        w, scene = (n - 1) // 6, g.world_of(n)["scene"]
        hexed = w % 5 == 4
        assert (lv["kind"] == "hex") == hexed and lv["tut"] == (n <= 2)
        if not hexed:
            assert bool(lv["walls"]) == (w >= 1), n                 # 篱笆小院起有篱笆
            assert bool(lv["bridges"]) == (w >= 2), n               # 小溪起有小桥：横竖各走一次（两个点都在路上）
        assert bool(lv["warps"]) == (w >= 3), n                     # 魔法花园起有魔法花
        rows, cols = lv["rows"], lv["cols"]
        for a, b in lv["walls"]:                                   # 篱笆只围在两个相邻格中间，路不从那里走
            assert abs(a - b) in (1, cols) and frozenset((a, b)) not in {frozenset((x % (rows * cols), y % (rows * cols))) for x, y in zip(lv["path"], lv["path"][1:])}
        for b in lv["bridges"]:
            assert b in lv["path"] and rows * cols + b in lv["path"]
        assert scene in ("day", "candy", "night", "sunset")
    assert g.level(1)["rows"] == 4 and g.level(1000)["rows"] > g.level(30)["rows"]
    assert g.level(9) == g.level(9)   # 种子固定，每次一样
    assert g.title(7).startswith("篱笆小院") and g.title(31).startswith("晴空花园 2")


def test_link_bad_level_is_refused():
    g = arena.GAMES["link"]
    lv = dict(g.level(3))
    lv["path"] = lv["path"][:-1]   # 少走一格
    try:
        g.check(lv, "x")
        raise AssertionError("少走一格也通过了")
    except ValueError:
        pass


def test_link_stars_for_teams():
    g = arena.GAMES["link"]
    lv = g.level(5)
    rep = g.clean(lv, {"seconds": 40, "deaths": 0, "gates": 1})
    assert g.checks(lv, rep, None) == {"clear": True, "safe": True, "gates": True}
    assert g.checks(lv, g.clean(lv, {"deaths": 1, "gates": 0}), None) == {"clear": True, "safe": False, "gates": False}
    assert g.checks(g.level(1), g.clean(g.level(1), {"gates": 0}), None)["gates"]   # 教玩法的关没有题
    team = g.for_team(lv, 3)
    assert team["team"] == 3 and "team" not in lv
    assert g.checks(team, {"deaths": 2, "gates": 1}, None) == {"clear": True, "safe": True, "gates": True}
    assert g.checks(team, {"deaths": 3, "gates": 0}, None) == {"clear": True, "safe": False, "gates": False}


def test_link_each_player_gets_own_garden():
    """几个人一起：每人一个花园（同一关转过、翻过或倒着走），一样大、一样难，路各不一样——一个人走满了，别人照着抄不了。"""
    g = arena.GAMES["link"]
    assert "seats" not in g.for_team(g.level(9), 1)   # 一个人玩：原样
    for n in (1, 3, 9, 15, 20, 27, 100, 145, 250, 1000):
        lv = g.level(n)
        team = g.for_team(lv, 4)
        seats = team["seats"]
        assert list(seats) == list(g.roles) and seats["sun"]["path"] == lv["path"]   # 第一个角色原样
        cells = set()
        for role, st in seats.items():
            v = {**lv, **st}
            adj = graph(v)
            assert is_path(adj, v["path"]), (n, role)
            assert len(adj) == len(graph(lv)) and v["rows"] == lv["rows"] and v["kind"] == lv["kind"]
            assert len(v["walls"]) == len(lv["walls"]) and len(v["bridges"]) == len(lv["bridges"]) and len(v["warps"]) == len(lv["warps"])
            for b in v["bridges"]:
                assert b in v["path"] and v["rows"] * v["cols"] + b in v["path"]
            cells.add(tuple(x % (v["rows"] * v["cols"]) for x in v["path"]))
        assert len(cells) == 4, n   # 四个人的路都不一样
        # 拿别人的路在自己的花园里走：走不通（或者起点、终点不一样）
        mine, theirs = {**lv, **seats["berry"]}, seats["sun"]["path"]
        assert not (is_path(graph(mine), theirs) and theirs[0] == mine["path"][0] and theirs[-1] == mine["path"][-1]), n
    for n in (13, 145):   # 方格 8 种样子、六边形 4 种，都走得满、都不一样
        lv = g.level(n)
        k = 8 if lv["kind"] == "sq" else 4
        assert len({tuple(variant(lv, i)["path"]) for i in range(k)}) == k
    js = open("app/static/play/link.js", encoding="utf-8").read()
    assert "seatOf(S.base, p.role)" in js and "lineTo' : 'moveTo'](cx, cy)" not in js   # 小窗按他的花园画，不画线


def test_link_spelling_sequence(monkeypatch):
    """学英语的孩子：花上写一个词的字母，按顺序拼出来；交上来的是先后连过的花，一步没错才对。"""
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "lk1@x.com")
        arena.set_game_unlock(k["id"], "free")
        k = dict(db.one("SELECT * FROM users WHERE id=?", k["id"]))
        page = c.get("/arena/link")
        assert page.status_code == 200 and "/static/play/link.js" in page.text and "/static/play/link.css" in page.text
        m = matches.start(k, "link")
        seen = 0
        for i in range(6):
            q = matches.question(k, m["match_id"], random.Random(i), gate=f"link{i}")["item"]
            seq = q["seq"]
            assert seq and seq["zh"] and 3 <= len(seq["tokens"]) <= 8 and all(len(t) == 1 and t.isalpha() for t in seq["tokens"])
            ans = "|".join(seq["tokens"])
            if i % 2:   # 中间连错过一次
                ans = "|".join(seq["tokens"][:1] + ["?"] + seq["tokens"][1:])
            res = matches.answer(k, m["match_id"], q["id"], ans)
            assert res["correct"] == (i % 2 == 0)
            if not res["correct"]:
                assert res["freeze_ms"] == 0   # 不冷冻
            seen += 1
        assert seen == 6


def test_link_help_panel_asks_plain_questions(monkeypatch):
    """「帮帮我」盖上来的答题面板画不出排序题：面板里出普通题（拼写就是打整个词），排序题只上花。
    以前面板也拿排序题：出现「按得数从小到大连」让人打字，拼写的答案成了 a|p|p|l|e，打对了也判错。
    答错了隔几道再考：花上的回花上，面板的回面板，不串。"""
    with TestClient(app) as c:
        k = _kid(c, monkeypatch, "lk-help@x.com")
        arena.set_game_unlock(k["id"], "free")
        k = dict(db.one("SELECT * FROM users WHERE id=?", k["id"]))
        m = matches.start(k, "link")
        mid = m["match_id"]
        for i in range(12):
            q = matches.question(k, mid, random.Random(100 + i))["item"]            # 面板（gate 不写）
            assert not q["seq"] and "从小到大" not in q["q"] and "|" not in str(q.get("options") or ""), q
            st = matches._match(k, mid)["state"]
            it = st["pending"]["item"]
            assert sources.shape_of(it) == ""
            if it.get("fmt") == "spell":                                            # 拼写：打整个词，算对
                assert matches.answer(k, mid, q["id"], it["word"].upper())["correct"]
            elif i % 3 == 0:                                                        # 故意答错，排进再考
                matches.answer(k, mid, q["id"], "不对")
            else:
                ans = it["answer"][0] if isinstance(it["answer"], list) else it["answer"]
                assert matches.answer(k, mid, q["id"], ans)["correct"]
            g = matches.question(k, mid, random.Random(200 + i), gate=f"link{i}")["item"]   # 花上（题板）
            assert g["seq"] and g["seq"]["tokens"], g
            matches.answer(k, mid, g["id"], "?" if i % 2 else "|".join(g["seq"]["tokens"]))
        assert sources.panel_shape("sequence") == "" and sources.panel_shape("choices") == "choices"


def test_link_math_sequence_is_sorted():
    with TestClient(app):
        kid = _user("lk-m@x.com", "G3", ["math-shanghai"])
        rng, st = random.Random(5), {}
        got = [sources.next_item(kid, st, 0.75, rng, shape="sequence") for _ in range(20)]
        maths = [it for it in got if it["src"] == "math"]
        assert maths
        for it in maths:
            seq = it["seq"]
            assert len(seq["tokens"]) == sources.SEQ_MATH and all(len(t) <= sources.SEQ_TOKEN for t in seq["tokens"])
            vals = [float(v) for v in seq["vals"]]
            assert vals == sorted(vals) and len(set(vals)) == len(vals)        # 得数从小到大、不重复
            assert it["family"].endswith(":seq") and it["answer"] == ["|".join(seq["tokens"])]
            assert sources.public(it)["seq"] == seq and "answer" not in sources.public(it)
        assert all(it["src"] != "bank" for it in got)   # 题库的选择题排不成顺序，不出


def test_link_front_end_uses_shared_shell():
    with TestClient(app) as c:
        js = c.get("/static/play/link.js").text
        shell = c.get("/static/play/shell.js").text
    assert "sh.gateAsk(" in js and "sh.gateSend(" in js and "sh.gateSend = " in shell
    assert "finale()" in js and "far:" in js and "c.die()" in js   # 过关仪式的收尾、没过关走了多远、帮帮我 = 答对 1 道
    assert "function graph(lv)" in js and "hexNbs" in js
