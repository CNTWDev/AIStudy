"""提前备题（docs/DESIGN.md 5.24）：孩子做题时不等 AI 现场出题。

以前题库里题不够就当场请 AI 出，孩子盯着「正在准备题目」等十几秒到一两分钟（混合练习一次取 4 道，缺几道就串行等几次），
一等就切走、走神。现在分三层：

- 每天早上（worker，默认 6 点以后）按每个孩子今天的清单备好：每门课的混合练习题单先建好，题单里每个知识点、
  学新 / 补弱 / 预习的知识点，题库里给这个孩子备够没做过的题，学新的讲解也先写好；热身的摸底题也备几道。
- 页面上提前一步（kick）：取题时题库里没有现成的，不停下来等，这道往后排、后台去出，孩子先做别的；
  学习页一打开就在后台备练习题，孩子看讲解时就在出。
- 真要等（题库空、AI 也还没出好），前端放一张今天要复习的单词小卡（/api/filler），等的也是学习。

备下的题存进公共题库，和现场出的一样复用。早上备题有每天的上限（DAILY_AI），只为当天清单里的知识点出题。
"""
import logging
import os
import threading
from datetime import datetime, timedelta

from . import bank, db, engine, llm
from .catalog import catalog

log = logging.getLogger("aistudy.prefetch")

JOB = "prefetch"
HOUR = 6               # 早上几点以后备题（跟每日新闻同一个钟点，新闻先跑）
DAILY_AI = 200         # 早上备题每天最多调用几次 AI
RETRY_MINUTES = 60
ACTIVE_DAYS = 14
LEARN_TYPES = ("sync", "preview", "weak", "backfill", "check", "exam")  # 带 kp、会去 /learn 做题的任务
WARMUP_PROBES = 4
MAX_THREADS = 3        # 同时在后台出题的最多几个（别一下子打满 AI 的并发）

# 测试里设 AISTUDY_PREFETCH=inline：后台出题直接在当前线程跑完，结果确定
INLINE = os.environ.get("AISTUDY_PREFETCH") == "inline"
_running: dict[tuple, threading.Thread] = {}
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_THREADS)


# ------------------------------------------------------------------ 页面上提前一步

def kick(user_id: int, kp_id: str, n: int = 3, purpose: str = "practice", grade: str = "", uses=None, teach=False) -> None:
    """后台给这个孩子把这个知识点的题备够 n 道（题库够了就什么都不做）；teach=True 同时把讲解写好（另起一份，先好先用）。
    同一个孩子同一个点同一种用途，同时只出一份。"""
    if not llm.enabled() or not catalog.kp(kp_id):
        return
    if teach and not has_teach(kp_id):
        _start((user_id, kp_id, "teach"), n, grade, uses)
    _start((user_id, kp_id, purpose), n, grade, uses)


def _start(key, n, grade, uses):
    with _lock:
        if key in _running:
            return
        t = threading.Thread(target=_run, args=(key, n, grade, uses), name=f"prefetch-{key[1]}", daemon=True)
        _running[key] = t
    if INLINE:
        _run(key, n, grade, uses)
    else:
        t.start()


def _run(key, n, grade, uses):
    user_id, kp_id, purpose = key
    try:
        with _slots:
            if purpose == "teach":
                bank.teach(catalog.kp(kp_id), grade or catalog.default_grade, user_id=user_id)
            else:
                ensure(user_id, kp_id, n, purpose, grade, uses)
    except Exception:  # noqa: BLE001  后台备题失败不影响做题：到时候没题就照常现场出
        log.exception("prefetch failed: %s", key)
    finally:
        with _lock:
            _running.pop(key, None)
        if not db.IS_PG and not INLINE:
            db.close()


def has_teach(kp_id: str) -> bool:
    """库里有这个知识点的讲解了（bank.teach 会直接用，不用再写）。只查不计用量。"""
    return bool(db.one("SELECT id FROM contents WHERE kind='teach' AND status='active' AND kp_id=?", kp_id))


def wait(user_id: int, kp_id: str, purpose: str = "practice", timeout: float = 90) -> None:
    """后台正在出这一份：等它出完再取题，不重复请 AI。"""
    t = _running.get((user_id, kp_id, purpose))
    if t and t.is_alive() and t is not threading.current_thread():
        t.join(timeout)


def ensure(user_id: int, kp_id: str, n: int, purpose: str = "practice", grade: str = "", uses=None) -> bool:
    """题库里这个孩子能用的题不够 n 道才请 AI 出（存进题库）。返回这次有没有调用 AI。"""
    have = engine.items_for(user_id, kp_id, n=n, purpose=purpose, grade=grade, uses=uses, ai=False)
    if len(have) >= n:
        return False
    engine.items_for(user_id, kp_id, n=n, purpose=purpose, grade=grade, uses=uses)
    return True


