"""打卡榜（app/boards.py）：只有家长打开的孩子上榜、只显示昵称；连续 / 本月 / 累计三种天数；城市榜够人才开。"""
from datetime import timedelta

from fastapi.testclient import TestClient

from app import arena, auth, boards, db
from app.main import app


def _days(kid, offsets):
    today = db.today()
    for o in offsets:
        db.run("INSERT INTO days(user_id, day, plan, minutes, checked_in) VALUES(?,?,?,?,?)",
               kid, (today - timedelta(days=o)).isoformat(), "[]", 30, 1)


def _family(tag, city=("浙江", "杭州市"), name="孩子"):
    pid = auth.create_user(f"bd-p-{tag}@x.com", "secret1", "家长", "parent")
    boards.set_city(pid, *city)
    k = auth.create_user(f"bd-{tag}@x.com", "2580", name, "kid", parent_id=pid, grade="G4")
    return pid, k


def test_norm_city():
    assert boards.norm_city("浙江", " 杭州市 ") == "浙江·杭州"
    assert boards.norm_city("上海", "浦东") == "上海"
    assert boards.norm_city("海外", "London") == "海外"
    assert boards.norm_city("火星", "x") == ""
    assert boards.norm_city("新疆", "阿克苏地区") == "新疆·阿克苏"
    assert boards.city_label("浙江·杭州") == "杭州"


def test_boards():
    with TestClient(app) as c:
        _, a = _family("a", name="甲甲甲")
        _, b = _family("b")
        _, hidden = _family("h")
        _, far = _family("f", city=("广东", "广州"))
        auth.set_nickname(auth.get_user(a), "火箭")
        auth.set_private(hidden, True)
        _days(a, range(0, 5))               # 连续 5 天
        _days(b, [0, 1, 3, 4, 5, 6, 7, 8])  # 断过：连续 2 天，一共 8 天
        _days(hidden, range(0, 30))         # 隐私模式：不在榜上
        _days(far, range(0, 3))
        boards.refresh()

        kid = dict(auth.get_user(b))
        r = boards.board(kid, "streak")
        names = [x["name"] for x in r["top"]]
        assert names[0] == "火箭" and "甲甲甲" not in names and hidden not in [x["id"] for x in r["top"]]
        assert r["mine"]["value"] == 2 and r["ahead"] == 2   # 第 3 名（广州 3 天），再 2 天超过前面一位
        r = boards.board(kid, "best")   # 最长连续：断之前连着 6 天
        assert r["mine"]["value"] == 6 and r["top"][0]["id"] == b
        r = boards.board(kid, "total")
        assert r["top"][0]["id"] == b and r["top"][0]["value"] == 8 and r["mine"]["rank"] == 1
        # 城市榜：杭州上榜的不到 5 个，不开
        assert not r["city_ok"] and boards.board(kid, "total", "city")["scope"] == "all"
        for i in range(3):
            _, k = _family(f"c{i}")
            _days(k, [0])
        boards.refresh()
        r = boards.board(kid, "total", "city")
        assert r["city_ok"] and r["scope"] == "city" and r["city"] == "杭州" and far not in [x["id"] for x in r["top"]]
        # 一样的天数名次一样
        r = boards.board(kid, "streak", "city")
        assert [x["rank"] for x in r["top"]] == [1, 2, 3, 3, 3]

        # 页面：孩子能看；没上榜的孩子看到怎么上榜
        c.post("/login", data={"email": "bd-h@x.com", "password": "2580"})
        page = c.get("/boards?metric=month").text
        assert "火箭" in page and "隐私模式" in page and "甲甲甲" not in page and "学习榜" in page
        assert "/boards" in c.get("/records").text


def test_parent_privacy_mode():
    with TestClient(app) as c:
        c.cookies.clear()
        pid = auth.create_user("bd-pp@x.com", "secret1", "家长", "parent")
        k = auth.create_user("bd-pk@x.com", "2580", "小可", "kid", parent_id=pid, grade="G3")
        c.post("/login", data={"email": "bd-pp@x.com", "password": "secret1"})
        assert "隐私模式" in c.get(f"/parent/kids/{k}/edit").text
        base = {"id": str(k), "name": "小可", "email": "bd-pk@x.com", "grade": "G3", "daily_minutes": "60", "game_minutes": "20"}
        assert boards.on_board(dict(auth.get_user(k)))   # 默认上榜
        c.post("/parent/kids/save", data={**base, "private": "1", "province": "江苏", "city": "南京市"})
        u = dict(auth.get_user(k))
        assert not boards.on_board(u) and not arena.public_rooms(u) and auth.get_user(pid)["city"] == "江苏·南京"
        c.post("/parent/kids/save", data={**base, "province": "江苏", "city": "南京市"})
        assert boards.on_board(dict(auth.get_user(k)))
        assert "南京" in c.get(f"/parent/kids/{k}/edit").text


