"""play HTTP routes. Learning commands live in app.learning."""
import json
import time

from fastapi import APIRouter
from fastapi import Body, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from .. import arena, auth, boards, db, engine, sprint, streak
from ..arena import bus, rooms
from ..web import (
    kid_or_redirect,
    render,
    templates,
)

router = APIRouter()

@router.get("/arena", response_class=HTMLResponse)
def arena_home(request: Request):
    k = kid_or_redirect(request)
    return render(request, "arena.html", a=arena.hub(k), nick=(k["nickname"] or "").strip(), invites=rooms.invites(k), board_tip=boards.nudge(dict(k), park=True))


@router.get("/arena/{game}", response_class=HTMLResponse)
def arena_game(request: Request, game: str, reward: int = 0, join: int = 0):
    """所有游戏一个页面：外壳 shell.js（单机、联网都是它），玩法 <游戏>.js。
    reward：做完一组抓词送的那一局（app/wordpick.py）：只出那一组的词、不算游戏时间，玩完回抓词。
    join：乐园首页「X 开了房间，在等你」点进来的那个房间：直接加入它（我原来在别的房间就先离开），不回我原来的房间。"""
    from .. import wordpick
    k = kid_or_redirect(request)
    if game not in arena.GAMES:
        raise HTTPException(404)
    g, a, room = arena.GAMES[game], arena.hub(k), rooms.my_room(k, game)
    gift = bool(reward) and g.study and wordpick.reward_ok(k["id"], reward)
    free = arena.free_for(k, g) or gift   # 成人玩学单词的游戏：不受乐园时长限制，从「我的词」进来，返回也回那里
    if free:
        a = {**a, "locked": "", "status": {**a["status"], "unlimited": True}}
    if a["locked"] and not rooms.playing(room):   # 还不能玩：回乐园首页，那里写着为什么、去做任务
        return RedirectResponse("/arena?locked=1", 303)
    return render(request, "arena_game.html", g=g, game=game, a=a, me=rooms.member(k), room=room, back="/words/map" if free else "/arena",
                  reward=reward if gift else 0, join=join if g.multi and join != room else 0,
                  lobby=rooms.lobby(k, game) if g.multi else None, gb=boards.live(dict(k), game))


@router.get("/arena/{game}/board", response_class=HTMLResponse)
def arena_game_board(request: Request, game: str):
    """游戏页上的榜，页面看着的时候每几秒拉一次（play/gboard.js）：只要榜那一段，不要整页。"""
    k = kid_or_redirect(request)
    if game not in arena.GAMES:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "_gboard.html", {"gb": boards.live(dict(k), game)},
                                      headers={"Cache-Control": "no-store"})


@router.post("/api/arena/start")
def arena_start(request: Request, body: dict = Body(...)):
    return arena.start(kid_or_redirect(request), str(body.get("game") or ""), body.get("session"), body.get("resume"), body.get("reward"))


@router.post("/api/arena/nickname")
def arena_nickname(request: Request, body: dict = Body(...)):
    try:
        nick = auth.set_nickname(kid_or_redirect(request), str(body.get("nickname") or ""))
        boards.refresh()   # 榜上马上换成新昵称（别的孩子看到的也是）
        return {"nickname": nick}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/arena/avatar")
def arena_avatar(request: Request, body: dict = Body(...)):
    if not arena.set_avatar(kid_or_redirect(request)["id"], str(body.get("avatar") or "")):
        raise HTTPException(400, "这个角色还没解锁")
    return {"ok": True}


@router.get("/api/arena/{match_id}/q")
def arena_question(request: Request, match_id: int, gate: str = ""):
    return arena.question(kid_or_redirect(request), match_id, gate=gate)


@router.post("/api/arena/{match_id}/a")
def arena_answer(request: Request, match_id: int, body: dict = Body(...)):
    return arena.answer(kid_or_redirect(request), match_id, str(body.get("item_id") or ""), body.get("answer"),
                        dont_know=bool(body.get("dont_know")))


@router.post("/api/arena/{match_id}/pause")
def arena_pause(request: Request, match_id: int, body: dict = Body(...)):
    return arena.pause(kid_or_redirect(request), match_id, bool(body.get("on")))


