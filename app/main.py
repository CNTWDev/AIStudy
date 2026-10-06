"""AIStudy Web 应用入口。启动：uvicorn app.main:app"""
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Body, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, db, engine, llm
from .auth import LoginRequired
from .catalog import GRADES, catalog, stage_label, stage_rank


@asynccontextmanager
async def lifespan(app):
    db.init()
    catalog.load()
    engine.load_seed_items(config.SEED_DIR)
    yield
    db.close()


app = FastAPI(title="AIStudy", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=60 * 60 * 24 * 60,
                   same_site="lax", https_only=config.HTTPS_ONLY)
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=config.BASE_DIR / "app" / "templates")
templates.env.globals.update(stage_label=stage_label, catalog=catalog, STATUS_LABEL=engine.STATUS_LABEL,
                             llm_enabled=llm.enabled, GRADES=GRADES)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "请先登录"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(llm.LLMError)
async def _llm_error(request: Request, exc):
    return JSONResponse({"error": str(exc)}, status_code=503)


def render(request: Request, name: str, **ctx):
    user = auth.current_user(request)
    kid = None
    if user:
        kid = user if user["role"] == "kid" else (
            db.one("SELECT * FROM users WHERE id=? AND parent_id=?", request.session.get("as_kid"), user["id"])
            if request.session.get("as_kid") else None)
    return templates.TemplateResponse(request, name, {"user": user, "kid": kid, **ctx})


def kid_or_redirect(request: Request):
    """当前操作的孩子：孩子本人，或家长正在「以孩子视角」使用。"""
    user = auth.require_user(request)
    if user["role"] == "kid":
        return user
    kid_id = request.session.get("as_kid")
    if kid_id:
        k = db.one("SELECT * FROM users WHERE id=? AND parent_id=?", kid_id, user["id"])
        if k:
            return k
    raise HTTPException(400, "请先在家长页选择一个孩子")


def enrollments(kid_id: int):
    return [dict(r) for r in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", kid_id)
            if r["pack_id"] in catalog.packs]


# ================================================================== 登录 / 账号

def _reg_mode() -> str:
    return "first" if auth.user_count() == 0 else config.REGISTRATION


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", 303)
    if user["role"] == "parent" and not request.session.get("as_kid"):
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
        uid = auth.create_user(email, password, name, "parent", is_admin=(mode == "first"), status=status,
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
    return RedirectResponse("/parent", 303)


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
            "weak": [catalog.kp(w["kp_id"]) for w in weak[:5]], "weak_n": len(weak),
            "due": len(engine.due_cards(k["id"], 500)),
            "words": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='word'", k["id"])["n"]}


ADMIN_TABS = [("overview", "概览"), ("families", "家庭与孩子"), ("invites", "邀请码"), ("tree", "邀请关系"),
              ("log", "安全日志"), ("system", "系统")]


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
        parents = [u for u in users if u["role"] == "parent"]
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
    elif tab == "log":
        ctx["events"] = db.q("SELECT * FROM auth_events ORDER BY id DESC LIMIT 300")
    elif tab == "system":
        from . import migrate
        ctx.update(reg_mode=config.REGISTRATION, llm_status=llm.check(), migrations=migrate.status(),
                   version=_version(), db_dialect=db.DIALECT)
    ctx["me"] = a
    return render(request, "admin.html", **ctx)


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
def admin_create_user(request: Request, email: str = Form(...), name: str = Form(""), password: str = Form(...)):
    a = auth.require_admin(request)
    try:
        uid = auth.create_user(email, password, name, "parent", invited_by=a["id"])
    except ValueError as e:
        return _admin_back("families", msg=str(e))
    auth.log_event("admin_create_user", user_id=uid, email=email, detail=f"by {a['email']}", request=request)
    return _admin_back("families", msg="已创建家长账号 " + email.strip().lower())


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
                  limit=None if p["is_admin"] else config.PARENT_INVITE_LIMIT, open_n=auth.open_invites(p["id"]),
                  reg_mode=config.REGISTRATION)


@app.post("/invite")
def my_invite_create(request: Request, note: str = Form("")):
    from urllib.parse import urlencode
    p = auth.require_parent(request)
    if config.REGISTRATION == "closed":
        return RedirectResponse("/invite?" + urlencode({"msg": "系统目前不开放注册，请联系管理员"}), 303)
    if not p["is_admin"] and auth.open_invites(p["id"]) >= config.PARENT_INVITE_LIMIT:
        return RedirectResponse("/invite?" + urlencode({"msg": f"你手里还有 {config.PARENT_INVITE_LIMIT} 个没用完的邀请码，先把它们发出去吧"}), 303)
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


@app.get("/parent/kids/{kid_id}", response_class=HTMLResponse)
def kid_report(request: Request, kid_id: int):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id)
    return render(request, "records.html", **_records_ctx(k), report_for=k)


@app.get("/parent/as/{kid_id}")
def parent_as(request: Request, kid_id: int):
    p = auth.require_parent(request)
    auth.kid_of(p, kid_id)
    request.session["as_kid"] = kid_id
    return RedirectResponse("/today", 303)


# ================================================================== 今天

