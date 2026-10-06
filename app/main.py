"""AIStudy Web 应用入口。启动：uvicorn app.main:app"""
import re
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import Body, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, db, engine, insights, llm, papers, sitecfg
from .auth import LoginRequired
from .catalog import GRADES, catalog, stage_label, stage_rank
from .content import content


@asynccontextmanager
async def lifespan(app):
    db.init()
    catalog.load()
    content.load()
    engine.load_seed_items(config.SEED_DIR)
    yield
    db.close()


app = FastAPI(title="AIStudy", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=60 * 60 * 24 * 60,
                   same_site="lax", https_only=config.HTTPS_ONLY)
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=config.BASE_DIR / "app" / "templates")
templates.env.globals.update(stage_label=stage_label, catalog=catalog, STATUS_LABEL=engine.STATUS_LABEL,
                             llm_enabled=llm.enabled, GRADES=GRADES, answer_display=engine.answer_display)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "请先登录"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    p = request.url.path
    if p.startswith("/api/") or p.startswith("/ext/") or request.method != "GET":
        return JSONResponse({"error": exc.detail, "detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
    return render(request, "message.html", title="提示" if exc.status_code < 500 else "出错了",
                  text=str(exc.detail), link="/", status_code=exc.status_code)


@app.exception_handler(llm.LLMError)
async def _llm_error(request: Request, exc):
    return JSONResponse({"error": str(exc)}, status_code=503)


def render(request: Request, name: str, status_code: int = 200, **ctx):
    user = auth.current_user(request)
    kid = None
    if user:
        kid = user if user["role"] == "kid" else (
            db.one("SELECT * FROM users WHERE id=? AND parent_id=?", request.session.get("as_kid"), user["id"])
            if request.session.get("as_kid") and user["role"] == "parent" else None)
    # readonly：家长在看孩子的页面——只能查看和管理，不能替孩子做题
    base = {"user": user, "kid": kid, "readonly": bool(user and user["role"] != "kid"),
            "SITE": sitecfg.get("site_name"), "ASSISTANT": sitecfg.get("assistant_name"),
            "ASSISTANT_ICON": sitecfg.get("assistant_icon")}
    return templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)


PARENT_READONLY = "家长账号用来查看和管理。做题、阅读、复习请让孩子用自己的账号登录。"


def kid_or_redirect(request: Request, manage: bool = False):
    """当前操作的孩子：孩子本人；或家长在家长页选中的孩子（manage=True 的页面：查看、更新进度、导入试卷等）。
    做题、阅读、复习这类「学习」操作只有孩子自己的账号能做。"""
    user = auth.require_user(request)
    if user["role"] == "kid":
        return user
    if user["role"] != "parent":
        raise HTTPException(403, "管理员账号不用来学习；在「管理 → 家庭与孩子」里可以查看每个孩子的情况。")
    kid_id = request.session.get("as_kid")
    k = db.one("SELECT * FROM users WHERE id=? AND parent_id=?", kid_id, user["id"]) if kid_id else None
    if not k:
        raise HTTPException(400, "请先在家长页选择一个孩子")
    if not manage:
        raise HTTPException(403, PARENT_READONLY)
    return k


def enrollments(kid_id: int):
    return [dict(r) for r in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", kid_id)
            if r["pack_id"] in catalog.packs]


# ================================================================== 登录 / 账号

def _reg_mode() -> str:
    return "first" if auth.user_count() == 0 else sitecfg.registration()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", 303)
    if user["role"] == "admin":
        return RedirectResponse("/admin", 303)
    if user["role"] == "parent":
        return RedirectResponse("/parent", 303)
    return RedirectResponse("/today", 303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, invite: str = ""):
    if auth.current_user(request):
        return RedirectResponse("/", 303)
    return render(request, "login.html", mode=_reg_mode(), invite=invite, show_register=bool(invite))


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    u, err = auth.authenticate(email, password, request)
    if not u:
        return render(request, "login.html", error=err, mode=_reg_mode(), email=email)
    auth.login(request, u)
    return RedirectResponse("/", 303)


@app.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form(...),
             invite: str = Form(""), note: str = Form("")):
    mode = _reg_mode()
    if mode == "closed":
        raise HTTPException(403, "注册已关闭，请联系管理员创建账号")
    invite = (invite or "").strip().upper()
    try:
        email = auth.validate_email(email)
        auth.validate_pw(password)
        if auth.by_email(email):
            raise ValueError("这个邮箱已注册，可以直接登录")
        inv = None
        if invite:
            with db.tx() as t:
                inv = auth.use_invite(t, invite)
            if not inv:
                raise ValueError("邀请码不对、已用完或已过期")
        elif mode == "invite":
            raise ValueError("需要邀请码才能注册（向管理员或已经在用的家长索取）")
        status = "pending" if (mode == "approval" and not inv) else "active"
        # 第一个账号是网站管理员（不带孩子）；之后注册的都是家长
        uid = auth.create_user(email, password, name, "admin" if mode == "first" else "parent", is_admin=(mode == "first"),
                               status=status,
                               invited_by=inv["created_by"] if inv else None, invite_code=inv["code"] if inv else None,
                               apply_note=note)
    except ValueError as e:
        return render(request, "login.html", error=str(e), mode=mode, email=email, invite=invite, show_register=True)
    auth.log_event("register" if status == "active" else "apply", user_id=uid, email=email,
                   detail=f"mode={mode} invite={invite}", request=request)
    if status == "pending":
        return render(request, "message.html", title="申请已提交",
                      text="管理员审批通过后，就可以用这个邮箱和密码登录了。", link="/login")
    auth.login(request, auth.get_user(uid))
    return RedirectResponse("/admin" if mode == "first" else "/parent", 303)


@app.get("/logout")
def logout(request: Request):
    auth.logout(request)
    return RedirectResponse("/login", 303)


@app.get("/reset/{token}", response_class=HTMLResponse)
def reset_page(request: Request, token: str):
    u = auth.reset_target(token)
    return render(request, "reset.html", target=u, token=token)


@app.post("/reset/{token}")
def reset_submit(request: Request, token: str, password: str = Form(...), password2: str = Form(...)):
    u = auth.reset_target(token)
    if not u:
        return render(request, "reset.html", target=None, token=token)
    if password != password2:
        return render(request, "reset.html", target=u, token=token, error="两次输入的密码不一样")
    try:
        auth.consume_reset(token, password)
    except ValueError as e:
        return render(request, "reset.html", target=u, token=token, error=str(e))
    auth.log_event("password_reset", user_id=u["id"], email=u["email"], request=request)
    return render(request, "message.html", title="密码已重设", text="请用新密码登录。", link="/login")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str = "", err: str = ""):
    u = auth.require_user(request)
    return render(request, "settings.html", sessions=auth.sessions_of(u["id"]), this_sid=auth.current_session_hash(request),
                  msg=msg, err=err)


def _back(msg="", err=""):
    from urllib.parse import urlencode
    return RedirectResponse("/settings?" + urlencode({"msg": msg, "err": err}), 303)


@app.post("/settings/profile")
def settings_profile(request: Request, name: str = Form(...), email: str = Form(...)):
    u = auth.require_user(request)
    if u["role"] == "kid":
        return _back(err="孩子账号的名字和邮箱由家长修改")
    try:
        email = auth.validate_email(email)
    except ValueError as e:
        return _back(err=str(e))
    other = auth.by_email(email)
    if other and other["id"] != u["id"]:
        return _back(err="这个邮箱已被别的账号使用")
    db.run("UPDATE users SET name=?, email=? WHERE id=?", (name or "").strip()[:40] or u["name"], email, u["id"])
    if email != u["email"]:
        auth.log_event("email_change", user_id=u["id"], email=email, detail=f"from {u['email']}", request=request)
    return _back(msg="已保存")


@app.post("/password")
def change_password(request: Request, old: str = Form(...), new: str = Form(...)):
    u = auth.require_user(request)
    if not auth.check_pw(old, u["pw_hash"]):
        return _back(err="旧密码不对")
    try:
        auth.set_password(u["id"], new, keep_session=request.session.get("sid"))
    except ValueError as e:
        return _back(err=str(e))
    auth.log_event("password_change", user_id=u["id"], email=u["email"], request=request)
    return _back(msg="密码已修改，其他设备上的登录已退出")


@app.post("/settings/sessions/revoke")
def revoke_session(request: Request, token_hash: str = Form(""), all_others: str = Form("")):
    u = auth.require_user(request)
    if all_others:
        db.run("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", u["id"], auth.current_session_hash(request))
    else:
        auth.revoke_session(u["id"], token_hash)
    return _back(msg="已退出所选设备")


# ================================================================== 管理后台