def test_game_board():
    """乐园榜：这周在乐园答对几道题（周一起数），只算上榜的孩子。"""
    with TestClient(app) as c:
        _, a = _family("ga")
        _, b = _family("gb")
        monday = boards._monday()
        for k, n in ((a, 3), (b, 5)):
            for i in range(n):
                db.run("INSERT INTO arena_answers(user_id, kp_id, family, level, correct, created_at) VALUES(?,?,?,?,?,?)",
                       k, f"x{i}", "add", 1, 1, monday + "T08:00:00")
        db.run("INSERT INTO arena_answers(user_id, kp_id, family, level, correct, created_at) VALUES(?,?,?,?,?,?)",
               a, "old", "add", 1, 1, "2000-01-01T08:00:00")   # 上周以前的不算
        boards.refresh()
        r = boards.board(dict(auth.get_user(a)), "game")
        mine = r["mine"]
        assert r["unit"] == "道" and mine["value"] == 3 and r["ahead"] is not None
        assert next(x for x in r["top"] if x["id"] == b)["value"] == 5
        assert boards.board(dict(auth.get_user(a)), "game_all")["mine"]["value"] == 4   # 一共答对：上周以前的也算
        c.post("/login", data={"email": "bd-ga@x.com", "password": "2580"})
        page = c.get("/boards?metric=game").text
        assert "乐园榜" in page and "本周答对" in page and "一共答对" in page and "连续打卡" not in page
        assert "/boards?metric=game" in c.get("/arena").text
        # 单款游戏：在那款游戏里答对的才算
        arena.set_game_unlock(a, "free")
        mid = db.insert("INSERT INTO arena_matches(user_id, game, mode, started_at) VALUES(?,?,?,?)", a, "bubble", "team", monday + "T08:00:00")
        for i in range(2):
            db.run("INSERT INTO arena_answers(user_id, match_id, kp_id, family, level, correct, created_at) VALUES(?,?,?,?,?,?,?)",
                   a, mid, f"y{i}", "add", 1, 1, monday + "T09:00:00")
        boards.refresh()
        r = boards.board(dict(auth.get_user(a)), "game", "all", "bubble")
        assert r["mine"]["rank"] == 1 and r["mine"]["value"] == 2 and [x["id"] for x in r["top"]] == [a]
        assert boards.board(dict(auth.get_user(a)), "game")["mine"]["value"] == 5
        page = c.get("/arena/bubble").text
        assert "泡泡堂榜" in page and "我第 1 名" in page and "一共答对" in page
        assert "泡泡堂榜" in c.get("/boards?metric=game&game=bubble").text


def test_live_game_board():
    """游戏页上的榜实时跳：只数这款游戏、不等 1 分钟的缓存；隐私模式不上；只要榜那一段，带 data-id 给页面对上每个人。"""
    with TestClient(app) as c:
        _, a = _family("la")
        _, b = _family("lb")
        _, hid = _family("lh")
        auth.set_nickname(auth.get_user(b), "追风")
        auth.set_private(hid, True)
        monday = boards._monday()

        def play(k, game, n, day=monday):
            mid = db.insert("INSERT INTO arena_matches(user_id, game, mode, started_at) VALUES(?,?,?,?)", k, game, "team", day + "T08:00:00")
            for i in range(n):
                db.run("INSERT INTO arena_answers(user_id, match_id, kp_id, family, level, correct, created_at) VALUES(?,?,?,?,?,?,?)",
                       k, mid, f"z{i}", "add", 1, 1, day + "T09:00:00")

        play(a, "snake", 9)                 # 别的游戏不算
        play(b, "snake", 1)
        play(a, "bubble", 2, "2000-01-03")  # 上周以前：只算一共答对
        play(hid, "bubble", 30)             # 隐私模式：不上榜
        boards.refresh()
        ours = lambda t: [(x["id"], x["value"]) for x in t if x["id"] in (a, b, hid)]   # 前面的测试里也有人玩过泡泡堂
        r = boards.live(dict(auth.get_user(a)), "bubble")
        assert ours(r["game"]["top"]) == [] and r["game_all"]["mine"]["value"] == 2
        play(b, "bubble", 3)
        boards._cache.pop(("live", "bubble"), None)   # 过了 LIVE_S 秒（1 分钟的榜单缓存不用等）
        r = boards.live(dict(auth.get_user(a)), "bubble")
        assert ours(r["game"]["top"]) == [(b, 3)] and r["game"]["mine"] is None
        assert ours(r["game_all"]["top"]) == [(b, 3), (a, 2)] and r["game_all"]["ahead"] == 2
        arena.set_game_unlock(a, "free")
        c.post("/login", data={"email": "bd-la@x.com", "password": "2580"})
        page = c.get("/arena/bubble").text
        assert 'data-live="/arena/bubble/board"' in page and "gboard.js" in page
        part = c.get("/arena/bubble/board")
        assert part.status_code == 200 and part.headers["cache-control"] == "no-store"
        assert f'data-id="{b}"' in part.text and "追风" in part.text and "<html" not in part.text
        assert "再答对 2 道超过前面一位" in part.text
        assert c.get("/arena/nope/board").status_code == 404


