"""accounts HTTP routes. Learning commands live in app.learning."""
from fastapi import APIRouter
from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .. import auth, brand, config, db, sitecfg
from ..web import (
    _back,
    _reg_mode,
    render,
)

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", 303)
    if user["role"] == "admin":
        return RedirectResponse("/admin", 303)
    if user["role"] == "parent":
        return RedirectResponse("/parent", 303)
    return RedirectResponse("/today", 303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, invite: str = ""):
    if auth.current_user(request):
        return RedirectResponse("/", 303)
    return render(request, "login.html", mode=_reg_mode(), invite=invite, show_register=bool(invite))


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    u, err = auth.authenticate(email, password, request)
    if not u:
        return render(request, "login.html", error=err, mode=_reg_mode(), email=email)
    auth.login(request, u)
    return RedirectResponse("/", 303)


@router.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form(...),
             invite: str = Form(""), note: str = Form(""), who: str = Form("parent")):
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
        # 第一个账号是网站管理员（不带孩子）；之后注册的是家长，或者自己学的自学者（没有家长的学习账号，默认成人）
        self_learn = who == "self" and mode != "first"
        role = "admin" if mode == "first" else "kid" if self_learn else "parent"
        uid = auth.create_user(email, password, name, role, is_admin=(mode == "first"), status=status,
                               grade="ADULT" if self_learn else "",
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
    return RedirectResponse("/admin" if mode == "first" else "/me/courses" if self_learn else "/parent", 303)


@router.get("/logout")
def logout(request: Request):
    auth.logout(request)
    return RedirectResponse("/login", 303)


@router.get("/reset/{token}", response_class=HTMLResponse)
def reset_page(request: Request, token: str):
    u = auth.reset_target(token)
    return render(request, "reset.html", target=u, token=token)


@router.post("/reset/{token}")
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


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str = "", err: str = "", force: str = ""):
    u = auth.require_user(request)
    return render(request, "settings.html", sessions=auth.sessions_of(u["id"]), this_sid=auth.current_session_hash(request),
                  msg=msg, err=err)


@router.post("/settings/mascot")
def settings_mascot(request: Request, mascot: str = Form(...)):
    u = auth.require_user(request)
    if not brand.set_mascot(u["id"], mascot):
        return _back(err="没有这个形象")
    return _back(msg=f"换好了，以后陪你学习的是「{brand.MASCOTS[mascot][0]}」")


@router.post("/settings/profile")
def settings_profile(request: Request, name: str = Form(...), email: str = Form(...)):
    u = auth.require_user(request)
    if u["role"] == "kid" and not auth.is_self_learner(u):
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


@router.post("/settings/sessions/revoke")
def revoke_session(request: Request, token_hash: str = Form(""), all_others: str = Form("")):
    u = auth.require_user(request)
    if all_others:
        db.run("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", u["id"], auth.current_session_hash(request))
    else:
        auth.revoke_session(u["id"], token_hash)
    return _back(msg="已退出所选设备")


@router.get("/invite", response_class=HTMLResponse)
def my_invites(request: Request, msg: str = "", link: str = ""):
    p = auth.require_parent(request)
    invites = db.q("SELECT * FROM invites WHERE created_by=? ORDER BY created_at DESC", p["id"])
    joined = db.q("SELECT id, name, email, status, invite_code, created_at FROM users WHERE invited_by=? ORDER BY id", p["id"])
    return render(request, "invite.html", invites=invites, joined=joined, msg=msg, link=link, now=db.now(),
                  limit=None if p["is_admin"] else sitecfg.parent_invite_limit(), open_n=auth.open_invites(p["id"]),
                  reg_mode=sitecfg.registration())


@router.post("/invite")
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


