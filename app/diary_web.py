"""迭代日记的后台页面（app/diary.py）：只有管理员能看。时间线、读一篇、改一改、收下或不要、上传日记文件。"""
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from . import auth, diary

router = APIRouter()


def _render(request, name, **ctx):
    from . import web
    return web.render(request, name, **ctx)


@router.get("/admin/diary")
def admin_diary(request: Request, msg: str = "", show: str = ""):
    auth.require_admin(request)
    s = diary.sync()
    kept = diary.entries("kept")
    return _render(request, "admin_diary.html", drafts=diary.entries("draft"), months=diary.months(kept), n=len(kept),
                   dropped=diary.entries("dropped") if show == "dropped" else None, sync=s, msg=msg, REPO=diary.REPO)


@router.post("/admin/diary/upload")
async def admin_upload(request: Request, files: list[UploadFile] = File(...)):
    auth.require_admin(request)
    got = {"new": 0, "updated": 0, "kept": 0}
    for f in files:
        got[diary.upload(f.filename or "", await f.read())] += 1
    msg = f"新的 {got['new']} 篇，更新 {got['updated']} 篇" + (f"，{got['kept']} 篇没变或在后台改过、没覆盖" if got["kept"] else "")
    return RedirectResponse("/admin/diary?msg=" + msg, 303)


@router.get("/admin/diary/{eid}")
def admin_entry(request: Request, eid: int, edit: int = 0):
    auth.require_admin(request)
    e = diary.get(eid)
    prev, nxt = diary.neighbors(e)
    return _render(request, "admin_diary_entry.html", e=e, prev=prev, nxt=nxt, edit=edit, STATUS=diary.STATUS, REPO=diary.REPO)


@router.post("/admin/diary/{eid}/save")
async def admin_save(request: Request, eid: int):
    auth.require_admin(request)
    diary.save(eid, dict(await request.form()))
    return RedirectResponse(f"/admin/diary/{eid}", 303)


@router.post("/admin/diary/{eid}/status")
def admin_status(request: Request, eid: int, status: str = Form(...)):
    auth.require_admin(request)
    diary.set_status(eid, status)
    return RedirectResponse("/admin/diary" if status == "dropped" else f"/admin/diary/{eid}", 303)
