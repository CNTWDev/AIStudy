"""每日新闻的页面：孩子打开今天的新闻（放进站内阅读器），管理员看选题结果、各新闻源状态，手动重选或换一篇。"""
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, db, llm, news

router = APIRouter()


def _main():
    from . import main
    return main


@router.get("/news")
def news_today(request: Request, level: str = ""):
    m = _main()
    k = m.kid_or_redirect(request)
    if level in news.levels():  # 孩子自己换难度：记住，以后都按这个级别
        st = db.jload(k.get("settings"), {}) or {}
        st["news_level"] = level
        db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), k["id"])
        k = {**k, "settings": db.jdump(st)}
    try:
        rid = news.open_for(k)
    except llm.LLMError as e:
        return m.render(request, "message.html", title="今天的新闻还没写好", status_code=503,
                        text=f"{e}。可以先读一篇 AI 写的短文，或者去书库读书。", link="/reading")
    if not rid:
        return m.render(request, "message.html", title="今天的新闻还没准备好",
                        link="/reading", text="每天早上系统会从新闻网站选一条，写成适合你读的英文短文。现在还没有，可以先读书库里的书，或者让 AI 写一篇短文。")
    return RedirectResponse(f"/reading/{rid}", 303)


@router.get("/admin/news", response_class=HTMLResponse)
def admin_news(request: Request, msg: str = ""):
    auth.require_admin(request)
    picks = db.q("SELECT id, day, band, source_name, license, title, url, why, topic, subjects, alts, LENGTH(facts) AS n_facts "
                 "FROM news_picks ORDER BY day DESC, band DESC LIMIT 30")
    written = {}
    for r in db.q("SELECT topic, grade, title FROM contents WHERE kind='news' AND status='active'"):
        written.setdefault(r["topic"], []).append(r["grade"])
    reads = {r["source"]: r["n"] for r in db.q("SELECT source, COUNT(*) AS n FROM readings WHERE source LIKE 'news:%' GROUP BY source")}
    rows = []
    for p in picks:
        n_read = sum(n for s, n in reads.items() if s.startswith(f"news:{p['id']}:"))
        rows.append({**p, "subjects": db.jload(p["subjects"], []), "n_alts": len(db.jload(p["alts"], [])),
                     "levels": sorted(written.get(f"news:{p['id']}", [])), "n_read": n_read})
    c = news.cfg()
    return _main().render(request, "admin_news.html", rows=rows, st=news.status(), sources=c.get("sources", []),
                          levels=c.get("levels", {}), hour=c.get("hour", 6), bands=news.BANDS, LICENSE=news.LICENSE_LABEL,
                          ai=llm.enabled(), msg=msg)


@router.post("/admin/news/run")
def admin_run(request: Request):
    auth.require_admin(request)
    news.run_async(force=True)
    return RedirectResponse("/admin/news?msg=" + "已开始重新抓取和精选，大约一两分钟，刷新这个页面看结果", 303)


@router.post("/admin/news/swap")
def admin_swap(request: Request, pick_id: int = Form(...)):
    auth.require_admin(request)
    try:
        new = news.swap(pick_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"换不了：{e}")
    return RedirectResponse("/admin/news?msg=" + ("已换成备选里的下一条" if new else "没有能用的备选了，可以点「重新抓取精选」"), 303)
