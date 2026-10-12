"""play HTTP routes. Learning commands live in app.learning."""
from fastapi import APIRouter
from fastapi import Body, HTTPException, Request
from fastapi.responses import HTMLResponse
from .. import arena, db, engine, sprint, streak
from ..web import (
    kid_or_redirect,
    render,
)

router = APIRouter()

@router.get("/arena", response_class=HTMLResponse)
def arena_home(request: Request):
    k = kid_or_redirect(request)
    return render(request, "arena.html", a=arena.hub(k))


@router.get("/arena/{game}", response_class=HTMLResponse)
def arena_game(request: Request, game: str):
    k = kid_or_redirect(request)
    if game not in arena.GAMES:
        raise HTTPException(404)
    return render(request, "arena_game.html", g=arena.GAMES[game], game=game, a=arena.hub(k))


@router.post("/api/arena/start")
def arena_start(request: Request, body: dict = Body(...)):
    return arena.start(kid_or_redirect(request), str(body.get("game") or ""), str(body.get("src") or "mix"), body.get("stage"))


@router.post("/api/arena/avatar")
def arena_avatar(request: Request, body: dict = Body(...)):
    if not arena.set_avatar(kid_or_redirect(request)["id"], str(body.get("avatar") or "")):
        raise HTTPException(400, "这个角色还没解锁")
    return {"ok": True}


@router.get("/api/arena/{match_id}/q")
def arena_question(request: Request, match_id: int):
    return arena.question(kid_or_redirect(request), match_id)


@router.post("/api/arena/{match_id}/a")
def arena_answer(request: Request, match_id: int, body: dict = Body(...)):
    return arena.answer(kid_or_redirect(request), match_id, str(body.get("item_id") or ""), body.get("answer"),
                        dont_know=bool(body.get("dont_know")))


@router.post("/api/arena/{match_id}/end")
def arena_end(request: Request, match_id: int, body: dict = Body(...)):
    return arena.end(kid_or_redirect(request), match_id, str(body.get("result") or ""), body.get("stats") or {})


@router.get("/sprint", response_class=HTMLResponse)
def sprint_page(request: Request):
    k = kid_or_redirect(request)
    ok, left = sprint.is_open(k)
    return render(request, "sprint.html", open=ok, left=left, today_points=sprint.points_today(k["id"]),
                  best=sprint.best_day(k["id"], db.today().isoformat()), tier_at=sprint.TIER_AT,
                  goal=streak.today_state(k, engine.today_plan(k["id"])["plan"], 0, sprint.points_today(k["id"])))


@router.post("/api/sprint/start")
def sprint_start(request: Request):
    return sprint.start(kid_or_redirect(request))


@router.get("/api/sprint/{run_id}/q")
def sprint_question(request: Request, run_id: int):
    return sprint.question(kid_or_redirect(request), run_id)


@router.post("/api/sprint/{run_id}/a")
def sprint_answer(request: Request, run_id: int, body: dict = Body(...)):
    return sprint.answer(kid_or_redirect(request), run_id, str(body.get("item_id") or ""), body.get("answer"),
                         dont_know=bool(body.get("dont_know")))


@router.post("/api/sprint/{run_id}/end")
def sprint_end(request: Request, run_id: int):
    return sprint.end(kid_or_redirect(request), run_id)


@router.post("/api/goal")
def goal_set(request: Request, body: dict = Body(...)):
    if not streak.set_goal(kid_or_redirect(request)["id"], str(body.get("goal") or "")):
        raise HTTPException(400, "没有这个目标")
    return {"ok": True}