# ------------------------------------------------------------------ 每天早上按清单备题

def needs(user_id: int) -> list[dict]:
    """今天要备的：[{kp, n, purpose, uses, teach}]。会先建好今天的清单和每门课的混合练习题单（和孩子打开时建的一样）。"""
    from . import explore, mix
    u = db.one("SELECT grade FROM users WHERE id=?", user_id)
    grade = (u and u["grade"]) or ""
    want: dict[tuple, dict] = {}

    def add(kp, purpose="practice", n=1, uses=None, teach=False):
        if not catalog.kp(kp):
            return
        w = want.setdefault((kp, purpose), {"kp": kp, "purpose": purpose, "n": 0, "uses": uses, "teach": False})
        w["n"] = min(5, w["n"] + n)
        w["teach"] = w["teach"] or teach

    for t in engine.today_plan(user_id)["plan"]:
        if t.get("done"):
            continue
        if t.get("type") == "mix" and t.get("pack"):
            s = mix.get_or_create(user_id, t["pack"], t.get("n") or mix.size_for(15, grade))
            for sl in db.jload(s["slots"], [])[s["pos"]:]:
                add(sl["kp"], sl.get("purpose", "practice"), 1, sl.get("uses"))
        elif t.get("type") in LEARN_TYPES and t.get("kp"):
            add(t["kp"], "preview" if t["type"] == "preview" else "practice", 3, teach=t["type"] in ("sync", "preview"))
        elif t.get("type") == "warmup":
            for c in explore.candidates(user_id)[:WARMUP_PROBES]:
                add(c["kp"], "diagnose", 1)
    return [{**w, "grade": grade} for w in want.values()]


def prepare_kid(user_id: int, budget: int) -> int:
    """给一个孩子备今天的题，最多用 budget 次 AI。返回用了几次。"""
    used = 0
    for w in needs(user_id):
        if used >= budget:
            break
        try:
            used += ensure(user_id, w["kp"], w["n"], w["purpose"], w["grade"], w["uses"])
            if w["teach"] and used < budget and not has_teach(w["kp"]):
                bank.teach(catalog.kp(w["kp"]), w["grade"] or catalog.default_grade, user_id=user_id)
                used += 1
        except llm.LLMError as e:
            log.warning("prefetch stopped for kid %s: %s", user_id, e)
            break
    return used


def kids() -> list:
    """要备题的孩子：最近 ACTIVE_DAYS 天学过、或者刚加进来的（很久没来的账号不白花 AI）。最近学过的排前面。"""
    since = (db.today() - timedelta(days=ACTIVE_DAYS)).isoformat()
    return db.q("SELECT u.id, MAX(d.day) AS last FROM users u LEFT JOIN days d ON d.user_id=u.id AND d.day>=? AND d.active_seconds>0 "
                "WHERE u.role='kid' AND u.status='active' GROUP BY u.id, u.created_at "
                "HAVING MAX(d.day) IS NOT NULL OR u.created_at>=? ORDER BY COALESCE(MAX(d.day), '') DESC, u.id", since, since)


def _status() -> dict:
    db.run("INSERT INTO jobs(name) VALUES(?) ON CONFLICT(name) DO NOTHING", JOB)
    return db.jload(db.one("SELECT last_result FROM jobs WHERE name=?", JOB)["last_result"], {}) or {}


def run_daily(force: bool = False) -> dict:
    """每天一次：到了钟点就给每个孩子备今天的题。做完的不重复做；中途失败隔一小时再试（已备好的不会再出）。"""
    if not llm.enabled():
        return {"skipped": "AI 没开"}
    now = datetime.fromisoformat(db.now())
    day = db.today().isoformat()
    last = _status()
    if not force:
        if now.hour < HOUR:
            return {"skipped": "还没到时间"}
        if last.get("day") == day and (last.get("done") or last.get("tried_at", "") > (now - timedelta(minutes=RETRY_MINUTES)).isoformat()):
            return {"skipped": "今天备过了"}
    from .jobs import lease
    with lease(JOB) as task:
        if task is None:
            return {"skipped": "另一个进程正在跑"}
        res = {"day": day, "tried_at": db.now(), "kids": 0, "ai_calls": 0, "done": False}
        try:
            left = DAILY_AI - (last.get("ai_calls", 0) if last.get("day") == day else 0)
            for k in kids():
                if left <= 0:
                    break
                try:
                    used = prepare_kid(k["id"], left)
                except Exception:  # noqa: BLE001  一个孩子出错（数据有问题）不耽误给别的孩子备题
                    log.exception("prefetch failed for kid %s", k["id"])
                    continue
                left -= used
                res["ai_calls"] += used
                res["kids"] += 1
            if last.get("day") == day:
                res["ai_calls"] += last.get("ai_calls", 0)
            res["done"] = True
        finally:
            task.result = res  # lease 结束时写进 jobs.last_result
        return res
