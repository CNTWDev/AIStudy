"""Shared presentation and account helpers; no router or application lifecycle."""

import re

from datetime import date, timedelta

from fastapi import HTTPException, Request

from fastapi.responses import RedirectResponse

from fastapi.templating import Jinja2Templates

from . import arena, auth, bankpapers, boards, coach, config, db, engine, evidence, explore, focus, insights, itemtypes, llm, papers, records, sitecfg, sprint, streak, trace

from . import figures

from .figures import photo

from .catalog import GRADES, catalog, is_adult, stage_label, stage_rank

from .methods import methods

from . import brand

from . import catalog as catalog_mod

from . import news, tts_web

templates = Jinja2Templates(directory=config.BASE_DIR / "app" / "templates")

templates.env.globals.update(stage_label=stage_label, catalog=catalog, STATUS_LABEL=engine.STATUS_LABEL, methods=methods,
                             llm_enabled=llm.enabled, GRADES=GRADES, answer_display=engine.answer_display,
                             stage_rank=stage_rank, is_adult=is_adult, is_self_learner=auth.is_self_learner, self_managed=auth.self_managed, uses_pin=auth.uses_pin, account_name=auth.account_name, game_minutes=arena.game_minutes, game_unlock=arena.game_unlock, is_private=auth.is_private, PROVINCES=boards.PROVINCES, city_label=boards.city_label,
                             GAME_MINUTE_CHOICES=arena.GAME_MINUTE_CHOICES, UNLOCK_CHOICES=arena.UNLOCK_CHOICES,
                             trend_words=records.trend_words, tts_info=tts_web.client_info, qview=itemtypes.view,
                             qfig=figures.present)

templates.env.globals.update(MASCOTS=brand.MASCOTS, mascot_of=brand.mascot_of, mascot_chosen=brand.has_chosen, mascot_svg=brand.mascot_svg,
                             wordmark_svg=brand.wordmark_svg)

templates.env.globals["news_today_title"] = news.today_title

_STATIC = config.BASE_DIR / "app" / "static"

# 静态文件的版本号：任何一个 js / css 改了（升级后）就变，浏览器不会拿旧的缓存配新的页面
templates.env.globals["ASSET_V"] = str(int(max(f.stat().st_mtime for f in _STATIC.rglob("*") if f.suffix in (".css", ".js"))))