def kid_brief(k) -> dict:
    """一个孩子的概况：家长页和管理后台共用。"""
    m = engine.get_mastery(k["id"])
    packs = [{"pack": catalog.packs[e["pack_id"]], "stage": e["stage"], "sum": engine.pack_summary(k["id"], e["pack_id"], m)}
             for e in enrollments(k["id"])]
    cal = engine.calendar(k["id"], weeks=1)
    today = engine.today_plan(k["id"]) if packs else {"plan": [], "minutes": 0}
    weak = sorted([v for v in m.values() if v["status"] == "weak" and catalog.kp(v["kp_id"])], key=lambda v: v["score"])
    return {"u": k, "packs": packs, "streak": engine.streak(k["id"]), "week": cal,
            "week_min": sum(d["minutes"] for d in cal), "week_days": sum(1 for d in cal if d["minutes"] or d["checked"]),
            "today": today, "today_done": sum(1 for t in today["plan"] if t.get("done")),
            "insights": insights.open_insights(k["id"], limit=6),
            "weak": [catalog.kp(w["kp_id"]) for w in weak[:5]], "weak_n": len(weak),
            "due": len(engine.due_cards(k["id"], 500)),
            "words": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='word'", k["id"])["n"]}


ADMIN_TABS = [("overview", "概览"), ("stats", "数据统计"), ("families", "家庭与孩子"), ("invites", "邀请码"),
              ("tree", "邀请关系"), ("log", "安全日志"), ("system", "站点设置")]


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, tab: str = "overview", msg: str = "", link: str = "", q: str = ""):
    a = auth.require_admin(request)
    ctx = {"tab": tab, "tabs": ADMIN_TABS, "msg": msg, "link": link, "now": db.now(), "q": q}
    users = db.q("SELECT u.*, i.name AS inviter_name, i.email AS inviter_email, p.name AS parent_name "
                 "FROM users u LEFT JOIN users i ON i.id=u.invited_by LEFT JOIN users p ON p.id=u.parent_id ORDER BY u.id")
    by_id = {u["id"]: u for u in users}
    ctx["pending"] = [u for u in users if u["status"] == "pending"]
    week_ago = (db.today() - timedelta(days=7)).isoformat()
    ctx["stats"] = {
        "families": sum(1 for u in users if u["role"] == "parent" and u["status"] == "active"),
        "kids": sum(1 for u in users if u["role"] == "kid"),
        "pending": len(ctx["pending"]),
        "active_kids": db.one("SELECT COUNT(DISTINCT user_id) AS n FROM days WHERE day>=? AND (minutes>0 OR checked_in=1)",
                              week_ago)["n"],
        "invites_open": db.one("SELECT COUNT(*) AS n FROM invites WHERE used<max_uses AND (expires_at IS NULL OR expires_at>?)",
                               db.now())["n"],
    }
    if tab == "overview":
        ctx["recent"] = sorted([u for u in users if u["role"] == "parent"], key=lambda u: u["created_at"], reverse=True)[:8]
    elif tab == "families":
        fams = []
        for p in users:
            if p["role"] != "parent":
                continue
            if q and q.lower() not in (p["email"] + p["name"]).lower():
                continue
            kids = [kid_brief(k) for k in users if k["parent_id"] == p["id"]]
            fams.append({"p": p, "kids": kids,
                         "invited": [u for u in users if u["invited_by"] == p["id"]]})
        ctx["families"] = fams
    elif tab == "invites":
        invites = db.q("SELECT v.*, u.name AS creator_name, u.email AS creator_email FROM invites v "
                       "LEFT JOIN users u ON u.id=v.created_by ORDER BY v.created_at DESC LIMIT 200")
        used_by = {}
        for u in users:
            if u["invite_code"]:
                used_by.setdefault(u["invite_code"], []).append(u)
        ctx["invites"], ctx["used_by"] = invites, used_by
    elif tab == "tree":
        parents = [u for u in users if u["role"] in ("parent", "admin")]  # 管理员也是邀请树的起点
        children = {}
        for u in parents:
            children.setdefault(u["invited_by"] if u["invited_by"] in by_id else None, []).append(u)
        kids_of = {}
        for u in users:
            if u["role"] == "kid":
                kids_of.setdefault(u["parent_id"], []).append(u)

        def walk(pid, depth):
            out = []
            for u in children.get(pid, []):
                out.append({"u": u, "depth": depth, "kids": kids_of.get(u["id"], []),
                            "n_invited": len(children.get(u["id"], []))})
                if depth < 20:
                    out += walk(u["id"], depth + 1)
            return out
        ctx["tree"] = walk(None, 0)
    elif tab == "stats":
        ctx["daily"], ctx["totals"], ctx["kid_rows"] = _site_stats(users)
    elif tab == "log":
        ctx["events"] = db.q("SELECT * FROM auth_events ORDER BY id DESC LIMIT 300")
    elif tab == "system":
        from . import migrate
        ctx["admins"] = [u for u in users if u["role"] == "admin" or u["is_admin"]]
        ctx.update(reg_mode=sitecfg.registration(), reg_modes=sitecfg.REG_MODES, site={k: sitecfg.get(k) for k in sitecfg.DEFAULTS},
                   llm_status=llm.check(), migrations=migrate.status(),
                   version=_version(), db_dialect=db.DIALECT)
    ctx["me"] = a
    return render(request, "admin.html", **ctx)


