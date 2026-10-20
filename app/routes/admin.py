"""admin HTTP routes. Learning commands live in app.learning."""
from fastapi import APIRouter
from fastapi import Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .. import arena, auth, bank, boards, bankflow, bankpapers, config, db, evidence, itemtypes, llm, mailer, papers, sitecfg, tts_web
from datetime import timedelta
from urllib.parse import urlencode
from ..web import (
    ADMIN_TABS,
    _admin_back,
    enrollments,
    _admin_user_page,
    _bp_back,
    _bp_or_404,
    _fig_body,
    _read_photos,
    _records_ctx,
    _site_stats,
    _version,
    account_action,
    catalog,
    kid_brief,
    methods,
    render,
)

router = APIRouter()

def _active_week() -> set:
    """近 7 天学过（有学习分钟或打了卡）的学习账号。"""
    since = (db.today() - timedelta(days=7)).isoformat()
    return {r["user_id"] for r in db.q("SELECT DISTINCT user_id FROM days WHERE day>=? AND (minutes>0 OR checked_in=1)", since)}


def _family_row(p, users, active) -> dict:
    """家庭列表的一行：一个家长账号和他添加的孩子。"""
    kids = [k for k in users if k["role"] == "kid" and k["parent_id"] == p["id"]]
    seen = [x for x in [p["last_active_at"] or p["last_login_at"]] + [k["last_active_at"] or k["last_login_at"] for k in kids] if x]
    return {"p": p, "kids": kids, "active": sum(1 for k in kids if k["id"] in active), "last": max(seen) if seen else "",
            "n_invited": sum(1 for u in users if u["invited_by"] == p["id"])}


@router.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, tab: str = "overview", msg: str = "", link: str = "", q: str = ""):
    a = auth.require_admin(request)
    if tab == "curricula":
        return RedirectResponse("/admin/curricula", 303)
    tab = "invites" if tab == "tree" else tab   # 邀请关系并进了邀请码
    if tab not in ADMIN_TABS:
        tab = "overview"
    ctx = {"tab": tab, "msg": msg, "link": link, "now": db.now(), "q": q}
    users = db.q("SELECT u.*, i.name AS inviter_name, i.email AS inviter_email, p.name AS parent_name "
                 "FROM users u LEFT JOIN users i ON i.id=u.invited_by LEFT JOIN users p ON p.id=u.parent_id ORDER BY u.id")
    by_id = {u["id"]: u for u in users}
    ctx["pending"] = [u for u in users if u["status"] == "pending"]
    active = _active_week()
    ctx["stats"] = {
        "families": sum(1 for u in users if u["role"] == "parent" and u["status"] == "active"),
        "kids": sum(1 for u in users if u["role"] == "kid" and u["parent_id"]),
        "selfs": sum(1 for u in users if auth.is_self_learner(u)),
        "pending": len(ctx["pending"]),
        "active_kids": len(active),
        "invites_open": db.one("SELECT COUNT(*) AS n FROM invites WHERE used<max_uses AND (expires_at IS NULL OR expires_at>?)",
                               db.now())["n"],
    }
    if tab == "overview":
        ctx["recent"] = [_family_row(p, users, active) for p in
                         sorted([u for u in users if u["role"] == "parent"], key=lambda u: u["created_at"], reverse=True)[:8]]
        ctx["recent_selfs"] = sorted([u for u in users if auth.is_self_learner(u)], key=lambda u: u["created_at"], reverse=True)[:5]
    elif tab == "families":
        ctx["families"] = [_family_row(p, users, active) for p in users if p["role"] == "parent"
                           and (not q or q.lower() in (p["email"] + p["name"] + p["city"]).lower()
                                or any(q.lower() in k["name"].lower() for k in users if k["parent_id"] == p["id"]))]
    elif tab == "selfs":
        ctx["selfs"] = [{"u": u, "packs": [catalog.packs[e["pack_id"]].subject_name for e in enrollments(u["id"])],
                         "active": u["id"] in active} for u in users if auth.is_self_learner(u)
                        and (not q or q.lower() in (u["email"] + u["name"]).lower())]
    elif tab == "invites":
        invites = db.q("SELECT v.*, u.name AS creator_name, u.email AS creator_email FROM invites v "
                       "LEFT JOIN users u ON u.id=v.created_by ORDER BY v.created_at DESC LIMIT 200")
        used_by = {}
        for u in users:
            if u["invite_code"]:
                used_by.setdefault(u["invite_code"], []).append(u)
        ctx["invites"], ctx["used_by"] = invites, used_by
        # 邀请关系：谁邀请了谁（管理员也是邀请树的起点）
        parents = [u for u in users if u["role"] in ("parent", "admin") or auth.is_self_learner(u)]
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
    elif tab == "bank":
        ctx["bank"] = bank.overview()
        ctx["flow"] = bankflow.stats()
    elif tab == "papers":
        ctx.update(bps=bankpapers.listing(), bp_kinds=bankpapers.KINDS, bp_status=bankpapers.STATUS, bp_label=bankpapers.label,
                   bp_packs=sorted(catalog.packs.values(), key=lambda p: (p.subject_name, p.edition)),
                   max_images=papers.MAX_IMAGES, llm_on=llm.enabled())
    elif tab == "arena":
        ctx["fair"] = arena.fairness()
    elif tab == "log":
        ctx["events"] = db.q("SELECT * FROM auth_events ORDER BY id DESC LIMIT 300")
    elif tab == "system":
        from .. import migrate
        ctx["admins"] = [u for u in users if u["role"] == "admin" or u["is_admin"]]
        ctx.update(reg_mode=sitecfg.registration(), reg_modes=sitecfg.REG_MODES, site={k: sitecfg.get(k) for k in sitecfg.DEFAULTS},
                   llm_status=llm.check(), migrations=migrate.status(), tts_status=tts_web.check(), tts_stats=tts_web.stats(),
                   tts_speeds=tts_web.service().s.speeds, tts_audition=tts_web.AUDITION_SPEEDS,
                   version=_version(), db_dialect=db.DIALECT, mail_status=mailer.check(), mail_recent=mailer.recent(10))
    ctx["me"] = a
    return render(request, "admin.html", **ctx)


