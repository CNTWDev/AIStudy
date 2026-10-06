"""账号系统：密码、登录会话、锁定、邀请码、重设密码、安全日志。

- 密码：PBKDF2-SHA256，20 万次迭代，每个密码独立随机盐。
- 会话：登录后生成随机令牌放进签名 cookie，数据库只存令牌的 sha256；
  退出、改密码、停用账号、管理员强制下线都会删除对应会话，立即失效。
- 锁定：同一账号连续输错 LOGIN_MAX_FAILS 次，锁定 LOGIN_LOCK_MINUTES 分钟。
- 角色：admin（网站管理员，第一个注册的账号；不带孩子）/ parent（家长：添加孩子、查看和管理）/
  kid（孩子，属于某个家长；只有孩子账号能做题学习）。is_admin=1 表示有管理后台权限（旧版本的「家长 + 管理员」账号保留这个组合）。
- 状态：active 正常 / pending 等待审批 / rejected 未通过 / disabled 已停用。
- 邀请：管理员和家长都能生成邀请码；用邀请码注册的账号记录 invited_by（谁邀请的）和 invite_code。
"""
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request

from . import config, db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ------------------------------------------------------------------ 密码

def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(calc.hex(), h)


def validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 200:
        raise ValueError("邮箱格式不对")
    return email


def validate_pw(pw: str) -> str:
    if len(pw or "") < config.PASSWORD_MIN_LEN:
        raise ValueError(f"密码至少 {config.PASSWORD_MIN_LEN} 位")
    if len(pw) > 200:
        raise ValueError("密码太长")
    return pw


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE))


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def client_ip(request: Request | None) -> str:
    if request is None:
        return ""
    # 反向代理（Caddy/Nginx）传来的真实 IP 已由 uvicorn --proxy-headers 处理
    return (request.client.host if request.client else "")[:64]


def log_event(event: str, *, user_id=None, email="", detail="", request: Request | None = None) -> None:
    db.run("INSERT INTO auth_events(user_id,email,event,detail,ip,created_at) VALUES(?,?,?,?,?,?)",
           user_id, email or "", event, detail[:300], client_ip(request), db.now())


# ------------------------------------------------------------------ 账号

def user_count() -> int:
    return db.one("SELECT COUNT(*) AS n FROM users")["n"]


def get_user(uid: int):
    return db.one("SELECT * FROM users WHERE id=?", uid)


def by_email(email: str):
    return db.one("SELECT * FROM users WHERE email=?", (email or "").strip().lower())


def create_user(email: str, password: str, name: str, role: str = "parent", *, parent_id=None, is_admin=False,
                grade="G3", school="", daily_minutes=60, status="active", invited_by=None, invite_code=None,
                apply_note="") -> int:
    email = validate_email(email)
    validate_pw(password)
    if by_email(email):
        raise ValueError("这个邮箱已注册")
    now = db.now()
    return db.insert(
        "INSERT INTO users(email,pw_hash,name,role,parent_id,is_admin,grade,school,daily_minutes,status,invited_by,"
        "invite_code,apply_note,approved_at,pw_changed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        email, hash_pw(password), (name or "").strip()[:40] or {"parent": "家长", "admin": "管理员"}.get(role, "孩子"),
        role, parent_id, 1 if is_admin else 0, grade, school, daily_minutes, status, invited_by, invite_code,
        (apply_note or "")[:300], now if status == "active" else None, now, now)


def set_password(uid: int, password: str, *, keep_session: str | None = None) -> None:
    """改密码，并让这个账号的其他登录会话全部失效。"""
    validate_pw(password)
    with db.tx() as t:
        t.run("UPDATE users SET pw_hash=?, pw_changed_at=?, failed_logins=0, locked_until=NULL WHERE id=?",
              hash_pw(password), db.now(), uid)
        if keep_session:
            t.run("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", uid, _sha(keep_session))
        else:
            t.run("DELETE FROM sessions WHERE user_id=?", uid)


