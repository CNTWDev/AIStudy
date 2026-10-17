"""Learning commands: one transaction per answer, independent of HTTP routing."""
import hashlib
import json
import re

from fastapi import HTTPException

from . import db, engine, explore, itemtypes, trace
from .catalog import catalog


@db.atomic_user
def submit(user_id: int, body: dict):
    request_id = body.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", request_id)):
        raise HTTPException(400, "作答标识不正确")
    semantic = {k: body[k] for k in ("item_id", "kp_id", "mode", "answer", "self", "dont_know", "probe") if k in body}
    fingerprint = hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if request_id:
        receipt = db.one("SELECT * FROM answer_receipts WHERE user_id=? AND request_id=?", user_id, request_id)
        if receipt:
            if receipt["fingerprint"] != fingerprint:
                raise HTTPException(409, "这次作答已提交，请开始下一题")
            return db.jload(receipt["response"], {})
    result = _answer(user_id, body)
    if request_id and not result.get("reveal"):
        db.run("INSERT INTO answer_receipts(user_id,request_id,fingerprint,response,created_at) VALUES(?,?,?,?,?)",
               user_id, request_id, fingerprint, db.jdump(result), db.now())
    return result


def _answer(user_id, body):
    row = db.one("SELECT * FROM items WHERE id=?", body.get("item_id"))
    if not row:
        raise HTTPException(404)
    it = engine._item_row_to_dict(row)
    kp_id = body.get("kp_id") or it["kp_id"]
    if not catalog.kp(kp_id):
        raise HTTPException(400, "知识点不存在")
    allowed = {it["kp_id"], *(it.get("kp_ids") or [])}
    allowed |= {r["kp_id"] for r in db.q("SELECT kp_id FROM item_kps WHERE item_id=?", it["id"])}
    allowed |= {kp["id"] for target in list(allowed) for kp in catalog.equivalents(target)}
    if kp_id not in allowed:
        raise HTTPException(400, "题目与知识点不匹配")
    if row["source"] == "paper" and not db.one(
            "SELECT p.id FROM papers p JOIN paper_items pi ON pi.paper_id=p.id WHERE p.user_id=? AND pi.item_id=?",
            user_id, row["id"]):
        raise HTTPException(403, "不能访问这道题")
    mode = body.get("mode") or "practice"
    if mode not in ("practice", "preview", "probe"):
        raise HTTPException(400, "作答模式不正确")
    if body.get("dont_know"):  # 「这道题还不会」：不算错，给讲解，进错题本，过几天再练
        engine.record_attempt(user_id, it, kp_id, mode, False, "", dont_know=True)
        if mode == "probe":
            pr = body.get("probe") or {}
            explore.after_probe(user_id, kp_id, False, int(pr.get("depth") or 0), pr.get("from") or "")
        return {"correct": False, "dont_know": True, **itemtypes.reveal(it), "hint": it.get("hint", ""),
                "trace": trace.offer(user_id, kp_id, True) if mode in trace.COUNT_MODES else None}
    if itemtypes.of(it).self_rated:
        if "self" not in body:  # 先给参考答案，孩子对照后自评
            return {"reveal": True, "answer": it.get("model", ""), "points": it.get("points", []), "explain": it.get("explain", "")}
        correct = body["self"] == "ok"
    else:
        correct = engine.check_answer(it, body.get("answer"))
    old = (db.one("SELECT status FROM mastery WHERE user_id=? AND kp_id=?", user_id, kp_id) or {}).get("status")
    status = engine.record_attempt(user_id, it, kp_id, mode, bool(correct), body.get("answer", body.get("self", "")),
                                   ms=body.get("ms"))
    out = {"correct": bool(correct), **itemtypes.reveal(it), "status": status, "status_label": engine.STATUS_LABEL[status]}
    if row["purpose"] == "combine" and mode in ("practice", "preview"):
        _combine_evidence(user_id, it, kp_id, bool(correct))
    if mode == "probe":
        pr = body.get("probe") or {}
        out["probe"] = explore.after_probe(user_id, kp_id, bool(correct), int(pr.get("depth") or 0), pr.get("from") or "")
    if not correct and mode in trace.COUNT_MODES:  # 今天第二次错：可以当场追根
        out["trace"] = trace.offer(user_id, kp_id)
    if explore.light(user_id, kp_id, old, status):  # 第一次掌握：点亮
        out["lit"] = {"kp": kp_id, "name": catalog.kp(kp_id)["name"], "today": len(explore.lit_today(user_id))}
    return out



COMBINE_WEIGHT = 0.5


def _combine_evidence(user_id: int, it: dict, kp_id: str, correct: bool) -> None:
    """融合题（新知识里带着旧知识，见 app/mix.py）：做对了，用到的旧知识点各记半条「会」的证据；
    做错了说不清是哪块没会，不扣旧知识点的分，把它们排进「往回追」队列，之后的热身和往回题里单独测。"""
    uses = [k for k in (it.get("kp_ids") or []) if k != kp_id and catalog.kp(k)]
    for k in uses:
        if correct:
            engine.update_mastery(user_id, k, True, weight=COMBINE_WEIGHT, source="practice", item=it)
        elif not db.one("SELECT id FROM probes WHERE user_id=? AND kind='kp' AND kp_id=? AND status='queued'", user_id, k):
            db.run("INSERT INTO probes(user_id,kind,kp_id,reason,from_kp,depth,priority,created_at) VALUES(?,'kp',?,?,?,1,8,?)",
                   user_id, k, f"「{catalog.kp(kp_id)['name']}」的融合题没做对，查查这块旧知识", kp_id, db.now())