@router.post("/admin/settings")
async def admin_settings(request: Request):
    a = auth.require_admin(request)
    f = await request.form()
    vals = {k: (f.get(k) or "").strip()[:40] for k in sitecfg.DEFAULTS if k in f}
    if vals.get("registration") and vals["registration"] not in sitecfg.REG_MODES:
        raise HTTPException(400, "注册方式不对")
    if vals.get("parent_invite_limit") and not vals["parent_invite_limit"].isdigit():
        raise HTTPException(400, "邀请码上限要填数字")
    if vals.get("method_profile") and vals["method_profile"] not in {m.id for m in methods.choices()}:
        raise HTTPException(400, "学习方式不对")
    sitecfg.set_many(vals)
    if "method_profile" in vals:
        evidence.ensure_current()  # 跟着全站默认的孩子：按新方式重算
    auth.log_event("site_settings", user_id=a["id"], email=a["email"], detail=",".join(f"{k}={v}" for k, v in vals.items()),
                   request=request)
    return _admin_back("system", msg="站点设置已保存，立即生效")


@router.post("/admin/mail/test")
def admin_mail_test(request: Request, to: str = Form(...)):
    a = auth.require_admin(request)
    try:
        mailer.send_test(auth.validate_email(to))
    except Exception as e:  # noqa: BLE001  直接把原因给管理员看（认证失败、连不上、证书……）
        return _admin_back("system", msg=f"测试邮件没发出去：{type(e).__name__}: {e}")
    auth.log_event("mail_test", user_id=a["id"], email=a["email"], detail=to, request=request)
    return _admin_back("system", msg=f"测试邮件已发给 {to}，去收件箱看看（也看看垃圾箱）")


@router.post("/admin/users/create")
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


@router.post("/admin/users/{uid}/status")
def admin_user_status(request: Request, uid: int, status: str = Form(...), back: str = Form("families")):
    a = auth.require_admin(request)
    if uid == a["id"]:
        return _admin_back(back, msg="不能修改自己的状态", uid=uid)
    u = auth.get_user(uid)
    if not u or status not in ("active", "disabled", "rejected"):
        raise HTTPException(404)
    auth.set_status(uid, status, by=a["id"])
    event = {"active": "approve" if u["status"] == "pending" else "enable", "disabled": "disable", "rejected": "reject"}[status]
    auth.log_event("admin_" + event, user_id=uid, email=u["email"], detail=f"by {a['email']}", request=request)
    word = {"approve": "已审批通过", "enable": "已启用", "disable": "已停用", "reject": "已拒绝"}[event]
    return _admin_back(back, msg=f"{u['email']} {word}", uid=uid)


