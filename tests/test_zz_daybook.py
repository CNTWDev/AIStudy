"""我的日记（docs/DESIGN.md §5.57）：只有本人能看、内容加密落库、家长只看到记了几天和孩子自己分享的那天；
心情一点就算记了，+1 ⭐，不算进今天的清单；城市只存名字不存坐标；天气同城一天查一次；邮戳和封面从日记里算出来。"""
import uuid

from fastapi.testclient import TestClient

from app import auth, daybook, db, engine
from app.catalog import catalog
from app.main import app


def _family(c, grade="G3"):
    tag = uuid.uuid4().hex[:8]
    pid = auth.create_user(f"db-p-{tag}@x.com", "secret1", "家长", "parent")
    db.run("UPDATE users SET city=? WHERE id=?", "江苏·南京", pid)
    kid = auth.create_user(f"db-k-{tag}@x.com", "2580", "小本", role="kid", parent_id=pid, grade=grade)
    db.run("INSERT INTO enrollments(user_id,pack_id,stage,active) VALUES(?,?,?,1)", kid, "phy-cambridge", catalog.packs["phy-cambridge"].stages[0])
    return tag, pid, kid


def _login(c, email):
    c.cookies.clear()
    assert c.post("/login", data={"email": email, "password": "2580" if "db-k-" in email else "secret1"}).status_code < 400


def test_mood_marks_the_day_encrypted_and_one_star():
    with TestClient(app) as c:
        tag, pid, kid = _family(c)
        _login(c, f"db-k-{tag}@x.com")
        stars = engine.total_stars(kid)
        page = c.get("/daybook").text
        assert 'data-private="1"' in page and 'id="askbtn"' not in page   # 小助手不在日记页
        assert "南京" in page and "12355" in page and "只有你登录了才看得到" in page
        r = c.post("/api/daybook/mood", json={"mood": "good"}).json()
        assert r["new"]["first_today"] and r["new"]["cover"]["id"] == "sprout"
        assert engine.total_stars(kid) == stars + 1
        r = c.post("/api/daybook/note", json={"text": "今天在操场捡到一片很大的银杏叶"}).json()
        assert not r["new"]["first_today"] and engine.total_stars(kid) == stars + 1   # 一天只算一颗
        raw = db.one("SELECT * FROM daybook WHERE user_id=?", kid)
        assert raw["marked"] == 1 and "银杏" not in raw["data"] and "good" not in raw["data"] and "南京" not in raw["data"]
        assert "银杏" in c.get("/daybook").text
        # 不算进今天的清单：分母不变
        plan = engine.plan_of_today(kid)
        assert all(t["type"] != "daybook" for t in plan)
        assert "今天记了 🙂" in c.get("/today").text


def test_parent_sees_only_count_and_what_kid_shared():
    with TestClient(app) as c:
        tag, pid, kid = _family(c)
        _login(c, f"db-k-{tag}@x.com")
        c.post("/api/daybook/note", json={"text": "不想让人知道的小秘密"})
        day = db.today().isoformat()
        _login(c, f"db-p-{tag}@x.com")
        assert c.get("/daybook").status_code == 403
        home = c.get("/parent").text
        assert "这一周记了 1 天日记" in home and "小秘密" not in home and "给你看的" not in home
        assert "小秘密" not in c.get(f"/parent/kids/{kid}/daybook").text
        c.get(f"/parent/as/{kid}")
        assert "小秘密" not in c.get("/today").text and "今天记了" not in c.get("/today").text
        # 孩子自己分享这一天，家长才看得到；收回就又看不到了
        _login(c, f"db-k-{tag}@x.com")
        assert c.post("/api/daybook/share", json={"day": day, "on": True}).json() == {"shared": True}
        _login(c, f"db-p-{tag}@x.com")
        assert "小秘密" in c.get(f"/parent/kids/{kid}/daybook").text and "给你看的 1 天" in c.get("/parent").text
        _login(c, f"db-k-{tag}@x.com")
        c.post("/api/daybook/share", json={"day": day, "on": False})
        _login(c, f"db-p-{tag}@x.com")
        assert "小秘密" not in c.get(f"/parent/kids/{kid}/daybook").text


def test_city_from_point_keeps_only_the_name(monkeypatch):
    with TestClient(app) as c:
        tag, pid, kid = _family(c)
        _login(c, f"db-k-{tag}@x.com")
        monkeypatch.setattr(daybook, "_reverse", lambda lat, lon: "三亚市")
        monkeypatch.setattr(daybook, "_fetch_sky", lambda city: {"code": 61, "hi": 29, "lo": 24})
        r = c.post("/api/daybook/city", json={"lat": 18.2528, "lon": 109.5119}).json()
        assert r["page"]["city"] == "三亚" and r["sky"]["kind"] == "rain"
        assert not daybook.marked_today(kid)   # 只改城市不算记了
        r = c.post("/api/daybook/mood", json={"mood": "great"}).json()
        names = [s["name"] for s in r["new"]["stamps"]]
        assert "雨天章" in names and "三亚章" in names
        assert r["page"]["sky"]["word"] == "小雨" and r["page"]["sky"]["hi"] == 29
        assert "18.25" not in str(db.q("SELECT * FROM daybook")) + str(db.q("SELECT * FROM sky"))
        bk = daybook.book(kid)
        assert {s["name"] for s in bk["stamps"]} == {"雨天章", "三亚章"} and bk["days"] == 1


