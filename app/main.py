"""Application composition and lifecycle. Routes and presentation live in dedicated modules."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from . import arena, bank, config, curriculum_admin, db, engine, evidence, llm, sprint
from .catalog import catalog
from .content import content
from .methods import methods
from .auth import LoginRequired
from .web import render, kid_or_redirect, set_method
from . import diary_web, library_web, news_web, tts_web

@asynccontextmanager
async def lifespan(app):
    db.init()
    curriculum_admin.refresh(force=True)
    content.load()
    engine.load_seed_items(config.SEED_DIR)
    engine.retire_open_mistakes()
    engine.backfill_lessons()
    engine.load_seed_lessons(config.SEED_DIR)
    bank.sync()
    methods.load()
    engine.fit_courses()   # 在学的课都得是给这个人群的（课程按人群分开之前报的课）
    yield
    db.close()


app = FastAPI(title="beejoy", lifespan=lifespan)


app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=60 * 60 * 24 * 60,
                   same_site="lax", https_only=config.HTTPS_ONLY)


app.include_router(news_web.router)
app.include_router(diary_web.router)


app.include_router(tts_web.router)


app.include_router(library_web.router)


app.mount("/static", StaticFiles(directory=config.BASE_DIR / "app" / "static"), name="static")


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "请先登录"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    p = request.url.path
    # 浏览器直接提交的表单（不是页面脚本发的请求）出错也给一个页面，不把 JSON 原样甩给家长（Safari 会显示成乱码）
    form_post = request.method == "POST" and "text/html" in request.headers.get("accept", "")
    if p.startswith("/api/") or p.startswith("/ext/") or (request.method != "GET" and not form_post):
        return JSONResponse({"error": exc.detail, "detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
    back = request.headers.get("referer", "") if form_post else ""
    return render(request, "message.html", title="提示" if exc.status_code < 500 else "出错了",
                  text=str(exc.detail), link=back if back.startswith(str(request.base_url)) else "/", status_code=exc.status_code)


@app.exception_handler(llm.LLMError)
async def _llm_error(request: Request, exc):
    return JSONResponse({"error": str(exc)}, status_code=503)


@app.exception_handler(arena.ArenaError)
async def _arena_error(request: Request, exc: arena.ArenaError):
    return JSONResponse({"error": str(exc), **exc.extra}, status_code=exc.status)


@app.exception_handler(sprint.SprintError)
async def _sprint_error(request: Request, exc: sprint.SprintError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


from .routes import accounts, admin, curricula, family, focus, learning, play, papers, reading, records

for router_module in (accounts, admin, curricula, family, focus, learning, play, papers, reading, records):
    app.include_router(router_module.router)


@app.middleware("http")
async def refresh_curriculum(request: Request, call_next):
    if not request.url.path.startswith('/static/') and request.url.path != '/healthz':
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(curriculum_admin.refresh)
    return await call_next(request)