@router.post("/admin/users/{uid}/admin")
def admin_toggle_admin(request: Request, uid: int, on: str = Form(""), back: str = Form("families")):
    a = auth.require_admin(request)
    u = auth.get_user(uid)
    if not u or u["role"] != "parent" or uid == a["id"]:
        return _admin_back("families", msg="只能把其他家长账号设为管理员")
    db.run("UPDATE users SET is_admin=? WHERE id=?", 1 if on else 0, uid)
    auth.log_event("admin_grant" if on else "admin_revoke", user_id=uid, email=u["email"], detail=f"by {a['email']}", request=request)
    if back == "family":
        return RedirectResponse(f"/admin/families/{uid}?" + urlencode({"msg": "已更新管理员权限"}), 303)
    return _admin_back("families", msg="已更新管理员权限")


@router.post("/admin/users/{uid}/reset")
def admin_reset_link(request: Request, uid: int, back: str = Form("families")):
    a = auth.require_admin(request)
    u = auth.get_user(uid)
    if not u:
        raise HTTPException(404)
    token = auth.create_reset(uid, a["id"])
    base = config.PUBLIC_URL or str(request.base_url).rstrip("/")
    auth.log_event("admin_reset_link", user_id=uid, email=u["email"], detail=f"by {a['email']}", request=request)
    return _admin_back(back, msg=f"已生成 {u['email']} 的重设密码链接（48 小时内有效，只能用一次），请发给对方：",
                       link=f"{base}/reset/{token}", uid=uid)


@router.get("/admin/users/{uid}", response_class=HTMLResponse)
def admin_user(request: Request, uid: int, msg: str = "", link: str = ""):
    a = auth.require_admin(request)
    if uid == a["id"]:
        return RedirectResponse("/settings", 303)
    u = auth.get_user(uid)
    if not u:
        raise HTTPException(404)
    return _admin_user_page(request, a, u, msg, link)


@router.post("/admin/users/{uid}/profile")
def admin_user_profile(request: Request, uid: int, name: str = Form(...), email: str = Form(...)):
    a = auth.require_admin(request)
    u = auth.get_user(uid)
    if not u or uid == a["id"]:
        raise HTTPException(404)
    try:
        email = auth.validate_email(email)
    except ValueError as e:
        return _admin_back("user", msg=str(e), uid=uid)
    other = auth.by_email(email)
    if other and other["id"] != uid:
        return _admin_back("user", msg="这个邮箱已被别的账号使用", uid=uid)
    db.run("UPDATE users SET name=?, email=? WHERE id=?", (name or "").strip()[:40] or u["name"], email, uid)
    auth.log_event("admin_edit_profile", user_id=uid, email=email, detail=f"from {u['email']} by {a['email']}", request=request)
    return _admin_back("user", msg="已保存", uid=uid)


@router.post("/admin/users/{uid}/account")
async def admin_user_account(request: Request, uid: int):
    a = auth.require_admin(request)
    u = auth.get_user(uid)
    if not u or uid == a["id"]:
        raise HTTPException(404)
    res = account_action(request, a, u, await request.form())
    return _admin_user_page(request, a, auth.get_user(uid), **res)


@router.get("/admin/kids/{kid_id}", response_class=HTMLResponse)
def admin_kid_report(request: Request, kid_id: int):
    auth.require_admin(request)
    k = db.one("SELECT * FROM users WHERE id=? AND role='kid'", kid_id)
    if not k:
        raise HTTPException(404)
    return render(request, "records.html", **_records_ctx(k), report_for=k)


@router.post("/admin/bank/{target}/{tid}/status")
def admin_bank_status(request: Request, target: str, tid: str, status: str = Form(...)):
    auth.require_admin(request)
    if target not in ("item", "content") or status not in ("active", "retired"):
        raise HTTPException(400)
    bank.set_status(target, int(tid) if target == "content" else tid, status)
    return _admin_back("bank", "已恢复使用" if status == "active" else "已下架")


@router.post("/admin/bank/maintain")
async def admin_bank_maintain(request: Request):
    """手动跑一轮题库流水线（平时后台每 15 分钟自动跑）。"""
    auth.require_admin(request)
    from starlette.concurrency import run_in_threadpool
    res = await run_in_threadpool(bankflow.run_once, True)
    if res.get("skipped"):
        return _admin_back("bank", "另一个进程正在跑，稍后再看")
    v, p = res.get("verify") or {}, res.get("variants") or {}
    return _admin_back("bank", f"跑完了：改编新题 {p.get('made', 0)} 道，校对 {v.get('checked', 0)} 道（拦下 {v.get('held', 0)} 道），"
                               f"校准难度 {res.get('calibrated', 0)} 道")


