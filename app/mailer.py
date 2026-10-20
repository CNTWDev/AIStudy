"""发邮件给家长（每周学习报告等）。

系统不自己当邮件服务器（云服务器一般封了 25 端口，新 IP 发到 QQ/163 也多半进垃圾箱），而是：
1. 要发的信先写进 mail_outbox（发信队列，migrations/0041_mail.sql），同一个人、同一种信、同一个 ref 只写一次；
2. 后台 worker（app/worker.py）每隔一会儿调用 run()：到点了把每周报告写进队列，再把队列里的信通过发信服务的 SMTP 发出去，
   失败了隔一段时间重试，试 MAX_TRIES 次还不行就标成 failed；
3. 发信服务在 config/mail.toml 里配（见 config/mail.example.toml）：阿里云邮件推送、Amazon SES、Resend、企业邮箱都支持 SMTP，
   换服务商只改配置。provider = "none"（默认）时什么都不发，信也不进队列。

每封信底部都有一键退订链接（也写在 List-Unsubscribe 头里，邮箱客户端能直接显示「退订」）。
退订记在 users.settings.mail[种类] = false；家长在「我的账号」里也能开关。
"""
import hashlib
import hmac
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import config, db

log = logging.getLogger("aistudy.mail")

MAIL_CONFIG_FILE = Path(os.environ.get("MAIL_CONFIG_FILE", config.BASE_DIR / "config" / "mail.toml"))
# 家长能开关的信：种类 → 在设置页上的名字
KINDS = {"weekly": "每周学习报告"}
MAX_TRIES = 6           # 第 n 次失败后隔 RETRY_MINUTES * 2^(n-1) 分钟再试：5、10、20、40、80 分钟
RETRY_MINUTES = 5
BATCH = 20              # 每轮最多发几封（发信服务一般限每秒几封，慢慢发）
SENT: list[EmailMessage] = []   # provider = "mock" 时发出的信都放在这里（只用于测试）


@dataclass
class MailSettings:
    provider: str = "none"          # smtp / mock / none
    sender: str = ""                # 发件人，例如 "BeeJoy <report@mail.beejoy.ai>"
    reply_to: str = ""
    host: str = ""
    port: int = 465
    security: str = "ssl"           # ssl（465）/ starttls（587、25、80）/ none
    username: str = ""
    password: str = ""
    timeout: float = 30
    daily_limit: int = 500          # 每天最多发几封（防止程序出错时一下子发出一大堆）
    weekly_weekday: int = 1         # 每周报告：周几发（1 = 周一，讲上周一到周日）
    weekly_hour: int = 8            # 几点以后发
    source: str = ""
    error: str = ""

    @property
    def enabled(self) -> bool:
        return self.provider not in ("", "none") and not self.error


def _env(v):
    import re
    if isinstance(v, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), v)
    return v


def parse_settings(data: dict, source: str = "") -> MailSettings:
    s = MailSettings(source=source)
    s.provider = str(_env(data.get("provider", "none")) or "none").lower()
    s.sender = str(_env(data.get("from", "")))
    s.reply_to = str(_env(data.get("reply_to", "")))
    s.daily_limit = int(data.get("daily_limit", s.daily_limit))
    smtp = {k: _env(v) for k, v in (data.get("smtp") or {}).items()}
    s.host = str(smtp.get("host", ""))
    s.port = int(smtp.get("port", s.port))
    s.security = str(smtp.get("security", s.security)).lower()
    s.username = str(smtp.get("username", ""))
    s.password = str(smtp.get("password", ""))
    s.timeout = float(smtp.get("timeout", s.timeout))
    weekly = data.get("weekly") or {}
    s.weekly_weekday = int(weekly.get("weekday", s.weekly_weekday))
    s.weekly_hour = int(weekly.get("hour", s.weekly_hour))
    if s.provider not in ("smtp", "mock", "none", ""):
        s.error = f"不认识的 provider：{s.provider}（可以填 smtp / none）"
    elif s.provider == "smtp" and not (s.host and s.sender and "@" in parseaddr(s.sender)[1]):
        s.error = "还没填好：[smtp] 的 host 和发件人 from 都要填"
    elif s.security not in ("ssl", "starttls", "none"):
        s.error = f"security 只能是 ssl / starttls / none，现在是 {s.security}"
    return s


