"""开放题的 AI 点评和追问（docs/DESIGN.md §4.6）。

课程包写了 "open_review": "ai" 的课（CEO 专项课），简答题不再让学员对照参考答案自己打分：
学员写下答案 → AI 按题目的评分要点逐条点评，并就最关键的缺口追问一句（只问不答）→ 学员接着回答 →
最多追问 MAX_FOLLOWUPS 次，结束时给出参考思路。最后的结论决定这次算不算答对：
第一次就想到位记一条完整的「会」，追问后才想到位记 FOLLOWUP_WEIGHT 条，没想到位记「还不会」。
没配置 AI 或 AI 第一轮就出错时退回原来的自评。
"""
from fastapi import HTTPException

from . import db, itemtypes, llm
from .catalog import catalog

MAX_FOLLOWUPS = 2
FOLLOWUP_WEIGHT = 0.6
VERDICTS = {"good": "想到位了", "partial": "想到了一部分", "miss": "还没想到点子上"}


def on(item: dict, kp_id: str) -> bool:
    kp = catalog.kp(kp_id)
    return (itemtypes.of(item).self_rated and bool(kp) and catalog.packs[kp["pack"]].open_review == "ai"
            and llm.enabled())


def review(user_id: int, item: dict, kp_id: str, body: dict, grade: str) -> dict | None:
    """点评这一轮。返回 None：这道题退回自评。done=True 时带上 correct / weight / answer，交给作答流程记下。"""
    text = str(body.get("answer") or "").strip()[:3000]
    if len(text) < 4:
        raise HTTPException(400, "先写下你的想法，再看点评")
    row = None
    if body.get("review_id"):
        row = db.one("SELECT * FROM open_reviews WHERE id=? AND user_id=? AND item_id=? AND done=0",
                     body["review_id"], user_id, item["id"])
        if not row:
            raise HTTPException(409, "这道题的点评已经结束，请开始下一题")
    rounds = (db.jload(row["rounds"], []) if row else []) + [{"answer": text}]
    last = len(rounds) > MAX_FOLLOWUPS
    kp = catalog.kp(kp_id)
    try:
        r = llm.review_open(item, kp, catalog.packs[kp["pack"]], grade, rounds, last, user_id=user_id)
    except llm.LLMError:
        if not row:
            return None
        r = {**rounds[-2], "followup": ""}  # 追问那一轮出错：按上一轮的结论收尾
    verdict = r.get("verdict") if r.get("verdict") in VERDICTS else "partial"
    notes = [p if isinstance(p, dict) else {} for p in r.get("points") or []]
    points = [{"text": p, "hit": bool(notes[i].get("hit")) if i < len(notes) else False,
               "note": str(notes[i].get("note") or "") if i < len(notes) else ""} for i, p in enumerate(item.get("points") or [])]
    followup = "" if last or verdict == "good" else str(r.get("followup") or "").strip()
    rounds[-1].update(verdict=verdict, points=points, feedback=str(r.get("feedback") or ""), followup=followup)
    done = not followup
    now = db.now()
    if row:
        db.run("UPDATE open_reviews SET rounds=?, verdict=?, done=?, updated_at=? WHERE id=?",
               db.jdump(rounds), verdict, 1 if done else 0, now, row["id"])
        rid = row["id"]
    else:
        rid = db.insert("INSERT INTO open_reviews(user_id,item_id,kp_id,rounds,verdict,done,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?)", user_id, item["id"], kp_id, db.jdump(rounds), verdict, 1 if done else 0, now, now)
    out = {"coach": True, "review_id": rid, "round": len(rounds), "verdict": verdict, "verdict_label": VERDICTS[verdict],
           "checks": points, "feedback": rounds[-1]["feedback"], "followup": followup, "done": done}
    if done:
        out.update(correct=verdict == "good", weight=1.0 if len(rounds) == 1 else FOLLOWUP_WEIGHT,
                   answer=" / ".join(x["answer"] for x in rounds))
    return out
