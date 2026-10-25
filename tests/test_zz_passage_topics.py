"""每日短文话题多样化（docs/每日短文话题多样化-讨论稿.md，app/passage_topics.py）：
系统替孩子定话题，领域轮换、体裁兼顾考试、最近写过的不再写；读完有值得学的句子、想一想和「喜欢 / 一般」。"""
import random
import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import auth, bank, db, llm
from app import passage_topics as pt
from app.main import app


def _kid(c, grade="G8"):
    email = f"{uuid.uuid4().hex[:8]}@pt.example"
    auth.create_user(email, "secret123", "读者", role="kid", grade=grade)
    c.post("/login", data={"email": email, "password": "secret123"})
    return db.one("SELECT id FROM users WHERE email=?", email)["id"]


def _read(uid, lang, day, p, title, feel=""):
    cid = bank.save_content("passage", {"title": title, "body": f"{title} {uuid.uuid4().hex}", "questions": []}, "", lang, "G8",
                            topic=p["key"], title=title, meta={"pick": p})
    db.insert("INSERT INTO readings(user_id,lang,title,body,source,questions,content_id,created_at,feel) VALUES(?,?,?,?,?,?,?,?,?)",
              uid, lang, title, "x", "ai", "[]", cid, f"{day.isoformat()}T08:00:00", feel)


def test_two_weeks_are_broad_and_balance_exams(monkeypatch):
    with TestClient(app) as c:
        uid = _kid(c)
    base = date(2026, 11, 1)
    picks = []
    for i in range(14):
        day = base + timedelta(days=i)
        monkeypatch.setattr(db, "today", lambda d=day: d)
        p = pt.pick(uid, "zh", "G8", rng=random.Random(i))
        picks.append(p)
        _read(uid, "zh", day, p, f"第{i}篇")
    doms = [p["domain"] for p in picks]
    for i in range(len(doms)):  # 3 天内不重复领域
        assert doms[i] not in doms[max(0, i - 2):i]
    for i in range(len(doms) - 6):  # 一周内同一领域最多 2 次
        week = doms[i:i + 7]
        assert max(week.count(d) for d in week) <= pt.WEEK_MAX
    assert len(set(doms)) >= 8  # 读得广
    exam = [p["exam"] for p in picks]
    assert sum(exam) >= len(exam) // 2  # 兼顾考试：考试文体占多数
    for i in range(2, len(exam)):  # 连着两篇不是考试文体，下一篇一定是
        assert exam[i] or exam[i - 1] or exam[i - 2]
    assert len({p["genre"] for p in picks}) >= 5


def test_daily_one_tap_writes_with_pick_and_feedback(monkeypatch):
    seen = []
    real = llm.make_passage

    def spy(*a, **kw):
        seen.append(kw)
        out = real(*a, **kw)
        return {**out, "body": out["body"] + uuid.uuid4().hex, "title": f"今天的美文 {len(seen)}",
                "learn": out["learn"] + [{"text": "A sentence the passage never had.", "note": "编的"}]}
    monkeypatch.setattr(llm, "make_passage", spy)
    with TestClient(app) as c:
        uid = _kid(c)
        _read(uid, "zh", db.today() - timedelta(days=1), {"source": "explore", "domain": "nature", "angle": "", "genre": "散文",
                                                          "exam": True, "key": "auto:nature/散文"}, "雨后泥土的味道")
        page = c.get("/reading/today?lang=zh").text
        assert "写一篇今天的短文" in page and 'id="topics"' not in page and 'name="topic"' in page  # 每日一键；自己挑话题收在下面
        r = c.post("/reading/new", data={"mode": "ai", "lang": "zh"}, follow_redirects=False)
        rid = int(r.headers["location"].rsplit("/", 1)[1])
        kw = seen[-1]
        assert kw["pick"] and kw["pick"]["domain"] != "nature" and "雨后泥土的味道" in kw["avoid"]
        row = db.one("SELECT c.topic, c.gen_meta FROM readings r JOIN contents c ON c.id=r.content_id WHERE r.id=?", rid)
        assert row["topic"].startswith("auto:") and db.jload(row["gen_meta"])["pick"]["genre"] == kw["pick"]["genre"]
        page = c.get(f"/reading/{rid}").text
        assert "never had" not in page  # 不在正文里的句子不显示
        assert 'id="after"' in page and "这篇值得学的" in page and "想一想" in page and 'data-feel="like"' in page
        c.post(f"/api/reading/{rid}/finish", json={"answers": {"0": 1}, "minutes": 3})
        assert c.post(f"/api/reading/{rid}/feel", json={"feel": "like"}).json()["feel"] == "like"
        assert db.one("SELECT feel FROM readings WHERE id=?", rid)["feel"] == "like"
        assert c.post("/api/reading/999999/feel", json={"feel": "like"}).status_code == 404


