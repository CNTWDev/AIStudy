"""每周学习报告邮件：每周一早上（时间在 config/mail.toml 的 [weekly] 里改）给每个家长发一封，讲每个孩子上周的情况。

一封信里一家所有还没成年的孩子（家里的成人成员学习自己安排，记录只有本人能看，不进报告）。
内容只放家长用得上的：上周学了几天、多少分钟、现在连续几天；比再上一周进步在哪（和孩子看到的每周进步卡同一份，
app/records.py 的 weekly）；系统发现的问题（insights）。看细节点链接回网站。
上周之后才加进来的孩子不算；一个孩子都没有就不发。
"""
from datetime import date, datetime, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import auth, config, db, engine, insights, mailer, records, sitecfg

_env = Environment(loader=FileSystemLoader(config.BASE_DIR / "app" / "templates" / "mail"),
                   autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)


def due_week(now: datetime | None = None) -> date | None:
    """现在该发哪一周的报告：过了设定的周几几点，返回上一周的周一；还没到返回 None。"""
    s = mailer.settings()
    now = now or datetime.fromisoformat(db.now())
    this_mon = now.date() - timedelta(days=now.weekday())
    send_at = datetime.combine(this_mon + timedelta(days=s.weekly_weekday - 1), datetime.min.time()).replace(
        hour=s.weekly_hour, tzinfo=now.tzinfo)
    return this_mon - timedelta(days=7) if now >= send_at else None


def kid_week(k: dict, mon: date) -> dict:
    end = mon + timedelta(days=7)
    row = db.one("SELECT COUNT(*) AS days, COALESCE(SUM(minutes),0) AS minutes FROM days "
                 "WHERE user_id=? AND day>=? AND day<? AND (minutes>0 OR checked_in=1)", k["id"], mon.isoformat(), end.isoformat())
    card = records.weekly(k["id"], mon)
    return {"name": k["name"], "id": k["id"], "days": row["days"], "minutes": int(row["minutes"]),
            "streak": engine.streak(k["id"]), "lines": card["lines"][:-1] if card else [],
            "attempts": card["this"]["attempts"] if card else 0,
            "found": [{"title": f["title"], "detail": f["detail"]} for f in insights.open_insights(k["id"], limit=3)]}


def build_weekly(parent: dict, mon: date) -> dict | None:
    """一个家长这一周的报告：{subject, text, html}；没有要报告的孩子返回 None。"""
    end = mon + timedelta(days=7)
    kids = [dict(k) for k in db.q("SELECT * FROM users WHERE parent_id=? AND status='active' AND created_at<? ORDER BY id",
                                  parent["id"], end.isoformat())]
    kids = [kid_week(k, mon) for k in kids if not auth.self_managed(k)]
    if not kids:
        return None
    site = sitecfg.get("site_name")
    span = f"{mon.month}/{mon.day}–{(end - timedelta(days=1)).month}/{(end - timedelta(days=1)).day}"
    subject = f"{site}周报（{span}）：" + "，".join(f"{k['name']}学了 {k['days']} 天" for k in kids)
    ctx = {"site": site, "parent": parent, "kids": kids, "span": span, "base": config.PUBLIC_URL,
           "unsub": mailer.unsub_url(parent["id"], "weekly"), "kind_name": mailer.KINDS["weekly"]}
    return {"subject": subject, "text": _env.get_template("weekly.txt").render(**ctx),
            "html": _env.get_template("weekly.html").render(**ctx)}


def queue_weekly(now: datetime | None = None) -> int:
    """到点了就把上周的报告写进发信队列（每个家长每周只写一次）。返回这次新写了几封。"""
    mon = due_week(now)
    if not mon:
        return 0
    ref = mon.isoformat()
    n = 0
    for p in db.q("SELECT * FROM users WHERE role='parent' AND status='active' AND id NOT IN "
                  "(SELECT user_id FROM mail_outbox WHERE kind='weekly' AND ref=? AND user_id IS NOT NULL)", ref):
        p = dict(p)
        if not mailer.wants(p, "weekly"):
            continue
        mail = build_weekly(p, mon)
        if mail and mailer.enqueue(p, "weekly", ref, mail["subject"], mail["text"], mail["html"]):
            n += 1
    return n