def set_status(uid: int, status: str, by: int | None = None) -> None:
    assert status in ("active", "disabled", "pending", "rejected")
    with db.tx() as t:
        if status == "active":
            t.run("UPDATE users SET status=?, approved_at=COALESCE(approved_at, ?), approved_by=COALESCE(approved_by, ?) "
                  "WHERE id=?", status, db.now(), by, uid)
        else:
            t.run("UPDATE users SET status=? WHERE id=?", status, uid)
            t.run("DELETE FROM sessions WHERE user_id=?", uid)


def authenticate(email: str, password: str, request: Request | None = None):
    """返回 (user, 错误信息)。"""
    email = (email or "").strip().lower()
    u = by_email(email)
    if not u:
        check_pw(password, hash_pw("x"))  # 耗时相同，避免通过响应时间判断邮箱是否存在
        log_event("login_fail", email=email, detail="no such user", request=request)
        return None, "邮箱或密码不对"
    if u["locked_until"] and u["locked_until"] > db.now():
        log_event("login_locked", user_id=u["id"], email=email, request=request)
        return None, f"密码错误次数太多，账号已临时锁定，请 {config.LOGIN_LOCK_MINUTES} 分钟后再试"
    if not check_pw(password, u["pw_hash"]):
        fails = u["failed_logins"] + 1
        locked = _iso(_now() + timedelta(minutes=config.LOGIN_LOCK_MINUTES)) if fails >= config.LOGIN_MAX_FAILS else None
        db.run("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", 0 if locked else fails, locked, u["id"])
        log_event("login_fail", user_id=u["id"], email=email, detail="locked" if locked else f"fail {fails}", request=request)
        return None, "邮箱或密码不对"
    if u["status"] != "active":
        log_event("login_" + u["status"], user_id=u["id"], email=email, request=request)
        return None, {"pending": "账号已提交申请，正在等待管理员审批，通过后就能登录",
                      "rejected": "账号申请没有通过，请联系管理员"}.get(u["status"], "这个账号已被停用，请联系家长或管理员")
    return u, ""


# ------------------------------------------------------------------ 会话

def login(request: Request, u) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with db.tx() as t:
        t.run("INSERT INTO sessions(token_hash,user_id,created_at,last_seen,expires_at,ip,user_agent) VALUES(?,?,?,?,?,?,?)",
              _sha(token), u["id"], _iso(now), _iso(now), _iso(now + timedelta(days=config.SESSION_DAYS)),
              client_ip(request), (request.headers.get("user-agent") or "")[:200])
        t.run("UPDATE users SET last_login_at=?, failed_logins=0, locked_until=NULL WHERE id=?", _iso(now), u["id"])
        t.run("DELETE FROM sessions WHERE expires_at<?", _iso(now))
    request.session.clear()
    request.session["sid"] = token
    log_event("login", user_id=u["id"], email=u["email"], request=request)
    return token


def logout(request: Request) -> None:
    token = request.session.get("sid")
    if token:
        db.run("DELETE FROM sessions WHERE token_hash=?", _sha(token))
    request.session.clear()


def current_session_hash(request: Request) -> str:
    return _sha(request.session.get("sid") or "")


def current_user(request: Request):
    if hasattr(request.state, "user"):
        return request.state.user
    user = None
    token = request.session.get("sid")
    if token:
        s = db.one("SELECT * FROM sessions WHERE token_hash=?", _sha(token))
        now = _now()
        if s and s["expires_at"] > _iso(now):
            u = get_user(s["user_id"])
            if u and u["status"] == "active":
                user = u
                if s["last_seen"] < _iso(now - timedelta(minutes=5)):
                    with db.tx() as t:
                        t.run("UPDATE sessions SET last_seen=? WHERE token_hash=?", _iso(now), s["token_hash"])
                        t.run("UPDATE users SET last_active_at=? WHERE id=?", _iso(now), u["id"])
        if user is None:
            request.session.clear()
    request.state.user = user
    return user


def sessions_of(uid: int) -> list:
    return db.q("SELECT * FROM sessions WHERE user_id=? AND expires_at>? ORDER BY last_seen DESC", uid, db.now())


def revoke_session(uid: int, token_hash: str) -> None:
    db.run("DELETE FROM sessions WHERE user_id=? AND token_hash=?", uid, token_hash)


# ------------------------------------------------------------------ 邀请码 / 重设密码

