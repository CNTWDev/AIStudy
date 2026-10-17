"""learning HTTP routes. Learning commands live in app.learning."""
from ..content import content
from fastapi import APIRouter
from fastapi import Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .. import arena, auth, bank, db, engine, evidence, explore, insights, itemtypes, llm, mix, recall, records, sitecfg, sprint, streak, trace
from datetime import date
from ..web import (
    GRADES,
    _ask_item,
    _back,
    _check_exam_dates,
    _kid_form,
    _public_item,
    _review_tasks_done,
    _save_learning,
    _trace_or_404,
    catalog,
    enrollments,
    kid_or_redirect,
    render,
    require_self_learner,
    stage_label,
    stage_rank,
)

router = APIRouter()

@router.post("/password")
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


@router.post("/api/flag")
def api_flag(request: Request, body: dict = Body(...)):
    """孩子 / 家长标记「这道题有问题」。"""
    u = auth.require_user(request)
    target, tid, reason = body.get("target", "item"), str(body.get("id") or ""), body.get("reason", "")
    if target not in ("item", "content") or reason not in bank.FLAG_REASONS or not tid:
        raise HTTPException(400, "参数不对")
    try:
        status = bank.flag(u["id"], target, tid, reason)
    except (KeyError, ValueError):
        raise HTTPException(404, "没有这道题")
    return {"ok": True, "status": status}


@router.get("/me/courses", response_class=HTMLResponse)
def my_courses(request: Request):
    return _kid_form(request, require_self_learner(request), self_mode=True)


@router.post("/me/courses")
async def my_courses_save(request: Request):
    u = require_self_learner(request)
    form = await request.form()
    _check_exam_dates(form)
    grade = form.get("grade") or u["grade"]
    if grade not in GRADES:
        raise HTTPException(400, "年级不对")
    minutes = max(15, min(240, int(form.get("daily_minutes") or u["daily_minutes"] or 60)))
    db.run("UPDATE users SET name=?, grade=?, daily_minutes=? WHERE id=?",
           (form.get("name") or "").strip()[:40] or u["name"], grade, minutes, u["id"])
    _save_learning(u["id"], form, grade, u["grade"], games=False)
    return RedirectResponse("/today", 303)


@router.get("/today", response_class=HTMLResponse)
def today(request: Request):
    k = kid_or_redirect(request, manage=True)
    if not enrollments(k["id"]):
        if auth.is_self_learner(k):
            return RedirectResponse("/me/courses", 303)
        return render(request, "message.html", title="还没有选择学科",
                      text="请家长在「家长页 → 编辑孩子」里勾选要学的教材。")
    t = engine.today_plan(k["id"])
    me = auth.current_user(request)
    settled = streak.settle(k["id"]) if me["role"] == "kid" else {"used": [], "earned": 0}   # 家长查看不结算
    st = engine.streak(k["id"])
    cal = engine.calendar(k["id"], 4, full_weeks=True)
    m = engine.get_mastery(k["id"])
    rec = engine.day_record(k["id"], t["day"])
    play = arena.status(k)
    exams = [x for x in (engine.exam_view(k["id"], e, m) for e in enrollments(k["id"])) if x and x["days_left"] >= 0]
    day = streak.today_state(k, t["plan"], t["minutes"], sprint.points_today(k["id"]))
    return render(request, "today.html", t=t, play=play, day=day, settled=settled, exams=exams,
                  goals=streak.GOALS, sprint_best=sprint.best_day(k["id"]), play_locked=arena.locked_reason(play),
                  due=len(engine.due_cards(k["id"], 99)), progress_ask=engine.progress_prompt(k), streak=st, badges=engine.badges(st), cal=cal, week=cal[-7:],
                  stars=engine.total_stars(k["id"]), rec=rec, auto=engine.day_summary(rec),
                  cov=explore.coverage(k["id"], m), lit=explore.lit_today(k["id"]), ahead=explore.ahead(k["id"], m),
                  vocab=explore.word_stats(k["id"]), weekly=records.weekly(k["id"]), mine=records.summary(k["id"]),
                  found=insights.open_insights(k["id"], for_kid=me["role"] == "kid", limit=4 if me["role"] == "kid" else 10))


@router.get("/go")
def go_next(request: Request, after: str = ""):
    """今天的统一入口：直接进下一项没做的任务（after：先跳过这一项）。都做完了回到今天。"""
    k = kid_or_redirect(request)
    if not engine.plan_of_today(k["id"]):
        engine.today_plan(k["id"])
    t = engine.next_task(engine.plan_of_today(k["id"]), after or None)
    return RedirectResponse(t["url"] if t else "/today", 303)


