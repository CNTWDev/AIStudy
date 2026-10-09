"""书库（内置公版名著）的页面和接口：书架、书的首页（章节地图）、按页读 / 听、每章导读、管理员下载原文。

和每日计划的关系：家长在「阅读与单词」里给孩子选书库的书（tracks.ref = "lib:<书 id>"，按页算），
孩子在网站上每读完一页就自动记进度；读够今天的页数 / 听够今天的分钟，今天的任务自动打勾。
"""
from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, bank, db, engine, llm
from .library import FetchError, library

router = APIRouter()
LIB = "lib:"


def _main():
    from . import main
    return main


def book_or_404(bid: str) -> dict:
    b = library.books.get(bid)
    if not b:
        raise HTTPException(404, "书单里没有这本书")
    return b


def progress(uid: int, bid: str) -> dict:
    r = db.one("SELECT * FROM book_progress WHERE user_id=? AND book_id=?", uid, bid)
    return {**dict(r), "chapters_done": db.jload(r["chapters_done"], [])} if r else \
        {"page": 0, "listen_page": 0, "pages_read": 0, "listen_seconds": 0, "chapters_done": [], "finished_at": None}


def _ensure(uid: int, bid: str):
    if not db.one("SELECT 1 AS x FROM book_progress WHERE user_id=? AND book_id=?", uid, bid):
        db.run("INSERT INTO book_progress(user_id,book_id,started_at,updated_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
               uid, bid, db.now(), db.now())
    day = db.today().isoformat()
    if not db.one("SELECT 1 AS x FROM book_days WHERE user_id=? AND day=? AND book_id=?", uid, day, bid):
        db.run("INSERT INTO book_days(user_id,day,book_id) VALUES(?,?,?) ON CONFLICT DO NOTHING", uid, day, bid)
    return day


def today_of(uid: int, bid: str | None = None) -> dict:
    day = db.today().isoformat()
    sql = "SELECT COALESCE(SUM(pages),0) AS pages, COALESCE(SUM(listen_seconds),0) AS s FROM book_days WHERE user_id=? AND day=?"
    r = db.one(sql + (" AND book_id=?" if bid else ""), uid, day, *([bid] if bid else []))
    return {"pages": int(r["pages"]), "listen_seconds": int(r["s"])}


def track_for(uid: int, bid: str, kind: str | None = None):
    rows = [t for t in engine.tracks(uid) if t["ref"] == LIB + bid and (kind is None or t["kind"] == kind)]
    return rows[0] if rows else None


def _check_goals(uid: int, bid: str):
    """读够今天的页数、听够今天的分钟：今天的任务自动打勾。"""
    t = today_of(uid, bid)
    for tr in engine.tracks(uid):
        if tr["ref"] != LIB + bid:
            continue
        if tr["kind"] == "listen":
            if t["listen_seconds"] >= tr["daily_minutes"] * 60:
                engine.mark_task_by(uid, type="listen")
        elif t["pages"] >= max(1, tr["daily_amount"]):
            engine.mark_task_by(uid, type=tr["kind"])


# ================================================================== 孩子：书架、书、读

@router.get("/books", response_class=HTMLResponse)
def shelf(request: Request, lang: str = ""):
    m = _main()
    k = m.kid_or_redirect(request, manage=True)
    lang = lang if lang in ("en", "zh") else ""
    reading = []
    for r in db.q("SELECT * FROM book_progress WHERE user_id=? ORDER BY updated_at DESC", k["id"]):
        b, t = library.books.get(r["book_id"]), library.text(r["book_id"])
        if b and t:
            reading.append({**b, "p": dict(r), "n_pages": t["n_pages"]})
    books = library.shelf(lang)
    grade = k["grade"] or ""
    for b in books:
        b["for_me"] = grade in (b.get("grades") or [])
    books.sort(key=lambda b: (b["lang"] != "en" if lang != "zh" else b["lang"] != "zh", b.get("level", ""), not b["for_me"]))
    return m.render(request, "books.html", books=books, reading=reading, lang=lang, levels=library.levels,
                    not_pd=library.not_pd, missing=[b for b in library.books.values() if not library.ready(b["id"])],
                    tracks={t["ref"][len(LIB):]: t for t in engine.tracks(k["id"]) if t["ref"].startswith(LIB)})