def _site_stats(users):
    """数据统计：近 14 天每天的在学孩子数、学习分钟、做题数、AI 调用次数；每个孩子近 7 天的情况。"""
    days = [(db.today() - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    since = days[0]
    per_day = {d: {"day": d, "kids": 0, "minutes": 0, "attempts": 0, "ai": 0, "asks": 0} for d in days}
    for r in db.q("SELECT day, COUNT(DISTINCT user_id) AS kids, SUM(minutes) AS minutes FROM days "
                  "WHERE day>=? AND (minutes>0 OR checked_in=1) GROUP BY day", since):
        if r["day"] in per_day:
            per_day[r["day"]].update(kids=r["kids"], minutes=r["minutes"] or 0)
    for r in db.q("SELECT SUBSTR(created_at,1,10) AS day, COUNT(*) AS n FROM attempts WHERE created_at>=? GROUP BY SUBSTR(created_at,1,10)", since):
        if r["day"] in per_day:
            per_day[r["day"]]["attempts"] = r["n"]
    for r in db.q("SELECT day, SUM(calls) AS n FROM llm_usage WHERE day>=? GROUP BY day", since):
        if r["day"] in per_day:
            per_day[r["day"]]["ai"] = r["n"] or 0
    for r in db.q("SELECT SUBSTR(m.created_at,1,10) AS day, COUNT(*) AS n FROM ask_messages m WHERE m.role='user' "
                  "AND m.created_at>=? GROUP BY SUBSTR(m.created_at,1,10)", since):
        if r["day"] in per_day:
            per_day[r["day"]]["asks"] = r["n"]
    one = lambda sql, *a: db.one(sql, *a)["n"] or 0  # noqa: E731
    totals = {"attempts": one("SELECT COUNT(*) AS n FROM attempts"),
              "accuracy": one("SELECT CAST(100*AVG(correct) AS INTEGER) AS n FROM attempts WHERE mode<>'exam'"),
              "cards": one("SELECT COUNT(*) AS n FROM cards"), "papers": one("SELECT COUNT(*) AS n FROM papers"),
              "asks": one("SELECT COUNT(*) AS n FROM ask_messages WHERE role='user'"),
              "reads": one("SELECT COUNT(*) AS n FROM reading_logs"),
              "insights": one("SELECT COUNT(*) AS n FROM insights WHERE resolved_at IS NULL"),
              "insights_solved": one("SELECT COUNT(*) AS n FROM insights WHERE resolved_at IS NOT NULL")}
    week = (db.today() - timedelta(days=7)).isoformat()
    by_id = {u["id"]: u for u in users}
    rows = []
    for k in users:
        if k["role"] != "kid":
            continue
        r = db.one("SELECT COUNT(*) AS n, SUM(correct) AS ok, SUM(dont_know) AS dk FROM attempts WHERE user_id=? AND created_at>=?",
                   k["id"], week)
        d = db.one("SELECT COUNT(*) AS n, SUM(minutes) AS m FROM days WHERE user_id=? AND day>=? AND (minutes>0 OR checked_in=1)",
                   k["id"], week)
        rows.append({"u": k, "parent": by_id.get(k["parent_id"]), "days": d["n"], "minutes": d["m"] or 0,
                     "attempts": r["n"], "acc": int(100 * (r["ok"] or 0) / r["n"]) if r["n"] else None, "dk": r["dk"] or 0,
                     "asks": one("SELECT COUNT(*) AS n FROM ask_threads WHERE user_id=? AND created_at>=?", k["id"], week),
                     "insights": one("SELECT COUNT(*) AS n FROM insights WHERE user_id=? AND resolved_at IS NULL", k["id"]),
                     "streak": engine.streak(k["id"])})
    rows.sort(key=lambda r: -r["minutes"])
    return list(per_day.values()), totals, rows


@app.post("/admin/settings")
async def admin_settings(request: Request):
    a = auth.require_admin(request)
    f = await request.form()
    vals = {k: (f.get(k) or "").strip()[:40] for k in sitecfg.DEFAULTS if k in f}
    if vals.get("registration") and vals["registration"] not in sitecfg.REG_MODES:
        raise HTTPException(400, "注册方式不对")
    if vals.get("parent_invite_limit") and not vals["parent_invite_limit"].isdigit():
        raise HTTPException(400, "邀请码上限要填数字")
    sitecfg.set_many(vals)
    auth.log_event("site_settings", user_id=a["id"], email=a["email"], detail=",".join(f"{k}={v}" for k, v in vals.items()),
                   request=request)
    return _admin_back("system", msg="站点设置已保存，立即生效")


def _version() -> str:
    head = config.BASE_DIR / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref:"):
            p = config.BASE_DIR / ".git" / ref.split(" ", 1)[1]
            return p.read_text().strip()[:7] if p.exists() else ref
        return ref[:7]
    except OSError:
        return "未知"


def _admin_back(tab="overview", msg="", link=""):
    from urllib.parse import urlencode
    return RedirectResponse("/admin?" + urlencode({"tab": tab, "msg": msg, "link": link}), 303)


@app.post("/admin/users/create")
def admin_create_user(request: Request, email: str = Form(...), name: str = Form(""), password: str = Form(...),
                      role: str = Form("parent")):
    a = auth.require_admin(request)
    role = "admin" if role == "admin" else "parent"
    try:
        uid = auth.create_user(email, password, name, role, invited_by=a["id"], is_admin=role == "admin")
    except ValueError as e:
        return _admin_back("families", msg=str(e))
    auth.log_event("admin_create_user", user_id=uid, email=email, detail=f"role={role} by {a['email']}", request=request)
    return _admin_back("families", msg=f"已创建{'管理员' if role == 'admin' else '家长'}账号 " + email.strip().lower())


@app.post("/admin/users/{uid}/status")
def admin_user_status(request: Request, uid: int, status: str = Form(...), back: str = Form("families")):
    a = auth.require_admin(request)
    if uid == a["id"]:
        return _admin_back(back, msg="不能修改自己的状态")
    u = auth.get_user(uid)
    if not u or status not in ("active", "disabled", "rejected"):
        raise HTTPException(404)
    auth.set_status(uid, status, by=a["id"])
    event = {"active": "approve" if u["status"] == "pending" else "enable", "disabled": "disable", "rejected": "reject"}[status]
    auth.log_event("admin_" + event, user_id=uid, email=u["email"], detail=f"by {a['email']}", request=request)
    word = {"approve": "已审批通过", "enable": "已启用", "disable": "已停用", "reject": "已拒绝"}[event]
    return _admin_back(back, msg=f"{u['email']} {word}")


@app.post("/admin/users/{uid}/admin")
def admin_toggle_admin(request: Request, uid: int, on: str = Form("")):
    a = auth.require_admin(request)
    u = auth.get_user(uid)
    if not u or u["role"] != "parent" or uid == a["id"]:
        return _admin_back("families", msg="只能把其他家长账号设为管理员")
    db.run("UPDATE users SET is_admin=? WHERE id=?", 1 if on else 0, uid)
    auth.log_event("admin_grant" if on else "admin_revoke", user_id=uid, email=u["email"], detail=f"by {a['email']}", request=request)
    return _admin_back("families", msg="已更新管理员权限")


@app.post("/admin/users/{uid}/reset")
def admin_reset_link(request: Request, uid: int, back: str = Form("families")):
    a = auth.require_admin(request)
    u = auth.get_user(uid)
    if not u:
        raise HTTPException(404)
    token = auth.create_reset(uid, a["id"])
    base = config.PUBLIC_URL or str(request.base_url).rstrip("/")
    auth.log_event("admin_reset_link", user_id=uid, email=u["email"], detail=f"by {a['email']}", request=request)
    return _admin_back(back, msg=f"已生成 {u['email']} 的重设密码链接（48 小时内有效，只能用一次），请发给对方：",
                       link=f"{base}/reset/{token}")


@app.get("/admin/kids/{kid_id}", response_class=HTMLResponse)
def admin_kid_report(request: Request, kid_id: int):
    auth.require_admin(request)
    k = db.one("SELECT * FROM users WHERE id=? AND role='kid'", kid_id)
    if not k:
        raise HTTPException(404)
    return render(request, "records.html", **_records_ctx(k), report_for=k)


@app.post("/admin/invites/create")
def admin_invite(request: Request, note: str = Form(""), max_uses: int = Form(1), days: int = Form(14)):
    a = auth.require_admin(request)
    code = auth.create_invite(a["id"], note, max_uses, days)
    return _admin_back("invites", msg=f"新邀请码：{code}（可用 {max_uses} 次，{days} 天内有效）",
                       link=f"{config.PUBLIC_URL or str(request.base_url).rstrip('/')}/login?invite={code}")


@app.post("/admin/invites/{code}/delete")
def admin_invite_delete(request: Request, code: str):
    auth.require_admin(request)
    db.run("DELETE FROM invites WHERE code=?", code)
    return _admin_back("invites", msg="已作废邀请码 " + code)


# ================================================================== 家长邀请亲友

@app.get("/invite", response_class=HTMLResponse)
def my_invites(request: Request, msg: str = "", link: str = ""):
    p = auth.require_parent(request)
    invites = db.q("SELECT * FROM invites WHERE created_by=? ORDER BY created_at DESC", p["id"])
    joined = db.q("SELECT id, name, email, status, invite_code, created_at FROM users WHERE invited_by=? ORDER BY id", p["id"])
    return render(request, "invite.html", invites=invites, joined=joined, msg=msg, link=link, now=db.now(),
                  limit=None if p["is_admin"] else sitecfg.parent_invite_limit(), open_n=auth.open_invites(p["id"]),
                  reg_mode=sitecfg.registration())


@app.post("/invite")
def my_invite_create(request: Request, note: str = Form("")):
    from urllib.parse import urlencode
    p = auth.require_parent(request)
    if sitecfg.registration() == "closed":
        return RedirectResponse("/invite?" + urlencode({"msg": "系统目前不开放注册，请联系管理员"}), 303)
    if not p["is_admin"] and auth.open_invites(p["id"]) >= sitecfg.parent_invite_limit():
        return RedirectResponse("/invite?" + urlencode({"msg": f"你手里还有 {sitecfg.parent_invite_limit()} 个没用完的邀请码，先把它们发出去吧"}), 303)
    code = auth.create_invite(p["id"], note, 1, 14)
    auth.log_event("invite_create", user_id=p["id"], email=p["email"], detail=code, request=request)
    base = config.PUBLIC_URL or str(request.base_url).rstrip("/")
    return RedirectResponse("/invite?" + urlencode({"msg": f"邀请码：{code}（一次有效，14 天内使用）。把下面的链接发给对方：",
                                                    "link": f"{base}/login?invite={code}"}), 303)


# ================================================================== 家长

@app.get("/parent", response_class=HTMLResponse)
def parent_home(request: Request):
    p = auth.require_parent(request)
    request.session.pop("as_kid", None)
    kids = [kid_brief(k) for k in db.q("SELECT * FROM users WHERE parent_id=? ORDER BY id", p["id"])]
    return render(request, "parent.html", kids=kids)


def _kid_form_packs(form) -> list[tuple[str, str]]:
    out = []
    for pid in catalog.packs:
        if form.get(f"pack_{pid}"):
            out.append((pid, form.get(f"stage_{pid}") or catalog.packs[pid].stages[0]))
    return out


@app.get("/parent/kids/new", response_class=HTMLResponse)
def kid_new_page(request: Request):
    auth.require_parent(request)
    return render(request, "kid_form.html", k=None, enrolled={}, packs_by_subject=catalog.by_subject())


@app.get("/parent/kids/{kid_id}/edit", response_class=HTMLResponse)
def kid_edit_page(request: Request, kid_id: int):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id)
    enrolled = {e["pack_id"]: e["stage"] for e in enrollments(kid_id)}
    return render(request, "kid_form.html", k=k, enrolled=enrolled, packs_by_subject=catalog.by_subject())


@app.post("/parent/kids/save")
async def kid_save(request: Request):
    p = auth.require_parent(request)
    form = await request.form()
    kid_id = form.get("id")
    email = (form.get("email") or "").strip().lower()
    name = (form.get("name") or "").strip()
    grade = form.get("grade") or "G3"
    minutes = int(form.get("daily_minutes") or 60)
    pw = form.get("password") or ""
    if not name:
        raise HTTPException(400, "请填写孩子的名字")
    try:
        email = auth.validate_email(email)
        if pw:
            auth.validate_pw(pw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    other = auth.by_email(email)
    if kid_id:
        k = auth.kid_of(p, int(kid_id))
        if other and other["id"] != k["id"]:
            raise HTTPException(400, "这个邮箱已被别的账号使用")
        db.run("UPDATE users SET email=?, name=?, grade=?, school=?, daily_minutes=? WHERE id=?",
               email, name, grade, form.get("school") or "", minutes, k["id"])
        if pw:
            auth.set_password(k["id"], pw)
            auth.log_event("password_set_by_parent", user_id=k["id"], email=email, detail=f"by {p['email']}", request=request)
        status = "disabled" if form.get("disabled") else "active"
        if status != k["status"]:
            auth.set_status(k["id"], status)
        kid_id = k["id"]
    else:
        if not pw:
            raise HTTPException(400, "请给孩子设一个密码")
        try:
            kid_id = auth.create_user(email, pw, name, "kid", parent_id=p["id"], grade=grade,
                                      school=form.get("school") or "", daily_minutes=minutes)
        except ValueError as e:
            raise HTTPException(400, str(e))
    chosen = _kid_form_packs(form)
    db.run("UPDATE enrollments SET active=0 WHERE user_id=?", kid_id)
    for pid, stage in chosen:
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active) VALUES(?,?,?,1) "
               "ON CONFLICT(user_id,pack_id) DO UPDATE SET stage=excluded.stage, active=1", kid_id, pid, stage)
        engine.seed_vocab(kid_id, pid, config.SEED_DIR)
    engine.today_plan(kid_id, rebuild=True)
    return RedirectResponse("/parent", 303)