@app.get("/today", response_class=HTMLResponse)
def today(request: Request):
    k = kid_or_redirect(request)
    if not enrollments(k["id"]):
        return render(request, "message.html", title="还没有选择学科",
                      text="请家长在「家长页 → 编辑孩子」里勾选要学的教材。")
    t = engine.today_plan(k["id"])
    return render(request, "today.html", t=t, streak=engine.streak(k["id"]), cal=engine.calendar(k["id"], 4))


@app.post("/api/plan/rebuild")
def plan_rebuild(request: Request):
    k = kid_or_redirect(request)
    return engine.today_plan(k["id"], rebuild=True)


@app.post("/api/plan/task")
def plan_task(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    engine.mark_task(k["id"], body["id"], bool(body.get("done", True)))
    return {"ok": True}


@app.post("/api/checkin")
def checkin(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    day = db.today().isoformat()
    engine.today_plan(k["id"])
    db.run(f"UPDATE days SET checked_in=1, reflection=?, minutes={db.greatest('minutes', '?')} WHERE user_id=? AND day=?",
           (body.get("reflection") or "")[:1000], int(body.get("minutes") or 0), k["id"], day)
    return {"ok": True, "streak": engine.streak(k["id"])}


# ================================================================== 学科与知识图谱

@app.get("/subjects", response_class=HTMLResponse)
def subjects(request: Request):
    k = kid_or_redirect(request)
    m = engine.get_mastery(k["id"])
    rows = [{"e": e, "pack": catalog.packs[e["pack_id"]], "sum": engine.pack_summary(k["id"], e["pack_id"], m),
             "diag": db.one("SELECT id, finished_at FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='done' "
                            "ORDER BY id DESC", k["id"], e["pack_id"])} for e in enrollments(k["id"])]
    return render(request, "subjects.html", rows=rows)


@app.get("/map/{pack_id}", response_class=HTMLResponse)
def kmap(request: Request, pack_id: str):
    k = kid_or_redirect(request)
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


@app.get("/learn/{kp_id}", response_class=HTMLResponse)
def learn(request: Request, kp_id: str, task: str = "practice"):
    k = kid_or_redirect(request)
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


def _answer_display(it: dict) -> str:
    a = it.get("answer")
    if it["type"] == "mcq":
        try:
            return f"{'ABCD'[int(a)]}. {it['options'][int(a)]}"
        except (TypeError, ValueError, IndexError, KeyError):
            return str(a)
    if it["type"] == "short":
        return it.get("model", "")
    if isinstance(a, list):
        return " / ".join(map(str, a))
    return f"{a} {it.get('unit', '')}".strip()


@app.post("/api/answer")
def api_answer(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    row = db.one("SELECT * FROM items WHERE id=?", body.get("item_id"))
    if not row:
        raise HTTPException(404)
    it = engine._item_row_to_dict(row)
    kp_id = body.get("kp_id") or it["kp_id"]
    mode = body.get("mode") or "practice"
    if it["type"] == "short":
        if "self" not in body:  # 先给参考答案，孩子对照后自评
            return {"reveal": True, "answer": it.get("model", ""), "points": it.get("points", []), "explain": it.get("explain", "")}
        correct = body["self"] == "ok"
    else:
        correct = engine.check_answer(it, body.get("answer"))
    status = engine.record_attempt(k["id"], it, kp_id, mode, bool(correct), body.get("answer", body.get("self", "")))
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
    k = kid_or_redirect(request)
    s = db.one("SELECT * FROM diag_sessions WHERE id=? AND user_id=?", sid, k["id"])
    if not s:
        raise HTTPException(404)
    rep = engine.diag_report(sid)
    engine.today_plan(k["id"], rebuild=True)
    kp = catalog.kp
    return render(request, "diag_report.html", rep=rep, pack=catalog.packs[s["pack_id"]], kp=kp)


# ================================================================== 复习

@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request):
    kid_or_redirect(request)
    return render(request, "review.html")


@app.get("/api/review/due")
def review_due(request: Request):
    k = kid_or_redirect(request)
    cards = []
    for c in engine.due_cards(k["id"], 60):
        d = dict(c)
        d["extra"] = db.jload(c["extra"], {})
        d["kp_name"] = (catalog.kp(c["kp_id"]) or {}).get("name", "") if c["kp_id"] else ""
        cards.append(d)
    return {"cards": cards}


@app.post("/api/review/{card_id}")
def review_card(request: Request, card_id: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    r = engine.review_card(k["id"], card_id, body.get("grade", "good"))
    if not engine.due_cards(k["id"], 1):
        engine.mark_task_by(k["id"], type="review")
    return r or {}


@app.get("/words", response_class=HTMLResponse)
def words(request: Request, kind: str = "word"):
    k = kid_or_redirect(request)
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
    engine.mark_task_by(k["id"], type="reading", lang=r["lang"])
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
            "tot": tot, "mistakes": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='mistake'", k["id"])["n"],
            "words": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='word'", k["id"])["n"]}


@app.get("/records", response_class=HTMLResponse)
def records(request: Request):
    k = kid_or_redirect(request)
    return render(request, "records.html", **_records_ctx(k), report_for=None)


@app.get("/healthz")
def healthz():
    db.one("SELECT 1 AS ok")
    ok, _ = llm.check()
    return {"ok": True, "db": db.DIALECT, "packs": len(catalog.packs), "kps": len(catalog.kps),
            "llm": llm.settings().default if ok else "off"}
