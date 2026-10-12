"""papers HTTP routes. Learning commands live in app.learning."""
from fastapi import APIRouter
from fastapi import Body, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .. import auth, bankflow, bankpapers, db, engine, llm, papers
from ..web import (
    _fig_body,
    _paper_or_404,
    _public_item,
    _read_photos,
    catalog,
    enrollments,
    kid_or_redirect,
    photo,
    render,
)

router = APIRouter()

@router.get("/papers", response_class=HTMLResponse)
def papers_page(request: Request, pack: str = ""):
    k = kid_or_redirect(request, manage=True)
    es = enrollments(k["id"])
    lst = db.q("SELECT p.*, (SELECT COUNT(*) FROM paper_items i WHERE i.paper_id=p.id) AS n FROM papers p "
               "WHERE p.user_id=? ORDER BY p.id DESC", k["id"])
    return render(request, "papers.html", es=[(e, catalog.packs[e["pack_id"]]) for e in es], papers=lst, pack=pack,
                  max_images=papers.MAX_IMAGES, llm_on=llm.enabled(), helped=bankflow.contributed(k["id"]))


@router.post("/api/papers")
async def papers_create(request: Request):
    k = kid_or_redirect(request, manage=True)
    u = auth.current_user(request)
    f = await request.form()
    pack_id = f.get("pack_id") or ""
    if pack_id not in {e["pack_id"] for e in enrollments(k["id"])}:
        raise HTTPException(400, "请选择学科")
    images = await _read_photos(f)
    text = (f.get("text") or "").strip()
    if not images and len(text) < 10:
        raise HTTPException(400, "请拍照上传，或者粘贴题目文字")
    from starlette.concurrency import run_in_threadpool
    pid = await run_in_threadpool(papers.create, k["id"], pack_id, title=(f.get("title") or "").strip(),
                                  exam_date=f.get("exam_date") or "", images=images, text=text, created_by=u["id"],
                                  shared="share_choice" not in f or bool(f.get("shared")))
    engine.today_plan(k["id"], rebuild=True)
    return {"ok": True, "id": pid}


@router.get("/papers/{paper_id}", response_class=HTMLResponse)
def paper_page(request: Request, paper_id: int):
    k = kid_or_redirect(request, manage=True)
    p = _paper_or_404(k, paper_id)
    rs = papers.rows(paper_id)
    pack = catalog.packs.get(p["pack_id"])
    stage = engine.enrollment_stage(k["id"], p["pack_id"]) or k["grade"]
    bp = bankpapers.get(p["bank_paper_id"]) if p["bank_paper_id"] else None
    return render(request, "paper.html", p=p, rows=rs, pack=pack, images=db.jload(p["images"], []), bp_minutes=bp["minutes"] if bp else 0,
                  cands=papers.candidates(p["pack_id"], stage) if pack else [],
                  qs=[{**_public_item(r["item"]), "id": r["id"], "item_id": r["item_id"],
                       "done": bool(r["answered_at"]), "flagged": bool(r["flagged"])} for r in rs])


@router.get("/papers/{paper_id}/img/{name}")
def paper_image(request: Request, paper_id: int, name: str):
    from fastapi.responses import FileResponse
    k = kid_or_redirect(request, manage=True)
    p = _paper_or_404(k, paper_id)
    if name not in db.jload(p["images"], []):
        raise HTTPException(404)
    return FileResponse(papers.PAPER_DIR / str(paper_id) / name)


@router.post("/api/papers/{paper_id}/fig")
def paper_set_fig(request: Request, paper_id: int, body: dict = Body(...)):
    """原卷上的图裁歪了：家长 / 孩子重新框，或者去掉。"""
    k = kid_or_redirect(request, manage=True)
    p = _paper_or_404(k, paper_id)
    if p["bank_paper_id"]:
        raise HTTPException(400, "模拟考的题来自公共卷库，图由管理员核对")
    try:
        papers.set_fig(p, int(body.get("pi") or 0), *_fig_body(body))
    except KeyError:
        raise HTTPException(404)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/fig/{item_id}/{name}")
