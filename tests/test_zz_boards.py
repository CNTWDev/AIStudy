"""打卡榜（app/boards.py）：只有家长打开的孩子上榜、只显示昵称；连续 / 本月 / 累计三种天数；城市榜够人才开。"""
from datetime import timedelta

from fastapi.testclient import TestClient

from app import auth, boards, db
from app.main import app


def _days(kid, offsets):
    today = db.today()
    for o in offsets:
        db.run("INSERT INTO days(user_id, day, plan, minutes, checked_in) VALUES(?,?,?,?,?)",
               kid, (today - timedelta(days=o)).isoformat(), "[]", 30, 1)


def _family(tag, city=("浙江", "杭州市"), name="孩子"):
    pid = auth.create_user(f"bd-p-{tag}@x.com", "secret1", "家长", "parent")
    boards.set_city(pid, *city)
    k = auth.create_user(f"bd-{tag}@x.com", "secret1", name, "kid", parent_id=pid, grade="G4")
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
        for k in (a, b, far):
            boards.set_on_board(k, True)
        _days(a, range(0, 5))               # 连续 5 天
        _days(b, [0, 1, 3, 4, 5, 6, 7, 8])  # 断过：连续 2 天，一共 8 天
        _days(hidden, range(0, 30))         # 没打开上榜：不在榜上
        _days(far, range(0, 3))
        boards.refresh()

        kid = dict(auth.get_user(b))
        r = boards.board(kid, "streak")
        names = [x["name"] for x in r["top"]]
        assert names[0] == "火箭" and "甲甲甲" not in names and hidden not in [x["id"] for x in r["top"]]
        assert r["mine"]["value"] == 2 and r["ahead"] == 2   # 第 3 名（广州 3 天），再 2 天超过前面一位
        r = boards.board(kid, "total")
        assert r["top"][0]["id"] == b and r["top"][0]["value"] == 8 and r["mine"]["rank"] == 1
        # 城市榜：杭州上榜的不到 5 个，不开
        assert not r["city_ok"] and boards.board(kid, "total", "city")["scope"] == "all"
        for i in range(3):
            _, k = _family(f"c{i}")
            boards.set_on_board(k, True)
            _days(k, [0])
        boards.refresh()
        r = boards.board(kid, "total", "city")
        assert r["city_ok"] and r["scope"] == "city" and r["city"] == "杭州" and far not in [x["id"] for x in r["top"]]
        # 一样的天数名次一样
        r = boards.board(kid, "streak", "city")
        assert [x["rank"] for x in r["top"]] == [1, 2, 3, 3, 3]

        # 页面：孩子能看；没上榜的孩子看到怎么上榜
        c.post("/login", data={"email": "bd-h@x.com", "password": "secret1"})
        page = c.get("/boards?metric=month").text
        assert "火箭" in page and "爸爸妈妈" in page and "甲甲甲" not in page


def test_parent_turns_board_on():
    with TestClient(app) as c:
        c.cookies.clear()
        pid = auth.create_user("bd-pp@x.com", "secret1", "家长", "parent")
        k = auth.create_user("bd-pk@x.com", "secret1", "小可", "kid", parent_id=pid, grade="G3")
        c.post("/login", data={"email": "bd-pp@x.com", "password": "secret1"})
        assert "上排行榜" in c.get(f"/parent/kids/{k}/edit").text
        base = {"id": str(k), "name": "小可", "email": "bd-pk@x.com", "grade": "G3", "daily_minutes": "60", "game_minutes": "20"}
        assert not boards.on_board(dict(auth.get_user(k)))
        c.post("/parent/kids/save", data={**base, "board": "1", "province": "江苏", "city": "南京市"})
        assert boards.on_board(dict(auth.get_user(k))) and auth.get_user(pid)["city"] == "江苏·南京"
        assert "南京" in c.get(f"/parent/kids/{k}/edit").text


def test_game_board():
    """乐园榜：这周在乐园答对几道题（周一起数），只算上榜的孩子。"""
    with TestClient(app) as c:
        _, a = _family("ga")
        _, b = _family("gb")
        for k in (a, b):
            boards.set_on_board(k, True)
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
        assert r["unit"] == "道" and mine["value"] == 3 and r["ahead"] >= 3
        assert next(x for x in r["top"] if x["id"] == b)["value"] == 5
        c.post("/login", data={"email": "bd-ga@x.com", "password": "secret1"})
        page = c.get("/boards?metric=game").text
        assert "乐园榜" in page and "本周答对" in page and "连续打卡" not in page
        assert "/boards?metric=game" in c.get("/arena").text
