"""family HTTP routes. Learning commands live in app.learning."""
from ..content import content
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import HTTPException, Request
from .. import auth, db, engine
from ..web import (
    _check_exam_dates,
    _kid_form,
    _records_ctx,
    _save_learning,
    account_action,
    catalog,
    course_choices,
    enrollments,
    is_adult,
    kid_brief,
    kid_or_redirect,
    render,
    stage_label,
)

router = APIRouter()

@router.get("/parent", response_class=HTMLResponse)
def parent_home(request: Request):
    p = auth.require_parent(request)
    request.session.pop("as_kid", None)
    kids = [kid_brief(k) for k in db.q("SELECT * FROM users WHERE parent_id=? ORDER BY id", p["id"])]
    return render(request, "parent.html", kids=kids)


@router.get("/parent/kids/new", response_class=HTMLResponse)
def kid_new_page(request: Request):
    auth.require_parent(request)
    return render(request, "kid_form.html", k=None, enrolled={}, tracks={}, exams={}, presets=catalog.presets,
                  packs_by_subject=course_choices())


@router.get("/parent/kids/{kid_id}/edit", response_class=HTMLResponse)
def kid_edit_page(request: Request, kid_id: int):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id)
    return _kid_form(request, k)


@router.post("/parent/kids/{kid_id}/account")
async def kid_account(request: Request, kid_id: int):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id)
    res = account_action(request, p, k, await request.form())
    return _kid_form(request, auth.get_user(k["id"]), **res)


@router.post("/parent/kids/save")
async def kid_save(request: Request):
    p = auth.require_parent(request)
    form = await request.form()
    kid_id = form.get("id")
    email = (form.get("email") or "").strip().lower()
    name = (form.get("name") or "").strip()
    grade = form.get("grade") or catalog.default_grade
    minutes = int(form.get("daily_minutes") or 60)
    pw = form.get("password") or ""
    if not name:
        raise HTTPException(400, "请填写孩子的名字")
    _check_exam_dates(form)
    try:
        email = auth.validate_email(email)
        if pw:
            auth.validate_pw(pw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    other = auth.by_email(email)
    old_grade = None
    if kid_id:
        k = auth.kid_of(p, int(kid_id))
        old_grade = k["grade"]
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
    _save_learning(kid_id, form, grade, old_grade)
    return RedirectResponse("/parent", 303)


@router.get("/parent/kids/{kid_id}/plan", response_class=HTMLResponse)
def kid_plan_page(request: Request, kid_id: int, msg: str = ""):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id)
    all_tracks = engine.tracks(kid_id, active_only=False)
    active = {t["kind"]: t for t in all_tracks if t["active"]}
    from ..library import library
    return render(request, "kid_plan.html", k=k, active=active, history=[t for t in all_tracks if not t["active"]],
                  content=content, msg=msg, seg={kd: engine.track_today(t) for kd, t in active.items() if kd != "words"},
                  lib={l: sorted(library.shelf(l), key=lambda b: (k["grade"] not in b.get("grades", []), b.get("level", ""))) for l in ("en", "zh")})


@router.post("/parent/kids/{kid_id}/tracks")
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
        elif ref.startswith("lib:"):  # 书库：按页算，进度接着孩子已经在网站上读到的地方
            from ..library import library
            bid = ref[4:]
            lb, lt = library.books.get(bid), library.text(bid)
            if not lb or not lt:
                raise HTTPException(400, "这本书的原文还没下载好")
            title, unit_name, units, total = lb.get("title_zh") or lb["title"], "页", [], lt["n_pages"]
            if not position:
                from ..library_web import progress
                position = progress(kid_id, bid)["page"]
        elif kind == "listen":
            raise HTTPException(400, "听书请从书库里选一本")
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


@router.get("/parent/kids/{kid_id}", response_class=HTMLResponse)
def kid_report(request: Request, kid_id: int):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id)
    return render(request, "records.html", **_records_ctx(k), report_for=k)


@router.get("/parent/as/{kid_id}")
def parent_as(request: Request, kid_id: int, next: str = "/today"):
    p = auth.require_parent(request)
    auth.kid_of(p, kid_id)
    request.session["as_kid"] = kid_id
    return RedirectResponse(next if next in ("/today", "/progress", "/subjects", "/records", "/papers") else "/today", 303)


@router.get("/progress", response_class=HTMLResponse)
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
        v["next"] = engine.next_after(e["pack_id"], e["progress_kp"], m, engine.taught_set(k["id"]), e["track"]) if e["progress_kp"] else None
        v["total"] = len(catalog.ids_for(e["pack_id"], e["track"]))
        v["exam_date"], v["exam"] = e["exam_date"], engine.exam_view(k["id"], e, m)
        rows.append(v)
    return render(request, "progress.html", rows=rows, open_pack=pack, msg=msg, adult=is_adult(k["grade"]))


@router.post("/progress/{pack_id}")
async def progress_save(request: Request, pack_id: str):
    from urllib.parse import urlencode
    k = kid_or_redirect(request, manage=True)
    if pack_id not in {e["pack_id"] for e in enrollments(k["id"])}:
        raise HTTPException(404)
    f = await request.form()
    if "exam_date" in f and not engine.set_exam_date(k["id"], pack_id, f.get("exam_date")):
        raise HTTPException(400, "考试日期格式不对")
    stage = f.get("stage") or None
    old = engine.enrollment_stage(k["id"], pack_id)
    if stage and stage != old:
        # 只换学段：先保存学段，再回到页面勾选新学段学过的内容
        engine.set_progress(k["id"], pack_id, None, None, stage)
        msg = f"已切换到 {stage_label(stage)}，请勾选这个学段学校已经学过的内容"
    elif "chapter" in f or "done_ch" in f:  # 按章报（孩子记得的是学到第几章）
        engine.set_progress_chapters(k["id"], pack_id, f.get("chapter") or None, f.getlist("done_ch"), stage)
        msg = "进度已更新，今天的任务已按新进度重新安排"
    else:
        engine.set_progress(k["id"], pack_id, f.get("current") or None, f.getlist("taught"), stage)
        msg = "进度已更新，今天的任务已按新进度重新安排"
    engine.mark_task_by(k["id"], type="progress")
    engine.today_plan(k["id"], rebuild=True)
    return RedirectResponse("/progress?" + urlencode({"pack": pack_id, "msg": msg}) + f"#p-{pack_id}", 303)


