"""书库：原文整理（去头尾、切章、分页）、管理员上传、孩子按页读 / 听 / 导读 / 读后小题、和每日计划联动。
测试用的「书」是这里随手写的几段话，不是任何真实书籍的原文。"""
import io

from fastapi.testclient import TestClient

from app import db, engine
from app.library import library
from app.library.text import build_book, split_chapters

FAKE_EN = "Title: The Wonderful Wizard of Oz\n\n*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\n\nContents\n\nChapter I. The Kite\nChapter II. The Wind\n\n" + "".join(
    f"Chapter {r}. {name}\n\n" + "".join(f"{s} " * 40 + "\n\n" for s in sents)
    for r, name, sents in [("I", "The Kite", ["Mia had a red kite and a small dog.", "They ran up the hill after school."]),
                           ("II", "The Wind", ["The wind was very strong that day.", "The kite flew over the tall trees."]),
                           ("III", "Home Again", ["At last they walked home for dinner.", "Mia told Mum about the kite."])]
) + "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***\nlicense..."
FAKE_ZH = "".join(f"第{n}回 {t}\n\n" + ("小猴子在山上找桃子吃，找了很久也没找到。\n" * 30 + "\n") * 2
                  for n, t in (("一", "上山"), ("二", "下山")))


def test_build_book():
    b = build_book(FAKE_EN, "en", gutenberg=True, page_size=150)
    assert [c["title"] for c in b["chapters"]] == ["Chapter I. The Kite", "Chapter II. The Wind", "Chapter III. Home Again"]
    assert "license" not in str(b) and "Contents" not in str(b)
    assert b["n_pages"] == sum(len(c["pages"]) for c in b["chapters"]) and b["n_pages"] >= 6
    z = build_book(FAKE_ZH, "zh")
    assert [c["title"] for c in z["chapters"]] == ["第一回 上山", "第二回 下山"]
    one = split_chapters(["just one paragraph"] * 3, "en")  # 切不出章：整本当一部分
    assert len(one) == 1


def test_read_listen_guide_and_plan():
    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:
        if c.get("/login").text.count("创建网站管理员账号"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        else:
            c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        assert "下载所有还没有的" in c.get("/admin/library").text
        r = c.post("/admin/library/upload", data={"bid": "en-oz"},
                   files={"file": ("oz.txt", io.BytesIO(FAKE_EN.encode()), "text/plain")})
        assert "已导入" in r.text or r.status_code == 200
        assert library.ready("en-oz") and library.text("en-oz")["source"]["type"] == "upload"
        c.post("/admin/users/create", data={"email": "libp@x.com", "password": "secret1", "name": "书爸", "role": "parent"})
        c.get("/logout")
        c.post("/login", data={"email": "libp@x.com", "password": "secret1"})
        c.post("/parent/kids/save", data={"name": "书娃", "email": "libk@x.com", "password": "secret1", "grade": "G5",
                                          "daily_minutes": "60", "subj_eng": "eng-shanghai"})
        kid = db.one("SELECT id FROM users WHERE email='libk@x.com'")
        page = c.get(f"/parent/kids/{kid['id']}/plan").text
        assert 'value="lib:en-oz"' in page and "每天听书" in page
        c.post(f"/parent/kids/{kid['id']}/tracks", data={"kind": "read_en", "ref": "lib:en-oz", "daily_amount": "2", "daily_minutes": "15"})
        c.post(f"/parent/kids/{kid['id']}/tracks", data={"kind": "listen", "ref": "lib:en-oz", "daily_minutes": "5"})
        tr = {t["kind"]: t for t in engine.tracks(kid["id"])}
        assert tr["read_en"]["unit_name"] == "页" and tr["read_en"]["total_units"] == library.text("en-oz")["n_pages"]
        c.get("/logout")

        c.post("/login", data={"email": "libk@x.com", "password": "secret1"})
        plan = c.post("/api/plan/rebuild").json()["plan"]
        read = next(t for t in plan if t["type"] == "read_en")
        listen = next(t for t in plan if t["type"] == "listen")
        assert read["url"] == "/books/en-oz/p/1" and "第 1–2 页" in read["title"] and "listen=1" in listen["url"]
        shelf = c.get("/books").text
        assert "绿野仙踪" in shelf and "还在版权期的好书" in shelf
        assert "Chapter II. The Wind" in c.get("/books/en-oz").text
        p1 = c.get("/books/en-oz/p/1").text
        assert "Mia had a red kite" in p1 and 'id="prep"' in p1
        g = c.get("/api/books/en-oz/guide/1").json()  # AI（mock）写导读，存下来所有孩子共用
        assert g["words"] and g["quiz"]
        assert db.one("SELECT COUNT(*) AS n FROM contents WHERE kind='book_guide' AND topic='en-oz#1'")["n"] == 1
        c.get("/api/books/en-oz/guide/1")
        assert db.one("SELECT COUNT(*) AS n FROM contents WHERE kind='book_guide'")["n"] == 1  # 第二次读库，不重复生成
        assert c.post("/api/books/en-oz/read", json={"page": 1}).json()["new"]
        assert not c.post("/api/books/en-oz/read", json={"page": 1}).json()["new"]  # 同一页不重复算
        assert not next(t for t in engine.today_plan(kid["id"])["plan"] if t["type"] == "read_en").get("done")
        c.post("/api/books/en-oz/read", json={"page": 2})
        assert next(t for t in engine.today_plan(kid["id"])["plan"] if t["type"] == "read_en").get("done")
        assert engine.tracks(kid["id"])[0]["position"] in (2,) or any(t["position"] == 2 for t in engine.tracks(kid["id"]))
        for _ in range(5):
            c.post("/api/books/en-oz/listen", json={"page": 1, "seconds": 999})  # 每次最多算 60 秒
        assert next(t for t in engine.today_plan(kid["id"])["plan"] if t["type"] == "listen").get("done")
        r = c.post("/api/books/en-oz/quiz/1", json={"right": 2, "summary": "Mia flies a kite."}).json()
        assert r["bookmark"] and r["n"] == 1
        assert not c.post("/api/books/en-oz/quiz/1", json={"right": 2}).json()["bookmark"]
        g2 = c.get("/api/books/en-oz/guide/2").json()
        assert g2["recap"]  # 第二章开头显示「上回说到」
        assert c.get("/books/no-such-book").status_code == 404
        assert c.post("/api/books/en-oz/read", json={"page": 999}).status_code == 400
        assert c.get("/admin/library").status_code == 403
