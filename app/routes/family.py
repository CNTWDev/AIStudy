"""family HTTP routes. Learning commands live in app.learning."""
from ..content import content
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import HTTPException, Request
from .. import auth, db, engine
from ..web import (
    _check_exam_dates,
    GRADES,
    default_minutes,
    _kid_form,
    _records_ctx,
    _save_learning,
    account_action,
    adult_brief,
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
    kids = [adult_brief(k) if auth.self_managed(k) else kid_brief(k)
            for k in db.q("SELECT * FROM users WHERE parent_id=? ORDER BY id", p["id"])]
    return render(request, "parent.html", kids=kids)


@router.get("/parent/kids/new", response_class=HTMLResponse)
def kid_new_page(request: Request):
    return _kid_new(request)


def _kid_new(request: Request, draft: dict | None = None, msg: str = ""):
    """添加孩子：名字、年级、在哪上学、4 位数字；课按学校模板默认排好，家长可逐科换。"""
    p = auth.require_parent(request)
    d = dict(draft or {})
    if "region" not in d:  # 地区先按家里填过的城市、或者上一个孩子的学校模板猜
        prov = (p["city"] or "").split("·")[0]
        last = db.one("SELECT preset FROM users WHERE parent_id=? AND preset<>'' ORDER BY id DESC LIMIT 1", p["id"])
        d["region"] = prov if prov in catalog.regions else ((catalog.preset(last["preset"]) or {}).get("region", "") if last else "")
    grade = d.get("grade") or catalog.default_grade
    if d.get("courses") is None:
        preset, courses = catalog.default_courses(d["region"], d.get("school_type") or "public", grade) if d["region"] else (None, [])
        d["courses"], d["preset"] = {c["pack"]: c["track"] for c in courses}, preset["id"] if preset else ""
    return render(request, "kid_new.html", k=None, draft=d, msg=msg,
                  enrolled=d["courses"], tracks=d["courses"], exams={}, self_mode=False,
                  packs_by_subject=course_choices(d.get("school_type") or "", grade), regions=catalog.regions,
                  school_types=catalog.SCHOOL_TYPES, default_minutes=default_minutes(grade),
                  sum_title=_courses_title(d["region"], d.get("school_type") or "public", grade) if d["region"] else "选好在哪上学，这里列出要学的课")


def _courses_title(region: str, school_type: str, grade: str) -> str:
    preset = catalog.match_preset(region, school_type, grade)
    if preset:
        return f"按{preset['name'].split('（')[0]} · {stage_label(grade)}，先安排这几门："
    return f"{region if region in catalog.regions else '其它地区'}的{stage_label(grade)}教材还在整理：先安排统编语文，其它科目在下面逐科选。"


@router.get("/parent/kids/defaults")
def kid_defaults(request: Request, region: str = "", school_type: str = "public", grade: str = ""):
    """添加孩子时选了地区、学校类型、年级：按学校模板给出默认的课（页面据此预填，家长再按需要换）。"""
    auth.require_parent(request)
    grade = grade if grade in GRADES else catalog.default_grade
    preset, courses = catalog.default_courses(region, school_type, grade)
    return {"preset": preset["id"] if preset else "", "title": _courses_title(region, school_type, grade), "courses": courses}


@router.get("/parent/kids/login-suggest")
def login_suggest(request: Request, name: str = ""):
    """添加孩子时，填完名字自动给一个登录名（就用名字，重名加数字）。"""
    auth.require_parent(request)
    return {"login": auth.suggest_login(name)}


@router.get("/parent/kids/{kid_id}/edit", response_class=HTMLResponse)
def kid_edit_page(request: Request, kid_id: int):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id, account_only=True)
    return _kid_form(request, k)


@router.post("/parent/kids/{kid_id}/account")
async def kid_account(request: Request, kid_id: int):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id, account_only=True)
    res = account_action(request, p, k, await request.form())
    return _kid_form(request, auth.get_user(k["id"]), **res)


