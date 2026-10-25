"""reading HTTP routes. Learning commands live in app.learning."""
from fastapi import APIRouter
from fastapi import Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .. import auth, bank, config, db, engine, evidence, itemtypes, llm, news, recall, webpage
import re
from datetime import timedelta
from ..web import (
    _auto_collect,
    _has_card,
    _lang_of,
    _reading_view,
    _today_reading,
    _tools_render,
    kid_or_redirect,
    render,
)

router = APIRouter()

@router.get("/words", response_class=HTMLResponse)
def words(request: Request, kind: str = "word"):
    k = kid_or_redirect(request, manage=True)
    cards = db.q("SELECT * FROM cards WHERE user_id=? AND kind=? ORDER BY starred DESC, id DESC LIMIT 500", k["id"], kind)
    sents = db.q("SELECT * FROM sentences WHERE user_id=? ORDER BY id DESC LIMIT 100", k["id"]) if kind == "sentence" else []
    counts = {r["kind"]: r["n"] for r in db.q("SELECT kind, COUNT(*) AS n FROM cards WHERE user_id=? GROUP BY kind", k["id"])}
    counts["sentence"] = db.one("SELECT COUNT(*) AS n FROM sentences WHERE user_id=?", k["id"])["n"]
    cards = [{**dict(c), "extra": db.jload(c["extra"], {})} for c in cards]
    if kind == "mistake":  # 错题：按原题结构显示（题干、选项、答案、我的答案、解析），找不到原题的老错题退回文字
        for c in cards:
            it = recall._item(c["extra"].get("item_id"))
            c["view"] = itemtypes.view(it, c["extra"].get("my_answer")) if it else None
        cards.sort(key=lambda c: (bool(c["extra"].get("passed")), c["due"]))  # 要重做的在前，过关的在后
    paused = engine.Paused(k["id"])   # 退掉的课留下的：还在本子里，收在最下面，不叫回来复习
    stop = [c for c in cards if paused.card(c)]
    cards = [c for c in cards if c not in stop]
    return render(request, "words.html", cards=cards, paused=stop,
                  sents=[{**dict(s), "fb": db.jload(s["feedback"], {})} for s in sents], kind=kind, counts=counts,
                  today_iso=db.today().isoformat())