@router.get("/api/flow")
def flow_get(request: Request, current: str = ""):
    k = kid_or_redirect(request)
    return engine.flow_state(k["id"], current or None)


@router.post("/api/plan/rebuild")
def plan_rebuild(request: Request):
    k = kid_or_redirect(request)
    return engine.today_plan(k["id"], rebuild=True)


@router.post("/api/plan/task-done")
def plan_task_done(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    match = {"type": body.get("type")}
    if body.get("kp"):
        match["kp"] = body["kp"]
    if body.get("pack"):
        match["pack"] = body["pack"]
    hit = engine.mark_task_by(k["id"], **match)
    cur = (hit or [None])[0] or body.get("current")
    return {"ok": True, "stars": engine.total_stars(k["id"]), "flow": engine.flow_state(k["id"], cur)}


@router.post("/api/checkin")
def checkin(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    day = db.today().isoformat()
    engine.today_plan(k["id"])
    mood = (body.get("mood") or "")[:10]
    # 学习时长自动记录，不再手填；「今天学了什么」自动生成，孩子只选心情，想说的话可写可不写
    db.run("UPDATE days SET checked_in=1, reflection=?, mood=? WHERE user_id=? AND day=?",
           (body.get("reflection") or "")[:1000], mood, k["id"], day)
    s = engine.streak(k["id"])
    return {"ok": True, "streak": s, "badges": engine.badges(s)}


@router.post("/api/beat")
async def api_beat(request: Request):
    """页面自动计时的心跳（只有孩子自己的账号计时，家长查看不算）。也接受 sendBeacon 发来的请求。"""
    user = auth.current_user(request)
    if not user or user["role"] != "kid":
        return {"minutes": None}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return {"minutes": engine.beat(user["id"], (body or {}).get("s"))}


@router.get("/day/{day}", response_class=HTMLResponse)
def day_page(request: Request, day: str):
    k = kid_or_redirect(request, manage=True)
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(404)
    r = engine.day_record(k["id"], day)
    return render(request, "day.html", r=r, auto=engine.day_summary(r), streak=engine.streak(k["id"]))


@router.get("/track/{tid}", response_class=HTMLResponse)
def track_page(request: Request, tid: int):
    k = kid_or_redirect(request)
    t = db.one("SELECT * FROM tracks WHERE id=? AND user_id=?", tid, k["id"])
    if not t:
        raise HTTPException(404)
    logs = db.q("SELECT * FROM reading_logs WHERE track_id=? ORDER BY id DESC LIMIT 10", tid)
    book = content.book(t["ref"]) or {}
    return render(request, "track.html", t=t, seg=engine.track_today(t), logs=logs, book=book,
                  units=db.jload(t["units"], []))


@router.post("/api/track/{tid}/log")
def track_log(request: Request, tid: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    if not db.one("SELECT id FROM tracks WHERE id=? AND user_id=?", tid, k["id"]):
        raise HTTPException(404)
    engine.log_reading(k["id"], tid, to_pos=int(body.get("to") or 0), minutes=int(body.get("minutes") or 0),
                       summary=(body.get("summary") or "").strip(), feeling=body.get("feeling") or "",
                       pages=str(body.get("pages") or ""))
    cur = next((t["id"] for t in engine.plan_of_today(k["id"]) if t.get("track") == tid), None)
    return {"ok": True, "stars": engine.total_stars(k["id"]), "flow": engine.flow_state(k["id"], cur)}


@router.get("/subjects", response_class=HTMLResponse)
def subjects(request: Request):
    k = kid_or_redirect(request, manage=True)
    m = engine.get_mastery(k["id"])
    rows = [{"e": e, "pack": catalog.packs[e["pack_id"]], "sum": engine.pack_summary(k["id"], e["pack_id"], m),
             "diag": db.one("SELECT id, finished_at FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='done' "
                            "ORDER BY id DESC", k["id"], e["pack_id"])} for e in enrollments(k["id"])]
    return render(request, "subjects.html", rows=rows)


@router.get("/map/{pack_id}", response_class=HTMLResponse)
def kmap(request: Request, pack_id: str):
    k = kid_or_redirect(request, manage=True)
    if pack_id not in catalog.packs:
        raise HTTPException(404)
    pack = catalog.packs[pack_id]
    m = engine.get_mastery(k["id"])
    stage = engine.enrollment_stage(k["id"], pack_id) or catalog.default_stage(pack_id, k["grade"])
    groups = []
    for st in pack.stages:
        kps = [kp for kp in catalog.pack_kps(pack_id, track=engine.enroll_track(k["id"], pack_id)) if kp["stage"] == st]
        by_strand = {}
        for kp in kps:
            by_strand.setdefault(kp["strand"], []).append({**kp, "m": m.get(kp["id"])})
        groups.append({"stage": st, "strands": [(catalog.strand_name(pack_id, s), v) for s, v in by_strand.items()],
                       "current": st == stage, "past": stage_rank(st) < stage_rank(stage)})
    return render(request, "map.html", pack=pack, groups=groups, stage=stage, sum=engine.pack_summary(k["id"], pack_id, m))


@router.post("/api/progress/snooze")
def progress_snooze(request: Request):
    """「这周学校学了啥？」点了这周跳过：一周内不再提示。"""
    k = kid_or_redirect(request, manage=True)
    engine.snooze_progress(k["id"])
    return {"ok": True}


@router.get("/learn/{kp_id}", response_class=HTMLResponse)
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
    return render(request, "learn.html", kp=kp, pack=pack, pre=pre, post=post, me=m.get(kp_id), task=task, ev=evidence.explain(k['id'], m.get(kp_id)),
                  strand=catalog.strand_name(pack.id, kp["strand"]), vocab=vocab, bridges=engine.bridges(k["id"], kp_id, m))


@router.get("/api/teach/{kp_id}")
def api_teach(request: Request, kp_id: str):
    k = kid_or_redirect(request)
    kp = catalog.kp(kp_id)
    if not kp:
        raise HTTPException(404, "没有这个知识点")
    # 讲解本身存在题库里大家共用；「你在别的学科学过……」是每个孩子自己的，现场拼上（来自人工写的关联说明）
    bridge = [{"subject": b["subject"], "name": b["kp"]["name"], "label": b["label"], "note": b["note"]}
              for b in engine.bridges(k["id"], kp_id) if b["status"] in ("mastered", "learning")][:3]
    return {**bank.teach(kp, k["grade"], user_id=k["id"]), "bridge": bridge}


@router.get("/api/practice/{kp_id}")
def api_practice(request: Request, kp_id: str, n: int = 3, purpose: str = "practice"):
    k = kid_or_redirect(request)
    if not catalog.kp(kp_id):
        raise HTTPException(404)
    purpose = purpose if purpose in ("practice", "preview") else "practice"
    items = engine.items_for(k["id"], kp_id, n=min(n, 5), purpose=purpose, grade=k["grade"])
    # 穿插一道「以前学过的」：只出这个知识点要用到、还没测过 / 只是推断会 / 快忘了的前置（预习新内容时更要先看地基稳不稳）；
    # 前置都稳了就不穿插——学某个知识点时做的题都要和它有关，铺开摸底交给热身和每科练习
    if len(items) >= 2:
        probe = explore.pick(k["id"], 1, k["grade"], near_kp=kp_id, exclude={kp_id}, only_near=True)
        if probe:
            items.insert(1, probe[0])
    import uuid
    return {"items": [{**_public_item(i), "request_id": uuid.uuid4().hex} for i in items], "llm": llm.enabled()}


@router.post("/api/answer")
def api_answer(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    from .. import learning
    return learning.submit(k["id"], body)


@router.post("/api/trace/start")
def api_trace_start(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    kp_id = body.get("kp_id") or ""
    if not catalog.kp(kp_id):
        raise HTTPException(404)
    t = db.one("SELECT id FROM traces WHERE user_id=? AND kp_id=? AND day=?", k["id"], kp_id, db.today().isoformat())
    return {"id": t["id"] if t else trace.start(k["id"], kp_id, body.get("item_id") or "", body.get("answer"))}


@router.get("/trace/{tid}", response_class=HTMLResponse)
def trace_page(request: Request, tid: int, back: str = ""):
    k = kid_or_redirect(request)
    t = _trace_or_404(k, tid)
    back = back if back.startswith("/") and not back.startswith("//") else f"/learn/{t['kp_id']}"
    return render(request, "trace.html", t=t, kp=catalog.kp(t["kp_id"]), back=back, max_steps=trace.MAX_STEPS)


@router.get("/api/trace/{tid}/next")
def api_trace_next(request: Request, tid: int):
    k = kid_or_redirect(request)
    _trace_or_404(k, tid)
    return trace.next_step(k["id"], tid, k["grade"])


@router.post("/api/trace/{tid}/answer")
def api_trace_answer(request: Request, tid: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    _trace_or_404(k, tid)
    res = trace.answer(k["id"], tid, body)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@router.post("/api/learn/done")
def api_learn_done(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    kp_id = body["kp_id"]
    if body.get("summary"):
        kp = catalog.kp(kp_id)
        engine.add_card(k["id"], "kp", kp["name"], body["summary"][:500], {"method": kp.get("method", "")}, kp_id)
    hit = engine.mark_task_by(k["id"], kp=kp_id)
    return {"ok": True, "flow": engine.flow_state(k["id"], (hit or [None])[0])}


@router.get("/api/context/{kp_id}")
def api_context(request: Request, kp_id: str):
    """知识背景和用处（AI 生成，按知识点缓存）。没配置 AI 就返回空。"""
    k = kid_or_redirect(request, manage=True)
    kp = catalog.kp(kp_id)
    if not kp:
        raise HTTPException(404)
    if not llm.enabled():
        return {}
    try:
        return bank.context(kp, k["grade"], user_id=k["id"])
    except llm.LLMError:
        return {}


# ------------------------------------------------------------------ 每门课一组混合练习（app/mix.py）

def _mix_task(k, pack_id: str) -> dict | None:
    return next((t for t in engine.today_plan(k["id"])["plan"] if t.get("type") == "mix" and t.get("pack") == pack_id), None)


@router.get("/practice/{pack_id}", response_class=HTMLResponse)
def mix_page(request: Request, pack_id: str):
    k = kid_or_redirect(request)
    if pack_id not in {e["pack_id"] for e in enrollments(k["id"])}:
        raise HTTPException(404, "没有报这门课")
    t = _mix_task(k, pack_id)
    n = (t or {}).get("n") or mix.size_for(15, k["grade"])
    s = mix.get_or_create(k["id"], pack_id, n)
    slots = db.jload(s["slots"], [])
    return render(request, "mix.html", pack=catalog.packs[pack_id], set_id=s["id"], total=len(slots),
                  parts=mix.summary(slots), done=bool(s["done_at"]), answered=s["answered"], right=s["right_n"],
                  started=s["pos"] > 0)


@router.get("/api/mix/{set_id}/next")
def mix_next(request: Request, set_id: int, k_: int = 4):
    k = kid_or_redirect(request)
    r = mix.next_items(k["id"], set_id, k["grade"], k=min(max(k_, 1), 6))
    import uuid
    r["items"] = [{**_public_item(i), "request_id": uuid.uuid4().hex} for i in r["items"]]
    return r


@router.post("/api/mix/{set_id}/tick")
def mix_tick(request: Request, set_id: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    return mix.tick(k["id"], set_id, str(body.get("kp") or ""), str(body.get("role") or ""), bool(body.get("correct")))


@router.post("/api/mix/{set_id}/done")
def mix_done(request: Request, set_id: int):
    k = kid_or_redirect(request)
    r = mix.finish(k["id"], set_id)
    hit = engine.mark_task_by(k["id"], type="mix", pack=r.get("pack")) if r else []
    return {**r, "flow": engine.flow_state(k["id"], (hit or [None])[0])}


@router.get("/warmup", response_class=HTMLResponse)
def warmup_page(request: Request):
    kid_or_redirect(request)
    return render(request, "warmup.html")


@router.get("/api/warmup")
def api_warmup(request: Request):
    k = kid_or_redirect(request)
    words = explore.pick_words(k["id"], 2)
    kps = explore.pick(k["id"], 3 if words else 4, k["grade"])
    out, wi = [], iter(words)
    for it in kps:  # 知识点题和单词题交替
        out.append(_public_item(it))
        w = next(wi, None)
        if w:
            out.append(w)
    out += list(wi)
    return {"items": out, "llm": llm.enabled()}


@router.post("/api/warmup/word")
def api_warmup_word(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    res = explore.answer_word(k["id"], body.get("word", ""), body.get("list", ""), body.get("choice"),
                              dont_know=bool(body.get("dont_know")), recheck=bool(body.get("recheck")))
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@router.post("/api/ask")
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
        # 题库里的题会挂在好几个知识点上：在学习页问，就按这一页正在学的知识点
        path = str(ctx.get("path") or "")
        page_kp = path.split("/learn/", 1)[1].split("?")[0] if path.startswith("/learn/") else ""
        kp = catalog.kp(ctx.get("kp_id") or page_kp or (it or {}).get("kp_id") or "")
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


@router.get("/api/ask/{thread_id}")
def ask_history(request: Request, thread_id: int):
    k = kid_or_redirect(request)
    if not db.one("SELECT id FROM ask_threads WHERE id=? AND user_id=?", thread_id, k["id"]):
        raise HTTPException(404)
    return {"messages": [{"role": m["role"], "text": m["text"]} for m in
                         db.q("SELECT role, text FROM ask_messages WHERE thread_id=? ORDER BY id", thread_id)]}


@router.get("/diagnose/{pack_id}", response_class=HTMLResponse)
def diagnose_page(request: Request, pack_id: str):
    k = kid_or_redirect(request)
    pack = catalog.packs.get(pack_id) or (_ for _ in ()).throw(HTTPException(404))
    stage = engine.enrollment_stage(k["id"], pack_id) or catalog.default_stage(pack_id, k["grade"])
    running = db.one("SELECT id FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='running' ORDER BY id DESC",
                     k["id"], pack_id)
    history = db.q("SELECT * FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='done' ORDER BY id DESC LIMIT 5",
                   k["id"], pack_id)
    return render(request, "diagnose.html", pack=pack, stage=stage, running=running, history=history)


@router.post("/api/diag/start")
def diag_start(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    pack_id = body["pack_id"]
    stage = body.get("stage") or engine.enrollment_stage(k["id"], pack_id) or catalog.default_stage(pack_id, k["grade"])
    db.run("UPDATE diag_sessions SET status='abandoned' WHERE user_id=? AND pack_id=? AND status='running'", k["id"], pack_id)
    return {"sid": engine.start_diagnosis(k["id"], pack_id, stage)}


@router.get("/api/diag/{sid}/next")
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


@router.post("/api/diag/{sid}/answer")
def diag_answer(request: Request, sid: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    r = engine.diag_answer(k["id"], sid, body.get("answer"), body.get("self"))
    if not r:
        return {"ok": False}
    it = r["item"]
    return {"correct": bool(r["ok"]), **(itemtypes.reveal(it) if it else {"answer": "", "explain": ""})}


@router.post("/api/diag/{sid}/finish")
def diag_finish(request: Request, sid: int):
    k = kid_or_redirect(request)
    s = db.one("SELECT * FROM diag_sessions WHERE id=? AND user_id=?", sid, k["id"])
    if s and s["status"] == "running":
        engine.finish_diagnosis(k["id"], sid)
    return {"report": f"/diagnose/report/{sid}"}


@router.get("/diagnose/report/{sid}", response_class=HTMLResponse)
def diag_report(request: Request, sid: int):
    k = kid_or_redirect(request, manage=True)
    s = db.one("SELECT * FROM diag_sessions WHERE id=? AND user_id=?", sid, k["id"])
    if not s:
        raise HTTPException(404)
    rep = engine.diag_report(sid)
    engine.today_plan(k["id"], rebuild=True)
    kp = catalog.kp
    return render(request, "diag_report.html", rep=rep, pack=catalog.packs[s["pack_id"]], kp=kp)


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, group: str = ""):
    kid_or_redirect(request)
    return render(request, "review.html", group=group if group in engine.CARD_GROUPS else "")


@router.get("/api/review/due")
def review_due(request: Request, group: str = ""):
    k = kid_or_redirect(request)
    cards = []
    limit = 8 if group == "mistakes" else 60
    due = engine.due_cards(k["id"], limit, group or None)
    recall.prepare_variants(k["id"], due)  # 快要换变式题的错题：后台先把题出好，提交时不用等
    for c in due:
        d = dict(c)
        d["extra"] = db.jload(c["extra"], {})
        d["kp_name"] = (catalog.kp(c["kp_id"]) or {}).get("name", "") if c["kp_id"] else ""
        d["quiz"] = recall.quiz(k["id"], c)  # 复习要作答，系统判对错（见 app/recall.py）
        cards.append(d)
    return {"cards": cards}


@router.post("/api/review/{card_id}")
def review_card(request: Request, card_id: int, body: dict = Body(...)):
    """自评（出不了题的卡才用）。"""
    k = kid_or_redirect(request)
    r = engine.review_card(k["id"], card_id, body.get("grade", "good"))
    _review_tasks_done(k["id"])
    return r or {}


@router.post("/api/review/{card_id}/answer")
def review_answer(request: Request, card_id: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    r = recall.answer(k["id"], card_id, body)
    if r.get("error"):
        raise HTTPException(400, r["error"])
    if not r.get("reveal"):
        _review_tasks_done(k["id"])
    return r


@router.post("/api/cards")
def add_card(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    kind = body.get("kind", "word")
    if kind not in ("word", "term", "kp", "phrase"):
        kind = "word"
    cid = engine.add_card(k["id"], kind, body.get("front", ""), body.get("back", ""), body.get("extra") or {},
                          body.get("kp_id"), starred=1)
    return {"id": cid}


@router.post("/api/cards/{card_id}/delete")
def delete_card(request: Request, card_id: int):
    k = kid_or_redirect(request)
    db.run("DELETE FROM cards WHERE id=? AND user_id=?", card_id, k["id"])
    return {"ok": True}