@router.post("/parent/kids/save")
async def kid_save(request: Request):
    p = auth.require_parent(request)
    form = await request.form()
    kid_id = form.get("id")
    name = (form.get("name") or "").strip()
    grade = form.get("grade") or catalog.default_grade
    minutes = int(form.get("daily_minutes") or default_minutes(grade))
    pw = (form.get("password") or "").strip()
    # 家里的成员都用登录名登录（孩子记得住的小名），邮箱选填；孩子的密码是 4 位数字，成人成员是普通密码
    draft = {"name": name, "login": form.get("login") or "", "email": form.get("email") or "", "grade": grade,
             "region": form.get("region") or "", "school_type": form.get("school_type") or "public",
             "courses": {pid: form.get(f"track_{pid}") or "" for key in form.keys() if key.startswith("subj_") and (pid := form.get(key))}}
    # 填错的地方都回到表单上提示（保留填过的内容），先全部检查完再建账号：不能账号建好了才报错
    try:
        if not name:
            raise ValueError("请填写孩子的名字")
        _check_exam_dates(form)
        if (form.get("nickname") or "").strip():
            auth.check_nickname(name, form.get("nickname"))
        # 没填登录名（比如页面脚本没跑）：新加的就用名字起一个，编辑时保留原来的
        raw = (form.get("login") or "").strip() or ((auth.get_user(int(kid_id)) or {}).get("login") if kid_id else None) or auth.suggest_login(name)
        login = auth.validate_login(raw or "")
        email = auth.validate_email(form.get("email")) if (form.get("email") or "").strip() else None
        auth.check_free(email, login, int(kid_id) if kid_id else None)
    except (ValueError, HTTPException) as e:
        msg = e.detail if isinstance(e, HTTPException) else str(e)
        if kid_id:
            return _kid_form(request, auth.kid_of(p, int(kid_id), account_only=True), msg=msg)
        return _kid_new(request, draft=draft, msg=msg)
    old_grade = None
    if kid_id:
        k = auth.kid_of(p, int(kid_id), account_only=True)
        old_grade = k["grade"]
        if not auth.self_managed(k):
            db.run("UPDATE users SET login=?, email=?, name=?, grade=?, daily_minutes=? WHERE id=?",
                   login, email, name, grade, minutes, k["id"])
        else:  # 成人成员：家长只管账号，学校、每天时长由本人定
            db.run("UPDATE users SET login=?, email=?, name=?, grade=? WHERE id=?", login, email, name, grade, k["id"])
        status = "disabled" if form.get("disabled") else "active"
        if status != k["status"]:
            auth.set_status(k["id"], status)
        kid_id = k["id"]
        engine.fit_courses(kid_id)  # 年级跨过大学这条线：另一边的课暂停
    else:
        try:
            kid_id = auth.create_user(email, pw, name, "kid", parent_id=p["id"], grade=grade, login=login,
                                      daily_minutes=minutes)
        except ValueError as e:
            return _kid_new(request, draft=draft, msg=str(e))
    if not old_grade and form.get("region"):  # 新添加的孩子：学校模板按 地区 + 学校类型 + 年级 在服务器上重新匹配，不信页面传来的
        preset = catalog.match_preset(form.get("region"), form.get("school_type") or "public", grade)
        form = {**{key: form.get(key) for key in form.keys()}, "preset": preset["id"] if preset else ""}
        if form["region"] in catalog.regions and not p["city"]:  # 家里还没填城市：先记上省份（城市排行榜用），以后可以改
            from .. import boards
            boards.set_city(p["id"], form["region"], "")
    # 学习安排只给未成年的孩子：成人成员由本人在「我的课程」里安排。成人改回孩子年级的这一次，表单里没有学习安排，先不动原来的课程
    if not is_adult(grade) and not (old_grade and is_adult(old_grade)):
        _save_learning(kid_id, form, grade, old_grade)
    if not old_grade:  # 刚添加：给家长一张「登录卡」，照着告诉孩子怎么登录（4 位数字只在这一次显示）
        return render(request, "kid_card.html", k=auth.get_user(kid_id), pin=pw)
    return RedirectResponse("/parent", 303)


