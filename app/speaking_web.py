"""口语「开口练」的页面和接口（业务在 app/speaking.py）。只有本人能练、能回听自己的录音。"""
from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import auth, speaking as sp

router = APIRouter()


def _render(request, name, **ctx):
    from . import web
    return web.render(request, name, **ctx)


def _me(request: Request):
    u = auth.require_user(request)
    if u["role"] != "kid":
        raise HTTPException(403, "口语练习要用学习者自己的账号登录。")
    if not sp.available(u):
        raise HTTPException(403, "口语练习现在先给成人学习者用，孩子的口语课程包以后开放。")
    return u


async def _wav(file: UploadFile) -> bytes:
    data = await file.read(sp.CLIP_MAX + 1)
    if len(data) > sp.CLIP_MAX:
        raise HTTPException(400, "录音太长了，分几段说")
    return data


# ------------------------------------------------------------------ 页面

@router.get("/speak", response_class=HTMLResponse)
def home(request: Request):
    u = _me(request)
    pr = sp.prefs(u)
    plays = [{**x, "pack": p["id"]} for p in sp.packs(u) if p["id"] in pr["packs"] for x in p.get("plays", [])]
    return _render(request, "speak_home.html", prefs=pr, packs=sp.packs(u), nxt=sp.next_scene(u), st=sp.today_state(u),
                   due=len(sp.due_chunks(u["id"])), plays=plays, lib=sp.library_count(u["id"]), engine=sp.engine().status())


@router.get("/speak/go")
def go(request: Request, skip: int = 0):
    """「开始」：先复习到期的表达（保底那几分钟），再进今天的场景。"""
    u = _me(request)
    if not skip and sp.due_chunks(u["id"]):
        return RedirectResponse("/speak/review?then=scene", 303)
    s = sp.next_scene(u)
    if not s:
        return RedirectResponse("/speak", 303)
    return RedirectResponse(f"/speak/s/{sp.open_scene(u, s['pack'], s['id'])['id']}", 303)


@router.get("/speak/scene/{pid}/{sid}")
def scene_go(request: Request, pid: str, sid: str, again: int = 0):
    u = _me(request)
    return RedirectResponse(f"/speak/s/{sp.open_scene(u, pid, sid, again or None)['id']}", 303)


@router.post("/speak/own")
def own(request: Request, text: str = Form("")):
    u = _me(request)
    return RedirectResponse(f"/speak/s/{sp.own_scene(u, text)['id']}", 303)


@router.get("/speak/play/{play_id}")
def play_go(request: Request, play_id: str):
    u = _me(request)
    return RedirectResponse(f"/speak/s/{sp.open_chat(u, play_id)['id']}", 303)


@router.post("/speak/play")
def play_custom(request: Request, text: str = Form("")):
    u = _me(request)
    return RedirectResponse(f"/speak/s/{sp.open_chat(u, custom=text)['id']}", 303)


@router.get("/speak/s/{sid}", response_class=HTMLResponse)
def session_page(request: Request, sid: int):
    u = _me(request)
    s = sp._session(u["id"], sid)
    accent = sp.accent_of(s)
    if s["kind"] == "chat":
        return _render(request, "speak_chat.html", s=s, accent=accent, max_turns=sp.CHAT_MAX, hint_turns=sp.CHAT_HINT)
    return _render(request, "speak_scene.html", s=s, sc=sp.scene_of(s), accent=accent, nxt=sp.next_scene(u),
                   plays=[x for x in sp.pack(s["pack_id"]).get("plays", [])][:2],
                   assess=sp.engine().can("scripted"), timed=sp.engine().can("word_times"))


@router.get("/speak/review", response_class=HTMLResponse)
def review_page(request: Request, then: str = "", more: int = 0):
    u = _me(request)
    cs = sp.extra_chunks(u["id"]) if more else sp.due_chunks(u["id"])
    items = [{**c, "say": sp.sentence_for(c)} for c in cs]
    return _render(request, "speak_review.html", items=items, then="" if more else then, more=bool(more),
                   lib=sp.library_count(u["id"]), accent=sp.pack(sp.prefs(u)["packs"][0]).get("accent", "en-US"))