@router.post("/api/arena/{match_id}/end")
def arena_end(request: Request, match_id: int, body: dict = Body(...)):
    return arena.end(kid_or_redirect(request), match_id, str(body.get("result") or ""), body.get("stats") or {})


# ------------------------------------------------------------------ 房间（单机、联网都是房间，app/arena/rooms.py）
# 改房间的请求都带 device（这个页面的设备号）：不是这个账号现在在玩的那台设备就拒绝（409 moved）。

def _dev(body: dict) -> str:
    return str((body or {}).get("device") or "")


@router.get("/api/games/{game}/lobby")
def game_lobby(request: Request, game: str):
    return rooms.lobby(kid_or_redirect(request), game)


@router.post("/api/rooms")
def room_create(request: Request, body: dict = Body(...)):
    return rooms.create(kid_or_redirect(request), str(body.get("game") or ""), str(body.get("role") or ""), _dev(body),
                        str(body.get("kind") or "family"), str(body.get("pin") or ""))


@router.get("/api/rooms/{room_id}")
def room_get(request: Request, room_id: int, world: int = 0):
    return rooms.room_info(kid_or_redirect(request), room_id, world or None)


@router.post("/api/rooms/{room_id}/enter")
def room_enter(request: Request, room_id: int, body: dict = Body(...)):
    return rooms.enter(kid_or_redirect(request), room_id, _dev(body), str(body.get("pin") or ""))


@router.post("/api/rooms/{room_id}/take")
def room_take(request: Request, room_id: int, body: dict = Body(...)):
    return rooms.take(kid_or_redirect(request), room_id, _dev(body))


@router.post("/api/rooms/{room_id}/leave")
def room_leave(request: Request, room_id: int, body: dict = Body(...)):
    rooms.leave(kid_or_redirect(request), room_id, _dev(body))
    return {"ok": True}


@router.post("/api/rooms/{room_id}/role")
def room_role(request: Request, room_id: int, body: dict = Body(...)):
    return rooms.swap_roles(kid_or_redirect(request), room_id, _dev(body))


@router.post("/api/rooms/{room_id}/level")
def room_level(request: Request, room_id: int, body: dict = Body(...)):
    return rooms.set_level(kid_or_redirect(request), room_id, body.get("n"), _dev(body))


@router.post("/api/rooms/{room_id}/start")
def room_start(request: Request, room_id: int, body: dict = Body(...)):
    return rooms.start(kid_or_redirect(request), room_id, body.get("n"), _dev(body))


@router.post("/api/rooms/{room_id}/save")
async def room_save(request: Request, room_id: int):
    """存档（每 2 秒一次；关页面时用 sendBeacon）。最多 MAX_SAVE 字节。"""
    raw = await request.body()
    if len(raw) > rooms.MAX_SAVE:
        raise HTTPException(413, "存档太大了")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400)
    if not isinstance(body, dict):
        raise HTTPException(400)
    k = await run_in_threadpool(kid_or_redirect, request)
    return await run_in_threadpool(rooms.save, k, room_id, _dev(body), body)


@router.post("/api/rooms/{room_id}/finish")
def room_finish(request: Request, room_id: int, body: dict = Body(...)):
    return rooms.finish(kid_or_redirect(request), room_id, _dev(body), body.get("session"), str(body.get("result") or ""),
                        body.get("report") or {})


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


@router.websocket("/ws/room/{room_id}")
async def room_ws(ws: WebSocket, room_id: int):
    """联网房间里的实时消息：服务器只转发（盖上发送人 from），不看内容；每秒最多 60 条，每条最多 6000 字节（bus.MAX_MSG）。
    进来时告诉别人 hello，断开时告诉别人 bye。页面开着时每 20 秒记一次房间还有人（大厅只显示有人的房间）。
    存档、换设备不走这里（/api/rooms/…）。"""
    u, r = await run_in_threadpool(_ws_user, ws, room_id)
    if not u:
        await ws.close(code=4403)
        return
    await ws.accept()
    b = bus.bus()
    conn = bus.Conn(ws, r["id"], u["id"])
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
            if count > 60 or len(text) > bus.MAX_MSG:
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