def create_invite(created_by: int | None, note="", max_uses=1, days=14) -> str:
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    db.run("INSERT INTO invites(code,created_by,note,max_uses,used,expires_at,created_at) VALUES(?,?,?,?,0,?,?)",
           code, created_by, note[:100], max(1, min(int(max_uses), 1000)), _iso(_now() + timedelta(days=max(1, int(days)))),
           db.now())
    return code


def open_invites(uid: int) -> int:
    """这个人手里还能用的邀请码数量。"""
    return db.one("SELECT COUNT(*) AS n FROM invites WHERE created_by=? AND used<max_uses AND (expires_at IS NULL OR expires_at>?)",
                  uid, db.now())["n"]


def use_invite(t: "db.Tx", code: str):
    """邀请码有效则占用一次，返回邀请码记录；无效返回 None。"""
    code = (code or "").strip().upper()
    if not code:
        return None
    n = t.run("UPDATE invites SET used=used+1 WHERE code=? AND used<max_uses AND (expires_at IS NULL OR expires_at>?)",
              code, db.now())
    return t.one("SELECT * FROM invites WHERE code=?", code) if n == 1 else None


def create_reset(uid: int, created_by: int | None, hours=48) -> str:
    token = secrets.token_urlsafe(24)
    db.run("INSERT INTO password_resets(token_hash,user_id,created_by,expires_at,created_at) VALUES(?,?,?,?,?)",
           _sha(token), uid, created_by, _iso(_now() + timedelta(hours=hours)), db.now())
    return token


def reset_target(token: str):
    r = db.one("SELECT * FROM password_resets WHERE token_hash=? AND used_at IS NULL AND expires_at>?", _sha(token), db.now())
    return get_user(r["user_id"]) if r else None


def consume_reset(token: str, password: str) -> int | None:
    u = reset_target(token)
    if not u:
        return None
    set_password(u["id"], password)
    db.run("UPDATE password_resets SET used_at=? WHERE token_hash=?", db.now(), _sha(token))
    return u["id"]


# ------------------------------------------------------------------ 权限

class LoginRequired(Exception):
    pass


def require_user(request: Request):
    u = current_user(request)
    if not u:
        raise LoginRequired()
    return u


def require_parent(request: Request):
    u = require_user(request)
    if u["role"] != "parent":
        raise HTTPException(403, "只有家长可以访问")
    return u


def require_admin(request: Request):
    u = require_user(request)
    if not (u["is_admin"] or u["role"] == "admin"):
        raise HTTPException(403, "只有管理员可以访问")
    return u


def kid_of(parent, kid_id: int):
    k = db.one("SELECT * FROM users WHERE id=? AND parent_id=?", kid_id, parent["id"])
    if not k:
        raise HTTPException(404, "没有这个孩子账号")
    return k


# ------------------------------------------------------------------ 外部工具令牌（浏览器划词插件）

def create_api_token(uid: int, name: str = "浏览器插件") -> str:
    token = "ais_" + secrets.token_urlsafe(24)
    db.run("INSERT INTO api_tokens(token_hash,user_id,name,created_at) VALUES(?,?,?,?)", _sha(token), uid, name[:40], db.now())
    return token


def api_tokens_of(uid: int) -> list:
    return db.q("SELECT * FROM api_tokens WHERE user_id=? ORDER BY created_at DESC", uid)


def revoke_api_token(uid: int, token_hash: str) -> None:
    db.run("DELETE FROM api_tokens WHERE user_id=? AND token_hash=?", uid, token_hash)


def user_by_token(request: Request):
    """从 Authorization: Bearer ais_xxx 取用户；无效时抛 401。"""
    h = request.headers.get("authorization", "")
    token = h[7:].strip() if h.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(401, "缺少连接码")
    row = db.one("SELECT * FROM api_tokens WHERE token_hash=?", _sha(token))
    u = get_user(row["user_id"]) if row else None
    if not u or u["status"] != "active":
        raise HTTPException(401, "连接码无效或已被收回，请在 AIStudy「我的账号」里重新生成")
    if not row["last_used"] or row["last_used"][:13] != db.now()[:13]:
        db.run("UPDATE api_tokens SET last_used=? WHERE token_hash=?", db.now(), row["token_hash"])
    return u
