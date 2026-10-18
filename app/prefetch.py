"""提前备题（docs/DESIGN.md 5.24）：孩子做题时不等 AI 现场出题，也不提前把一整天的题都出好。

以前题库里题不够就当场请 AI 出，孩子盯着「正在准备题目」等十几秒到一两分钟（混合练习一次取 4 道，缺几道就串行等几次），
一等就切走、走神。现在只往前备一步：

- 做到上一项时备下一项：做完一项（flow_state 算出下一项）、打开今天页（第一项没做的），后台只给下一项备题——
  混合练习先建好题单、备前几道；学新 / 补弱 / 预习的知识点备几道练习题和讲解；热身备几道摸底题。
  不整天一次备好：孩子今天不一定来（白花钱），而且后面的题要跟着前面的对错调（同一天的个性化）。
- 一项里面也只往前看几步（kick）：混合练习每次取题，后台备后面 LOOKAHEAD 道；某一道没有现成题，不停下来等，
  往后挪、后台去出，孩子先做别的；答错要「再来一道」的那个点马上备。学习页一打开就在后台出练习题，孩子看讲解时就在出。
- 真要等（题库空、AI 也还没出好），前端放一张今天要复习的单词小卡（/api/filler），等的也是学习。

备下的题存进公共题库，和现场出的一样复用。
"""
import logging
import os
import threading

from . import bank, db, engine, llm
from .catalog import catalog

log = logging.getLogger("aistudy.prefetch")

LEARN_TYPES = ("sync", "preview", "weak", "backfill", "check", "exam")  # 带 kp、会去 /learn 做题的任务
WARMUP_PROBES = 4
LOOKAHEAD = 3          # 混合练习往前备几道
MAX_THREADS = 3        # 同时在后台出题的最多几个（别一下子打满 AI 的并发）

# AISTUDY_PREFETCH：off = 不提前备（测试默认，免得一个测试备下的题影响别的测试）；inline = 在当前线程跑完（测试 prefetch 本身）
MODE = os.environ.get("AISTUDY_PREFETCH", "")
_running: dict[tuple, threading.Thread] = {}
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_THREADS)


# ------------------------------------------------------------------ 页面上提前一步

def kick(user_id: int, kp_id: str, n: int = 3, purpose: str = "practice", grade: str = "", uses=None, teach=False) -> None:
    """后台给这个孩子把这个知识点的题备够 n 道（题库够了就什么都不做）；teach=True 同时把讲解写好（另起一份，先好先用）。
    同一个孩子同一个点同一种用途，同时只出一份。"""
    if MODE == "off" or not llm.enabled() or not catalog.kp(kp_id):
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
    if MODE == "inline":
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
        if not db.IS_PG and MODE != "inline":
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


# ------------------------------------------------------------------ 做到上一项时备下一项

def prepare_next(user_id: int, t: dict | None) -> None:
    """后台给今天清单里的下一项 t 备题（做完上一项、打开今天页时调；只在孩子自己用的时候）。"""
    if MODE == "off" or not t or t.get("done") or not llm.enabled():
        return
    key = (user_id, "task:" + t["id"], "task")
    with _lock:
        if key in _running:
            return
        th = threading.Thread(target=_task_run, args=(key, t), name="prefetch-task", daemon=True)
        _running[key] = th
    if MODE == "inline":
        _task_run(key, t)
    else:
        th.start()


def _task_run(key, t):
    try:
        prepare_task(key[0], t)
    except Exception:  # noqa: BLE001  备不成就到时候现场出，不影响做题
        log.exception("prepare task failed: %s", key)
    finally:
        with _lock:
            _running.pop(key, None)
        if not db.IS_PG and MODE != "inline":
            db.close()


def prepare_task(user_id: int, t: dict) -> None:
    """给一项任务备题：混合练习建好题单、备前 LOOKAHEAD 道；学知识点的备练习题（学新 / 预习连讲解）；热身备摸底题。"""
    from . import explore, mix
    u = db.one("SELECT grade FROM users WHERE id=?", user_id)
    grade = (u and u["grade"]) or ""
    if t.get("type") == "mix" and t.get("pack"):
        s = mix.get_or_create(user_id, t["pack"], t.get("n") or mix.size_for(15, grade))
        ahead(user_id, db.jload(s["slots"], []), s["pos"], grade)
    elif t.get("type") in LEARN_TYPES and t.get("kp"):
        kick(user_id, t["kp"], 3, "preview" if t["type"] == "preview" else "practice", grade, teach=t["type"] in ("sync", "preview"))
    elif t.get("type") == "warmup":
        for c in explore.candidates(user_id)[:WARMUP_PROBES]:
            kick(user_id, c["kp"], 1, "diagnose", grade)


def ahead(user_id: int, slots: list, pos: int, grade: str) -> None:
    """混合练习：后台备从 pos 起的 LOOKAHEAD 道（同一个点出几次就备几道）。"""
    want: dict[tuple, dict] = {}
    for sl in slots[pos:pos + LOOKAHEAD]:
        k = (sl["kp"], sl.get("purpose", "practice"))
        want.setdefault(k, {"n": 0, "uses": sl.get("uses")})["n"] += 1
    for (kp, purpose), w in want.items():
        kick(user_id, kp, w["n"], purpose, grade, uses=w["uses"])
