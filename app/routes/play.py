"""play HTTP routes. Learning commands live in app.learning."""
import json
import time

from fastapi import APIRouter
from fastapi import Body, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool
from .. import arena, auth, db, engine, sprint, streak
from ..arena.mp import bus as mp_bus, rooms
from ..web import (
    kid_or_redirect,
    render,
)

router = APIRouter()

@router.get("/arena", response_class=HTMLResponse)
def arena_home(request: Request):
    k = kid_or_redirect(request)
    return render(request, "arena.html", a=arena.hub(k), invites=rooms.invites(k))


@router.get("/arena/{game}", response_class=HTMLResponse)
def arena_game(request: Request, game: str):
    k = kid_or_redirect(request)
    if game not in arena.GAMES:
        raise HTTPException(404)
    if arena.GAMES[game].get("multi"):
        return render(request, "arena_mp.html", g=arena.GAMES[game], game=game, a=arena.hub(k), lobby=rooms.lobby(k, game),
                      me=rooms.member(k))
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


# ------------------------------------------------------------------ 联网游戏（app/arena/mp/）

@router.get("/api/mp/{game}/lobby")
def mp_lobby(request: Request, game: str):
    return rooms.lobby(kid_or_redirect(request), game)


@router.post("/api/mp/rooms")
def mp_create(request: Request, body: dict = Body(...)):
    return rooms.create_room(kid_or_redirect(request), str(body.get("game") or ""), str(body.get("role") or ""))


@router.get("/api/mp/rooms/{room_id}")
def mp_room(request: Request, room_id: int):
    return rooms.room_info(kid_or_redirect(request), room_id)


@router.post("/api/mp/rooms/{room_id}/join")
def mp_join(request: Request, room_id: int):
    return rooms.join(kid_or_redirect(request), room_id)


@router.post("/api/mp/rooms/{room_id}/leave")
def mp_leave(request: Request, room_id: int):
    rooms.leave(kid_or_redirect(request), room_id)
    return {"ok": True}


@router.post("/api/mp/rooms/{room_id}/role")
def mp_role(request: Request, room_id: int, body: dict = Body(...)):
    return rooms.set_role(kid_or_redirect(request), room_id, str(body.get("role") or ""))


@router.post("/api/mp/rooms/{room_id}/level")
def mp_level(request: Request, room_id: int, body: dict = Body(...)):
    return rooms.set_level(kid_or_redirect(request), room_id, body.get("n"))


@router.post("/api/mp/rooms/{room_id}/finish")
def mp_finish(request: Request, room_id: int, body: dict = Body(...)):
    return rooms.finish(kid_or_redirect(request), room_id, body.get("n"), body.get("report") or {})


def _ws_user(ws: WebSocket, room_id: int):
    u = auth.current_user(ws)   # WebSocket 和 Request 一样带 session（SessionMiddleware 两种都管）
    if not u or u["role"] != "kid":
        return None, None
    try:
        r = rooms.member_room(dict(u), room_id)
    except arena.ArenaError:
        return None, None
    rooms.touch(r["id"])
    return dict(u), r


@router.websocket("/ws/mp/{room_id}")
async def mp_ws(ws: WebSocket, room_id: int):
    """房间里的实时消息：服务器只转发（盖上发送人 from），不看内容；每秒最多 60 条，每条最多 2000 字节。
    进来时告诉别人 hello，断开时告诉别人 bye。页面开着时每 20 秒记一次房间还有人（大厅只显示有人的房间）。"""
    u, r = await run_in_threadpool(_ws_user, ws, room_id)
    if not u:
        await ws.close(code=4403)
        return
    await ws.accept()
    b = mp_bus.bus()
    conn = mp_bus.Conn(ws, r["id"], u["id"])
    await b.join(conn)
    await b.publish(r["id"], conn.id, json.dumps({"t": "hello", "from": u["id"]}))
    last_touch = window = time.monotonic()
    count = 0
    try:
        while True:
            text = await ws.receive_text()
            now = time.monotonic()
            if now - window > 1:
                window, count = now, 0
            count += 1
            if count > 60 or len(text) > mp_bus.MAX_MSG:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if not isinstance(msg, dict) or not isinstance(msg.get("t"), str) or len(msg["t"]) > 12:
                continue
            if now - last_touch > 20:
                last_touch = now
                await run_in_threadpool(rooms.touch, r["id"])
            if msg["t"] == "ping":
                continue
            msg["from"] = u["id"]
            await b.publish(r["id"], conn.id, json.dumps(msg, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    finally:
        await b.leave(conn)
        try:
            await b.publish(r["id"], conn.id, json.dumps({"t": "bye", "from": u["id"]}))
        except Exception:
            pass


@router.get("/sprint", response_class=HTMLResponse)
def sprint_page(request: Request, drill: int = 0):
    k = kid_or_redirect(request)
    ok, left = sprint.is_open(k)
    return render(request, "sprint.html", open=ok or bool(drill), left=left, drill=bool(drill), drill_min=sprint.DRILL_S // 60, today_points=sprint.points_today(k["id"]),
                  best=sprint.best_day(k["id"], db.today().isoformat()), tier_at=sprint.TIER_AT,
                  goal=streak.today_state(k, engine.today_plan(k["id"])["plan"], 0, sprint.points_today(k["id"])))


@router.post("/api/sprint/start")
def sprint_start(request: Request, body: dict = Body(default={})):
    return sprint.start(kid_or_redirect(request), drill=bool((body or {}).get("drill")))


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