def device_label(ua: str | None) -> str:
    """把浏览器 User-Agent 变成人看得懂的「Chrome · iPad」。"""
    ua = ua or ""
    dev = next((n for k, n in [("iPad", "iPad"), ("iPhone", "iPhone"), ("Android", "安卓"), ("Windows", "Windows 电脑"),
                               ("Macintosh", "Mac"), ("CrOS", "Chromebook"), ("Linux", "Linux")] if k in ua), "未知设备")
    app_ = next((n for k, n in [("MicroMessenger", "微信"), ("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                ("Safari/", "Safari")] if k in ua), "浏览器")
    return f"{app_} · {dev}"

templates.env.filters["device"] = device_label
templates.env.filters["stage_name"] = stage_label

def ui_tone(learner) -> str:
    """界面口吻：上中小学的孩子是 kid（圆体、小伙伴会动）；成人学习者、家长、管理员是 adult（同一套颜色，少一点童趣）。"""
    if not learner or learner["role"] != "kid":
        return "adult"
    is_adult = getattr(catalog_mod, "is_adult", None)  # 成人学习者（年级「成人」/大学）
    return "adult" if is_adult and is_adult(learner["grade"]) else "kid"

def render(request: Request, name: str, status_code: int = 200, **ctx):
    user = auth.current_user(request)
    if user and user["must_change_pw"] and name not in ("settings.html", "message.html"):
        return RedirectResponse("/settings?force=1", 303)
    kid = None
    if user:
        kid = user if user["role"] == "kid" else (
            db.one("SELECT * FROM users WHERE id=? AND parent_id=?", request.session.get("as_kid"), user["id"])
            if request.session.get("as_kid") and user["role"] == "parent" else None)
    # readonly：家长在看孩子的页面——只能查看和管理，不能替孩子做题
    if user and user["role"] == "kid" and request.url.path != "/today":
        ctx["dayflow"] = _flow_here(request, user)
    base = {"user": user, "kid": kid, "readonly": bool(user and user["role"] != "kid"),
            "SITE": sitecfg.get("site_name"), "ASSISTANT": sitecfg.get("assistant_name"),
            "ASSISTANT_ICON": sitecfg.get("assistant_icon"), "tone": ui_tone(kid or user)}
    # simple：孩子自己在看（不是家长、不是成人学习者）——少字多图，说明文字只给家长看（docs/KID_UX.md）
    base["simple"] = bool(user and user["role"] == "kid" and base["tone"] == "kid")
    return templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)

def _flow_here(request: Request, kid) -> dict | None:
    """这个页面是今天清单里的哪一项：页面顶上显示「今天 3/8 · 正在做…」，随时能去下一项（见 engine.flow_match）。"""
    plan = engine.plan_of_today(kid["id"])
    if not plan:
        return None
    path, query = request.url.path, dict(request.query_params)
    lang = query.get("lang") if path == "/reading/today" else None
    m = re.fullmatch(r"/reading/(\d+)", path)
    if m:
        r = db.one("SELECT lang FROM readings WHERE id=? AND user_id=?", int(m.group(1)), kid["id"])
        lang = r["lang"] if r else None
    t = engine.flow_match(plan, path, query, lang)
    if not t:
        return None
    nxt = engine.next_task(plan, t["id"])
    return {"task": t, "n": plan.index(t) + 1, "total": len(plan), "done": sum(1 for x in plan if x.get("done")),
            "dots": [bool(x.get("done")) for x in plan], "next": nxt}

PARENT_READONLY = "家长账号用来查看和管理。做题、阅读、复习请让孩子用自己的账号登录。"

def kid_or_redirect(request: Request, manage: bool = False):
    """当前操作的孩子：孩子本人；或家长在家长页选中的孩子（manage=True 的页面：查看、更新进度、导入试卷等）。
    做题、阅读、复习这类「学习」操作只有孩子自己的账号能做。"""
    user = auth.require_user(request)
    if user["role"] == "kid":
        return user
    if user["role"] != "parent":
        raise HTTPException(403, "管理员账号不用来学习；在「管理 → 家庭」里可以查看每个孩子的情况。")
    kid_id = request.session.get("as_kid")
    k = db.one("SELECT * FROM users WHERE id=? AND parent_id=?", kid_id, user["id"]) if kid_id else None
    if not k:
        raise HTTPException(400, "请先在家长页选择一个孩子")
    if auth.self_managed(k):  # 家里的成人成员：学习记录只有本人能看
        raise HTTPException(403, f"{k['name']} 是家里的成人成员，学习由 TA 自己安排，学习记录也只有 TA 自己能看。")
    if not manage:
        raise HTTPException(403, PARENT_READONLY)
    return k

def enrollments(kid_id: int):
    return [dict(r) for r in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", kid_id)
            if r["pack_id"] in catalog.packs]

def _reg_mode() -> str:
    return "first" if auth.user_count() == 0 else sitecfg.registration()

def account_action(request: Request, actor, target, form) -> dict:
    """家长管孩子账号、管理员管其他账号共用：重置密码 / 解除锁定 / 退出所有设备。返回给模板的 msg、shown_pw。"""
    action = form.get("action") or ""
    who = f"by {auth.account_name(actor)}"
    if action == "password":
        pw = (form.get("password") or "").strip()
        generated = not pw
        pw = pw or auth.temp_password(target)
        try:
            auth.set_password(target["id"], pw, must_change=bool(form.get("force")))
        except ValueError as e:
            return {"msg": str(e)}
        auth.log_event("password_reset_by_" + actor["role"], user_id=target["id"], email=auth.account_name(target), detail=who, request=request)
        return {"msg": f"已重置 {target['name']} 的{'数字' if auth.uses_pin(target) else '密码'}，其他设备上的登录已退出。" + ("" if generated else ("新的数字" if auth.uses_pin(target) else "新密码") + "就是你刚才输入的。"),
                "shown_pw": pw if generated else ""}
    if action == "unlock":
        auth.unlock(target["id"])
        auth.log_event("unlock", user_id=target["id"], email=auth.account_name(target), detail=who, request=request)
        return {"msg": "已解除锁定，可以重新登录了"}
    if action == "signout":
        n = auth.signout_all(target["id"])
        auth.log_event("signout_all", user_id=target["id"], email=auth.account_name(target), detail=who, request=request)
        return {"msg": f"已让 {target['name']} 在 {n} 台设备上退出登录"}
    raise HTTPException(400, "不认识的操作")

def account_ctx(acct, url: str, force: bool, reset_link: bool = False) -> dict:
    return {"acct": auth.get_user(acct["id"]), "acct_url": url, "acct_sessions": len(auth.sessions_of(acct["id"])),
            "acct_force": force, "acct_reset_link": reset_link, "now": db.now()}

def _back(msg="", err=""):
    from urllib.parse import urlencode
    return RedirectResponse("/settings?" + urlencode({"msg": msg, "err": err}), 303)

def kid_brief(k) -> dict:
    """一个孩子的概况：家长页和管理后台共用。"""
    m = engine.get_mastery(k["id"])
    packs = [{"pack": catalog.packs[e["pack_id"]], "stage": e["stage"], "sum": engine.pack_summary(k["id"], e["pack_id"], m)}
             for e in enrollments(k["id"])]
    cal = engine.calendar(k["id"], weeks=1)
    today = engine.today_plan(k["id"]) if packs else {"plan": [], "minutes": 0}
    weak = sorted([v for v in m.values() if v["status"] == "weak" and catalog.kp(v["kp_id"])], key=lambda v: v["score"])
    return {"u": k, "packs": packs, "streak": engine.streak(k["id"]), "cards": streak.cards(k["id"]), "week": cal,
            "sprint_week": sprint.week(k["id"], cal[0]["day"], (db.today() + timedelta(days=1)).isoformat()),
            "week_min": sum(d["minutes"] for d in cal), "week_days": sum(1 for d in cal if d["minutes"] or d["checked"]),
            "today": today, "today_done": sum(1 for t in today["plan"] if t.get("done")),
            "insights": insights.open_insights(k["id"], limit=6),
            "cov": explore.coverage(k["id"], m), "vocab": explore.word_stats(k["id"]),
            "weak": [catalog.kp(w["kp_id"]) for w in weak[:5]], "weak_n": len(weak),
            "due": len(engine.due_cards(k["id"], 500)), "weekly": records.weekly(k["id"]),
            "words": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='word'", k["id"])["n"],
            "arena": arena.parent_summary(k), "focus": focus.parent_summary(k), "trend": records.accuracy_trend(k["id"], 14), "school": _school_progress(k)}


def adult_brief(k) -> dict:
    """家里的成人成员在家长页上：只有坚持（连续天数、这一周学了几天），学了什么、错在哪只有本人能看。"""
    cal = engine.calendar(k["id"], weeks=1)
    return {"u": k, "adult": True, "streak": engine.streak(k["id"]),
            "week_days": sum(1 for d in cal if d["minutes"] or d["checked"])}


def _school_progress(k) -> list[dict]:
    """家长页：每门课按校历估计学校学到哪了，一个月没核对的标出来。"""
    from . import schoolyear
    out = []
    confirm_before = (db.today() - timedelta(days=engine.CONFIRM_DAYS)).isoformat()
    for e in enrollments(k["id"]):
        est = schoolyear.estimate(dict(k), dict(e))
        if est:
            out.append({"subject": catalog.packs[e["pack_id"]].subject_name, "pack": e["pack_id"],
                        "where": est["current"]["name"] if est["current"] else ("放假中" if est["phase"] == "vacation" else
                                                                                "复习周" if est["phase"] == "review" else "本学段学完"),
                        "check": not e["progress_at"] or e["progress_at"] < confirm_before})
    return out

# 管理后台导航：按「管谁、管什么内容、看运行、改设置」分四组，每个后台页面顶部都用这一份（_admin.html）。
ADMIN_NAV = [
    ("用户", [("overview", "概览", "/admin"), ("families", "家庭", "/admin?tab=families"), ("selfs", "自学者", "/admin?tab=selfs"),
             ("invites", "邀请码", "/admin?tab=invites"), ("log", "安全日志", "/admin?tab=log")]),
    ("内容", [("curricula", "教材与知识点", "/admin/curricula"), ("bank", "题库", "/admin?tab=bank"), ("papers", "卷库", "/admin?tab=papers"),
             ("library", "书库", "/admin/library"), ("news", "每日新闻", "/admin/news")]),
    ("运行", [("stats", "数据统计", "/admin?tab=stats"), ("arena", "游戏公平", "/admin?tab=arena"),
             ("operations", "后台任务与 AI", "/admin/operations")]),
    ("设置", [("system", "站点设置", "/admin?tab=system")]),
]
ADMIN_TABS = {key for _, items in ADMIN_NAV for key, _, url in items if url.startswith("/admin?") or url == "/admin"}
templates.env.globals["ADMIN_NAV"] = ADMIN_NAV

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

def _admin_back(tab="overview", msg="", link="", uid=None):
    from urllib.parse import urlencode
    if tab == "user" and uid:
        return RedirectResponse(f"/admin/users/{uid}?" + urlencode({"msg": msg, "link": link}), 303)
    return RedirectResponse("/admin?" + urlencode({"tab": tab, "msg": msg, "link": link}), 303)

def _admin_user_page(request: Request, a, u, msg="", link="", **extra):
    parent = auth.get_user(u["parent_id"]) if u["parent_id"] else None
    kids = db.q("SELECT * FROM users WHERE parent_id=? AND role='kid' ORDER BY id", u["id"])
    inviter = auth.get_user(u["invited_by"]) if u["invited_by"] else None
    events = db.q("SELECT * FROM auth_events WHERE user_id=? ORDER BY id DESC LIMIT 12", u["id"])
    families = db.q("SELECT id, name, email FROM users WHERE role='parent' AND status='active' ORDER BY name, id") if u["role"] == "kid" else []
    return render(request, "admin_user.html", u=u, me=a, parent=parent, kids=kids, inviter=inviter, events=events, families=families,
                  msg=msg, link=link, **account_ctx(u, f"/admin/users/{u['id']}/account", force=u["role"] != "kid",
                                                     reset_link=True), **extra)

def _kid_form_packs(form, grade: str, old: dict, grade_changed: bool) -> list[tuple[str, str, str]]:
    """每门课选一个教材版本（有方向的再选方向）；学段按年级自动对应。
    年级没变、教材没换时保留原学段（「学校进度」里可能已经往后调过）。"""
    out = []
    for subj in {p.subject for p in catalog.packs.values()}:
        pid = form.get(f"subj_{subj}") or ""
        if pid in catalog.packs and catalog.packs[pid].subject == subj:
            keep = pid in old and not grade_changed
            out.append((pid, old[pid] if keep else catalog.default_stage(pid, grade),
                        catalog.packs[pid].track(form.get(f"track_{pid}"))))
    return out

def course_choices(school_type='', grade=None):
    order = {subject: i for i, subject in enumerate(('chinese', 'math', 'english', 'science', 'physics', 'chemistry', 'biology', 'history', 'geography', 'economics'))}
    return dict(sorted(catalog.by_subject(school_type).items(), key=lambda kv:
                       (0 if is_adult(grade) and any(is_adult(p.stages[0]) for p in kv[1] if p.stages) else 1,
                        order.get(kv[1][0].subject, 99))))


def _kid_form(request: Request, k, self_mode=False, **extra):
    """家长编辑孩子，和自学者编辑「我的课程」共用一张表单（self_mode：不显示邮箱密码、游戏、学校这些家长管的项）。"""
    es = enrollments(k["id"])
    enrolled = {e["pack_id"]: e["stage"] for e in es}
    acct = {} if self_mode else account_ctx(k, f"/parent/kids/{k['id']}/account", force=False)
    by_subject = course_choices(k["school_type"] or "", k["grade"])
    return render(request, "kid_form.html", k=k, enrolled=enrolled, tracks={e["pack_id"]: e["track"] for e in es},
                  exams={e["pack_id"]: e["exam_date"] for e in es}, self_mode=self_mode,
                  presets=catalog.presets, packs_by_subject=by_subject, **acct, **extra)

def set_method(kid_id: int, method: str) -> bool:
    """给孩子换学习方式：存进 users.settings，并按已有的学习记录重算掌握状态。"""
    if method not in {m.id for m in methods.choices()}:
        return False
    before = evidence.profile(kid_id).key
    u = db.one("SELECT settings FROM users WHERE id=?", kid_id)
    st = db.jload(u["settings"], {}) if u else {}
    if st.get("method") == method or (not st.get("method") and method == methods.site_default()):
        return False  # 没改；用全站默认的孩子不固定下来，管理员改默认时跟着变
    st["method"] = method
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), kid_id)
    if evidence.profile(kid_id).key != before:
        evidence.replay(kid_id)
    engine.today_plan(kid_id, rebuild=True)
    return True

def _check_exam_dates(form) -> None:
    for key in form.keys():
        if key.startswith("exam_") and (form.get(key) or "").strip():
            try:
                date.fromisoformat(form.get(key).strip())
            except ValueError:
                raise HTTPException(400, "考试日期格式不对")

def _save_learning(kid_id: int, form, grade: str, old_grade, by_parent: bool = True) -> None:
    """保存学习安排：学习方式、游戏、每门课的教材 / 方向 / 考试日期、学校模板。家长编辑孩子和「我的课程」（自己安排学习的人）共用；
    隐私模式、城市、学校模板只在家长的表单里。"""
    set_method(kid_id, form.get("method") or "")
    if form.get("game_minutes") is not None:
        arena.set_game_minutes(kid_id, form.get("game_minutes"))
    if form.get("game_unlock"):
        arena.set_game_unlock(kid_id, form.get("game_unlock"))
    if form.get("game_minutes") is not None:
        nick = (form.get("nickname") or "").strip()
        k = db.one("SELECT * FROM users WHERE id=?", kid_id)
        if nick and nick != (k["nickname"] or ""):
            try:
                auth.set_nickname(dict(k), nick)
            except ValueError as e:
                raise HTTPException(400, str(e))
    if by_parent and form.get("game_minutes") is not None:
        auth.set_private(kid_id, bool(form.get("private")))
        if k["parent_id"] and form.get("province") is not None:
            boards.set_city(k["parent_id"], form.get("province") or "", form.get("city") or "")
    old = {e["pack_id"]: e["stage"] for e in enrollments(kid_id)}
    chosen = _kid_form_packs(form, grade, old, grade != old_grade)
    db.run("UPDATE enrollments SET active=0 WHERE user_id=?", kid_id)
    for pid, stage, track in chosen:
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active,track) VALUES(?,?,?,1,?) "
               "ON CONFLICT(user_id,pack_id) DO UPDATE SET stage=excluded.stage, active=1, track=excluded.track",
               kid_id, pid, stage, track)
        if f"exam_{pid}" in form:  # 考试日期：学习者（或家长）自己定，系统按剩下的天数排计划
            engine.set_exam_date(kid_id, pid, form.get(f"exam_{pid}"))
        engine.seed_vocab(kid_id, pid, config.SEED_DIR)
    if by_parent:  # 学校模板只在家长的表单里
        preset = catalog.preset(form.get("preset") or "")
        db.run("UPDATE users SET preset=?, school_type=? WHERE id=?", preset["id"] if preset else "",
               preset["school_type"] if preset else "", kid_id)
    engine.today_plan(kid_id, rebuild=True)

