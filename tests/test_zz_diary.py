"""迭代日记：读 DATA_DIR/diary/ 的文件进表、后台改过的不被文件覆盖、上传、收下 / 不要、只有管理员能看。"""
import uuid

from fastapi.testclient import TestClient

from app import auth, db, diary
from app.main import app

ONE = """---
day: 2026-10-07
title: 弟弟也要能赢
quote: 他们说，想玩火柴人大战。
changes: 乐园上线。
prs: 14, #15
tags: 游戏，公平
status: kept
---
那天我问他们，什么样的学习不讨厌。

五秒。
"""


def _admin(c):
    email = uuid.uuid4().hex + "@example.test"
    auth.create_user(email, "secret123", "站长", role="admin", is_admin=True)
    c.post("/login", data={"email": email, "password": "secret123"})


def test_parse():
    e = diary.parse(ONE)
    assert e["day"] == "2026-10-07" and e["prs"] == [14, 15] and e["tags"] == ["游戏", "公平"] and e["status"] == "kept"
    assert e["body"].startswith("那天我问他们") and e["body"].endswith("五秒。")
    for bad in ("没有开头", "---\ntitle: x\n---\n正文", "---\nday: 2026-10-07\n---\n正文"):
        try:
            diary.parse(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert diary.parse(ONE.replace("status: kept", "status: whatever"))["status"] == "draft"


def test_sync_upload_review_and_admin_only():
    db.init()
    db.run("DELETE FROM diary")
    diary.DIR.mkdir(parents=True, exist_ok=True)
    for f in diary.DIR.glob("*.md"):
        f.unlink()
    (diary.DIR / "2026-10-07-win.md").write_text(ONE, encoding="utf-8")
    (diary.DIR / "broken.md").write_text("没有开头", encoding="utf-8")
    with TestClient(app) as c:
        _admin(c)
        r = c.get("/admin/diary")
        assert r.status_code == 200 and "弟弟也要能赢" in r.text and "2026 年 10 月" in r.text and "broken.md" in r.text
        e = db.one("SELECT * FROM diary WHERE slug='2026-10-07-win'")
        assert e["status"] == "kept"
        page = c.get(f"/admin/diary/{e['id']}").text
        assert "<p>五秒。</p>" in page and "/pull/15" in page and "他们说，想玩火柴人大战。" in page

        # 文件变了、后台没改过：跟着变
        (diary.DIR / "2026-10-07-win.md").write_text(ONE.replace("五秒。", "五秒钟。"), encoding="utf-8")
        assert diary.sync()["updated"] == 1 and "五秒钟。" in diary.get(e["id"])["body"]

        # 后台改一改以后，文件再变也不覆盖
        c.post(f"/admin/diary/{e['id']}/save", data={"day": "2026-10-07", "title": "弟弟也要能赢", "quote": "",
                                                     "body": "我自己改的。", "changes": "", "tags": "游戏"})
        (diary.DIR / "2026-10-07-win.md").write_text(ONE, encoding="utf-8")
        diary.sync()
        assert diary.get(e["id"])["body"] == "我自己改的。"

        # 上传一篇草稿：存进目录、进表；收下以后进时间线
        draft = ONE.replace("2026-10-07", "2026-10-19").replace("弟弟也要能赢", "我不想让你觉得被盯着").replace("status: kept", "status: draft")
        r = c.post("/admin/diary/upload", files=[("files", ("2026-10-19-focus.md", draft.encode(), "text/markdown"))])
        assert r.status_code == 200 and "新的 1 篇" in r.text and "等你看的草稿（1）" in r.text
        assert (diary.DIR / "2026-10-19-focus.md").exists()
        d = db.one("SELECT id FROM diary WHERE slug='2026-10-19-focus'")["id"]
        assert c.post("/admin/diary/upload", files=[("files", ("../x.md", b"---\n", "text/markdown"))]).status_code == 400
        c.post(f"/admin/diary/{d}/status", data={"status": "kept"})
        assert diary.get(d)["status"] == "kept" and "等你看的草稿" not in c.get("/admin/diary").text

        # 不要：从时间线拿掉，文件再变也不回来；在「不要了的」里还能找到
        c.post(f"/admin/diary/{d}/status", data={"status": "dropped"})
        diary.sync()
        assert "我不想让你觉得被盯着" not in c.get("/admin/diary").text
        assert "我不想让你觉得被盯着" in c.get("/admin/diary?show=dropped").text

        # 只有管理员能看
        c.get("/logout")
        pe = uuid.uuid4().hex + "@example.test"
        auth.create_user(pe, "secret123", "家长", role="parent")
        c.post("/login", data={"email": pe, "password": "secret123"})
        assert c.get("/admin/diary").status_code == 403
        assert c.get(f"/admin/diary/{e['id']}").status_code == 403
    for f in diary.DIR.glob("*.md"):
        f.unlink()
