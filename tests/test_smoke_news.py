"""每日新闻：读 RSS / Atom、AI（mock）选题和改写、孩子打开今天的新闻、按级别共用、换难度、每日计划、管理后台。
测试里的新闻是随手写的，不联网。"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import db, engine, news, webpage

NOW = datetime.now(timezone.utc)
RSS = f"""<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>T</title>
<item><title>Central bank holds rates</title><link>https://example.com/a</link><pubDate>{NOW.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>
<description>&lt;p&gt;The bank kept rates the same.&lt;/p&gt;</description><content:encoded><![CDATA[<p>{'Prices rose slowly this year. ' * 40}</p><script>x()</script>]]></content:encoded></item>
<item><title>Old story</title><link>https://example.com/old</link><pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate><description>old</description></item>
</channel></rss>"""
ATOM = f"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>Scientists find a new frog</title><link rel="alternate" href="https://example.org/frog"/><published>{(NOW - timedelta(hours=1)).isoformat()}</published>
<summary type="html">A tiny frog.</summary><content type="html">&lt;p&gt;{'The frog is very small and bright green. ' * 30}&lt;/p&gt;</content></entry></feed>"""


def test_parse_feed():
    r = news.parse_feed(RSS)
    assert [x["title"] for x in r] == ["Central bank holds rates", "Old story"]
    assert r[0]["url"] == "https://example.com/a" and r[0]["summary"] == "The bank kept rates the same."
    assert "Prices rose" in r[0]["content"] and "x()" not in r[0]["content"] and r[0]["published"]
    a = news.parse_feed(ATOM)
    assert a[0]["url"] == "https://example.org/frog" and "bright green" in a[0]["content"]
    assert news.parse_feed("not xml <") == []


def _fake_feeds(monkeypatch):
    def fake(src):
        if src["id"] == "nasa":
            return news.parse_feed(ATOM), ""
        if src["id"] == "bbc-world":
            return news.parse_feed(RSS), ""
        return [], "网页打开超时或连接失败"
    monkeypatch.setattr(news, "fetch_source", fake)

    def no_article(url):
        raise webpage.FetchError("打不开")
    monkeypatch.setattr(webpage, "article", no_article)


def test_daily_news_flow(monkeypatch):
    _fake_feeds(monkeypatch)
    cands, status = news.collect()
    assert {c["title"] for c in cands} == {"Central bank holds rates", "Scientists find a new frog"}  # 旧新闻不要
    assert status["bbc-world"]["ok"] and not status["guardian-world"]["ok"]

    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:
        if c.get("/login").text.count("创建网站管理员账号"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        else:
            c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        c.post("/admin/users/create", data={"email": "newsp@x.com", "password": "secret1", "name": "新闻爸", "role": "parent"})
        c.get("/logout")
        c.post("/login", data={"email": "newsp@x.com", "password": "secret1"})
        for email, grade in (("newsk8@x.com", "G8"), ("newsk3@x.com", "G3"), ("newsk7@x.com", "G7")):
            c.post("/parent/kids/save", data={"name": email[:6], "email": email, "password": "secret1", "grade": grade,
                                              "daily_minutes": "60", "subj_english": "eng-shanghai"})
        c.get("/logout")

        res = news.run_daily(force=True)
        assert not res.get("error"), res
        picks = {p["band"]: p for p in db.q("SELECT * FROM news_picks WHERE day=?", db.today().isoformat())}
        assert picks["secondary"]["title"] == "Central bank holds rates" and picks["primary"]["title"] == "Scientists find a new frog"
        assert "Prices rose" in picks["secondary"]["facts"] and picks["primary"]["license"] == "pd"
        assert set(res["written"]) >= {"L1", "L3"}  # 用得到的级别先写好
        n_contents = db.one("SELECT COUNT(*) AS n FROM contents WHERE kind='news'")["n"]
        assert news.run_daily(force=False).get("skipped") or db.one("SELECT COUNT(*) AS n FROM contents WHERE kind='news'")["n"] == n_contents

        # 初二：中学那条，L3，有讨论题；打开两次是同一条阅读记录
        c.post("/login", data={"email": "newsk8@x.com", "password": "secret1"})
        plan = c.post("/api/plan/rebuild").json()["plan"]
        t = next(t for t in plan if t["type"] == "read_en")
        assert t["url"] == "/news" and "Central bank holds rates" in t["title"]
        r = c.get("/news", follow_redirects=False)
        assert r.status_code == 303
        rid = r.headers["location"]
        assert c.get("/news", follow_redirects=False).headers["location"] == rid
        page = c.get(rid).text
        assert "根据 BBC News" in page and "读前了解" in page and "说一说" in page and "battery" in page
        assert "挑战读原文" not in page  # 有版权的源不提供原文
        assert "今日新闻" in c.get("/reading").text
        # 换简单一点：L2 现场写一篇，记住偏好
        r2 = c.get("/news?level=L2", follow_redirects=False).headers["location"]
        assert r2 != rid
        assert db.one("SELECT level FROM readings WHERE id=?", int(r2.rsplit("/", 1)[1]))["level"] == "L2"
        assert news.level_for(db.one("SELECT * FROM users WHERE email='newsk8@x.com'")) == "L2"
        c.post(f"/api/reading/{rid.rsplit('/', 1)[1]}/finish", json={"minutes": 3, "answers": {"0": 1}})
        c.get("/logout")

        # 初一和初二同级别：共用同一篇改写稿，不重新生成
        c.post("/login", data={"email": "newsk7@x.com", "password": "secret1"})
        before = db.one("SELECT COUNT(*) AS n FROM contents WHERE kind='news'")["n"]
        c.get("/news")
        assert db.one("SELECT COUNT(*) AS n FROM contents WHERE kind='news'")["n"] == before
        c.get("/logout")

        # 三年级：小学那条，公有领域的源可以读原文
        c.post("/login", data={"email": "newsk3@x.com", "password": "secret1"})
        page = c.get("/news").text
        assert "根据 NASA" in page and "挑战读原文" in page and "说一说" not in page
        c.get("/logout")

        # 管理后台：看结果、换一篇（没有备选时给提示）
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        a = c.get("/admin/news").text
        assert "Central bank holds rates" in a and "BBC News" in a and "网页打开超时" in a
        assert "没有能用的备选" in c.post("/admin/news/swap", data={"pick_id": picks["primary"]["id"]}).text
        assert c.post("/admin/news/swap", data={"pick_id": picks["secondary"]["id"]}).status_code == 200
        assert db.one("SELECT title FROM news_picks WHERE day=? AND band='secondary'", db.today().isoformat())["title"] == \
            "Scientists find a new frog"


def test_level_for_grades():
    assert news.level_for({"grade": "G2", "settings": "{}"}) == "L1"
    assert news.level_for({"grade": "G5", "settings": "{}"}) == "L2"
    assert news.level_for({"grade": "G8", "settings": "{}"}) == "L3"
    assert news.level_for({"grade": "G11", "settings": "{}"}) == "L4"
    assert news.level_for({"grade": "G8", "settings": '{"news_level": "L4"}'}) == "L4"
    assert news.band_of("L1") == "primary" and news.band_of("L3") == "secondary"


def test_no_news_yet(monkeypatch):
    monkeypatch.setattr(news, "latest_pick", lambda band: None)
    assert news.today_title({"grade": "G8", "settings": "{}"}) is None