@router.get("/parent/kids/{kid_id}/plan", response_class=HTMLResponse)
def kid_plan_page(request: Request, kid_id: int, msg: str = ""):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id)
    from .. import words
    all_tracks = [t for t in engine.tracks(kid_id, active_only=False) if t["kind"] != "words"]   # 单词改成单词包（word_packs）
    active = {t["kind"]: t for t in all_tracks if t["active"]}
    from ..library import library
    return render(request, "kid_plan.html", k=k, active=active, wpacks=words.attached(kid_id), wstats=words.stats(kid_id), wgroups=words.grouped(), history=[t for t in all_tracks if not t["active"]],
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

    def back(msg):  # 填得不对：回到这一页，在上面提示
        return RedirectResponse(f"/parent/kids/{kid_id}/plan?" + urlencode({"msg": msg}), 303)
    if kind == "words":   # 单词包（app/words.py）：加上 / 停掉一个包、改每天几个新词
        from .. import words
        ref = f.get("ref") or ""
        if ref not in words.packs():
            return back("请选择词表")
        on = f.get("action") != "stop"
        amount = f.get("daily_amount")
        words.set_pack(kid_id, ref, on, int(amount) if str(amount or "").strip().isdigit() else None)
        msg = f"已安排「{engine.TRACK_KINDS[kind]}」：{words.packs()[ref]['title']}" if on else f"已停用单词包：{words.packs()[ref]['title']}"
    elif f.get("action") == "stop":
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
        if ref.startswith("lib:"):  # 书库：按页算，进度接着孩子已经在网站上读到的地方
            from ..library import library
            bid = ref[4:]
            lb, lt = library.books.get(bid), library.text(bid)
            if not lb or not lt:
                return back("这本书的原文还没下载好，换一本或者过一会儿再试")
            title, unit_name, units, total = lb.get("title_zh") or lb["title"], "页", [], lt["n_pages"]
            if not position:
                from ..library_web import progress
                position = progress(kid_id, bid)["page"]
        elif kind == "listen":
            return back("听书请从书库里选一本")
        elif ref and ref != "custom":
            b = content.book(ref)
            if not b:
                return back("没有这本书，请重新选一本")
            title, unit_name, units, total = b["title"], b.get("unit_name") or "章", b.get("units", []), b.get("total_units", 0)
        else:
            title = (f.get("title") or "").strip().strip("《》")[:60]
            if not title:
                return back("请填写书名")
            unit_name = (f.get("unit_name") or "章").strip()[:4] or "章"
            units, total, ref = [], max(0, int(f.get("total_units") or 0)), ""
        with db.tx() as t:
            t.run("UPDATE tracks SET active=0 WHERE user_id=? AND kind=?", kid_id, kind)
            t.run("INSERT INTO tracks(user_id,kind,ref,title,unit_name,units,total_units,position,daily_amount,daily_minutes,"
                  "active,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?)", kid_id, kind, ref, title, unit_name,
                  db.jdump(units), total, position, max(1, amount), minutes, db.now())
        msg = f"已安排「{engine.TRACK_KINDS[kind]}」：{title}"
    engine.today_plan(kid_id, rebuild=True)
    return RedirectResponse(f"/parent/kids/{kid_id}/plan?" + urlencode({"msg": msg}), 303)


@router.get("/parent/kids/{kid_id}", response_class=HTMLResponse)
def kid_report(request: Request, kid_id: int):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id, report=True)   # 家里的成人成员的学习记录家长也能看
    return render(request, "records.html", **_records_ctx(k), report_for=k)


@router.get("/parent/as/{kid_id}")
def parent_as(request: Request, kid_id: int, next: str = "/today"):
    p = auth.require_parent(request)
    auth.kid_of(p, kid_id)
    request.session["as_kid"] = kid_id
    return RedirectResponse(next if next in ("/today", "/progress", "/subjects", "/records", "/papers") else "/today", 303)


@router.get("/progress", response_class=HTMLResponse)
def progress_page(request: Request, pack: str = "", msg: str = ""):
    from .. import schoolyear
    k = kid_or_redirect(request, manage=True)
    schoolyear.sync(k["id"])   # 按今天的日期推演一遍（孩子不用报）
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
    if "exam_date" in f and not engine.set_exam_date(k["id"], pack_id, f.get("exam_date")):  # 填错了回到这一门课提示
        return RedirectResponse("/progress?" + urlencode({"pack": pack_id, "msg": "考试日期格式不对，请重新选一下"}) + f"#p-{pack_id}", 303)
    stage = f.get("stage") or None
    old = engine.enrollment_stage(k["id"], pack_id)
    adjust = f.get("adjust") or ""
    if adjust in ("ok", "+1", "-1"):   # 按校历推演的进度：对 / 学校快一章 / 慢一章（只在这一学期有效）
        from .. import schoolyear
        schoolyear.correct(k["id"], pack_id, confirm=adjust == "ok", delta=0 if adjust == "ok" else int(adjust))
        msg = "好的，进度就按这个走" if adjust == "ok" else "已调整，以后按调整后的进度接着往前走"
    elif stage and stage != old:
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