@router.get("/books/{bid}", response_class=HTMLResponse)
def book_home(request: Request, bid: str):
    m = _main()
    k = m.kid_or_redirect(request, manage=True)
    b = book_or_404(bid)
    t = library.text(bid)
    if not t:
        return m.render(request, "message.html", title=b["title"], text="这本书的原文还没下载好，请家长或管理员在「管理 → 书库」里下载。",
                        status_code=404)
    p = progress(k["id"], bid)
    chs, start = [], 1
    for i, c in enumerate(t["chapters"], 1):
        chs.append({"n": i, "title": c["title"] or f"第 {i} 部分", "start": start, "pages": len(c["pages"]),
                    "done": start + len(c["pages"]) - 1 <= p["page"], "star": i in p["chapters_done"]})
        start += len(c["pages"])
    return m.render(request, "book.html", b=b, t=t, p=p, chs=chs, today=today_of(k["id"], bid),
                    track=track_for(k["id"], bid), ltrack=track_for(k["id"], bid, "listen"))


@router.get("/books/{bid}/p/{n}", response_class=HTMLResponse)
def read_page(request: Request, bid: str, n: int, listen: int = 0):
    m = _main()
    k = m.kid_or_redirect(request, manage=True)
    b = book_or_404(bid)
    pg = library.page(bid, n)
    if not pg:
        if library.ready(bid):
            return RedirectResponse(f"/books/{bid}", 303)
        raise HTTPException(404)
    p = progress(k["id"], bid)
    tr = track_for(k["id"], bid)
    goal = max(1, tr["daily_amount"]) if tr and tr["kind"] != "listen" else 0
    ltr = track_for(k["id"], bid, "listen")
    return m.render(request, "book_read.html", b=b, pg=pg, p=p, listen=bool(listen), goal=goal,
                    lgoal=ltr["daily_minutes"] if ltr else 0, today=today_of(k["id"], bid))


@router.post("/api/books/{bid}/read")
def api_read(request: Request, bid: str, body: dict = Body(...)):
    """读完一页（孩子翻到下一页时报）。同一页只算一次；进度只往前走。"""
    k = _main().kid_or_redirect(request)
    book_or_404(bid)
    n = int(body.get("page") or 0)
    t = library.text(bid)
    if not t or not 1 <= n <= t["n_pages"]:
        raise HTTPException(400, "页码不对")
    day = _ensure(k["id"], bid)
    p = progress(k["id"], bid)
    new = n > p["page"]
    if new:
        db.run("UPDATE book_progress SET page=?, pages_read=pages_read+1, updated_at=?, finished_at=? WHERE user_id=? AND book_id=?",
               n, db.now(), db.now() if n >= t["n_pages"] else None, k["id"], bid)
        db.run("UPDATE book_days SET pages=pages+1 WHERE user_id=? AND day=? AND book_id=?", k["id"], day, bid)
        for tr in engine.tracks(k["id"]):  # 每日计划里的进度跟着走
            if tr["ref"] == LIB + bid and tr["kind"] != "listen":
                db.run("UPDATE tracks SET position=?, last_day=?, finished_at=? WHERE id=?", n, day,
                       db.now() if n >= t["n_pages"] else None, tr["id"])
        _check_goals(k["id"], bid)
    return {"ok": True, "new": new, "today": today_of(k["id"], bid), "finished": n >= t["n_pages"]}


@router.post("/api/books/{bid}/listen")
def api_listen(request: Request, bid: str, body: dict = Body(...)):
    """听书时长：播放中每 30 秒报一次（最多算 60 秒，防止重复报）。"""
    k = _main().kid_or_redirect(request)
    book_or_404(bid)
    s = max(0, min(60, int(body.get("seconds") or 0)))
    n = int(body.get("page") or 0)
    day = _ensure(k["id"], bid)
    db.run("UPDATE book_progress SET listen_seconds=listen_seconds+?, listen_page=?, updated_at=? WHERE user_id=? AND book_id=?",
           s, n, db.now(), k["id"], bid)
    db.run("UPDATE book_days SET listen_seconds=listen_seconds+? WHERE user_id=? AND day=? AND book_id=?", s, k["id"], day, bid)
    _check_goals(k["id"], bid)
    return {"ok": True, "today": today_of(k["id"], bid)}