def test_rewrites_once_when_title_repeats(monkeypatch):
    calls = []

    def same(*a, **kw):
        calls.append(kw["avoid"])
        title = "雨后的那股味道" if len(calls) == 1 else "候鸟怎么认路"
        return {"title": title, "body": f"{title} {uuid.uuid4().hex}", "questions": []}
    monkeypatch.setattr(llm, "make_passage", same)
    monkeypatch.setattr(pt, "pick", lambda *a, **kw: {"source": "explore", "domain": "nature", "name": "自然与生命", "angle": "",
                                                        "ideas": "", "genre": "说明文", "exam": True, "key": f"auto:{uuid.uuid4().hex}"})
    with TestClient(app) as c:
        uid = _kid(c)
        _read(uid, "zh", db.today() - timedelta(days=1), {"source": "explore", "domain": "nature", "angle": "", "genre": "散文",
                                                          "exam": True, "key": "auto:nature/散文"}, "雨后的那股味道")
        body, _ = bank.passage(uid, "zh", "G8", "", "500-800 字", [])
    assert len(calls) == 2 and calls[1][0] == "雨后的那股味道" and body["title"] == "候鸟怎么认路"


def test_liked_domains_come_back_more():
    with TestClient(app) as c:
        uid = _kid(c)
    old = db.today() - timedelta(days=10)
    for i in range(3):
        _read(uid, "zh", old, {"source": "explore", "domain": "space", "angle": "", "genre": "说明文", "exam": True,
                               "key": "auto:space/说明文"}, f"太空 {i}", feel="like")
    got = [pt.pick(uid, "zh", "G8", rng=random.Random(i)) for i in range(60)]
    assert any(p["source"] == "interest" and p["domain"] == "space" for p in got)
    assert pt.similar("雨后，泥土为什么会香", "雨后泥土香从哪里来") >= pt.SIMILAR
    assert pt.similar("候鸟怎么认路", "雨后的那股味道") < pt.SIMILAR
    assert pt.similar("鸟为什么会飞", "天为什么会黑") < pt.SIMILAR  # 只是句式一样不算重复
    assert pt.similar("Why Rain Smells Good", "The Good Smell After Rain") >= pt.SIMILAR


def _only(monkeypatch, source):
    monkeypatch.setattr(pt, "SOURCES", ((source, 1),))