def load_settings(path) -> MailSettings:
    """读配置文件；文件不存在 = 关闭（不报错），格式错 = 关闭并在 error 里写原因。"""
    path = Path(path)
    if not path.exists():
        return MailSettings(source=str(path))
    try:
        return parse_settings(tomllib.loads(path.read_text(encoding="utf-8")), str(path))
    except (ValueError, TypeError) as e:
        return MailSettings(source=str(path), error=f"{path.name} 格式不对：{e}")


_settings: MailSettings | None = None


def settings() -> MailSettings:
    global _settings
    if _settings is None:
        s = load_settings(MAIL_CONFIG_FILE)
        if os.environ.get("MAIL_PROVIDER") and not MAIL_CONFIG_FILE.exists():  # 测试 / 本地演示
            s.provider, s.sender = os.environ["MAIL_PROVIDER"], s.sender or "BeeJoy <report@example.com>"
        _settings = s
    return _settings


def reload() -> MailSettings:
    global _settings
    _settings = None
    return settings()


def check() -> tuple[bool, str]:
    s = settings()
    if s.provider in ("", "none") and not s.error:
        return False, "未开启（config/mail.toml 里 provider = \"none\"，不发任何邮件）"
    if s.error:
        return False, s.error
    return True, f"{s.provider} · {s.host or '-'}:{s.port} · 发件人 {s.sender}"


# ------------------------------------------------------------------ 家长的开关和退订

def wants(user: dict, kind: str) -> bool:
    """这个人要不要收这种信：默认要，退订或在设置里关掉了就不要。"""
    prefs = (db.jload(user.get("settings"), {}) or {}).get("mail") or {}
    return prefs.get(kind, True) is not False


def set_wants(user_id: int, kind: str, on: bool) -> None:
    with db.tx() as t:
        st = db.jload(t.one("SELECT settings FROM users WHERE id=?", user_id)["settings"], {}) or {}
        st.setdefault("mail", {})[kind] = bool(on)
        t.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), user_id)


def _sig(user_id: int, kind: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), f"unsub:{user_id}:{kind}".encode(), hashlib.sha256).hexdigest()[:24]


def unsub_token(user_id: int, kind: str) -> str:
    return f"{user_id}.{kind}.{_sig(user_id, kind)}"


def read_unsub_token(token: str) -> tuple[int, str] | None:
    try:
        uid, kind, sig = (token or "").split(".")
        uid = int(uid)
    except ValueError:
        return None
    if kind not in KINDS or not hmac.compare_digest(sig, _sig(uid, kind)):
        return None
    return uid, kind


def unsub_url(user_id: int, kind: str) -> str:
    return f"{config.PUBLIC_URL}/mail/unsub?t={unsub_token(user_id, kind)}" if config.PUBLIC_URL else ""


# ------------------------------------------------------------------ 队列

def enqueue(user: dict, kind: str, ref: str, subject: str, text: str, html: str = "") -> int | None:
    """写进发信队列。没开发信、对方退订了、这封已经写过了，都返回 None。"""
    if not settings().enabled or not wants(user, kind) or not user.get("email"):
        return None
    now = db.now()
    n = db.run("INSERT INTO mail_outbox(user_id,to_addr,kind,ref,subject,body_text,body_html,next_at,created_at) "
               "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,kind,ref) DO NOTHING",
               user["id"], user["email"], kind, ref, subject, text, html, now, now)
    if not n:
        return None
    return db.one("SELECT id FROM mail_outbox WHERE user_id=? AND kind=? AND ref=?", user["id"], kind, ref)["id"]


def build_message(row: dict, s: MailSettings | None = None) -> EmailMessage:
    s = s or settings()
    msg = EmailMessage()
    msg["Subject"] = row["subject"]
    name, addr = parseaddr(s.sender)
    msg["From"] = formataddr((name, addr))
    msg["To"] = row["to_addr"]
    if s.reply_to:
        msg["Reply-To"] = s.reply_to
    msg["Message-ID"] = make_msgid(domain=addr.rpartition("@")[2] or None)
    url = unsub_url(row["user_id"], row["kind"]) if row.get("user_id") and row["kind"] in KINDS else ""
    if url:  # 邮箱客户端显示「退订」按钮；一键退订（RFC 8058）由 POST /mail/unsub 处理
        msg["List-Unsubscribe"] = f"<{url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(row["body_text"])
    if row.get("body_html"):
        msg.add_alternative(row["body_html"], subtype="html")
    return msg