def guide(bid: str, ch: int, grade: str, user_id=None, generate=True) -> dict:
    """某一章的导读：库里有就用（所有孩子共用），没有再让 AI 写一份存下。"""
    topic = f"{bid}#{ch}"
    c = bank.find_content("book_guide", topic=topic)
    if c:
        return c["body"]
    if not generate or not llm.enabled():
        return {}
    b = library.books[bid]
    pg = library.page(bid, library.chapter_start(bid, ch))
    try:
        body = llm.book_guide(b, ch, pg["chapter_title"] if pg else "", library.chapter_text(bid, ch, 6000), grade, user_id=user_id)
    except llm.LLMError:
        return {}
    bank.save_content("book_guide", body, "", b["lang"], "", topic=topic, title=f"{b['title']} · {ch}",
                      meta=bank.gen_meta("book_guide"), created_by=user_id)
    return body


@router.get("/api/books/{bid}/guide/{ch}")
def api_guide(request: Request, bid: str, ch: int):
    k = _main().kid_or_redirect(request, manage=True)
    book_or_404(bid)
    g = guide(bid, ch, k["grade"] or "", k["id"])
    prev = guide(bid, ch - 1, k["grade"] or "", generate=False) if ch > 1 else {}
    return {**g, "recap": prev.get("gist", "")}


@router.post("/api/books/{bid}/quiz/{ch}")
def api_quiz(request: Request, bid: str, ch: int, body: dict = Body(...)):
    """读完一章答小题：答对的话这一章得一枚书签。"""
    k = _main().kid_or_redirect(request)
    book_or_404(bid)
    _ensure(k["id"], bid)
    p = progress(k["id"], bid)
    got = False
    if int(body.get("right") or 0) >= 1 and ch not in p["chapters_done"]:
        db.run("UPDATE book_progress SET chapters_done=? WHERE user_id=? AND book_id=?",
               db.jdump(sorted(p["chapters_done"] + [ch])), k["id"], bid)
        got = True
    if (body.get("summary") or "").strip():
        engine.log_reading(k["id"], None, minutes=0, summary=body["summary"].strip(), title=f"{library.books[bid]['title']} · 第 {ch} 部分")
    return {"ok": True, "bookmark": got, "n": len(progress(k["id"], bid)["chapters_done"])}


# ================================================================== 管理员：下载 / 上传原文

@router.get("/admin/library", response_class=HTMLResponse)
def admin_library(request: Request, msg: str = ""):
    m = _main()
    auth.require_admin(request)
    return m.render(request, "admin_library.html", books=library.status(), not_pd=library.not_pd, msg=msg)


@router.post("/admin/library/fetch")
def admin_fetch(request: Request, bid: str = Form(""), missing: str = Form("")):
    auth.require_admin(request)
    ids = [bid] if bid else [b for b in library.books if not (missing and library.ready(b))]
    library.fetch_background(ids)
    return RedirectResponse("/admin/library?msg=" + f"开始下载 {len(ids)} 本，过一会儿刷新这个页面看结果", 303)


@router.post("/admin/library/upload")
async def admin_upload(request: Request, bid: str = Form(...), file: UploadFile = File(...)):
    auth.require_admin(request)
    raw = (await file.read())[:30_000_000]
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(400, "文件编码看不懂，请存成 UTF-8")
    try:
        d = library.import_text(bid, text, note=f"管理员上传 {file.filename}")
    except (FetchError, ValueError) as e:
        return RedirectResponse(f"/admin/library?msg=上传失败：{e}", 303)
    return RedirectResponse(f"/admin/library?msg=已导入：{len(d['chapters'])} 章 · {d['n_pages']} 页", 303)