def test_season_and_textbook_sources(monkeypatch):
    with TestClient(app) as c:
        uid = _kid(c)
    monkeypatch.setattr(db, "today", lambda: date(2026, 10, 25))
    assert [e["name"] for e in pt.season_events(date(2026, 10, 25))] == ["霜降"]
    assert "中秋节" in [e["name"] for e in pt.season_events(date(2026, 9, 24))]  # 农历节日按每年的公历日期
    _only(monkeypatch, "season")
    p = pt.pick(uid, "zh", "G8", rng=random.Random(1))
    assert p["source"] == "season" and "霜降" in p["angle"] and pt.facty(p)

    from app import schoolyear
    from app.catalog import catalog
    zh = next(pid for pid, pk in catalog.packs.items() if pk.lang == "zh" and not pk.standard)
    db.run("INSERT INTO enrollments(user_id,pack_id,stage,active) VALUES(?,?,?,1)", uid, zh, "G8")
    monkeypatch.setattr(schoolyear, "estimate", lambda u, e, *a, **kw: {"current": {"name": "第三单元 山水之美", "kps": []}})
    _only(monkeypatch, "textbook")
    p = pt.pick(uid, "zh", "G8", rng=random.Random(1))
    assert p["source"] == "textbook" and "第三单元 山水之美" in p["angle"]
    assert pt.pick(uid, "en", "G8", rng=random.Random(1))["source"] == "explore"  # 英语课本没选：退回拓展


ZH_RSS = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>中文</title>
<item><title>空间站完成新一轮实验</title><link>https://example.cn/a</link><pubDate>{d}</pubDate><description>航天员完成了实验。</description></item>
<item><title>大熊猫宝宝满月</title><link>https://example.cn/b</link><pubDate>{d}</pubDate><description>熊猫宝宝满月了。</description></item>
</channel></rss>"""


def test_weekly_zh_news_feeds_passages_with_facts_and_check(monkeypatch):
    from datetime import datetime, timezone
    from app import news, webpage
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    monkeypatch.setattr(news, "fetch_source", lambda src: (news.parse_feed(ZH_RSS.format(d=now)), "") if src["id"] == "chinanews-top"
                        else ([], "打不开"))
    monkeypatch.setattr(webpage, "article", lambda url: {"body": "航天员在空间站里完成了三项材料实验，样品明年返回地面。" * 20})
    res = news.pick_week_zh(force=True)
    assert res["zh-secondary"] >= 1 and res["sources"]["chinanews-top"]["ok"]
    rows = db.q("SELECT * FROM news_picks WHERE band='zh-secondary' AND day=?", news.week_of())
    assert rows and "材料实验" in rows[0]["facts"]
    assert news.pick_week_zh()["done"]  # 一周只挑一次

    seen, checked = [], []

    def write(*a, **kw):
        seen.append(kw)
        return {"title": "太空里的实验室", "topic": "空间站做实验", "body": f"空间站里有 99 个实验柜。{uuid.uuid4().hex}",
                "questions": [{"q": "?", "options": ["a", "b"], "answer": 0}], "learn": [], "think": ""}

    def check(body, lang, grade, user_id=None):
        checked.append(body["title"])
        return {"ok": False, "problems": ["实验柜数量材料里没有"],
                "passage": {**body, "body": "空间站里有不少实验柜。" + uuid.uuid4().hex}}
    monkeypatch.setattr(llm, "make_passage", write)
    monkeypatch.setattr(llm, "check_passage", check)
    _only(monkeypatch, "news")
    with TestClient(app) as c:
        uid = _kid(c)
        r = c.post("/reading/new", data={"mode": "ai", "lang": "zh"}, follow_redirects=False)
        rid = int(r.headers["location"].rsplit("/", 1)[1])
        p = seen[-1]["pick"]
        assert p["source"] == "news" and "材料实验" in p["facts"] and p["cite"] == "中国新闻网"
        assert checked == ["太空里的实验室"]
        row = db.one("SELECT r.body, c.gen_meta FROM readings r JOIN contents c ON c.id=r.content_id WHERE r.id=?", rid)
        assert "99 个" not in row["body"] and "不少实验柜" in row["body"] and db.jload(row["gen_meta"])["checked"]["problems"] == ["实验柜数量材料里没有"]
        assert "根据中国新闻网的报道写成" in c.get(f"/reading/{rid}").text
        # 英文短文的新闻来源还是每天的英文新闻，不拿中文热点
        assert all(not (x["source"] == "news" and "空间站" in x["angle"]) for x in [pt.pick(uid, "en", "G8", rng=random.Random(i)) for i in range(3)])