def test_sky_cached_per_city_and_day(monkeypatch):
    with TestClient(app):
        calls = []
        monkeypatch.setattr(daybook, "_fetch_sky", lambda city: calls.append(city) or {"code": 0, "hi": 20, "lo": 10})
        day = db.today().isoformat()
        city = "测试城" + uuid.uuid4().hex[:4]
        assert daybook.sky(city, day)["kind"] == "sun"
        assert daybook.sky(city, day)["word"] == "晴" and calls == [city]
        other = "查不到" + uuid.uuid4().hex[:4]
        monkeypatch.setattr(daybook, "_fetch_sky", lambda c: calls.append(c) or None)
        assert daybook.sky(other, day) is None and daybook.sky(other, day) is None
        assert calls.count(other) == 1   # 查不到的城市 10 分钟内不再试


def test_voice_saved_encrypted_and_deletable():
    with TestClient(app) as c:
        tag, pid, kid = _family(c)
        _login(c, f"db-k-{tag}@x.com")
        audio = b"\x1aE\xdf\xa3fake-webm-voice"
        r = c.post("/api/daybook/voice", files={"file": ("voice", audio, "audio/webm")}, data={"secs": "5"}).json()
        n = r["page"]["notes"][0]
        assert n["voice"] and n["secs"] == 5 and r["new"]["first_today"]
        f = daybook._voice_file(kid, n["voice"])
        assert f.exists() and audio not in f.read_bytes()
        got = c.get(f"/daybook/voice/{n['voice']}")
        assert got.content == audio and got.headers["content-type"].startswith("audio/webm")
        assert c.post("/api/daybook/voice", files={"file": ("v", b"x", "text/html")}).status_code == 400
        c.post("/api/daybook/delete", json={"day": db.today().isoformat(), "id": n["id"]})
        assert not f.exists() and not daybook.marked_today(kid)   # 删光了：这一天不算记了


def test_other_kid_cannot_read_and_covers_need_days():
    with TestClient(app) as c:
        tag, pid, kid = _family(c)
        _login(c, f"db-k-{tag}@x.com")
        r = c.post("/api/daybook/voice", files={"file": ("voice", b"abc", "audio/mp4")}, data={"secs": "2"}).json()
        vid = r["page"]["notes"][0]["voice"]
        tag2, _, _ = _family(c)
        _login(c, f"db-k-{tag2}@x.com")
        assert c.get(f"/daybook/voice/{vid}").status_code == 404
        assert c.post("/api/daybook/cover", json={"cover": "rainbow"}).status_code == 400
        assert c.get("/daybook/day/2999-01-01").status_code == 404


def test_low_mood_only_tells_the_kid():
    with TestClient(app) as c:
        tag, pid, kid = _family(c)
        from datetime import timedelta
        for i, m in enumerate(["sad", "low", "sad"]):
            day = (db.today() - timedelta(days=i)).isoformat()
            daybook._save(kid, day, {"mood": m})
        assert daybook.low_streak(kid)
        _login(c, f"db-k-{tag}@x.com")
        assert "这几天好像不太开心" in c.get("/daybook").text
        _login(c, f"db-p-{tag}@x.com")
        assert "不太开心" not in c.get("/parent").text
        back = daybook.lookback(kid)
        assert back is None   # 一周前没记：没有可回看的
        daybook._save(kid, (db.today() - timedelta(days=7)).isoformat(), {"mood": "great", "notes": [{"id": "a", "at": db.now(), "text": "去动物园"}]})
        assert daybook.lookback(kid)["ago"] == "一周前的今天"


def test_weekly_mail_and_flow_mention_diary():
    from app import reports
    with TestClient(app) as c:
        tag, pid, kid = _family(c)
        from datetime import timedelta
        mon = db.today() - timedelta(days=db.today().weekday() + 7)
        daybook._save(kid, (mon + timedelta(days=2)).isoformat(), {"mood": "good"})
        db.run("UPDATE users SET created_at=? WHERE id=?", mon.isoformat(), kid)
        mail = reports.build_weekly(dict(db.one("SELECT * FROM users WHERE id=?", pid)), mon)
        assert "上周记了 1 天日记" in mail["text"]
        st = engine.flow_state(kid)
        assert "diary" not in st or st["diary"] is False
