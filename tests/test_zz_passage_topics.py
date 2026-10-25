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
        return {**out, "body": out["body"] + uuid.uuid4().hex, "title": f"今天的美文 {len(seen)}"}
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