@router.get("/speak/records", response_class=HTMLResponse)
def records_page(request: Request):
    u = _me(request)
    return _render(request, "speak_records.html", r=sp.records(u["id"]), hist=sp.history(u["id"]))


@router.get("/speak/clip/{clip_id}")
def clip_audio(request: Request, clip_id: str):
    u = _me(request)
    f = sp.clip_file(u["id"], clip_id)
    if not sp.owns_clip(u["id"], clip_id) or not f.exists():
        raise HTTPException(404, "没有这段录音")
    return Response(f.read_bytes(), media_type="audio/wav", headers={"Cache-Control": "private, no-store"})


# ------------------------------------------------------------------ 接口

@router.post("/api/speak/clip")
async def api_clip(request: Request, file: UploadFile = File(...), session: int = Form(...), step: str = Form(...)):
    u = _me(request)
    return sp.clip(u, session, step, await _wav(file))


@router.post("/api/speak/rewrite")
def api_rewrite(request: Request, body: dict = Body(...)):
    from .web import streamed
    u = _me(request)
    return streamed(request, lambda: sp.rewrite(u, int(body.get("session") or 0)))


@router.post("/api/speak/review/clip")
async def api_review_clip(request: Request, file: UploadFile = File(...), chunk: int = Form(...)):
    """复习：看中文说英文。识别出来的话里有没有这个表达，系统先帮着判一下，最后由本人点「说出来了 / 没说出来」。"""
    u = _me(request)
    c = sp.known_chunk(u["id"], chunk)   # 到期的、「再多说几个」抽到的、看完跟着再说一遍的，都是表达库里的
    if not c:
        raise HTTPException(404, "表达库里没有这一个")
    r = sp.transcribe(u, await _wav(file), f"review:{chunk}")
    return {"clip": r["clip"], "text": r["text"], "hit": bool(sp.used_chunks(r["text"], [c])), "say": sp.sentence_for(c),
            "over": sp.OVER_NOTE if r["over"] else ""}


@router.post("/api/speak/review/grade")
def api_review_grade(request: Request, body: dict = Body(...)):
    u = _me(request)
    if body.get("free"):   # 「再多说几个」：自由练，不改复习节奏
        return {"ok": bool(body.get("ok")), "free": True}
    return sp.grade(u["id"], int(body.get("chunk") or 0), bool(body.get("ok")))


@router.post("/api/speak/listen")
def api_listen(request: Request, body: dict = Body(...)):
    u = _me(request)
    sp.add_listen(u["id"], body.get("secs") or 0)
    return {"ok": True}


@router.get("/api/speak/totals")
def api_totals(request: Request):
    return sp.totals(_me(request)["id"])


@router.post("/api/speak/chat/turn")
async def api_chat_turn(request: Request, file: UploadFile = File(...), session: int = Form(...)):
    u = _me(request)
    return sp.chat_turn(u, session, await _wav(file))


@router.post("/api/speak/chat/close")
def api_chat_close(request: Request, body: dict = Body(...)):
    u = _me(request)
    return sp.chat_close(u, int(body.get("session") or 0))


@router.post("/api/speak/prefs")
def api_prefs(request: Request, body: dict = Body(...)):
    u = _me(request)
    sp.set_prefs(u, body.get("packs") if isinstance(body.get("packs"), list) else None,
                 body.get("daily") if "daily" in body else None)
    return sp.prefs(auth.get_user(u["id"]))


@router.post("/api/admin/stt/test")
async def api_admin_test(request: Request, file: UploadFile = File(...)):
    auth.require_admin(request)
    return sp.test_engine(await _wav(file))


@router.post("/api/admin/stt/reload")
def api_admin_reload(request: Request):
    auth.require_admin(request)
    return sp.reload_engine().status()
