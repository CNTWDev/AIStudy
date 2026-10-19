"""一口气（app/focus.py）：开始 / 接着、同步、记一次离开、结束。只有学习者自己的账号算，家长查看不算。
同步和离开也接受 sendBeacon（关页面、切走时发）。"""
from fastapi import APIRouter, Request

from .. import auth, focus

router = APIRouter()


async def _learner(request: Request):
    user = auth.current_user(request)
    if not user or user["role"] != "kid":
        return None, {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return user, body if isinstance(body, dict) else {}


@router.post("/api/focus/start")
async def focus_start(request: Request):
    user, body = await _learner(request)
    return focus.start(user, body.get("resume")) if user else {}


@router.post("/api/focus/sync")
async def focus_sync(request: Request):
    user, body = await _learner(request)
    return {"ok": bool(user and focus.sync(user, body))}


@router.post("/api/focus/leave")
async def focus_leave(request: Request):
    user, body = await _learner(request)
    if user:
        focus.leave(user, body)
    return {"ok": bool(user)}


@router.post("/api/focus/end")
async def focus_end(request: Request):
    user, body = await _learner(request)
    return (focus.end(user, body) or {}) if user else {}