def deliver(msg: EmailMessage, s: MailSettings | None = None) -> None:
    """真正发出去一封信；失败抛异常。"""
    s = s or settings()
    if s.provider == "mock":
        SENT.append(msg)
        return
    if s.security == "ssl":
        server = smtplib.SMTP_SSL(s.host, s.port, timeout=s.timeout, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(s.host, s.port, timeout=s.timeout)
    with server:
        if s.security == "starttls":
            server.starttls(context=ssl.create_default_context())
        if s.username:
            server.login(s.username, s.password)
        server.send_message(msg)


def sent_today() -> int:
    start = datetime.fromisoformat(db.now()).replace(hour=0, minute=0, second=0).isoformat()
    return db.one("SELECT COUNT(*) AS n FROM mail_outbox WHERE status='sent' AND sent_at>=?", start)["n"]


def send_due(limit: int = BATCH) -> dict:
    """把到时间的信发出去。返回 {sent, failed, retry}。"""
    s = settings()
    out = {"sent": 0, "failed": 0, "retry": 0}
    if not s.enabled:
        return out
    room = s.daily_limit - sent_today()
    if room <= 0:
        out["limit"] = True
        return out
    now = db.now()
    for row in db.q("SELECT * FROM mail_outbox WHERE status='queued' AND next_at<=? ORDER BY id LIMIT ?", now, min(limit, room)):
        row = dict(row)
        try:
            deliver(build_message(row, s), s)
        except Exception as e:  # noqa: BLE001  SMTP、网络、证书……都当作这次没发出去
            tries = row["tries"] + 1
            gave_up = tries >= MAX_TRIES or isinstance(e, smtplib.SMTPRecipientsRefused)  # 收件地址不存在：重试也没用
            nxt = (datetime.fromisoformat(now) + timedelta(minutes=RETRY_MINUTES * 2 ** (tries - 1))).isoformat()
            db.run("UPDATE mail_outbox SET tries=?, error=?, status=?, next_at=? WHERE id=?",
                   tries, f"{type(e).__name__}: {e}"[:500], "failed" if gave_up else "queued", nxt, row["id"])
            out["failed" if gave_up else "retry"] += 1
            log.warning("mail %s to %s failed (try %s): %s", row["id"], row["to_addr"], tries, e)
            continue
        db.run("UPDATE mail_outbox SET status='sent', tries=tries+1, error='', sent_at=? WHERE id=?", db.now(), row["id"])
        out["sent"] += 1
    return out


def run() -> dict:
    """worker 每轮调用：到点了生成每周报告，再发队列里的信。没开发信就什么都不做。"""
    if not settings().enabled:
        return {"skipped": "没开发信"}
    from . import reports
    from .jobs import lease
    with lease("mail") as task:
        if task is None:
            return {"skipped": "另一个进程正在发"}
        res = {"queued": reports.queue_weekly(), **send_due()}
        task.result.update(res)
        return res


def recent(limit: int = 20) -> list:
    return db.q("SELECT id, to_addr, kind, subject, status, tries, error, created_at, sent_at FROM mail_outbox "
                "ORDER BY id DESC LIMIT ?", limit)


def send_test(to_addr: str) -> None:
    """管理员「发一封测试信」：不进队列，直接发，失败抛异常（让管理员马上看到原因）。"""
    s = settings()
    if not s.enabled:
        raise RuntimeError(check()[1])
    from . import sitecfg
    site = sitecfg.get("site_name")
    deliver(build_message({"user_id": None, "kind": "test", "to_addr": to_addr, "subject": f"{site} 测试邮件",
                           "body_text": f"这是一封测试邮件。收到了说明 {site} 的发信配置是好的。\n\n发信服务：{check()[1]}"}, s), s)