def require_self_managed(request: Request):
    """「我的课程」：自己安排学习的人（自学者、家里的成人成员）。"""
    u = auth.require_user(request)
    if not auth.self_managed(u):
        raise HTTPException(403, "孩子的课程由家长在家长页安排" if u["role"] == "kid" else "「我的课程」是自学账号用的")
    return u

def _public_item(it: dict) -> dict:
    out = {k: v for k, v in itemtypes.public(it).items() if k not in ("tol", "source")}
    if itemtypes.of(it).self_rated and coach.on(it, it.get("kp_id", "")):
        out["coach"] = True  # 开放题由 AI 点评和追问，不让学员自评（app/coach.py）
    return out

_answer_display = engine.answer_display

def _trace_or_404(k, tid: int):
    t = trace.get(k["id"], tid)
    if not t:
        raise HTTPException(404, "没有这次追根")
    return t

def _paper_or_404(k, paper_id: int):
    p = papers.get(k["id"], paper_id)
    if not p:
        raise HTTPException(404, "没有这份试卷")
    return p

async def _read_photos(f) -> list:
    """上传的试卷照片 → [(media_type, bytes)]，顺便检查格式、大小、张数。"""
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
        try:  # 按拍照方向摆正、太大的缩小：AI 报告的图的位置和以后裁图都按摆正后的照片算
            images.append(photo.prepare_page(up.content_type, data))
        except ValueError as e:
            raise HTTPException(400, str(e))
    if len(images) > papers.MAX_IMAGES:
        raise HTTPException(400, f"一次最多 {papers.MAX_IMAGES} 张照片")
    return images

