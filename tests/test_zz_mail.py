"""发邮件给家长：每周学习报告进发信队列（每人每周一次）、发出去、失败重试、退订（docs/MAIL.md）。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import auth, config, db, mailer, reports
from app.main import app


@pytest.fixture(autouse=True)
def _app():
    with TestClient(app):  # 启动应用：建库、迁移
        yield


def _monday_9am():
    now = datetime.fromisoformat(db.now())
    mon = now.date() - timedelta(days=now.weekday())
    return datetime.combine(mon, datetime.min.time()).replace(hour=9, tzinfo=now.tzinfo), mon


def _login(c, email, pw="secret1"):
    c.post("/logout")
    c.post("/login", data={"email": email, "password": pw})


def _ours(pid):
    return [dict(r) for r in db.q("SELECT * FROM mail_outbox WHERE user_id=? ORDER BY id", pid)]


def test_weekly_report_queue_send_retry_unsub(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "https://study.example.com")
    nine, mon = _monday_9am()
    last_mon = mon - timedelta(days=7)
    pa = auth.create_user("mail-p@x.com", "secret1", "爸爸", "parent")
    kid = auth.create_user("mail-k@x.com", "2580", "小明", "kid", parent_id=pa, grade="G5")
    adult = auth.create_user("mail-m@x.com", "secret1", "妈妈", "kid", parent_id=pa, grade="ADULT")
    newbie = auth.create_user("mail-n@x.com", "2580", "新来的", "kid", parent_id=pa, grade="G3")
    old = (last_mon - timedelta(days=30)).isoformat() + "T08:00:00+08:00"
    db.run("UPDATE users SET created_at=? WHERE id IN (?,?)", old, kid, adult)  # newbie 是这周才加的：不进上周的报告
    for i, m in enumerate((20, 35, 0)):
        db.run("INSERT INTO days(user_id,day,plan,minutes) VALUES(?,?,?,?)", kid, (last_mon + timedelta(days=i)).isoformat(), "[]", m)

    # 还没到点：不写；到点：每个家长一封，再跑一次也不会多写
    assert reports.due_week(nine - timedelta(hours=2)) is None
    assert reports.due_week(nine) == last_mon
    reports.queue_weekly(nine - timedelta(hours=2))
    assert not _ours(pa)
    reports.queue_weekly(nine)
    reports.queue_weekly(nine)
    rows = _ours(pa)
    assert len(rows) == 1 and rows[0]["ref"] == last_mon.isoformat() and rows[0]["to_addr"] == "mail-p@x.com"
    r = rows[0]
    assert "小明学了 2 天" in r["subject"] and "新来的" not in r["body_text"]
    assert "【妈妈】" in r["body_text"] and f"/parent/kids/{adult}" in r["body_html"]   # 家里的成人成员也进报告，家长能点进去看
    assert "一共 55 分钟" in r["body_text"] and f"/parent/kids/{kid}" in r["body_html"] and "/mail/unsub?t=" in r["body_html"]

    # 发出去：带一键退订头；发过的不再发
    mailer.SENT.clear()
    assert mailer.send_due()["sent"] >= 1
    msg = next(m for m in mailer.SENT if m["To"] == "mail-p@x.com")
    assert msg["List-Unsubscribe"].startswith("<https://study.example.com/mail/unsub?t=")
    assert msg.get_body(("html",)) is not None
    assert _ours(pa)[0]["status"] == "sent"
    mailer.SENT.clear()
    mailer.send_due()
    assert not [m for m in mailer.SENT if m["To"] == "mail-p@x.com"]

    # 发失败：留在队列里，过一会儿再试；试到上限就放弃
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(mailer, "deliver", boom)
    pb = dict(auth.get_user(auth.create_user("mail-q@x.com", "secret1", "家长二", "parent")))
    mid = mailer.enqueue(pb, "weekly", "retry-test", "主题", "正文")
    mailer.send_due()
    row = db.one("SELECT * FROM mail_outbox WHERE id=?", mid)
    assert row["status"] == "queued" and row["tries"] == 1 and "connection refused" in row["error"] and row["next_at"] > db.now()
    db.run("UPDATE mail_outbox SET tries=?, next_at=? WHERE id=?", mailer.MAX_TRIES - 1, "2000-01-01", mid)
    mailer.send_due()
    assert db.one("SELECT status FROM mail_outbox WHERE id=?", mid)["status"] == "failed"
    monkeypatch.undo()

    # 退订：链接打开先确认，确认后不再写进队列；改过的签名不认
    token = mailer.unsub_token(pa, "weekly")
    with TestClient(app) as c:
        assert "确认退订" in c.get(f"/mail/unsub?t={token}").text
        assert "链接不对" in c.get(f"/mail/unsub?t={pa}.weekly.0000").text
        assert c.post(f"/mail/unsub?t={pa + 1}.weekly.{token.split('.')[2]}").status_code == 404
        assert "已退订" in c.post(f"/mail/unsub?t={token}").text
        assert not mailer.wants(auth.get_user(pa), "weekly")
        db.run("DELETE FROM mail_outbox WHERE user_id=?", pa)
        reports.queue_weekly(nine)
        assert not _ours(pa)

        # 家长在「我的账号」里重新打开
        _login(c, "mail-p@x.com")
        assert "邮件通知" in c.get("/settings").text
        c.post("/settings/mail", data={"weekly": "1"})
        assert mailer.wants(auth.get_user(pa), "weekly")
        reports.queue_weekly(nine)
        assert len(_ours(pa)) == 1
        # 孩子不能改家长的邮件设置
        _login(c, "mail-k@x.com", "2580")   # 孩子用 4 位数字
        assert c.post("/settings/mail", data={}).status_code == 403


def test_mail_off_and_config_errors(monkeypatch):
    s = mailer.parse_settings({"provider": "smtp", "smtp": {"host": ""}})
    assert not s.enabled and "host" in s.error
    s = mailer.parse_settings({"provider": "smtp", "from": "BeeJoy <r@mail.example.com>",
                               "smtp": {"host": "smtpdm.aliyun.com", "port": 465}, "weekly": {"weekday": 1, "hour": 7}})
    assert s.enabled and s.weekly_hour == 7 and s.security == "ssl"
    assert "不认识" in mailer.parse_settings({"provider": "sendgrid"}).error
    monkeypatch.setattr(mailer, "_settings", mailer.MailSettings())
    pa = dict(auth.get_user(auth.create_user("mail-off@x.com", "secret1", "家长三", "parent")))
    assert mailer.enqueue(pa, "weekly", "x", "主题", "正文") is None
    assert mailer.run() == {"skipped": "没开发信"}