def test_empty_board_explains_and_admin_can_view():
    """没人上榜：写明白要家长打开；家长没选孩子、管理员也能看榜。"""
    with TestClient(app) as c:
        boards.refresh()
        pid, k = _family("em")
        c.cookies.clear()
        c.post("/login", data={"email": "bd-p-em@x.com", "password": "secret1"})
        page = c.get("/boards").text
        assert "学习榜" in page and "榜上的小伙伴" in page and "在家长页选一个孩子" in page
        auth.create_user("bd-admin@x.com", "secret1", "管理员", "admin")
        c.cookies.clear()
        c.post("/login", data={"email": "bd-admin@x.com", "password": "secret1"})
        r = c.get("/boards?metric=game")
        assert r.status_code == 200 and "乐园榜" in r.text and pid and k


def test_progress_and_rank_moves():
    """和上周同一天比：本周进步榜、比上周前进几名、再多少进前 10；今天页、乐园页各一句。"""
    with TestClient(app) as c:
        boards.refresh()
        _, me = _family("pr")
        _, other = _family("po")
        _days(other, [7, 8, 9, 10])          # 上周打卡多，这周没来
        _days(me, [0, 1, 2])                 # 这周（往前 3 天里的）打卡
        boards.refresh()
        kid = dict(auth.get_user(me))
        r = boards.board(kid, "total")
        assert r["week"]["now"] >= 1 and r["week"]["prev"] == 0
        assert r["moved"] == "new" or (isinstance(r["moved"], int) and r["moved"] >= 0)
        up = boards.board(kid, "up")
        assert up["mine"]["value"] == r["week"]["now"] and other not in [x["id"] for x in up["top"]] and up["moved"] is None
        tip = boards.nudge(kid)
        assert tip and "学习榜" in tip or "👍" in tip
        auth.set_private(me, True)
        assert boards.nudge(dict(auth.get_user(me))) == ""
        auth.set_private(me, False)
        boards.refresh()
        c.post("/login", data={"email": "bd-pr@x.com", "password": "2580"})
        page = c.get("/boards?metric=total").text
        assert "上周这时候" in page and "bd-podium" in page and "bd-rank" in page


def test_board_asks_for_nickname():
    """还没起昵称：榜上叫「小玩家 + 编号」，打开排行榜先请孩子起一个；起好了就不再问。"""
    with TestClient(app) as c:
        boards.refresh()
        _, k = _family("nn", name="张三丰")
        c.cookies.clear()
        c.post("/login", data={"email": "bd-nn@x.com", "password": "2580"})
        page = c.get("/boards").text
        assert "给自己起个昵称" in page and f"小玩家{k}" in page and "nm\">张三丰" not in page
        assert c.post("/api/arena/nickname", json={"nickname": "三丰"}).status_code == 400   # 不能用名字
        assert c.post("/api/arena/nickname", json={"nickname": "太极高手"}).status_code == 200
        boards.refresh()
        page = c.get("/boards").text
        assert "给自己起个昵称" not in page
        assert "榜上大家看到的你叫" not in page   # 乐园首页能改了，这里不再多一句


def test_board_name_click_to_edit_and_fresh():
    """榜上自己那一行：点名字就地改昵称；刚改的昵称马上换上（榜单缓存着也一样），上下不会是两个名字。"""
    with TestClient(app) as c:
        _, k = _family("ne", name="赵六六")
        auth.set_nickname(auth.get_user(k), "旧名字")
        _days(k, range(0, 40))   # 连续 40 天：在领奖台上
        boards.refresh()
        c.cookies.clear()
        c.post("/login", data={"email": "bd-ne@x.com", "password": "2580"})
        page = c.get("/boards?metric=streak").text
        assert 'class="nm bd-nick-me"' in page and "旧名字<i>我</i>" in page
        boards.board(None, "streak")   # 缓存里是旧名字
        auth.set_nickname(auth.get_user(k), "新名字")
        page = c.get("/boards?metric=streak").text
        assert "新名字<i>我</i>" in page and "旧名字" not in page
        assert c.post("/api/arena/nickname", json={"nickname": "又一个"}).status_code == 200   # 改了就刷新：别人看到的也是新的
        assert "又一个" in [r["name"] for r in boards.board(None, "streak")["top"]]