# ================================================================== 家长：阅读与单词安排

@app.get("/parent/kids/{kid_id}/plan", response_class=HTMLResponse)
def kid_plan_page(request: Request, kid_id: int, msg: str = ""):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id)
    all_tracks = engine.tracks(kid_id, active_only=False)
    active = {t["kind"]: t for t in all_tracks if t["active"]}
    return render(request, "kid_plan.html", k=k, active=active, history=[t for t in all_tracks if not t["active"]],
                  content=content, msg=msg, seg={kd: engine.track_today(t) for kd, t in active.items() if kd != "words"})


@app.post("/parent/kids/{kid_id}/tracks")
async def kid_tracks_save(request: Request, kid_id: int):
    from urllib.parse import urlencode
    p = auth.require_parent(request)
    auth.kid_of(p, kid_id)
    f = await request.form()
    kind = f.get("kind")
    if kind not in engine.TRACK_KINDS:
        raise HTTPException(400)
    if f.get("action") == "stop":
        db.run("UPDATE tracks SET active=0 WHERE user_id=? AND kind=?", kid_id, kind)
        msg = f"已停止「{engine.TRACK_KINDS[kind]}」"
    elif f.get("action") == "move":
        db.run("UPDATE tracks SET position=? WHERE user_id=? AND kind=? AND active=1",
               max(0, int(f.get("position") or 0)), kid_id, kind)
        msg = "已调整进度"
    else:
        ref = f.get("ref") or ""
        minutes = max(5, min(90, int(f.get("daily_minutes") or 15)))
        amount = max(0, min(50, int(f.get("daily_amount") or 1)))
        position = max(0, int(f.get("position") or 0))
        if kind == "words":
            wl = content.word_lists.get(ref)
            if not wl:
                raise HTTPException(400, "请选择词表")
            title, unit_name, units, total = wl["title"], "词", [], len(wl["words"])
        elif ref and ref != "custom":
            b = content.book(ref)
            if not b:
                raise HTTPException(400, "没有这本书")
            title, unit_name, units, total = b["title"], b.get("unit_name") or "章", b.get("units", []), b.get("total_units", 0)
        else:
            title = (f.get("title") or "").strip().strip("《》")[:60]
            if not title:
                raise HTTPException(400, "请填写书名")
            unit_name = (f.get("unit_name") or "章").strip()[:4] or "章"
            units, total, ref = [], max(0, int(f.get("total_units") or 0)), ""
        with db.tx() as t:
            t.run("UPDATE tracks SET active=0 WHERE user_id=? AND kind=?", kid_id, kind)
            t.run("INSERT INTO tracks(user_id,kind,ref,title,unit_name,units,total_units,position,daily_amount,daily_minutes,"
                  "active,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?)", kid_id, kind, ref, title, unit_name,
                  db.jdump(units), total, position, amount if kind == "words" else max(1, amount), minutes, db.now())
        msg = f"已安排「{engine.TRACK_KINDS[kind]}」：{title}"
    engine.today_plan(kid_id, rebuild=True)
    return RedirectResponse(f"/parent/kids/{kid_id}/plan?" + urlencode({"msg": msg}), 303)


@app.get("/parent/kids/{kid_id}", response_class=HTMLResponse)
def kid_report(request: Request, kid_id: int):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id)
    return render(request, "records.html", **_records_ctx(k), report_for=k)


@app.get("/parent/as/{kid_id}")
def parent_as(request: Request, kid_id: int, next: str = "/today"):
    p = auth.require_parent(request)
    auth.kid_of(p, kid_id)
    request.session["as_kid"] = kid_id
    return RedirectResponse(next if next in ("/today", "/progress", "/subjects", "/records", "/papers") else "/today", 303)


# ================================================================== 今天

@app.get("/today", response_class=HTMLResponse)
def today(request: Request):
    k = kid_or_redirect(request, manage=True)
    if not enrollments(k["id"]):
        return render(request, "message.html", title="还没有选择学科",
                      text="请家长在「家长页 → 编辑孩子」里勾选要学的教材。")
    t = engine.today_plan(k["id"])
    st = engine.streak(k["id"])
    cal = engine.calendar(k["id"], 4)
    me = auth.current_user(request)
    return render(request, "today.html", t=t, streak=st, badges=engine.badges(st), cal=cal, week=cal[-7:],
                  stars=engine.total_stars(k["id"]), rec=engine.day_record(k["id"], t["day"]),
                  found=insights.open_insights(k["id"], for_kid=me["role"] == "kid", limit=4 if me["role"] == "kid" else 10))


@app.post("/api/plan/rebuild")
def plan_rebuild(request: Request):
    k = kid_or_redirect(request)
    return engine.today_plan(k["id"], rebuild=True)


