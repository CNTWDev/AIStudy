"""accounts HTTP routes. Learning commands live in app.learning."""
import json

from fastapi import APIRouter
from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .. import auth, brand, config, db, mailer, sitecfg
from ..catalog import SUBJECTS, SYSTEMS, catalog, stage_label
from ..library import CATALOG as BOOKS
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


SYSTEM_SHORT = {"cn": "上海教材", "cambridge": "剑桥国际", "cert": "职业资格"}


def _showcase() -> dict:
    """登录页「这里能学什么」：从课程目录直接生成（加一个教材包，这里自动多一门），只展示、不能选课。
    按课面向谁分中小学和成人两组（不分人群的课两组都有）；每门课能展开看板块（章）和知识点数。试点的零散包（知识点很少）不展示。"""
    groups, total = {"school": {}, "adult": {}}, 0
    for p in catalog.courses():
        if len(p.kp_ids) < 20:
            continue
        total += len(p.kp_ids)
        n = {}
        for k in p.kp_ids:
            n[catalog.kps[k]["strand"]] = n.get(catalog.kps[k]["strand"], 0) + 1
        stages = [stage_label(s) for s in p.stages]
        short = p.region.split("·")[-1].replace("公办", "") + "教材" if p.system == "cn" and p.region else SYSTEM_SHORT.get(p.system)
        item = {"system": short or SYSTEMS.get(p.system, {}).get("name", "").split("（")[0],
                "title": p.edition.split("（")[0], "edition": p.edition, "kps": len(p.kp_ids),
                "stages": stages[0] if len(stages) == 1 else f"{stages[0]}–{stages[-1]}",
                "strands": [{"name": s["name"], "n": n[s["id"]]} for s in p.strands if n.get(s["id"])]}
        for g in (("school", "adult") if p.audience == "all" else (p.audience,)):
            subj = groups[g].setdefault(p.subject, {
                "name": p.subject_name, "icon": SUBJECTS.get(p.subject, {}).get("icon", "📘"), "kps": 0, "packs": []})
            subj["kps"] += item["kps"]
            subj["packs"].append(item)
    order = {s: i for i, s in enumerate(SUBJECTS)}
    out = {g: [s for _, s in sorted(v.items(), key=lambda kv: order.get(kv[0], 99))] for g, v in groups.items()}
    for subj in out["school"]:
        subj["packs"].sort(key=lambda x: x["system"] != "上海教材")
    try:
        books = len(json.loads(BOOKS.read_text(encoding="utf-8")).get("books", []))
    except (OSError, ValueError):
        books = 0
    return {**out, "books": books, "kps": total}


def _login_page(request: Request, **ctx):
    return render(request, "login.html", mode=_reg_mode(), showcase=_showcase(), has_accts=bool(auth.device_accounts(request)), **ctx)


def _pick_page(request: Request, accts: list, **ctx):
    """这台设备上登录过的账号：点头像，孩子输 4 位数字、大人输密码（docs/DESIGN.md §5.37）。
    列表里有孩子就用孩子的样式和口吻；只有大人时用大人的样式。"""
    tone = "kid" if any(auth.uses_pin(u) for u in accts) else "adult"
    return render(request, "login_pick.html", accts=accts, tone=tone, **ctx)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, invite: str = "", tab: str = "", full: str = ""):
    if auth.current_user(request):
        return RedirectResponse("/", 303)
    accts = [] if (full or invite or tab) else auth.device_accounts(request)
    if accts:
        return _pick_page(request, accts)
    return _login_page(request, invite=invite, show_register=bool(invite) or tab == "register")


def _signed_in(request: Request, u):
    auth.login(request, u)
    resp = RedirectResponse("/", 303)
    auth.remember_device(request, resp, u)
    return resp


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    u, err = auth.authenticate(email, password, request)
    if not u:
        return _login_page(request, error=err, email=email)
    return _signed_in(request, u)


@router.post("/login/pick")
def login_pick(request: Request, uid: int = Form(...), password: str = Form("")):
    """在记住了的设备上点头像登录：只认这台设备上登录过的账号（别人在别的设备上看不到、也试不了）。"""
    accts = auth.device_accounts(request)
    u = next((a for a in accts if a["id"] == uid), None)
    if not u:
        return RedirectResponse("/login?full=1", 303)
    u, err = auth.authenticate("", password, request, u=u)
    if not u:
        return _pick_page(request, accts, pick=uid, error=err, locked=err == auth.LOCKED_KID)
    return _signed_in(request, u)


