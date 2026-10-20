"""账号系统：密码、登录会话、锁定、邀请码、重设密码、安全日志。

- 密码：PBKDF2-SHA256，20 万次迭代，每个密码独立随机盐。
- 会话：登录后生成随机令牌放进签名 cookie，数据库只存令牌的 sha256；
  退出、改密码、停用账号、管理员强制下线都会删除对应会话，立即失效。
- 锁定：同一账号连续输错 LOGIN_MAX_FAILS 次，锁定 LOGIN_LOCK_MINUTES 分钟。
- 角色：admin（网站管理员，第一个注册的账号；不带孩子）/ parent（家长：添加孩子、查看和管理）/
  kid（孩子，属于某个家长；只有孩子账号能做题学习）。is_admin=1 表示有管理后台权限（旧版本的「家长 + 管理员」账号保留这个组合）。
- 状态：active 正常 / pending 等待审批 / rejected 未通过 / disabled 已停用。
- 邀请：管理员和家长都能生成邀请码；用邀请码注册的账号记录 invited_by（谁邀请的）和 invite_code。
- 登录：大人用邮箱 + 密码；家长添加的孩子用登录名 + 4 位数字（孩子没有邮箱，也记不住长密码，见 uses_pin）。
  设备会记住在上面登录过的账号（device_logins），登录页先列出这些头像，点一下再输数字或密码就进去（像电脑的切换用户）。
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
from .catalog import is_adult

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOGIN_RE = re.compile(r"^[0-9a-z_\-\u4e00-\u9fff]{2,16}$")
PIN_RE = re.compile(r"^\d{4}$")
PIN_LOCKED = "9999-12-31T00:00:00"   # 孩子的数字连错 LOGIN_MAX_FAILS 次：一直锁着，家长解锁（4 位数字不能让人慢慢试）
DEVICE_COOKIE = "bj_dev"


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


def validate_login(login: str) -> str:
    login = re.sub(r"\s+", "", login or "").lower()
    if not LOGIN_RE.match(login):
        raise ValueError("登录名用 2～16 个汉字、字母或数字，比如：果果、dudu")
    return login


def validate_pin(pin: str) -> str:
    pin = (pin or "").strip()
    if not PIN_RE.match(pin):
        raise ValueError("请设 4 位数字，比如 2580")
    if len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210":
        raise ValueError("这个数字太好猜了（像 1111、1234），换一个")
    return pin


def uses_pin(u) -> bool:
    """家长管着的孩子（有家长、还没到大学）用登录名 + 4 位数字登录；大人（家长、自学者、家里的成人成员）用密码。"""
    return bool(u) and u["role"] == "kid" and bool(u["parent_id"]) and not is_adult(u["grade"])


def validate_secret(pw: str, pin: bool) -> str:
    return validate_pin(pw) if pin else validate_pw(pw)


def account_name(u) -> str:
    """账号在页面和日志里怎么称呼：有邮箱写邮箱，孩子写登录名。"""
    return (u.get("email") or u.get("login") or "") if u else ""


def validate_pw(pw: str) -> str:
    if len(pw or "") < config.PASSWORD_MIN_LEN:
        raise ValueError(f"密码至少 {config.PASSWORD_MIN_LEN} 位")
    if len(pw) > 200:
        raise ValueError("密码太长")
    return pw


NICK_MAX = 10


def set_nickname(user: dict, nick: str) -> str:
    """起昵称：和别的家庭的孩子一起玩、上排行榜时只显示昵称。1～10 个字，不能用自己的名字（真名不出现在公开的地方）。"""
    nick = check_nickname(user.get("name") or "", nick)
    db.run("UPDATE users SET nickname=? WHERE id=?", nick, user["id"])
    return nick


def check_nickname(name: str, nick: str) -> str:
    """昵称规则（不写库）：添加孩子时先检查，免得账号建好了才发现昵称不行。"""
    nick = re.sub(r"\s+", " ", "".join(ch for ch in (nick or "") if ch.isprintable())).strip()
    if not nick:
        raise ValueError("昵称不能是空的")
    if len(nick) > NICK_MAX:
        raise ValueError(f"昵称最多 {NICK_MAX} 个字")
    name = name.strip()
    given = name[1:] if len(name) >= 3 else ""   # 名（不带姓）也不行
    if len(name) >= 2 and (name in nick or nick in name or (given and given in nick)):
        raise ValueError("昵称别用自己的名字，起一个外号吧")
    if re.search(r"\d{5,}|@", nick):
        raise ValueError("昵称里不要放电话、邮箱这类信息")
    return nick


def public_name(user: dict) -> str:
    """在公开的地方（别的家庭的孩子能看到）显示的名字：昵称，没起就是「小玩家 + 编号」。"""
    return (user.get("nickname") or "").strip() or f"小玩家{user['id']}"


def is_private(user: dict) -> bool:
    """隐私模式（家长在孩子资料里打开，默认关）：不上任何排行榜，不进全站的公开房间（家里的、用密码约好的房间照样能进）。
    以前家长单独关过「公开房间」的，也算打开了隐私模式。"""
    st = db.jload(user.get("settings"), {}) or {}
    return bool(st.get("private", st.get("public_rooms") is False))


def set_private(user_id: int, on: bool) -> None:
    u = db.one("SELECT settings FROM users WHERE id=?", user_id)
    st = db.jload(u["settings"], {}) if u else {}
    st["private"] = bool(on)
    st.pop("public_rooms", None)
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), user_id)


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


def by_login(login: str):
    return db.one("SELECT * FROM users WHERE login=?", re.sub(r"\s+", "", login or "").lower())


def by_account(ident: str):
    """登录框里填的：带 @ 的是邮箱，不带的是登录名。"""
    return by_email(ident) if "@" in (ident or "") else by_login(ident)


def check_free(email: str | None, login: str | None, me: int | None = None) -> None:
    for col, v, msg in (("email", email, "这个邮箱已被别的账号使用"), ("login", login, "这个登录名已经有人用了，换一个，或者在后面加个数字")):
        other = v and db.one(f"SELECT id FROM users WHERE {col}=?", v)
        if other and other["id"] != me:
            raise ValueError(msg)


def suggest_login(name: str) -> str:
    """家长添加孩子时登录名的建议：就用名字（孩子最记得住），重名在后面加数字。"""
    base = re.sub(r"[^0-9a-z_\-\u4e00-\u9fff]", "", (name or "").lower())[:12] or "kid"
    for n in ([""] if len(base) >= 2 else []) + list(range(1 if len(base) < 2 else 2, 1000)):
        if not by_login(f"{base}{n}"):
            return f"{base}{n}"
    return ""


def create_user(email: str | None, password: str, name: str, role: str = "parent", *, parent_id=None, is_admin=False,
                grade="", school="", daily_minutes=60, status="active", invited_by=None, invite_code=None,
                apply_note="", login: str | None = None) -> int:
    """email 和 login 至少有一个：大人用邮箱，家长添加的孩子用登录名（邮箱选填）。"""
    email = validate_email(email) if email else None
    login = validate_login(login) if login else None
    if not (email or login):
        raise ValueError("请填登录名" if parent_id else "请填邮箱")
    if not grade:
        from .catalog import catalog
        grade = catalog.default_grade
    validate_secret(password, uses_pin({"role": role, "parent_id": parent_id, "grade": grade}))
    if email and by_email(email):
        raise ValueError("这个邮箱已注册")
    check_free(None, login)
    now = db.now()
    return db.insert(
        "INSERT INTO users(email,login,pw_hash,name,role,parent_id,is_admin,grade,school,daily_minutes,status,invited_by,"
        "invite_code,apply_note,approved_at,pw_changed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        email, login, hash_pw(password), (name or "").strip()[:40] or {"parent": "家长", "admin": "管理员"}.get(role, "孩子"),
        role, parent_id, 1 if is_admin else 0, grade, school, daily_minutes, status, invited_by, invite_code,
        (apply_note or "")[:300], now if status == "active" else None, now, now)


def set_password(uid: int, password: str, *, keep_session: str | None = None, must_change: bool = False) -> None:
    """改密码，并让这个账号的其他登录会话全部失效。must_change：临时密码，下次登录要先改成自己的。
    家长管着的孩子（uses_pin）密码就是 4 位数字。"""
    validate_secret(password, uses_pin(get_user(uid)))
    with db.tx() as t:
        t.run("UPDATE users SET pw_hash=?, pw_changed_at=?, failed_logins=0, locked_until=NULL, must_change_pw=? WHERE id=?",
              hash_pw(password), db.now(), 1 if must_change else 0, uid)
        if keep_session:
            t.run("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", uid, _sha(keep_session))
        else:
            t.run("DELETE FROM sessions WHERE user_id=?", uid)


def temp_password(u=None) -> str:
    """好读好输的临时密码：去掉了 0/O、1/l/I 这类容易看错的字符。孩子（uses_pin）是 4 位数字。"""
    while uses_pin(u):
        pin = "".join(secrets.choice("0123456789") for _ in range(4))
        try:
            return validate_pin(pin)
        except ValueError:
            continue
    abc = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(abc) for _ in range(4)) + "-" + "".join(secrets.choice(abc) for _ in range(4))


def unlock(uid: int) -> None:
    db.run("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=?", uid)


def signout_all(uid: int) -> int:
    return db.run("DELETE FROM sessions WHERE user_id=?", uid)


def set_status(uid: int, status: str, by: int | None = None) -> None:
    assert status in ("active", "disabled", "pending", "rejected")
    with db.tx() as t:
        if status == "active":
            t.run("UPDATE users SET status=?, approved_at=COALESCE(approved_at, ?), approved_by=COALESCE(approved_by, ?) "
                  "WHERE id=?", status, db.now(), by, uid)
        else:
            t.run("UPDATE users SET status=? WHERE id=?", status, uid)
            t.run("DELETE FROM sessions WHERE user_id=?", uid)


LOCKED_KID = "数字连着输错了好几次，先请爸爸妈妈在家长页点一下「解除锁定」"


def authenticate(ident: str, password: str, request: Request | None = None, u=None):
    """返回 (user, 错误信息)。ident 是邮箱或登录名；u：已经知道是谁（在设备上点了头像）。"""
    ident = (ident or "").strip().lower()
    u = u or by_account(ident)
    if not u:
        check_pw(password, hash_pw("x"))  # 耗时相同，避免通过响应时间判断账号是否存在
        log_event("login_fail", email=ident, detail="no such user", request=request)
        return None, "账号或密码不对"
    ident = account_name(u)
    pin = uses_pin(u)
    if u["locked_until"] and u["locked_until"] > db.now():
        log_event("login_locked", user_id=u["id"], email=ident, request=request)
        return None, LOCKED_KID if pin else f"密码错误次数太多，账号已临时锁定，请 {config.LOGIN_LOCK_MINUTES} 分钟后再试"
    if not check_pw(password, u["pw_hash"]):
        fails = u["failed_logins"] + 1
        locked = (PIN_LOCKED if pin else _iso(_now() + timedelta(minutes=config.LOGIN_LOCK_MINUTES))) \
            if fails >= config.LOGIN_MAX_FAILS else None
        db.run("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", 0 if locked else fails, locked, u["id"])
        log_event("login_fail", user_id=u["id"], email=ident, detail="locked" if locked else f"fail {fails}", request=request)
        if locked and pin:
            return None, LOCKED_KID
        return None, "数字不对，再想想" if pin else "账号或密码不对"
    if u["status"] != "active":
        log_event("login_" + u["status"], user_id=u["id"], email=ident, request=request)
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
    log_event("login", user_id=u["id"], email=account_name(u), request=request)
    return token


# ------------------------------------------------------------------ 记住设备上的孩子

def remember_device(request: Request, response, u) -> None:
    """登录成功：这台设备记住这个账号，下次登录页直接点头像。设备令牌放在长期 cookie 里，退出登录也不删。"""
    token = request.cookies.get(DEVICE_COOKIE) or ""
    if not re.fullmatch(r"[\w-]{20,64}", token):
        token = secrets.token_urlsafe(24)
    response.set_cookie(DEVICE_COOKIE, token, max_age=400 * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
    with db.tx() as t:
        t.run("DELETE FROM device_logins WHERE device_hash=? AND user_id=?", _sha(token), u["id"])
        t.run("INSERT INTO device_logins(device_hash,user_id,last_at) VALUES(?,?,?)", _sha(token), u["id"], db.now())


def device_accounts(request: Request) -> list:
    """这台设备上登录过的账号（还能用的），最近用过的在前；孩子点了输 4 位数字，大人输密码。"""
    token = request.cookies.get(DEVICE_COOKIE)
    if not token:
        return []
    rows = db.q("SELECT u.* FROM device_logins d JOIN users u ON u.id=d.user_id WHERE d.device_hash=? AND u.status='active' "
                "ORDER BY d.last_at DESC", _sha(token))
    return rows[:8]


def forget_device(request: Request, uid: int) -> None:
    token = request.cookies.get(DEVICE_COOKIE)
    if token:
        db.run("DELETE FROM device_logins WHERE device_hash=? AND user_id=?", _sha(token), uid)


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


def is_self_learner(u) -> bool:
    """自学者：自己注册、自己选课的学习账号（多半是备考的成人）。和孩子账号一样能学习，只是没有家长，
    课程、每天时长、考试日期都由自己在「我的课程」里设。挂在家庭里的成人成员也自己安排学习，见 self_managed。"""
    return bool(u) and u["role"] == "kid" and not u["parent_id"]


def self_managed(u) -> bool:
    """学习自己安排：自学者，或家庭里的成人成员（年级在大学及以上）。家庭只是把一家人放在一起（家里的房间、一起打卡），
    家长管的只是未成年的孩子。成人成员自己在「我的课程」里选课，学习记录只有本人能看；家长只能管账号（改密码、暂停）。"""
    return bool(u) and u["role"] == "kid" and (not u["parent_id"] or is_adult(u["grade"]))


def with_other_kids(u) -> bool:
    """能不能上排行榜、进全站的公开房间（和别的家庭的孩子在一起）：打开隐私模式的不行，成人也不行。"""
    return not is_private(u) and not is_adult(u.get("grade"))


def kid_of(parent, kid_id: int, account_only: bool = False):
    """家长名下的一个成员。默认是要看或安排学习：成人成员（self_managed）不行；account_only：只管账号（资料、密码、暂停）。"""
    k = db.one("SELECT * FROM users WHERE id=? AND parent_id=?", kid_id, parent["id"])
    if not k:
        raise HTTPException(404, "没有这个孩子账号")
    if not account_only and self_managed(k):
        raise HTTPException(403, f"{k['name']} 是家里的成人成员，学习由 TA 自己安排，学习记录也只有 TA 自己能看。")
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
        raise HTTPException(401, "连接码无效或已被收回，请在 beejoy「我的账号」里重新生成")
    if not row["last_used"] or row["last_used"][:13] != db.now()[:13]:
        db.run("UPDATE api_tokens SET last_used=? WHERE token_hash=?", db.now(), row["token_hash"])
    return u