@app.post("/api/plan/task")
def plan_task(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    engine.mark_task(k["id"], body["id"], bool(body.get("done", True)))
    return {"ok": True}


@app.post("/api/plan/task-done")
def plan_task_done(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    match = {"type": body.get("type")}
    if body.get("kp"):
        match["kp"] = body["kp"]
    engine.mark_task_by(k["id"], **match)
    return {"ok": True, "stars": engine.total_stars(k["id"])}


@app.post("/api/checkin")
def checkin(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    day = db.today().isoformat()
    engine.today_plan(k["id"])
    mood = (body.get("mood") or "")[:10]
    db.run(f"UPDATE days SET checked_in=1, reflection=?, mood=?, minutes={db.greatest('minutes', '?')} WHERE user_id=? AND day=?",
           (body.get("reflection") or "")[:1000], mood, int(body.get("minutes") or 0), k["id"], day)
    s = engine.streak(k["id"])
    return {"ok": True, "streak": s, "badges": engine.badges(s)}


@app.get("/day/{day}", response_class=HTMLResponse)
def day_page(request: Request, day: str):
    k = kid_or_redirect(request, manage=True)
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(404)
    return render(request, "day.html", r=engine.day_record(k["id"], day), streak=engine.streak(k["id"]))


# ================================================================== 阅读进度（名著接着读 / 英文分级读物）

@app.get("/track/{tid}", response_class=HTMLResponse)
def track_page(request: Request, tid: int):
    k = kid_or_redirect(request)
    t = db.one("SELECT * FROM tracks WHERE id=? AND user_id=?", tid, k["id"])
    if not t:
        raise HTTPException(404)
    logs = db.q("SELECT * FROM reading_logs WHERE track_id=? ORDER BY id DESC LIMIT 10", tid)
    book = content.book(t["ref"]) or {}
    return render(request, "track.html", t=t, seg=engine.track_today(t), logs=logs, book=book,
                  units=db.jload(t["units"], []))


@app.post("/api/track/{tid}/log")
def track_log(request: Request, tid: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    if not db.one("SELECT id FROM tracks WHERE id=? AND user_id=?", tid, k["id"]):
        raise HTTPException(404)
    engine.log_reading(k["id"], tid, to_pos=int(body.get("to") or 0), minutes=int(body.get("minutes") or 0),
                       summary=(body.get("summary") or "").strip(), feeling=body.get("feeling") or "",
                       pages=str(body.get("pages") or ""))
    return {"ok": True, "stars": engine.total_stars(k["id"])}


# ================================================================== 学科与知识图谱

@app.get("/subjects", response_class=HTMLResponse)
def subjects(request: Request):
    k = kid_or_redirect(request, manage=True)
    m = engine.get_mastery(k["id"])
    rows = [{"e": e, "pack": catalog.packs[e["pack_id"]], "sum": engine.pack_summary(k["id"], e["pack_id"], m),
             "diag": db.one("SELECT id, finished_at FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='done' "
                            "ORDER BY id DESC", k["id"], e["pack_id"])} for e in enrollments(k["id"])]
    return render(request, "subjects.html", rows=rows)


@app.get("/map/{pack_id}", response_class=HTMLResponse)
def kmap(request: Request, pack_id: str):
    k = kid_or_redirect(request, manage=True)
    if pack_id not in catalog.packs:
        raise HTTPException(404)
    pack = catalog.packs[pack_id]
    m = engine.get_mastery(k["id"])
    stage = engine.enrollment_stage(k["id"], pack_id) or catalog.default_stage(pack_id, k["grade"])
    groups = []
    for st in pack.stages:
        kps = [kp for kp in catalog.pack_kps(pack_id) if kp["stage"] == st]
        by_strand = {}
        for kp in kps:
            by_strand.setdefault(kp["strand"], []).append({**kp, "m": m.get(kp["id"])})
        groups.append({"stage": st, "strands": [(catalog.strand_name(pack_id, s), v) for s, v in by_strand.items()],
                       "current": st == stage, "past": stage_rank(st) < stage_rank(stage)})
    return render(request, "map.html", pack=pack, groups=groups, stage=stage, sum=engine.pack_summary(k["id"], pack_id, m))


# ================================================================== 课程进度（学校学到哪了）

@app.get("/progress", response_class=HTMLResponse)
def progress_page(request: Request, pack: str = "", msg: str = ""):
    k = kid_or_redirect(request, manage=True)
    m = engine.get_mastery(k["id"])
    rows = []
    for e in enrollments(k["id"]):
        v = engine.progress_view(k["id"], e)
        by_strand = {}
        for kp in v["stage_kps"]:
            by_strand.setdefault(kp["strand"], []).append({**kp, "m": m.get(kp["id"])})
        v["strands"] = [(catalog.strand_name(e["pack_id"], s), lst) for s, lst in by_strand.items()]
        v["next"] = engine.next_after(e["pack_id"], e["progress_kp"], m, engine.taught_set(k["id"])) if e["progress_kp"] else None
        v["total"] = len(v["pack"].kp_ids)
        rows.append(v)
    return render(request, "progress.html", rows=rows, open_pack=pack, msg=msg)


@app.post("/progress/{pack_id}")
async def progress_save(request: Request, pack_id: str):
    from urllib.parse import urlencode
    k = kid_or_redirect(request, manage=True)
    if pack_id not in {e["pack_id"] for e in enrollments(k["id"])}:
        raise HTTPException(404)
    f = await request.form()
    stage = f.get("stage") or None
    old = engine.enrollment_stage(k["id"], pack_id)
    if stage and stage != old:
        # 只换学段：先保存学段，再回到页面勾选新学段学过的内容
        engine.set_progress(k["id"], pack_id, None, None, stage)
        msg = f"已切换到 {stage_label(stage)}，请勾选这个学段学校已经学过的内容"
    else:
        engine.set_progress(k["id"], pack_id, f.get("current") or None, f.getlist("taught"), stage)
        msg = "进度已更新，今天的任务已按新进度重新安排"
    engine.mark_task_by(k["id"], type="progress")
    engine.today_plan(k["id"], rebuild=True)
    return RedirectResponse("/progress?" + urlencode({"pack": pack_id, "msg": msg}) + f"#p-{pack_id}", 303)


@app.get("/learn/{kp_id}", response_class=HTMLResponse)
def learn(request: Request, kp_id: str, task: str = "practice"):
    k = kid_or_redirect(request, manage=True)
    kp = catalog.kp(kp_id)
    if not kp:
        raise HTTPException(404, "没有这个知识点")
    m = engine.get_mastery(k["id"])
    pre = [{**p, "m": m.get(p["id"])} for p in catalog.prereqs(kp_id)]
    post = [{**s, "m": m.get(s["id"])} for s in catalog.successors(kp_id)]
    pack = catalog.packs[kp["pack"]]
    vocab = db.q("SELECT front, back FROM cards WHERE user_id=? AND kp_id=? AND kind='term'", k["id"], kp_id)
    return render(request, "learn.html", kp=kp, pack=pack, pre=pre, post=post, me=m.get(kp_id), task=task,
                  strand=catalog.strand_name(pack.id, kp["strand"]), vocab=vocab)


@app.get("/api/teach/{kp_id}")
def api_teach(request: Request, kp_id: str):
    k = kid_or_redirect(request)
    kp = catalog.kp(kp_id)
    return llm.teach(kp, catalog.packs[kp["pack"]], k["grade"], user_id=k["id"])


def _public_item(it: dict) -> dict:
    hide = {"answer", "explain", "model", "points", "tol", "source"}
    return {k: v for k, v in it.items() if k not in hide}


@app.get("/api/practice/{kp_id}")
def api_practice(request: Request, kp_id: str, n: int = 3, purpose: str = "practice"):
    k = kid_or_redirect(request)
    if not catalog.kp(kp_id):
        raise HTTPException(404)
    purpose = purpose if purpose in ("practice", "preview") else "practice"
    items = engine.items_for(k["id"], kp_id, n=min(n, 5), purpose=purpose, grade=k["grade"])
    return {"items": [_public_item(i) for i in items], "llm": llm.enabled()}


_answer_display = engine.answer_display


@app.post("/api/answer")
def api_answer(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    row = db.one("SELECT * FROM items WHERE id=?", body.get("item_id"))
    if not row:
        raise HTTPException(404)
    it = engine._item_row_to_dict(row)
    kp_id = body.get("kp_id") or it["kp_id"]
    mode = body.get("mode") or "practice"
    if body.get("dont_know"):  # 「这道题还不会」：不算错，给讲解，进错题本，过几天再练
        engine.record_attempt(k["id"], it, kp_id, mode, False, "", dont_know=True)
        return {"correct": False, "dont_know": True, "answer": _answer_display(it), "explain": it.get("explain", ""),
                "hint": it.get("hint", "")}
    if it["type"] == "short":
        if "self" not in body:  # 先给参考答案，孩子对照后自评
            return {"reveal": True, "answer": it.get("model", ""), "points": it.get("points", []), "explain": it.get("explain", "")}
        correct = body["self"] == "ok"
    else:
        correct = engine.check_answer(it, body.get("answer"))
    status = engine.record_attempt(k["id"], it, kp_id, mode, bool(correct), body.get("answer", body.get("self", "")),
                                   ms=body.get("ms"))
    return {"correct": bool(correct), "answer": _answer_display(it), "explain": it.get("explain", ""),
            "status": status, "status_label": engine.STATUS_LABEL[status]}


@app.post("/api/learn/done")
def api_learn_done(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    kp_id = body["kp_id"]
    if body.get("summary"):
        kp = catalog.kp(kp_id)
        engine.add_card(k["id"], "kp", kp["name"], body["summary"][:500], {"method": kp.get("method", "")}, kp_id)
    engine.mark_task_by(k["id"], kp=kp_id)
    return {"ok": True}


# ================================================================== 试卷：拍照导入 → 在线订正 → 诊断

def _paper_or_404(k, paper_id: int):
    p = papers.get(k["id"], paper_id)
    if not p:
        raise HTTPException(404, "没有这份试卷")
    return p


@app.get("/papers", response_class=HTMLResponse)
def papers_page(request: Request, pack: str = ""):
    k = kid_or_redirect(request, manage=True)
    es = enrollments(k["id"])
    lst = db.q("SELECT p.*, (SELECT COUNT(*) FROM paper_items i WHERE i.paper_id=p.id) AS n FROM papers p "
               "WHERE p.user_id=? ORDER BY p.id DESC", k["id"])
    return render(request, "papers.html", es=[(e, catalog.packs[e["pack_id"]]) for e in es], papers=lst, pack=pack,
                  max_images=papers.MAX_IMAGES, llm_on=llm.enabled())


@app.post("/api/papers")
async def papers_create(request: Request):
    k = kid_or_redirect(request, manage=True)
    u = auth.current_user(request)
    f = await request.form()
    pack_id = f.get("pack_id") or ""
    if pack_id not in {e["pack_id"] for e in enrollments(k["id"])}:
        raise HTTPException(400, "请选择学科")
    images = []
    for up in f.getlist("photos"):
        if not hasattr(up, "read"):
            continue
        data = await up.read()
        if not data:
            continue
        if up.content_type not in papers.IMAGE_TYPES:
            raise HTTPException(400, "只支持 JPG / PNG / WEBP 图片")
        if len(data) > papers.MAX_IMAGE_BYTES:
            raise HTTPException(400, "图片太大（单张不超过 6 MB）")
        images.append((up.content_type, data))
    if len(images) > papers.MAX_IMAGES:
        raise HTTPException(400, f"一次最多 {papers.MAX_IMAGES} 张照片")
    text = (f.get("text") or "").strip()
    if not images and len(text) < 10:
        raise HTTPException(400, "请拍照上传，或者粘贴题目文字")
    from starlette.concurrency import run_in_threadpool
    pid = await run_in_threadpool(papers.create, k["id"], pack_id, title=(f.get("title") or "").strip(),
                                  exam_date=f.get("exam_date") or "", images=images, text=text, created_by=u["id"])
    engine.today_plan(k["id"], rebuild=True)
    return {"ok": True, "id": pid}


@app.get("/papers/{paper_id}", response_class=HTMLResponse)
def paper_page(request: Request, paper_id: int):
    k = kid_or_redirect(request, manage=True)
    p = _paper_or_404(k, paper_id)
    rs = papers.rows(paper_id)
    pack = catalog.packs.get(p["pack_id"])
    stage = engine.enrollment_stage(k["id"], p["pack_id"]) or k["grade"]
    return render(request, "paper.html", p=p, rows=rs, pack=pack, images=db.jload(p["images"], []),
                  cands=papers.candidates(p["pack_id"], stage) if pack else [],
                  qs=[{"id": r["id"], "item_id": r["item_id"], "type": r["item"]["type"], "q": r["item"]["q"], "zh": r["item"].get("zh", ""),
                       "options": r["item"].get("options", []), "unit": r["item"].get("unit", ""),
                       "done": bool(r["answered_at"]), "flagged": bool(r["flagged"])} for r in rs])


@app.get("/papers/{paper_id}/img/{name}")
def paper_image(request: Request, paper_id: int, name: str):
    from fastapi.responses import FileResponse
    k = kid_or_redirect(request, manage=True)
    p = _paper_or_404(k, paper_id)
    if name not in db.jload(p["images"], []):
        raise HTTPException(404)
    return FileResponse(papers.PAPER_DIR / str(paper_id) / name)


@app.post("/api/papers/{paper_id}/answer")
def paper_answer(request: Request, paper_id: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    p = _paper_or_404(k, paper_id)
    return papers.answer(k["id"], p, int(body.get("pi") or 0), body)


@app.post("/api/papers/{paper_id}/kp")
def paper_set_kp(request: Request, paper_id: int, body: dict = Body(...)):
    """家长 / 孩子觉得 AI 对应的知识点不对，手动改。"""
    k = kid_or_redirect(request, manage=True)
    p = _paper_or_404(k, paper_id)
    kp_id = body.get("kp_id") or None
    if kp_id and kp_id not in catalog.packs[p["pack_id"]].kp_ids:
        raise HTTPException(400, "知识点不在这门课里")
    pi = db.one("SELECT item_id FROM paper_items WHERE id=? AND paper_id=?", int(body.get("pi") or 0), paper_id)
    if not pi:
        raise HTTPException(404)
    with db.tx() as t:
        t.run("UPDATE paper_items SET kp_id=? WHERE id=?", kp_id, int(body["pi"]))
        t.run("UPDATE items SET kp_id=?, kp_ids=? WHERE id=?", kp_id or "", db.jdump([kp_id] if kp_id else []), pi["item_id"])
    return {"ok": True}


@app.post("/api/papers/{paper_id}/finish")
def paper_finish(request: Request, paper_id: int):
    k = kid_or_redirect(request)
    p = _paper_or_404(k, paper_id)
    papers.finish(k["id"], p)
    engine.today_plan(k["id"], rebuild=True)
    return {"ok": True, "stars": engine.total_stars(k["id"])}


@app.get("/papers/{paper_id}/report", response_class=HTMLResponse)
def paper_report(request: Request, paper_id: int):
    k = kid_or_redirect(request, manage=True)
    p = _paper_or_404(k, paper_id)
    return render(request, "paper_report.html", p=p, r=papers.report(k["id"], p), pack=catalog.packs.get(p["pack_id"]))


@app.post("/papers/{paper_id}/delete")
def paper_delete(request: Request, paper_id: int):
    import shutil
    k = kid_or_redirect(request, manage=True)
    _paper_or_404(k, paper_id)
    with db.tx() as t:
        t.run("DELETE FROM paper_items WHERE paper_id=?", paper_id)
        t.run("DELETE FROM papers WHERE id=?", paper_id)
    shutil.rmtree(papers.PAPER_DIR / str(paper_id), ignore_errors=True)
    engine.today_plan(k["id"], rebuild=True)
    return RedirectResponse("/papers", 303)


# ================================================================== 问一问（小助手）：随时提问，引导式回答（不给答案）

def _ask_item(k, item_id: str):
    """孩子能看到的题：公共题库的题，或自己试卷里的题。"""
    row = db.one("SELECT * FROM items WHERE id=?", item_id) if item_id else None
    if not row:
        return None
    if row["source"] == "paper" and not db.one(
            "SELECT 1 AS ok FROM paper_items i JOIN papers p ON p.id=i.paper_id WHERE i.item_id=? AND p.user_id=?", item_id, k["id"]):
        return None
    return engine._item_row_to_dict(row)


@app.post("/api/ask")
def ask(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    question = (body.get("question") or "").strip()[:1000]
    if not question:
        raise HTTPException(400, "想问什么？写一句就行")
    ctx = body.get("ctx") or {}
    th = db.one("SELECT * FROM ask_threads WHERE id=? AND user_id=?", body.get("thread_id") or 0, k["id"])
    if th:
        context, item_id, kp_id = th["context"], th["item_id"], th["kp_id"]
    else:
        it = _ask_item(k, str(ctx.get("item_id") or ""))
        kp = catalog.kp((it or {}).get("kp_id") or ctx.get("kp_id") or "")
        lines = [f"页面：{str(ctx.get('title') or '')[:80]}（{str(ctx.get('path') or '')[:80]}）"]
        if kp:
            lines.append(f"知识点：{kp['name']} {kp.get('name_en', '')}；说明：{kp.get('desc', '')}")
        if it:
            lines.append("题目：" + it["q"] + (f"（{it['zh']}）" if it.get("zh") else ""))
            if it.get("options"):
                lines.append("选项：" + "；".join(f"{'ABCDEFG'[i]}. {o}" for i, o in enumerate(it["options"])))
            lines.append("孩子已经做完这道题，看过答案了。" if ctx.get("answered") else "孩子还没做完这道题。")
        if ctx.get("text"):
            lines.append("页面上的内容 / 孩子选中的文字：" + str(ctx["text"])[:1500])
        context, item_id, kp_id = "\n".join(lines), (it or {}).get("id"), kp["id"] if kp else None
        now = db.now()
        th = {"id": db.insert("INSERT INTO ask_threads(user_id,page,title,kp_id,item_id,context,created_at,updated_at) "
                              "VALUES(?,?,?,?,?,?,?,?)", k["id"], str(ctx.get("path") or "")[:200],
                              question[:80], kp_id, item_id, context, now, now)}
    it = _ask_item(k, item_id or "")
    secret = (engine.answer_display(it) + "。" + it.get("explain", "")) if it else ""
    kp = catalog.kp(kp_id or "")
    pack = catalog.packs.get(kp["pack"]) if kp else None
    history = [(m["role"], m["text"]) for m in db.q("SELECT role, text FROM ask_messages WHERE thread_id=? ORDER BY id", th["id"])]
    try:
        res = llm.ask_tutor(k["grade"], pack, context, history, question, secret, user_id=k["id"],
                            name=sitecfg.get("assistant_name"))
    except llm.LLMError:
        if not history:  # 新对话第一句就失败：不留空对话
            db.run("DELETE FROM ask_threads WHERE id=?", th["id"])
        raise
    now = db.now()
    with db.tx() as t:
        t.run("INSERT INTO ask_messages(thread_id,role,text,created_at) VALUES(?,?,?,?)", th["id"], "user", question, now)
        t.run("INSERT INTO ask_messages(thread_id,role,text,created_at) VALUES(?,?,?,?)", th["id"], "ai", res["reply"], now)
        t.run("UPDATE ask_threads SET updated_at=? WHERE id=?", now, th["id"])
    return {"thread_id": th["id"], "reply": res["reply"]}


@app.get("/api/ask/{thread_id}")
def ask_history(request: Request, thread_id: int):
    k = kid_or_redirect(request)
    if not db.one("SELECT id FROM ask_threads WHERE id=? AND user_id=?", thread_id, k["id"]):
        raise HTTPException(404)
    return {"messages": [{"role": m["role"], "text": m["text"]} for m in
                         db.q("SELECT role, text FROM ask_messages WHERE thread_id=? ORDER BY id", thread_id)]}


# ================================================================== 诊断

@app.get("/diagnose/{pack_id}", response_class=HTMLResponse)
def diagnose_page(request: Request, pack_id: str):
    k = kid_or_redirect(request)
    pack = catalog.packs.get(pack_id) or (_ for _ in ()).throw(HTTPException(404))
    stage = engine.enrollment_stage(k["id"], pack_id) or catalog.default_stage(pack_id, k["grade"])
    running = db.one("SELECT id FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='running' ORDER BY id DESC",
                     k["id"], pack_id)
    history = db.q("SELECT * FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='done' ORDER BY id DESC LIMIT 5",
                   k["id"], pack_id)
    return render(request, "diagnose.html", pack=pack, stage=stage, running=running, history=history)


@app.post("/api/diag/start")
def diag_start(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    pack_id = body["pack_id"]
    stage = body.get("stage") or engine.enrollment_stage(k["id"], pack_id) or catalog.default_stage(pack_id, k["grade"])
    db.run("UPDATE diag_sessions SET status='abandoned' WHERE user_id=? AND pack_id=? AND status='running'", k["id"], pack_id)
    return {"sid": engine.start_diagnosis(k["id"], pack_id, stage)}


@app.get("/api/diag/{sid}/next")
def diag_next(request: Request, sid: int):
    k = kid_or_redirect(request)
    cur = engine.diag_next(k["id"], sid, k["grade"])
    if not cur:
        engine.mark_task_by(k["id"], type="diagnose", pack=db.one("SELECT pack_id FROM diag_sessions WHERE id=?", sid)["pack_id"])
        return {"done": True, "report": f"/diagnose/report/{sid}"}
    kp = catalog.kp(cur["kp"])
    frm = catalog.kp(cur["from"]) if cur.get("from") else None
    return {"done": False, "n": cur["n"], "depth": cur["depth"],
            "kp": {"id": kp["id"], "name": kp["name"], "name_en": kp.get("name_en", ""), "stage": stage_label(kp["stage"]),
                   "desc": kp.get("desc", ""), "probe": kp.get("probe", "")},
            "from": frm["name"] if frm else None,
            "item": _public_item(cur["item"]) if cur.get("item") else None}


@app.post("/api/diag/{sid}/answer")
def diag_answer(request: Request, sid: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    r = engine.diag_answer(k["id"], sid, body.get("answer"), body.get("self"))
    if not r:
        return {"ok": False}
    it = r["item"]
    return {"correct": bool(r["ok"]), "answer": _answer_display(it) if it else "", "explain": it.get("explain", "") if it else ""}


@app.post("/api/diag/{sid}/finish")
def diag_finish(request: Request, sid: int):
    k = kid_or_redirect(request)
    s = db.one("SELECT * FROM diag_sessions WHERE id=? AND user_id=?", sid, k["id"])
    if s and s["status"] == "running":
        engine.finish_diagnosis(k["id"], sid)
    return {"report": f"/diagnose/report/{sid}"}


@app.get("/diagnose/report/{sid}", response_class=HTMLResponse)
def diag_report(request: Request, sid: int):
    k = kid_or_redirect(request, manage=True)
    s = db.one("SELECT * FROM diag_sessions WHERE id=? AND user_id=?", sid, k["id"])
    if not s:
        raise HTTPException(404)
    rep = engine.diag_report(sid)
    engine.today_plan(k["id"], rebuild=True)
    kp = catalog.kp
    return render(request, "diag_report.html", rep=rep, pack=catalog.packs[s["pack_id"]], kp=kp)


# ================================================================== 复习

@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request, group: str = ""):
    kid_or_redirect(request)
    return render(request, "review.html", group=group if group in engine.CARD_GROUPS else "")


@app.get("/api/review/due")
def review_due(request: Request, group: str = ""):
    k = kid_or_redirect(request)
    cards = []
    limit = 8 if group == "mistakes" else 60
    for c in engine.due_cards(k["id"], limit, group or None):
        d = dict(c)
        d["extra"] = db.jload(c["extra"], {})
        d["kp_name"] = (catalog.kp(c["kp_id"]) or {}).get("name", "") if c["kp_id"] else ""
        cards.append(d)
    return {"cards": cards}


@app.post("/api/review/{card_id}")
def review_card(request: Request, card_id: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    r = engine.review_card(k["id"], card_id, body.get("grade", "good"))
    for group, task in (("words", "words"), ("mistakes", "mistakes"), ("other", "review")):
        if not engine.due_cards(k["id"], 1, group):
            engine.mark_task_by(k["id"], type=task)
    return r or {}


@app.get("/words", response_class=HTMLResponse)
def words(request: Request, kind: str = "word"):
    k = kid_or_redirect(request, manage=True)
    cards = db.q("SELECT * FROM cards WHERE user_id=? AND kind=? ORDER BY starred DESC, id DESC LIMIT 500", k["id"], kind)
    sents = db.q("SELECT * FROM sentences WHERE user_id=? ORDER BY id DESC LIMIT 100", k["id"]) if kind == "sentence" else []
    counts = {r["kind"]: r["n"] for r in db.q("SELECT kind, COUNT(*) AS n FROM cards WHERE user_id=? GROUP BY kind", k["id"])}
    counts["sentence"] = db.one("SELECT COUNT(*) AS n FROM sentences WHERE user_id=?", k["id"])["n"]
    return render(request, "words.html", cards=[{**dict(c), "extra": db.jload(c["extra"], {})} for c in cards],
                  sents=[{**dict(s), "fb": db.jload(s["feedback"], {})} for s in sents], kind=kind, counts=counts)


@app.post("/api/cards")
def add_card(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    kind = body.get("kind", "word")
    if kind not in ("word", "term", "kp", "phrase"):
        kind = "word"
    cid = engine.add_card(k["id"], kind, body.get("front", ""), body.get("back", ""), body.get("extra") or {},
                          body.get("kp_id"), starred=1)
    return {"id": cid}


@app.post("/api/cards/{card_id}/delete")
def delete_card(request: Request, card_id: int):
    k = kid_or_redirect(request)
    db.run("DELETE FROM cards WHERE id=? AND user_id=?", card_id, k["id"])
    return {"ok": True}


# ================================================================== 阅读

@app.get("/reading", response_class=HTMLResponse)
def reading_list(request: Request, lang: str = "en"):
    k = kid_or_redirect(request)
    rows = db.q("SELECT id, title, lang, source, minutes, finished_at, created_at FROM readings WHERE user_id=? ORDER BY id DESC LIMIT 50", k["id"])
    return render(request, "reading_list.html", rows=rows, lang=lang)


@app.post("/reading/new")
async def reading_new(request: Request):
    k = kid_or_redirect(request)
    form = await request.form()
    lang = form.get("lang") if form.get("lang") in ("en", "zh") else "en"
    if form.get("mode") == "ai":
        review_words = [r["front"] for r in db.q(
            "SELECT front FROM cards WHERE user_id=? AND kind='word' AND due<=? ORDER BY due LIMIT 8",
            k["id"], (db.today() + timedelta(days=3)).isoformat())] if lang == "en" else []
        length = form.get("length") or ("150-250 词" if lang == "en" else "500-800 字")
        p = llm.make_passage(lang, k["grade"], form.get("topic") or "", length, review_words, user_id=k["id"])
        rid = db.insert("INSERT INTO readings(user_id,lang,title,body,source,questions,created_at) VALUES(?,?,?,?,?,?,?)",
                     k["id"], lang, p.get("title", "Reading"), p.get("body", ""), "ai", db.jdump(p.get("questions", [])), db.now())
    else:
        body = (form.get("body") or "").strip()
        if not body:
            raise HTTPException(400, "请粘贴要读的文章")
        rid = db.insert("INSERT INTO readings(user_id,lang,title,body,source,created_at) VALUES(?,?,?,?,?,?)",
                     k["id"], lang, (form.get("title") or "我的阅读").strip(), body[:20000], "paste", db.now())
    return RedirectResponse(f"/reading/{rid}", 303)


@app.get("/reading/{rid}", response_class=HTMLResponse)
def reading_view(request: Request, rid: int):
    k = kid_or_redirect(request)
    r = db.one("SELECT * FROM readings WHERE id=? AND user_id=?", rid, k["id"])
    if not r:
        raise HTTPException(404)
    qs = db.jload(r["questions"], [])
    looked = db.q("SELECT query, result FROM lookups WHERE user_id=? AND reading_id=? ORDER BY id", k["id"], rid)
    return render(request, "reading.html", r=r, paras=[p for p in r["body"].split("\n") if p.strip()],
                  questions=[{"q": q["q"], "options": q.get("options", [])} for q in qs],
                  looked=[{"q": l["query"], "r": db.jload(l["result"], {})} for l in looked])


@app.post("/api/lookup")
def api_lookup(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    query = (body.get("q") or "").strip()[:80]
    if not query:
        raise HTTPException(400)
    lang = body.get("lang", "en")
    res = llm.lookup(query, (body.get("context") or "")[:400], lang, k["grade"], user_id=k["id"])
    db.run("INSERT INTO lookups(user_id,reading_id,query,context,result,created_at) VALUES(?,?,?,?,?,?)",
           k["id"], body.get("reading_id"), query, (body.get("context") or "")[:400], db.jdump(res), db.now())
    return res


@app.post("/api/explain")
def api_explain(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    return llm.explain_sentence((body.get("sentence") or "")[:600], body.get("lang", "en"), k["grade"], user_id=k["id"])


@app.post("/api/sentence")
def api_sentence(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    word, sent = (body.get("word") or "").strip(), (body.get("sentence") or "").strip()
    if not word or not sent:
        raise HTTPException(400, "先写一个句子")
    fb = llm.sentence_feedback(word, body.get("meaning", ""), sent[:400], body.get("lang", "en"), k["grade"], user_id=k["id"])
    db.run("INSERT INTO sentences(user_id,word,sentence,feedback,ok,created_at) VALUES(?,?,?,?,?,?)",
           k["id"], word, sent[:400], db.jdump(fb), 1 if fb.get("ok") else 0, db.now())
    engine._touch_day(k["id"], 2)
    return fb


@app.post("/api/reading/{rid}/finish")
def reading_finish(request: Request, rid: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    r = db.one("SELECT * FROM readings WHERE id=? AND user_id=?", rid, k["id"])
    if not r:
        raise HTTPException(404)
    qs = db.jload(r["questions"], [])
    answers = body.get("answers") or {}
    results = []
    for i, q in enumerate(qs):
        a = answers.get(str(i))
        ok = a is not None and str(a) == str(q.get("answer"))
        results.append({"ok": ok, "answer": q.get("answer"), "explain": q.get("explain", "")})
    minutes = max(1, min(120, int(body.get("minutes") or 1)))
    db.run("UPDATE readings SET finished_at=?, minutes=minutes+? WHERE id=?", db.now(), minutes, rid)
    engine._touch_day(k["id"], minutes)
    engine.mark_task_by(k["id"], type="read_" + r["lang"], lang=r["lang"])
    return {"results": results}


# ================================================================== 记录

def _records_ctx(k):
    m = engine.get_mastery(k["id"])
    packs = [{"pack": catalog.packs[e["pack_id"]], "stage": e["stage"], "sum": engine.pack_summary(k["id"], e["pack_id"], m)}
             for e in enrollments(k["id"])]
    attempts = db.q("SELECT * FROM attempts WHERE user_id=? ORDER BY id DESC LIMIT 60", k["id"])
    att = [{**dict(a), "kp": catalog.kp(a["kp_id"])} for a in attempts]
    weak = sorted([v for v in m.values() if v["status"] == "weak" and catalog.kp(v["kp_id"])], key=lambda v: v["score"])
    days = db.q("SELECT * FROM days WHERE user_id=? AND (reflection<>'' OR checked_in=1) ORDER BY day DESC LIMIT 14", k["id"])
    lookups = db.q("SELECT query, created_at FROM lookups WHERE user_id=? ORDER BY id DESC LIMIT 30", k["id"])
    tot = db.one("SELECT COUNT(*) AS n, SUM(correct) AS ok FROM attempts WHERE user_id=?", k["id"])
    return {"k": k, "packs": packs, "attempts": att, "weak": [(catalog.kp(w["kp_id"]), w) for w in weak[:12]],
            "cal": engine.calendar(k["id"], 12), "streak": engine.streak(k["id"]), "days": days, "lookups": lookups,
            "reads": db.q("SELECT * FROM reading_logs WHERE user_id=? ORDER BY id DESC LIMIT 30", k["id"]),
            "found": insights.open_insights(k["id"]), "solved": insights.resolved_recent(k["id"]),
            "asks": db.q("SELECT t.id, t.page, t.created_at, m.text FROM ask_threads t JOIN ask_messages m ON m.thread_id=t.id "
                         "AND m.role='user' WHERE t.user_id=? ORDER BY m.id DESC LIMIT 20", k["id"]),
            "papers": [{**dict(p), "rep": db.jload(p["report"], {})} for p in
                       db.q("SELECT * FROM papers WHERE user_id=? ORDER BY id DESC LIMIT 20", k["id"])],
            "tot": tot, "mistakes": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='mistake'", k["id"])["n"],
            "words": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='word'", k["id"])["n"]}


@app.get("/records", response_class=HTMLResponse)
def records(request: Request):
    k = kid_or_redirect(request, manage=True)
    return render(request, "records.html", **_records_ctx(k), report_for=None)


# ================================================================== 浏览器划词插件（外部页面查词 → 单词本）

def _lang_of(text: str) -> str:
    return "zh" if re.search(r"[一-鿿]", text or "") else "en"


@app.get("/ext/me")
def ext_me(request: Request):
    u = auth.user_by_token(request)
    return {"name": u["name"], "due_words": len(engine.due_cards(u["id"], 300, "words")),
            "words": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind IN ('word','phrase')", u["id"])["n"]}


@app.post("/ext/lookup")
def ext_lookup(request: Request, body: dict = Body(...)):
    u = auth.user_by_token(request)
    text = (body.get("text") or "").strip()[:80]
    if not text:
        raise HTTPException(400, "没有选中文字")
    context = (body.get("context") or "")[:400]
    lang = _lang_of(text)
    res = llm.lookup(text, context, lang, u["grade"], user_id=u["id"])
    db.run("INSERT INTO lookups(user_id,reading_id,query,context,result,created_at) VALUES(?,?,?,?,?,?)",
           u["id"], None, text, context, db.jdump(res), db.now())
    saved = db.one("SELECT id FROM cards WHERE user_id=? AND kind IN ('word','phrase') AND LOWER(front) IN (?,?)", u["id"],
                   text.lower()[:300], (res.get("word") or text).lower()[:300])
    return {"lang": lang, "result": res, "saved": bool(saved)}


@app.post("/ext/save")
def ext_save(request: Request, body: dict = Body(...)):
    u = auth.user_by_token(request)
    word = (body.get("word") or "").strip()
    meaning = (body.get("meaning") or "").strip()
    if not word or not meaning:
        raise HTTPException(400, "缺少单词或意思")
    kind = "phrase" if " " in word.strip() else "word"
    extra = {k: (body.get(k) or "")[:400] for k in ("phonetic", "pinyin", "example", "example_zh", "context", "url", "pos")}
    extra["source"] = "插件"
    cid = engine.add_card(u["id"], kind, word, meaning, extra, starred=1)
    return {"ok": True, "id": cid, "due_words": len(engine.due_cards(u["id"], 300, "words"))}


@app.get("/tools", response_class=HTMLResponse)
def tools_page(request: Request, kid: int = 0):
    return _tools_render(request, kid)


def _tools_render(request: Request, kid: int, token: str = ""):
    u = auth.require_user(request)
    target = u
    kids = db.q("SELECT id, name FROM users WHERE parent_id=? ORDER BY id", u["id"]) if u["role"] == "parent" else []
    if u["role"] == "parent":
        kid = kid or request.session.get("as_kid") or (kids[0]["id"] if kids else 0)
        target = auth.kid_of(u, kid) if kid else None
    return render(request, "tools.html", target=target, kids=kids, token=token,
                  tokens=auth.api_tokens_of(target["id"]) if target else [],
                  server=config.PUBLIC_URL or str(request.base_url).rstrip("/"))


@app.post("/tools/token")
def tools_token(request: Request, kid: int = Form(0), action: str = Form("new"), token_hash: str = Form("")):
    u = auth.require_user(request)
    target = auth.kid_of(u, kid) if u["role"] == "parent" and kid else u
    if action == "revoke":
        auth.revoke_api_token(target["id"], token_hash)
        return RedirectResponse(f"/tools?kid={kid}", 303)
    token = auth.create_api_token(target["id"])
    auth.log_event("api_token_create", user_id=target["id"], email=target["email"], detail=f"by {u['email']}", request=request)
    return _tools_render(request, kid, token)  # 连接码只在这一次页面里显示，不放进网址


@app.get("/tools/extension.zip")
def tools_extension_zip(request: Request):
    import io
    import zipfile
    from fastapi.responses import Response
    auth.require_user(request)
    src = config.BASE_DIR / "extension"
    server = config.PUBLIC_URL or str(request.base_url).rstrip("/")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                data = p.read_bytes()
                if p.name == "config.js":
                    data = f'const AISTUDY_DEFAULT_SERVER = "{server}";\n'.encode()
                z.writestr("aistudy-extension/" + str(p.relative_to(src)), data)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="aistudy-extension.zip"'})


@app.get("/healthz")
def healthz():
    db.one("SELECT 1 AS ok")
    ok, _ = llm.check()
    return {"ok": True, "db": db.DIALECT, "packs": len(catalog.packs), "kps": len(catalog.kps),
            "llm": llm.settings().default if ok else "off"}
