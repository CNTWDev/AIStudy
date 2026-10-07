"""学习证据：事件日志 → 当前学习方式的掌握模型 → 掌握状态。

- 所有学习证据都先记进 events 表（做题、复习卡片、单词摸底、推断、自评、导入），这是唯一的原始记录。
- 掌握状态（mastery 表）是把一个孩子的事件按时间交给掌握模型（app/methods/mastery.py）一条条算出来的；
  每行记着算它的模型指纹（mastery.model）。
- 换学习方式、调参数、升级算法后指纹变了：按事件重放（replay），新方法立刻用上全部历史。
- 模型是什么、参数是多少，看 app/methods/（profiles.toml 是各种学习方式的配置）。

一道题答对不等于懂了，答错一次也不等于不懂：默认的掌握模型用贝叶斯知识追踪估算「真懂的概率」，
用遗忘模型估算「现在还记得多少」，要在不同的日子、不同的题、不同的题型上答对才算掌握。
"""
import json
from datetime import datetime

from . import db, itemtypes
from .methods import methods
from .methods.mastery import days_between, empty_evidence

_EMPTY = json.dumps(empty_evidence())


def profile(user_id: int | None):
    return methods.for_user(user_id)


def days_since(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        t = datetime.fromisoformat(ts[:19])
    except ValueError:
        return 0.0
    return max(0.0, (datetime.fromisoformat(db.now()[:19]) - t).total_seconds() / 86400)


# ------------------------------------------------------------------ 事件日志

def log(user_id: int, target: str, target_id: str, kind: str, *, correct=None, value=None, weight=1.0, mode="",
        fmt="", item_id=None, dont_know=False, data=None, at=None) -> dict:
    ev = {"user_id": user_id, "target": target, "target_id": str(target_id), "kind": kind,
          "correct": None if correct is None else (1 if correct else 0), "value": value, "weight": weight, "mode": mode or "",
          "fmt": fmt or "", "item_id": item_id, "dont_know": 1 if dont_know else 0, "data": db.jdump(data or {}),
          "created_at": at or db.now()}
    db.run("INSERT INTO events(user_id,target,target_id,kind,correct,value,weight,mode,fmt,item_id,dont_know,data,created_at) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", *ev.values())
    return ev


def _guess(item: dict | None) -> float:
    """不会也答对的概率：由题型决定（四选一 25%，填空 5%）；没有题目的（复习卡片自己说记得）15%。"""
    return itemtypes.of(item).guess(item) if item and item.get("type") else 0.15


def _model_ev(e: dict, item: dict | None, stats: dict | None) -> dict:
    """事件表的一行 → 掌握模型要的证据（蒙对概率记在事件里；题库统计用最新的：大家做得越多，难度估得越准）。"""
    data = db.jload(e["data"], {}) if isinstance(e.get("data"), str) else (e.get("data") or {})
    return {"kind": e["kind"], "correct": bool(e["correct"]), "at": e["created_at"], "mode": e["mode"],
            "fmt": e["fmt"] or "other", "weight": e["weight"] if e["weight"] is not None else 1.0,
            "dont_know": bool(e["dont_know"]), "value": e["value"], "item_id": e["item_id"],
            "guess": data.get("guess", _guess(item)), "stats": stats}


# ------------------------------------------------------------------ 掌握状态

def _state(row) -> dict | None:
    if not row:
        return None
    ev = db.jload(row["evidence"], None) if row.get("evidence") else None
    if not row["attempts"] and not row["score"]:
        return None
    return {"p": row["score"] or 0, "s": row.get("stability") or 0, "d": row.get("difficulty") or 5,
            "last": row.get("last_ev"), "n": row["attempts"] or 0, "ev": ev or empty_evidence()}


def _save(user_id, kp_id, st, *, attempts, correct, source, key):
    db.run("INSERT INTO mastery(user_id,kp_id,score,attempts,correct,status,source,updated_at,stability,difficulty,last_ev,evidence,model) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,kp_id) DO UPDATE SET score=excluded.score, "
           "attempts=excluded.attempts, correct=excluded.correct, status=excluded.status, source=excluded.source, "
           "updated_at=excluded.updated_at, stability=excluded.stability, difficulty=excluded.difficulty, "
           "last_ev=excluded.last_ev, evidence=excluded.evidence, model=excluded.model",
           user_id, kp_id, round(st["p"], 4), attempts, correct, st["status"], source, st["last"] or db.now(),
           round(st["s"], 3), round(st["d"], 3), st["last"], json.dumps(st["ev"], ensure_ascii=False), key)


def _apply(user_id: int, kp_id: str, e: dict, item: dict | None) -> str:
    prof = profile(user_id)
    row = db.one("SELECT * FROM mastery WHERE user_id=? AND kp_id=?", user_id, kp_id)
    row = dict(row) if row else None
    stats = None
    if item and item.get("id"):
        stats = db.one("SELECT n_attempts, n_correct FROM items WHERE id=?", item["id"])
        stats = dict(stats) if stats else None
    st = prof.mastery.step(_state(row), _model_ev(e, item, stats))
    answered = e["kind"] != "infer"
    _save(user_id, kp_id, st, attempts=(row["attempts"] if row else 0) + answered,
          correct=(row["correct"] if row else 0) + (answered and bool(e["correct"])), source=e["mode"], key=prof.key)
    return st["status"]


def record(user_id: int, kp_id: str, correct: bool, *, item=None, mode="practice", fmt=None, weight=1.0,
           dont_know=False, kind="answer") -> str:
    """一条作答证据（做题 kind=answer，复习卡片 kind=review）：记事件，更新掌握状态，返回新状态。"""
    fmt = fmt or itemtypes.fmt((item or {}).get("type"))
    e = log(user_id, "kp", kp_id, kind, correct=correct, weight=weight, mode=mode, fmt=fmt,
            item_id=(item or {}).get("id"), dont_know=dont_know, data={"guess": round(_guess(item), 4)})
    return _apply(user_id, kp_id, e, item)


def infer(user_id: int, kp_id: str, value: float, source: str) -> str:
    """没有作答的推断（同一概念、前置、自评、导入）：只给一个概率，不算交叉验证的证据，所以不会直接变成「掌握」。"""
    e = log(user_id, "kp", kp_id, "infer", value=value, mode=source)
    return _apply(user_id, kp_id, e, None)


# ------------------------------------------------------------------ 重放：换了学习方式 / 参数 / 算法后按全部历史重算

def replay(user_id: int) -> int:
    prof = profile(user_id)
    rows = db.q("SELECT e.*, i.type AS itype, i.data AS idata, i.n_attempts, i.n_correct FROM events e "
                "LEFT JOIN items i ON i.id = e.item_id WHERE e.user_id=? AND e.target='kp' ORDER BY e.id", user_id)
    by_kp: dict[str, list] = {}
    for r in rows:
        by_kp.setdefault(r["target_id"], []).append(r)
    for kp_id, evs in by_kp.items():
        st, n, ok = None, 0, 0
        for r in evs:
            item = {**db.jload(r["idata"], {}), "id": r["item_id"], "type": r["itype"]} if r["itype"] else None
            stats = {"n_attempts": r["n_attempts"] or 0, "n_correct": r["n_correct"] or 0} if r["itype"] else None
            st = prof.mastery.step(st, _model_ev(r, item, stats))
            if r["kind"] != "infer":
                n, ok = n + 1, ok + bool(r["correct"])
        _save(user_id, kp_id, st, attempts=n, correct=ok, source=evs[-1]["mode"], key=prof.key)
    db.run("UPDATE mastery SET model=? WHERE user_id=?", prof.key, user_id)  # 没有事件的行（很早以前的）也算对齐了
    return len(by_kp)


def ensure_current() -> int:
    """启动时：掌握状态不是当前学习方式算出来的孩子，按事件重算。返回重算了几个孩子。"""
    # 以前的复习卡片（固定间隔）换算成记忆稳定性；只处理一次
    db.run("UPDATE cards SET stability=CASE box WHEN 0 THEN 0.5 WHEN 1 THEN 1 WHEN 2 THEN 2 WHEN 3 THEN 4 WHEN 4 THEN 7 "
           "WHEN 5 THEN 15 WHEN 6 THEN 30 ELSE 60 END WHERE stability=0 AND reviews>0")
    stale = {r["user_id"] for r in db.q("SELECT DISTINCT user_id, model FROM mastery")
             if r["model"] != profile(r["user_id"]).key}
    for uid in stale:
        replay(uid)
    return len(stale)


# ------------------------------------------------------------------ 给页面和计划用

def now_p(user_id: int, m: dict) -> float:
    return profile(user_id).mastery.now_p(m, days_since(m.get("last_ev")))


def _ev(m) -> dict:
    return (db.jload(m["evidence"], None) if m and m.get("evidence") else None) or empty_evidence()


def missing(user_id: int, m: dict | None) -> list[str]:
    """离「掌握」还差哪些证据（给孩子和家长看）。"""
    if not m:
        return ["还没做过题"]
    return profile(user_id).mastery.missing(_ev(m), now_p(user_id, m))


def explain(user_id: int, m: dict | None) -> dict | None:
    """学习页上给孩子看的一行：真懂的概率、现在还记得多少、有哪些证据、还差什么。"""
    if not m or not m.get("attempts"):
        return None
    prof, ev = profile(user_id), _ev(m)
    s = m.get("stability") or 0
    r = prof.memory.recall(days_since(m.get("last_ev")), s)
    return {"p": int(round(now_p(user_id, m) * 100)), "recall": int(round(r * 100)) if s else None,
            "items": len(set(ev["items"])), "days": len(set(ev["days"])), "fmts": len(set(ev["fmts"])),
            "missing": [] if m.get("status") == "mastered" else missing(user_id, m),
            "next": prof.memory.interval(s) if s else None, "method": prof.name}


def due_checks(user_id: int, mastery: dict, limit=5) -> list[dict]:
    """学会过（掌握 / 学习中且有记忆数据）的知识点里，现在记得的概率掉到目标保持率以下的，最该复查的排前面。"""
    mem = profile(user_id).memory
    out = []
    for m in mastery.values():
        if m.get("status") not in ("mastered", "learning") or not m.get("stability") or not m.get("last_ev"):
            continue
        r = mem.recall(days_since(m["last_ev"]), m["stability"])
        if r < mem.target_r:
            out.append({**m, "recall": r})
    out.sort(key=lambda m: (m["status"] != "mastered", m["recall"]))
    return out[:limit]


def needs_work(user_id: int, mastery: dict) -> set[str]:
    """要攻克 / 补弱的知识点（薄弱，或学习中但还不太会）。规则只在掌握模型里写一处。"""
    model = profile(user_id).mastery
    return {k for k, m in mastery.items() if model.needs_work(m)}


__all__ = ["days_between", "days_since", "due_checks", "ensure_current", "explain", "infer", "log", "missing",
           "needs_work", "now_p", "profile", "record", "replay"]