@router.get("/admin/bank/export.json")
def admin_bank_export(request: Request):
    auth.require_admin(request)
    return JSONResponse(bank.export(), headers={"Content-Disposition": f'attachment; filename="aistudy-bank-{db.today().isoformat()}.json"'})


@router.post("/admin/invites/create")
def admin_invite(request: Request, note: str = Form(""), max_uses: int = Form(1), days: int = Form(14)):
    a = auth.require_admin(request)
    code = auth.create_invite(a["id"], note, max_uses, days)
    return _admin_back("invites", msg=f"新邀请码：{code}（可用 {max_uses} 次，{days} 天内有效）",
                       link=f"{config.PUBLIC_URL or str(request.base_url).rstrip('/')}/login?invite={code}")


@router.post("/admin/invites/{code}/delete")
def admin_invite_delete(request: Request, code: str):
    auth.require_admin(request)
    db.run("DELETE FROM invites WHERE code=?", code)
    return _admin_back("invites", msg="已作废邀请码 " + code)


@router.post("/admin/bank-papers")
async def admin_bp_create(request: Request):
    a = auth.require_admin(request)
    f = await request.form()
    pack_id = f.get("pack_id") or ""
    if pack_id not in catalog.packs:
        raise HTTPException(400, "请选择课程")
    images = await _read_photos(f)
    text = (f.get("text") or "").strip()
    if not images and len(text) < 10:
        raise HTTPException(400, "请上传卷子照片，或者粘贴题目文字")
    from starlette.concurrency import run_in_threadpool
    try:
        bp = await run_in_threadpool(bankpapers.create, a["id"], pack_id, kind=f.get("kind") or "real", stage=f.get("stage") or "",
                                     title=(f.get("title") or "").strip(), exam=(f.get("exam") or "").strip(),
                                     year=(f.get("year") or "").strip(), region=(f.get("region") or "").strip(),
                                     org=(f.get("org") or "").strip(),
                                     minutes=int(f.get("minutes")) if str(f.get("minutes") or "").isdigit() else 0,
                                     images=images, text=text)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": bp}


@router.get("/admin/bank-papers/{bp_id}", response_class=HTMLResponse)
def admin_bp_page(request: Request, bp_id: int, msg: str = ""):
    auth.require_admin(request)
    bp = _bp_or_404(bp_id)
    pack = catalog.packs.get(bp["pack_id"])
    return render(request, "admin_bank_paper.html", bp=bp, pack=pack, rows=bankpapers.rows(bp_id), msg=msg,
                  label=bankpapers.label(bp), kinds=bankpapers.KINDS, status=bankpapers.STATUS, types=itemtypes.TYPES,
                  images=db.jload(bp["images"], []), kps=[catalog.kps[k] for k in pack.kp_ids] if pack else [],
                  used=db.one("SELECT COUNT(*) AS n FROM papers WHERE bank_paper_id=?", bp_id)["n"])


@router.get("/admin/bank-papers/{bp_id}/img/{name}")
def admin_bp_image(request: Request, bp_id: int, name: str):
    from fastapi.responses import FileResponse
    auth.require_admin(request)
    if name not in db.jload(_bp_or_404(bp_id)["images"], []):
        raise HTTPException(404)
    return FileResponse(bankpapers.DIR / str(bp_id) / name)


@router.post("/admin/bank-papers/{bp_id}/meta")
async def admin_bp_meta(request: Request, bp_id: int):
    auth.require_admin(request)
    _bp_or_404(bp_id)
    bankpapers.update_meta(bp_id, dict(await request.form()))
    return _bp_back(bp_id, "已保存")


@router.post("/admin/bank-papers/{bp_id}/items/{bpi_id}")
async def admin_bp_item(request: Request, bp_id: int, bpi_id: int):
    auth.require_admin(request)
    _bp_or_404(bp_id)
    f = dict(await request.form())
    if f.get("delete"):
        bankpapers.remove_item(bp_id, bpi_id)
        return _bp_back(bp_id, "已删掉这道题")
    try:
        typ = bankpapers.update_item(bp_id, bpi_id, f)
    except KeyError:
        raise HTTPException(404)
    msg = "已保存" if typ == (f.get("type") or typ) else f"答案格式不对，这题改成了{itemtypes.TYPES[typ].label}，请再看一眼"
    return _bp_back(bp_id, msg, f"bpi-{bpi_id}")


@router.post("/admin/bank-papers/{bp_id}/items/{bpi_id}/fig")
def admin_bp_fig(request: Request, bp_id: int, bpi_id: int, body: dict = Body(...)):
    auth.require_admin(request)
    _bp_or_404(bp_id)
    try:
        bankpapers.set_fig(bp_id, bpi_id, *_fig_body(body))
    except KeyError:
        raise HTTPException(404)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/admin/bank-papers/{bp_id}/status")