def _fig_body(body: dict) -> tuple[int, list | None]:
    if body.get("remove"):
        return 0, None
    return int(body.get("page") or 1), body.get("box")

def _ask_item(k, item_id: str):
    """孩子能看到的题：公共题库的题，或自己试卷里的题。"""
    row = db.one("SELECT * FROM items WHERE id=?", item_id) if item_id else None
    if not row or row["status"] == "draft":
        return None
    if row["source"] == "paper" and not db.one(
            "SELECT 1 AS ok FROM paper_items i JOIN papers p ON p.id=i.paper_id WHERE i.item_id=? AND p.user_id=?", item_id, k["id"]):
        return None
    return engine._item_row_to_dict(row)

def _review_tasks_done(kid_id: int):
    for group, task in (("words", "words"), ("mistakes", "mistakes"), ("other", "review")):
        if not engine.due_cards(kid_id, 1, group):
            engine.mark_task_by(kid_id, type=task)

def _today_reading(uid: int, lang: str, ai_only=False):
    """今天已经开始的那篇（没读完的优先）：每日阅读进来先看它，不再一进来就生成新的。"""
    return db.one("SELECT * FROM readings WHERE user_id=? AND lang=? AND created_at>=?" + (" AND source='ai'" if ai_only else "") +
                  " ORDER BY CASE WHEN finished_at IS NULL THEN 0 ELSE 1 END, id DESC LIMIT 1", uid, lang, db.today().isoformat())