@router.get("/words/map", response_class=HTMLResponse)
def words_map(request: Request, msg: str = ""):
    """我的词：认识的词一共多少（只增不减），每个单词包一组一组抓牢了几个（app/words.py）。
    孩子的包由家长在学习安排里管；自己安排学习的成人在这里加、停。"""
    from .. import words
    k = kid_or_redirect(request, manage=True)
    me = auth.require_user(request)
    mine = me["id"] == k["id"] and auth.self_managed(me)
    st = words.stats(k["id"])
    due = (db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind IN ('word','term') AND due<=?", k["id"], db.today().isoformat()) or {}).get("n") or 0
    return render(request, "words_map.html", st=st, can_edit=mine, can_play=me["id"] == k["id"], msg=msg, lad_names=words.LEVEL_NAME,
                  shelves=words.shelves(k["id"]), due=due)


@router.get("/words/pack/{pack_id}", response_class=HTMLResponse)
def words_pack(request: Request, pack_id: str, msg: str = ""):
    """一个单词包：按册、按单元列出全部词，每个词我会到哪一级；课本包标出学校正在上的那一单元。"""
    from .. import words
    k = kid_or_redirect(request, manage=True)
    me = auth.require_user(request)
    v = words.pack_view(k["id"], pack_id)
    if not v:
        raise HTTPException(404, "没有这个单词包")
    return render(request, "words_pack.html", v=v, msg=msg, can_edit=me["id"] == k["id"] and auth.self_managed(me), lad_names=words.LEVEL_NAME)


@router.post("/words/packs")
async def words_packs(request: Request):
    """自己安排学习的成人：加上 / 停掉一个单词包、改每天几个新词。"""
    from urllib.parse import urlencode
    from .. import words
    me = auth.require_user(request)
    if not auth.self_managed(me):
        raise HTTPException(403, "孩子的单词包由家长在「学习安排」里管")
    f = await request.form()
    ref = f.get("ref") or ""
    if ref not in words.packs():
        return RedirectResponse("/words/map?" + urlencode({"msg": "请选一个单词包"}), 303)
    amount = str(f.get("daily_amount") or "").strip()
    words.set_pack(me["id"], ref, f.get("action") != "stop", int(amount) if amount.isdigit() else None)
    engine.today_plan(me["id"], rebuild=True)
    back = f"/words/pack/{ref}" if f.get("back") == "pack" else "/words/map"
    return RedirectResponse(back + "?" + urlencode({"msg": "已保存"}), 303)


@router.get("/reading/today")
def reading_today(request: Request, lang: str = "en"):
    """每日阅读任务的入口：今天读过 / 生成过就直接打开那篇；还没有才进写文章的工具。"""
    k = kid_or_redirect(request)
    lang = lang if lang in ("en", "zh") else "en"
    r = _today_reading(k["id"], lang)
    if r:
        return RedirectResponse(f"/reading/{r['id']}", 303)
    return reading_list(request, lang, daily=True)


READ_TASKS = ("read_en", "read_zh", "listen")


def _reading_page(request: Request, k: dict, lang: str = "en", daily: bool = False, **extra):
    """阅读页是来读的：先是今天要读的，再是每天的新闻、在读的书、读过的短文；自己找文章读的工具放在最下面。
    从每日任务进来、今天还没有短文（daily）：写短文的工具就是这一页要做的事，放到最上面打开。"""
    from ..library_web import in_progress
    rows = db.q("SELECT id, title, lang, source, minutes, finished_at, created_at FROM readings "
                "WHERE user_id=? AND source NOT LIKE 'news:%' ORDER BY created_at DESC, id DESC LIMIT 60", k["id"])
    tasks = [t for t in engine.plan_of_today(k["id"]) if t.get("type") in READ_TASKS]
    return render(request, "reading_list.html", rows=rows, lang=lang if lang in ("en", "zh") else "en", daily=daily,
                  tasks=tasks, next_task=next((t for t in tasks if not t.get("done")), None),
                  news_days=news.archive(k), books=in_progress(k["id"])[:6], today=db.today().isoformat(),
                  yesterday=(db.today() - timedelta(days=1)).isoformat(), **extra)


@router.get("/reading", response_class=HTMLResponse)
def reading_list(request: Request, lang: str = "en", daily: bool = False):
    return _reading_page(request, kid_or_redirect(request), lang, daily)


@router.post("/reading/new")
async def reading_new(request: Request):
    k = kid_or_redirect(request)
    form = await request.form()
    lang = form.get("lang") if form.get("lang") in ("en", "zh") else "en"
    if form.get("mode") == "ai":
        dup = _today_reading(k["id"], lang, ai_only=True)
        if dup and not dup["finished_at"] and not form.get("again"):  # 今天写的那篇还没读完：先读它（连点、来回进出不会多生成）
            return RedirectResponse(f"/reading/{dup['id']}?dup=1", 303)
        review_words = [r["front"] for r in db.q(
            "SELECT front FROM cards WHERE user_id=? AND kind='word' AND due<=? ORDER BY due LIMIT 8",
            k["id"], (db.today() + timedelta(days=3)).isoformat())] if lang == "en" else []
        length = form.get("length") or ("150-250 词" if lang == "en" else "500-800 字")
        p, cid = bank.passage(k["id"], lang, k["grade"], form.get("topic") or "", length, review_words)
        rid = db.insert("INSERT INTO readings(user_id,lang,title,body,source,questions,content_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     k["id"], lang, p.get("title", "Reading"), p.get("body", ""), "ai", db.jdump(p.get("questions", [])), cid, db.now())
    else:
        body = (form.get("body") or "").strip()
        if not body:
            raise HTTPException(400, "请粘贴要读的文章")
        rid = db.insert("INSERT INTO readings(user_id,lang,title,body,source,created_at) VALUES(?,?,?,?,?,?)",
                     k["id"], lang, (form.get("title") or "我的阅读").strip(), body[:20000], "paste", db.now())
    return RedirectResponse(f"/reading/{rid}", 303)


@router.post("/reading/url")
def reading_url(request: Request, url: str = Form(...), lang: str = Form("")):
    k = kid_or_redirect(request)
    try:
        a = webpage.article(url)
    except webpage.FetchError as e:
        return _reading_page(request, k, url_error=str(e), url=url, status_code=400)
    lang = lang if lang in ("en", "zh") else a["lang"]
    rid = db.insert("INSERT INTO readings(user_id,lang,title,body,source,created_at) VALUES(?,?,?,?,?,?)",
                    k["id"], lang, a["title"], a["body"], "web:" + a["url"][:500], db.now())
    return RedirectResponse(f"/reading/{rid}", 303)


def _quoted(learn, body: str) -> list[dict]:
    """「这篇值得学的」只留真在正文里的句子（AI 偶尔会改写或编一句）：比的时候不管空格和标点。"""
    def norm(t):
        return "".join(ch for ch in (t or "").lower() if ch.isalnum())
    text = norm(body)
    return [x for x in learn or [] if isinstance(x, dict) and len(norm(x.get("text"))) >= 4 and norm(x.get("text")) in text][:3]


@router.get("/reading/{rid}", response_class=HTMLResponse)
def reading_view(request: Request, rid: int):
    k = kid_or_redirect(request)
    r = db.one("SELECT * FROM readings WHERE id=? AND user_id=?", rid, k["id"])
    if not r:
        raise HTTPException(404)
    qs = db.jload(r["questions"], [])
    looked = db.q("SELECT query, result FROM lookups WHERE user_id=? AND reading_id=? ORDER BY id", k["id"], rid)
    answers = db.jload(r["answers"], None) if r["finished_at"] else None
    c = db.one("SELECT body FROM contents WHERE id=?", r["content_id"]) if r["source"] == "ai" and r["content_id"] else None
    extra = db.jload(c["body"], {}) if c else {}
    return render(request, "reading.html", r=r, learn=_quoted(extra.get("learn"), r["body"]),
                  think=extra.get("think") or "", paras=[p for p in r["body"].split("\n") if p.strip()],
                  questions=[{"q": q["q"], "options": q.get("options", [])} for q in qs], news=news.reading_extra(r),
                  done_views=[_reading_view(q, (answers or {}).get(str(i))) for i, q in enumerate(qs)] if answers is not None else None,
                  dup=request.query_params.get("dup") == "1", is_today=r["created_at"] >= db.today().isoformat(),
                  looked=[{"q": l["query"], "r": db.jload(l["result"], {})} for l in looked])


@router.post("/api/lookup")
def api_lookup(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    query = (body.get("q") or "").strip()[:80]
    if not query:
        raise HTTPException(400)
    lang = body.get("lang", "en")
    res = llm.lookup(query, (body.get("context") or "")[:400], lang, k["grade"], user_id=k["id"])
    db.run("INSERT INTO lookups(user_id,reading_id,query,context,result,created_at) VALUES(?,?,?,?,?,?)",
           k["id"], body.get("reading_id"), query, (body.get("context") or "")[:400], db.jdump(res), db.now())
    if body.get("auto"):  # 划词菜单查的：按行为自动记录
        why = _auto_collect(k["id"], query, res.get("meaning") or "",
                            {"phonetic": res.get("phonetic", ""), "example": res.get("example", ""),
                             "context": (body.get("context") or "")[:300], "page": (body.get("page") or "")[:100]},
                            in_question=bool(body.get("item_id")), front=res.get("word") or query)
        res = {**res, "auto_added": why, "saved": bool(why) or _has_card(k["id"], res.get("word") or query)}
    return res


@router.post("/api/translate")
def api_translate(request: Request, body: dict = Body(...)):
    """划词翻译一句话：给意思和难点；同一句查第二次（或做题时查的）自动进复习。"""
    k = kid_or_redirect(request)
    text = (body.get("text") or "").strip()[:600]
    if not text:
        raise HTTPException(400)
    lang = "zh" if re.search(r"[\u4e00-\u9fff]", text) and not re.search(r"[A-Za-z]{3,}", text) else "en"
    res = llm.explain_sentence(text, lang, k["grade"], user_id=k["id"])
    db.run("INSERT INTO lookups(user_id,reading_id,query,context,result,created_at) VALUES(?,?,?,?,?,?)",
           k["id"], None, text[:80], text[:400], db.jdump({"meaning": res.get("meaning", ""), "sentence": 1}), db.now())
    why = _auto_collect(k["id"], text[:300], res.get("meaning") or "", {"structure": res.get("structure", ""),
                        "page": (body.get("page") or "")[:100]}, in_question=bool(body.get("item_id")))
    return {**res, "lang": lang, "auto_added": why, "saved": bool(why) or _has_card(k["id"], text[:300])}


@router.post("/api/collect")
def api_collect(request: Request, body: dict = Body(...)):
    """孩子手动把选中的词 / 句子加入复习。意思由 AI 查好；AI 不可用时先存原文，复习时自己想。"""
    k = kid_or_redirect(request)
    text = (body.get("text") or "").strip()[:300]
    if not text:
        raise HTTPException(400)
    meaning = (body.get("meaning") or "").strip()
    word = len(text) <= 30 and len(text.split()) <= 3
    lang = "zh" if re.search(r"[\u4e00-\u9fff]", text) and not re.search(r"[A-Za-z]{3,}", text) else "en"
    extra = {"context": (body.get("context") or "")[:300], "page": (body.get("page") or "")[:100], "source": "划词收藏"}
    if not meaning and llm.enabled():
        try:
            if word:
                r = llm.lookup(text, extra["context"], lang, k["grade"], user_id=k["id"])
                meaning = r.get("meaning", "")
                extra.update(phonetic=r.get("phonetic", ""), example=r.get("example", ""))
                text = r.get("word") or text
            else:
                meaning = llm.explain_sentence(text, lang, k["grade"], user_id=k["id"]).get("meaning", "")
        except llm.LLMError:
            pass
    kind = "word" if word and " " not in text else "phrase"
    cid = engine.add_card(k["id"], kind, text, meaning or "（意思还没查到，复习时想一想，或问一问）", extra, starred=1)
    return {"id": cid, "front": text, "meaning": meaning}


@router.post("/api/explain")
def api_explain(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    return llm.explain_sentence((body.get("sentence") or "")[:600], body.get("lang", "en"), k["grade"], user_id=k["id"])


@router.post("/api/sentence")
def api_sentence(request: Request, body: dict = Body(...)):
    k = kid_or_redirect(request)
    word, sent = (body.get("word") or "").strip(), (body.get("sentence") or "").strip()
    if not word or not sent:
        raise HTTPException(400, "先写一个句子")
    fb = llm.sentence_feedback(word, body.get("meaning", ""), sent[:400], body.get("lang", "en"), k["grade"], user_id=k["id"])
    db.run("INSERT INTO sentences(user_id,word,sentence,feedback,ok,created_at) VALUES(?,?,?,?,?,?)",
           k["id"], word, sent[:400], db.jdump(fb), 1 if fb.get("ok") else 0, db.now())
    engine._touch_day(k["id"])
    return fb


@router.post("/api/reading/{rid}/finish")
def reading_finish(request: Request, rid: int, body: dict = Body(...)):
    k = kid_or_redirect(request)
    r = db.one("SELECT * FROM readings WHERE id=? AND user_id=?", rid, k["id"])
    if not r:
        raise HTTPException(404)
    qs = db.jload(r["questions"], [])
    answers = body.get("answers") or {}
    if r["finished_at"] and r["answers"]:  # 已经交过：不重复算，原样给回上次的结果
        answers = db.jload(r["answers"], {})
    results = []
    for i, q in enumerate(qs):
        a = answers.get(str(i))
        ok = a is not None and str(a) == str(q.get("answer"))
        results.append({"ok": ok, "answer": q.get("answer"), "explain": q.get("explain", ""), "view": _reading_view(q, a)})
    minutes = max(1, min(120, int(body.get("minutes") or 1)))
    if not r["finished_at"]:
        mode = "listen" if body.get("mode") == "listen" and r["lang"] == "en" and qs else "read"
        db.run("UPDATE readings SET finished_at=?, minutes=minutes+?, answers=?, mode=? WHERE id=?", db.now(), minutes,
               db.jdump({str(k2): v for k2, v in answers.items()}), mode, rid)
        for i, res in enumerate(results):   # 阅读理解的每道题也是一次作答：进学习报告（app/records.py），不进掌握度
            evidence.log(k["id"], "reading", f"{r['lang']}:{rid}:{i}", "answer", correct=res["ok"], mode=mode, fmt="choice")
    engine._touch_day(k["id"])  # 在线阅读的时间由页面自动计时
    hit = engine.mark_task_by(k["id"], type="read_" + r["lang"], lang=r["lang"])
    return {"results": results, "flow": engine.flow_state(k["id"], hit[0]) if hit else None}


@router.post("/api/reading/{rid}/feel")
def reading_feel(request: Request, rid: int, body: dict = Body(...)):
    """读完点「喜欢 / 一般」：喜欢过的领域以后多出现一些（app/passage_topics.py 的兴趣来源）。"""
    k = kid_or_redirect(request)
    feel = body.get("feel") if body.get("feel") in ("like", "meh") else ""
    if not db.one("SELECT id FROM readings WHERE id=? AND user_id=? AND source='ai'", rid, k["id"]):
        raise HTTPException(404)
    db.run("UPDATE readings SET feel=? WHERE id=?", feel, rid)
    return {"feel": feel}


@router.get("/ext/me")
def ext_me(request: Request):
    u = auth.user_by_token(request)
    return {"name": u["name"], "due_words": len(engine.due_cards(u["id"], 300, "words")),
            "words": db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind IN ('word','phrase')", u["id"])["n"]}


@router.post("/ext/lookup")
def ext_lookup(request: Request, body: dict = Body(...)):
    u = auth.user_by_token(request)
    text = (body.get("text") or "").strip()[:80]
    if not text:
        raise HTTPException(400, "没有选中文字")
    context = (body.get("context") or "")[:400]
    lang = _lang_of(text)
    res = llm.lookup(text, context, lang, u["grade"], user_id=u["id"])
    db.run("INSERT INTO lookups(user_id,reading_id,query,context,result,created_at) VALUES(?,?,?,?,?,?)",
           u["id"], None, text, context, db.jdump(res), db.now())
    saved = db.one("SELECT id FROM cards WHERE user_id=? AND kind IN ('word','phrase') AND LOWER(front) IN (?,?)", u["id"],
                   text.lower()[:300], (res.get("word") or text).lower()[:300])
    return {"lang": lang, "result": res, "saved": bool(saved)}


@router.post("/ext/save")
def ext_save(request: Request, body: dict = Body(...)):
    u = auth.user_by_token(request)
    word = (body.get("word") or "").strip()
    meaning = (body.get("meaning") or "").strip()
    if not word or not meaning:
        raise HTTPException(400, "缺少单词或意思")
    kind = "phrase" if " " in word.strip() else "word"
    extra = {k: (body.get(k) or "")[:400] for k in ("phonetic", "pinyin", "example", "example_zh", "context", "url", "pos")}
    extra["source"] = "插件"
    cid = engine.add_card(u["id"], kind, word, meaning, extra, starred=1)
    return {"ok": True, "id": cid, "due_words": len(engine.due_cards(u["id"], 300, "words"))}


@router.get("/tools", response_class=HTMLResponse)
def tools_page(request: Request, kid: int = 0):
    return _tools_render(request, kid)


@router.post("/tools/token")
def tools_token(request: Request, kid: int = Form(0), action: str = Form("new"), token_hash: str = Form("")):
    u = auth.require_user(request)
    target = auth.kid_of(u, kid) if u["role"] == "parent" and kid else u
    if action == "revoke":
        auth.revoke_api_token(target["id"], token_hash)
        return RedirectResponse(f"/tools?kid={kid}", 303)
    token = auth.create_api_token(target["id"])
    auth.log_event("api_token_create", user_id=target["id"], email=auth.account_name(target), detail=f"by {auth.account_name(u)}", request=request)
    return _tools_render(request, kid, token)  # 连接码只在这一次页面里显示，不放进网址


@router.get("/tools/extension.zip")
def tools_extension_zip(request: Request):
    import io
    import zipfile
    from fastapi.responses import Response
    auth.require_user(request)
    src = config.BASE_DIR / "extension"
    server = config.PUBLIC_URL or str(request.base_url).rstrip("/")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                data = p.read_bytes()
                if p.name == "config.js":
                    data = f'const AISTUDY_DEFAULT_SERVER = "{server}";\n'.encode()
                z.writestr("aistudy-extension/" + str(p.relative_to(src)), data)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="aistudy-extension.zip"'})