@router.post("/login/forget")
def login_forget(request: Request, uid: int = Form(...)):
    """不在这台设备上显示这个账号了（比如来家里玩的小朋友、借用过的电脑）。"""
    auth.forget_device(request, uid)
    return RedirectResponse("/login", 303)


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
        return _login_page(request, error=str(e), email=email, invite=invite, show_register=True, who=who, reg_name=name, note=note)
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
    auth.log_event("password_reset", user_id=u["id"], email=auth.account_name(u), request=request)
    return render(request, "message.html", title="密码已重设", text="请用新密码登录。", link="/login")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str = "", err: str = "", force: str = ""):
    u = auth.require_user(request)
    return render(request, "settings.html", sessions=auth.sessions_of(u["id"]), this_sid=auth.current_session_hash(request),
                  msg=msg, err=err, mail_kinds={k: (name, mailer.wants(u, k)) for k, name in mailer.KINDS.items()},
                  mail_on=mailer.settings().enabled)


@router.post("/settings/mail")
async def settings_mail(request: Request):
    u = auth.require_parent(request)
    form = await request.form()
    for kind in mailer.KINDS:
        mailer.set_wants(u["id"], kind, bool(form.get(kind)))
    return _back(msg="邮件通知已保存")


@router.get("/mail/unsub", response_class=HTMLResponse)
def mail_unsub_page(request: Request, t: str = ""):
    """邮件里的「不想再收」：先确认一下（有的邮箱会自动打开信里的链接，不能一打开就退订）。"""
    who = mailer.read_unsub_token(t)
    if not who:
        return render(request, "message.html", title="链接不对", text="这个退订链接不完整或已经失效。可以登录后在「我的账号」里关掉邮件通知。", link="/settings")
    u = auth.get_user(who[0])
    return render(request, "mail_unsub.html", token=t, kind_name=mailer.KINDS[who[1]], email=u["email"] if u else "",
                  done=bool(u) and not mailer.wants(u, who[1]))


@router.post("/mail/unsub", response_class=HTMLResponse)
def mail_unsub(request: Request, t: str = ""):
    """确认退订；邮箱客户端的「一键退订」（RFC 8058）也是 POST 到这里。"""
    who = mailer.read_unsub_token(t)
    if not who or not auth.get_user(who[0]):
        raise HTTPException(404, "这个退订链接不完整或已经失效")
    mailer.set_wants(who[0], who[1], False)
    auth.log_event("mail_unsub", user_id=who[0], detail=who[1], request=request)
    return render(request, "message.html", title="已退订", text=f"以后不再发「{mailer.KINDS[who[1]]}」。想再收，登录后在「我的账号」里打开。",
                  link="/settings", link_label="去「我的账号」")


@router.post("/settings/mascot")
def settings_mascot(request: Request, mascot: str = Form(...)):
    u = auth.require_user(request)
    if not brand.set_mascot(u["id"], mascot):
        return _back(err="没有这个形象")
    return _back(msg=f"换好了，以后陪你学习的是「{brand.MASCOTS[mascot][0]}」")


@router.post("/settings/profile")
def settings_profile(request: Request, name: str = Form(...), email: str = Form("")):
    u = auth.require_user(request)
    if u["role"] == "kid" and not auth.self_managed(u):
        return _back(err="孩子账号的名字和登录名由家长修改")
    try:
        # 有登录名的（家里的成员）邮箱可以不填；其他人用邮箱登录，必须有
        email = auth.validate_email(email) if (email.strip() or not u["login"]) else None
        auth.check_free(email, None, u["id"])
    except ValueError as e:
        return _back(err=str(e))
    db.run("UPDATE users SET name=?, email=? WHERE id=?", (name or "").strip()[:40] or u["name"], email, u["id"])
    if email != u["email"]:
        auth.log_event("email_change", user_id=u["id"], email=email or "", detail=f"from {u['email']}", request=request)
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


