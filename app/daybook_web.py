"""我的日记的页面（app/daybook.py）：只有本人能打开；家长只能看孩子自己点了分享的那几天。"""
from datetime import date

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from . import auth, daybook, db, growth

router = APIRouter()


def _render(request, name, **ctx):
    from . import web
    return web.render(request, name, private=True, **ctx)   # private：不出现「问小助手」、划词菜单，不计学习时长


def _me(request: Request):
    u = auth.require_user(request)
    if u["role"] != "kid":
        raise HTTPException(403, "日记是每个人自己的本子，只有本人登录才能打开。")
    return u


def _day(day: str) -> str:
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(404, "没有这一天")
    if d > db.today():
        raise HTTPException(404, "这一天还没到")
    return d.isoformat()


@router.get("/daybook", response_class=HTMLResponse)
def daybook_home(request: Request, m: str = ""):
    u = _me(request)
    bk = daybook.book(u["id"])
    today = daybook.page(u["id"], db.today().isoformat())
    return _render(request, "daybook.html", page=today, today=db.today().isoformat(), label=daybook.day_label(db.today().isoformat()),
                   moods=daybook.MOODS, home=daybook.home_city(u), prompt=daybook.prompt(u["id"], growth.teen_of(u)),
                   back=daybook.lookback(u["id"]), cal=daybook.month(u["id"], m or db.today().isoformat()[:7]),
                   book=bk, cover=daybook.cover_of(u, bk), low=daybook.low_streak(u["id"]), has_parent=bool(u["parent_id"]),
                   HOTLINE=daybook.HOTLINE)


@router.get("/daybook/day/{day}", response_class=HTMLResponse)
def daybook_day(request: Request, day: str):
    u = _me(request)
    day = _day(day)
    if day == db.today().isoformat():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/daybook", 303)
    return _render(request, "daybook_day.html", page=daybook.page(u["id"], day), day=day, label=daybook.day_label(day),
                   has_parent=bool(u["parent_id"]), HOTLINE=daybook.HOTLINE)


@router.post("/api/daybook/mood")
def api_mood(request: Request, body: dict = Body(...)):
    return daybook.set_mood(_me(request), str(body.get("mood") or ""))


@router.post("/api/daybook/note")
def api_note(request: Request, body: dict = Body(...)):
    return daybook.add_note(_me(request), str(body.get("text") or ""))


@router.post("/api/daybook/voice")
async def api_voice(request: Request, file: UploadFile = File(...), secs: int = Form(0)):
    u = _me(request)
    data = await file.read(daybook.VOICE_MAX + 1)
    return daybook.add_voice(u, data, file.content_type or "", secs)


@router.post("/api/daybook/delete")
def api_delete(request: Request, body: dict = Body(...)):
    u = _me(request)
    daybook.delete_note(u["id"], _day(str(body.get("day") or "")), str(body.get("id") or ""))
    return {"ok": True}


@router.post("/api/daybook/share")
def api_share(request: Request, body: dict = Body(...)):
    u = _me(request)
    if not u["parent_id"]:
        raise HTTPException(400, "没有可以分享的家长")
    return {"shared": daybook.share(u["id"], _day(str(body.get("day") or "")), bool(body.get("on")))}


@router.post("/api/daybook/city")
def api_city(request: Request, body: dict = Body(...)):
    """今天在哪：给城市名直接用；给坐标（「我今天不在家」）就换成城市名，坐标不存。"""
    u = _me(request)
    city = str(body.get("city") or "")
    if not city and body.get("lat") is not None:
        try:
            city = daybook.city_from_point(float(body["lat"]), float(body["lon"]))
        except (TypeError, ValueError, KeyError):
            raise HTTPException(400, "没拿到位置")
    return daybook.set_city(u, city)


@router.get("/api/daybook/sky")
def api_sky(request: Request):
    """今天的天气（页面打开后再查，不让页面等）。"""
    u = _me(request)
    p = daybook.page(u["id"], db.today().isoformat())
    city = (p or {}).get("city") or daybook.home_city(u)
    return {"city": city, "sky": daybook.sky(city, db.today().isoformat())}


@router.post("/api/daybook/cover")
def api_cover(request: Request, body: dict = Body(...)):
    u = _me(request)
    daybook.set_cover(u["id"], str(body.get("cover") or ""))
    return {"ok": True}


@router.get("/daybook/voice/{vid}")
def voice(request: Request, vid: str):
    u = _me(request)
    data, mime = daybook.voice(u["id"], vid)
    return Response(data, media_type=mime, headers={"Cache-Control": "private, no-store"})


# ------------------------------------------------------------------ 家长：只看孩子自己分享的那几天

@router.get("/parent/kids/{kid_id}/daybook", response_class=HTMLResponse)
def parent_shared(request: Request, kid_id: int):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id, report=True)
    return _render(request, "daybook_shared.html", k=k, pages=daybook.shared_pages(k["id"]))


@router.get("/parent/kids/{kid_id}/daybook/{day}/voice/{vid}")
def parent_voice(request: Request, kid_id: int, day: str, vid: str):
    p = auth.require_parent(request)
    k = auth.kid_of(p, kid_id, report=True)
    if not daybook.is_shared(k["id"], day):
        raise HTTPException(404, "没有这段录音")
    data, mime = daybook.voice(k["id"], vid, day)
    return Response(data, media_type=mime, headers={"Cache-Control": "private, no-store"})