def figure_file(request: Request, item_id: str, name: str):
    """题目上从原卷裁下来的图。孩子自己卷子上的图只给这个孩子、家长和管理员看。"""
    from fastapi.responses import FileResponse
    u = auth.require_user(request)
    r = db.one("SELECT source, data FROM items WHERE id=?", item_id)
    fig = db.jload(r["data"], {}).get("fig") if r else None
    f = photo.path(name)
    if not fig or fig.get("file") != name or not f or not f.exists():
        raise HTTPException(404)
    if r["source"] == "paper" and not (u["is_admin"] or u["role"] == "admin"):
        owner = db.one("SELECT p.user_id, u.parent_id FROM paper_items pi JOIN papers p ON p.id=pi.paper_id "
                       "JOIN users u ON u.id=p.user_id WHERE pi.item_id=?", item_id)
        if not owner or u["id"] not in (owner["user_id"], owner["parent_id"]):
            raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.post("/api/papers/{paper_id}/answer")
def paper_answer(request: Request, paper_id: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    p = _paper_or_404(k, paper_id)
    return papers.answer(k["id"], p, int(body.get("pi") or 0), body)


@router.post("/api/papers/{paper_id}/kp")
def paper_set_kp(request: Request, paper_id: int, body: dict = Body(...)):
    """家长 / 孩子觉得 AI 对应的知识点不对，手动改。"""
    k = kid_or_redirect(request, manage=True)
    p = _paper_or_404(k, paper_id)
    if p["bank_paper_id"]:
        raise HTTPException(400, "模拟考的题来自公共卷库，知识点由管理员核对")
    kp_id = body.get("kp_id") or None
    if kp_id and kp_id not in catalog.packs[p["pack_id"]].kp_ids:
        raise HTTPException(400, "知识点不在这门课里")
    pi = db.one("SELECT item_id FROM paper_items WHERE id=? AND paper_id=?", int(body.get("pi") or 0), paper_id)
    if not pi:
        raise HTTPException(404)
    with db.tx() as t:
        t.run("UPDATE paper_items SET kp_id=? WHERE id=?", kp_id, int(body["pi"]))
        t.run("UPDATE items SET kp_id=?, kp_ids=? WHERE id=?", kp_id or "", db.jdump([kp_id] if kp_id else []), pi["item_id"])
        t.run("DELETE FROM item_kps WHERE item_id=?", pi["item_id"])
        if kp_id:
            t.run("INSERT INTO item_kps(item_id, kp_id, role) VALUES(?,?,'main')", pi["item_id"], kp_id)
    return {"ok": True}


@router.post("/api/papers/{paper_id}/finish")
def paper_finish(request: Request, paper_id: int):
    k = kid_or_redirect(request)
    p = _paper_or_404(k, paper_id)
    papers.finish(k["id"], p)
    engine.today_plan(k["id"], rebuild=True)
    return {"ok": True, "stars": engine.total_stars(k["id"])}


@router.get("/papers/{paper_id}/report", response_class=HTMLResponse)
def paper_report(request: Request, paper_id: int):
    k = kid_or_redirect(request, manage=True)
    p = _paper_or_404(k, paper_id)
    return render(request, "paper_report.html", p=p, r=papers.report(k["id"], p), pack=catalog.packs.get(p["pack_id"]))


@router.post("/papers/{paper_id}/delete")
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


@router.get("/mocks", response_class=HTMLResponse)
def mocks_page(request: Request, pack: str = ""):
    k = kid_or_redirect(request, manage=True)
    es = enrollments(k["id"])
    lst = bankpapers.for_learner(k["id"], [e for e in es if not pack or e["pack_id"] == pack])
    return render(request, "mocks.html", mocks=lst, pack=pack, packs=[catalog.packs[e["pack_id"]] for e in es])


@router.post("/mocks/{bp_id}/start")
def mock_start(request: Request, bp_id: int):
    k = kid_or_redirect(request)
    if not any(m["bp"]["id"] == bp_id for m in bankpapers.for_learner(k["id"], enrollments(k["id"]))):
        raise HTTPException(404, "没有这套卷子")
    pid = bankpapers.start_mock(k["id"], bp_id)
    engine.today_plan(k["id"], rebuild=True)
    return RedirectResponse(f"/papers/{pid}", 303)