def _reading_view(q: dict, mine) -> dict:
    """阅读小题也是选择题：用统一的题目展示结构（itemtypes.view）。"""
    return itemtypes.view({"type": "mcq", "q": q.get("q", ""), "options": q.get("options", []), "answer": q.get("answer"),
                           "explain": q.get("explain", "")}, mine)

def _has_card(uid: int, front: str) -> bool:
    return bool(db.one("SELECT id FROM cards WHERE user_id=? AND LOWER(front)=? AND kind IN ('word','phrase','term')",
                       uid, front.strip().lower()))

def _auto_collect(uid: int, query: str, meaning: str, extra: dict, *, in_question: bool, front: str = "") -> str:
    """根据孩子的行为判断不熟：同一个词 / 句子查了第二次，或做题时查的，就自动放进复习（明天开始）。
    返回原因（空串表示没加）。"""
    front = front or query
    if not meaning or _has_card(uid, front):
        return ""
    n = db.one("SELECT COUNT(*) AS n FROM lookups WHERE user_id=? AND LOWER(query)=?", uid, query.strip().lower())["n"]
    why = "做题时查的" if in_question else ("查了 %d 次" % n if n >= 2 else "")
    if why:
        kind = "phrase" if (" " in front.strip() or len(front) > 12) else "word"
        engine.add_card(uid, kind, front, meaning, {**extra, "source": "自动", "why": why})
    return why

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
            "cal": engine.calendar(k["id"], 12, full_weeks=True), "streak": engine.streak(k["id"]), "days": days, "lookups": lookups,
            "reads": db.q("SELECT * FROM reading_logs WHERE user_id=? ORDER BY id DESC LIMIT 30", k["id"]),
            "found": insights.open_insights(k["id"]), "solved": insights.resolved_recent(k["id"]),
            "asks": db.q("SELECT t.id, t.page, t.created_at, m.text FROM ask_threads t JOIN ask_messages m ON m.thread_id=t.id "
                         "AND m.role='user' WHERE t.user_id=? ORDER BY m.id DESC LIMIT 20", k["id"]),
            "papers": [{**dict(p), "rep": db.jload(p["report"], {})} for p in
                       db.q("SELECT * FROM papers WHERE user_id=? ORDER BY id DESC LIMIT 20", k["id"])],
            "tot": tot, "trend": records.accuracy_trend(k["id"]), "mistakes": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='mistake'", k["id"])["n"],
            "words": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind='word'", k["id"])["n"]}

def _lang_of(text: str) -> str:
    return "zh" if re.search(r"[一-鿿]", text or "") else "en"

def _tools_render(request: Request, kid: int, token: str = ""):
    u = auth.require_user(request)
    target = u
    kids = [k for k in db.q("SELECT * FROM users WHERE parent_id=? ORDER BY id", u["id"])
            if not auth.self_managed(k)] if u["role"] == "parent" else []
    if u["role"] == "parent":
        kid = kid or request.session.get("as_kid") or (kids[0]["id"] if kids else 0)
        target = auth.kid_of(u, kid) if kid else None
    return render(request, "tools.html", target=target, kids=kids, token=token,
                  tokens=auth.api_tokens_of(target["id"]) if target else [],
                  server=config.PUBLIC_URL or str(request.base_url).rstrip("/"))

def _bp_or_404(bp_id: int):
    bp = bankpapers.get(bp_id)
    if not bp:
        raise HTTPException(404, "没有这份卷子")
    return bp

def _bp_back(bp_id: int, msg="", anchor=""):
    from urllib.parse import urlencode
    return RedirectResponse(f"/admin/bank-papers/{bp_id}?" + urlencode({"msg": msg}) + (f"#{anchor}" if anchor else ""), 303)