def admin_bp_status(request: Request, bp_id: int, action: str = Form(...)):
    auth.require_admin(request)
    _bp_or_404(bp_id)
    if action == "publish":
        try:
            n = bankpapers.publish(bp_id)
        except ValueError as e:
            return _bp_back(bp_id, str(e))
        return _bp_back(bp_id, f"已发布：{n} 道题进了公共题库，学习者可以做整卷模拟考")
    if action == "retire":
        bankpapers.retire(bp_id)
        return _bp_back(bp_id, "已下架")
    if action == "delete":
        if not bankpapers.delete(bp_id):
            return _bp_back(bp_id, "发布过或有人做过的卷子不能删，可以下架")
        return _admin_back("papers", "已删除")
    raise HTTPException(400)


@router.get('/admin/operations', response_class=HTMLResponse)
def operations(request: Request):
    auth.require_admin(request)
    calls = db.q("SELECT task,provider,model,status,COUNT(*) AS n,SUM(elapsed_ms) AS elapsed_ms,"
                 "SUM(input_tokens) AS input_tokens,SUM(output_tokens) AS output_tokens,COUNT(input_tokens) AS input_known,COUNT(output_tokens) AS output_known FROM llm_calls "
                 "WHERE created_at>=? GROUP BY task,provider,model,status", db.today().isoformat())
    return render(request, 'operations.html', jobs=db.q('SELECT * FROM jobs ORDER BY name'), calls=calls)


# ------------------------------------------------------------------ 家庭页、学习账号换家庭

@router.get("/admin/families/{pid}", response_class=HTMLResponse)
def admin_family(request: Request, pid: int, msg: str = ""):
    """一个家庭：家长账号 + 每个孩子的学习概况和家长给孩子定的规矩（游戏、公开房间、排行榜，只看不改：归家长管）。"""
    a = auth.require_admin(request)
    p = db.one("SELECT * FROM users WHERE id=? AND role='parent'", pid)
    if not p:
        raise HTTPException(404)
    kids = db.q("SELECT * FROM users WHERE parent_id=? AND role='kid' ORDER BY id", pid)
    inviter = auth.get_user(p["invited_by"]) if p["invited_by"] else None
    return render(request, "admin_family.html", p=p, me=a, inviter=inviter, kids=[kid_brief(k) for k in kids],
                  city=boards.city_label(p["city"]), msg=msg, now=db.now())


def move_learner(kid, parent, actor, request=None) -> str:
    """把学习账号挂到另一个家庭（parent），或者 parent=None 改为自学者。
    学习记录、课程、游戏和隐私设置都按账号存，原样跟着走（新家长在家长页能看到、能改）；只离开正在玩的联网房间。"""
    old = auth.get_user(kid["parent_id"]) if kid["parent_id"] else None
    db.run("UPDATE users SET parent_id=? WHERE id=?", parent["id"] if parent else None, kid["id"])
    arena.leave_rooms(dict(kid))
    boards.refresh()
    frm = f"{old['email']} 家" if old else "自学者"
    to = f"{parent['email']} 家" if parent else "自学者"
    auth.log_event("admin_move_learner", user_id=kid["id"], email=kid["email"], detail=f"{frm} → {to} by {actor['email']}", request=request)
    return to


@router.post("/admin/users/{uid}/family")
def admin_user_family(request: Request, uid: int, to: str = Form(...), confirm: str = Form("")):
    a = auth.require_admin(request)
    k = db.one("SELECT * FROM users WHERE id=? AND role='kid'", uid)
    if not k:
        raise HTTPException(404)
    if confirm.strip() != k["name"]:
        return _admin_back("user", msg="确认名字没填对，账号没有调整", uid=uid)
    if to == "self":
        if not k["parent_id"]:
            return _admin_back("user", msg="已经是自学者了", uid=uid)
        parent = None
    else:
        parent = db.one("SELECT * FROM users WHERE id=? AND role='parent' AND status='active'", int(to) if to.isdigit() else 0)
        if not parent:
            return _admin_back("user", msg="没有这个家庭（只能挂到正常使用中的家长账号下）", uid=uid)
        if parent["id"] == k["parent_id"]:
            return _admin_back("user", msg="已经在这个家庭里了", uid=uid)
    to = move_learner(k, parent, a, request)
    return _admin_back("user", msg=f"已把 {k['name']} 调整为：{to}。学习记录和设置都保留。", uid=uid)
