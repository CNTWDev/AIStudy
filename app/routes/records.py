"""records HTTP routes. Learning commands live in app.learning."""
from fastapi import APIRouter
from fastapi import Body, HTTPException, Request
from fastapi.responses import HTMLResponse
from .. import auth, boards, db, llm, records
from ..web import (
    _records_ctx,
    catalog,
    kid_or_redirect,
    render,
)

router = APIRouter()

@router.get("/api/records/{kind}")
def records_get(request: Request, kind: str):
    k = kid_or_redirect(request)
    if kind not in records.KINDS:
        raise HTTPException(404, "没有这种任务")
    return {"kind": kind, "label": records.KINDS[kind], "pb": records.personal_best(k["id"], kind),
            "ghost": records.ghost(k["id"], kind) if kind in records.SPEED_KINDS else None}


@router.post("/api/run")
def run_save(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    return records.record(k["id"], str(body.get("kind", "")), body.get("n_items"), body.get("n_right"),
                          body.get("ms_active"), body.get("ms_total"), body.get("best_combo"))


@router.get("/records", response_class=HTMLResponse)
def records_page(request: Request):
    k = kid_or_redirect(request, manage=True)
    return render(request, "records.html", **_records_ctx(k), report_for=None)


@router.get("/boards", response_class=HTMLResponse)
def boards_page(request: Request, metric: str = "streak", scope: str = "all", game: str = ""):
    """孩子看自己的名次；家长（选了孩子就看这个孩子的）、管理员也能看榜。"""
    user = auth.require_user(request)
    k = None
    if user["role"] == "kid":
        k = user
    elif user["role"] == "parent" and request.session.get("as_kid"):
        k = db.one("SELECT * FROM users WHERE id=? AND parent_id=?", request.session.get("as_kid"), user["id"])
    return render(request, "boards.html", b=boards.board(dict(k) if k else None, metric, scope, game),
                  parent_id=user["id"] if user["role"] == "parent" else None, viewer_kid=k)


@router.get("/healthz")
def healthz():
    db.one("SELECT 1 AS ok")
    ok, _ = llm.check()
    return {"ok": True, "db": db.DIALECT, "packs": len(catalog.packs), "kps": len(catalog.kps),
            "llm": llm.settings().default if ok else "off"}


