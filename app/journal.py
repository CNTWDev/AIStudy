"""决策日志：做一个决定之前写下理由和认错条件，到期回来对照结果复盘，分清判断和运气（docs/DESIGN.md §4.8）。

哪门课用、写哪几栏由课程包的 journal 声明（散户美股实战课是仓位、理由、优势、认错条件；CEO 专项课是一个经营决定的
关键假设和认错信号），这里不按学科写特例。一条日志（journal 表的一行）：
  写下决定、各栏、几成把握、多久以后回看 → 到了回看日期进「今天」的提醒 → 学员对照结果复盘：结果好还是差、
  当时的判断站不站得住、踩了哪些陷阱（课程包的 traps，都是本课的知识点）→ 放进四个格子。
攒多了以后自己找规律（patterns）：判断有问题却结果好的（最危险）、把握估得准不准、哪个陷阱反复出现，连到那一课。
日志只给本人看：家长、排行榜都看不到。
"""
from datetime import timedelta

from fastapi import HTTPException

from . import db, engine
from .catalog import catalog

OUTCOMES = {"good": "结果好", "bad": "结果差"}
JUDGMENTS = {"sound": "判断站得住", "flawed": "判断有问题"}
# 四个格子：判断 × 结果。判断有问题却结果好的最危险：它会让人把错的做法重复下去。
QUADRANTS = [
    {"id": "sound-good", "name": "判断对，结果也好", "note": "做对了，也有运气帮忙。"},
    {"id": "sound-bad", "name": "判断对，运气差", "note": "按规则做了还是亏，不要因此怀疑规则，要看很多次的整体。"},
    {"id": "flawed-good", "name": "判断错，运气好", "note": "最危险的一格：结果好会让人把错的做法重复下去。"},
    {"id": "flawed-bad", "name": "判断错，结果也差", "note": "最好学的一格：错在哪，写下来。"},
]
CONFIDENCE = range(10, 101, 10)


def packs(user_id: int) -> list:
    """这个人在学的、有决策日志的课。"""
    rows = db.q("SELECT pack_id FROM enrollments WHERE user_id=? AND active=1 ORDER BY pack_id", user_id)
    return [catalog.packs[r["pack_id"]] for r in rows if r["pack_id"] in catalog.packs and catalog.packs[r["pack_id"]].journal]


def _pack(user_id: int, pack_id: str):
    p = next((p for p in packs(user_id) if p.id == pack_id), None)
    if not p:
        raise HTTPException(404, "这门课没有决策日志")
    return p


def add(user_id: int, pack_id: str, form: dict) -> int:
    pack = _pack(user_id, pack_id)
    j = pack.journal
    subject = str(form.get("subject") or "").strip()[:200]
    if len(subject) < 2:
        raise HTTPException(400, f"先写下{j['subject_label']}")
    fields = {f["id"]: str(form.get(f["id"]) or "").strip()[:2000] for f in j["fields"]}
    if not any(fields.values()):
        raise HTTPException(400, "至少写一栏：理由也好，认错条件也好")
    try:
        conf = int(form.get("confidence") or 50)
        days = int(form.get("days") or j["review_days"][0])
    except ValueError:
        raise HTTPException(400, "把握和回看时间要选一个")
    if conf not in CONFIDENCE or not 1 <= days <= 730:
        raise HTTPException(400, "把握和回看时间要选一个")
    eid = db.insert("INSERT INTO journal(user_id,pack_id,subject,fields,confidence,review_on,created_at) VALUES(?,?,?,?,?,?,?)",
                    user_id, pack.id, subject, db.jdump(fields), conf, (db.today() + timedelta(days=days)).isoformat(), db.now())
    engine._touch_day(user_id)
    return eid


def _row(r, pack) -> dict:
    j = pack.journal
    fields = db.jload(r["fields"], {})
    quad = f"{r['judgment']}-{r['outcome']}" if r["reviewed_at"] else ""
    return {"id": r["id"], "pack": pack.id, "pack_name": pack.subject_name, "subject": r["subject"],
            "fields": [{"label": f["label"], "text": fields.get(f["id"], "")} for f in j["fields"] if fields.get(f["id"])],
            "confidence": r["confidence"], "review_on": r["review_on"], "created": r["created_at"][:10],
            "due": not r["reviewed_at"] and r["review_on"] <= db.today().isoformat(),
            "reviewed": bool(r["reviewed_at"]), "outcome": OUTCOMES.get(r["outcome"], ""),
            "judgment": JUDGMENTS.get(r["judgment"], ""), "quadrant": quad, "review": r["review"],
            "traps": [{"kp": t, "name": catalog.kp(t)["name"]} for t in db.jload(r["traps"], []) if catalog.kp(t)]}


def entries(user_id: int) -> list[dict]:
    """在学的课的日志，新的在前（退掉的课的日志留着，不显示）。"""
    by = {p.id: p for p in packs(user_id)}
    if not by:
        return []
    rows = db.q("SELECT * FROM journal WHERE user_id=? ORDER BY id DESC", user_id)
    return [_row(r, by[r["pack_id"]]) for r in rows if r["pack_id"] in by]


def entry(user_id: int, eid: int) -> dict:
    r = db.one("SELECT * FROM journal WHERE id=? AND user_id=?", eid, user_id)
    if not r:
        raise HTTPException(404, "没有这条日志")
    return _row(r, _pack(user_id, r["pack_id"]))


def due(user_id: int) -> list[dict]:
    """到了回看日期、还没复盘的。"""
    return [e for e in entries(user_id) if e["due"]]


def review(user_id: int, eid: int, form: dict) -> None:
    r = db.one("SELECT * FROM journal WHERE id=? AND user_id=?", eid, user_id)
    if not r:
        raise HTTPException(404, "没有这条日志")
    if r["reviewed_at"]:
        raise HTTPException(409, "这条已经复盘过了")
    pack = _pack(user_id, r["pack_id"])
    outcome, judgment = str(form.get("outcome") or ""), str(form.get("judgment") or "")
    if outcome not in OUTCOMES or judgment not in JUDGMENTS:
        raise HTTPException(400, "选一下结果好不好、当时的判断站不站得住")
    allowed = set(pack.journal.get("traps") or [])
    traps = [t for t in dict.fromkeys(form.get("traps") or []) if t in allowed]
    db.run("UPDATE journal SET reviewed_at=?, outcome=?, judgment=?, traps=?, review=? WHERE id=?",
           db.now(), outcome, judgment, db.jdump(traps), str(form.get("review") or "").strip()[:2000], eid)
    engine._touch_day(user_id)


def patterns(user_id: int) -> dict:
    """复盘过的日志里找规律：四个格子各几条、把握估得准不准、哪个陷阱反复出现。"""
    done = [e for e in entries(user_id) if e["reviewed"]]
    quads = [{**q, "n": sum(1 for e in done if e["quadrant"] == q["id"])} for q in QUADRANTS]
    found = []
    lucky = quads[2]["n"]
    if lucky:
        found.append(f"有 {lucky} 个决定判断有问题、结果却不错。这是最容易让人把错的做法重复下去的情况，回头再看看它们。")
    # 把握：说有七成以上把握的，结果好的有几成（至少 3 条才说）
    sure = [e for e in done if e["confidence"] >= 70]
    if len(sure) >= 3:
        hit = round(100 * sum(1 for e in sure if e["outcome"] == OUTCOMES["good"]) / len(sure))
        said = round(sum(e["confidence"] for e in sure) / len(sure))
        if hit <= said - 20:
            found.append(f"你说有七成以上把握的 {len(sure)} 个决定，结果好的只有 {hit}%，平均你说的是 {said}%：把握常常估高了，仓位可以再小一点。")
        elif hit >= said - 5:
            found.append(f"你说有七成以上把握的 {len(sure)} 个决定，结果好的有 {hit}%，和你估的 {said}% 差不多：你对把握的估计比较准。")
    counts: dict[str, dict] = {}
    for e in done:
        for t in e["traps"]:
            counts.setdefault(t["kp"], {**t, "n": 0})["n"] += 1
    traps = sorted(counts.values(), key=lambda t: -t["n"])
    if traps and traps[0]["n"] >= 2:
        found.append(f"「{traps[0]['name']}」出现了 {traps[0]['n']} 次，值得回去把那一课再看一遍。")
    return {"reviewed": len(done), "quadrants": quads, "traps": traps, "found": found}


def review_choices(pack_id: str) -> list[dict]:
    p = catalog.packs.get(pack_id)
    return [{"kp": t, "name": catalog.kp(t)["name"]} for t in (p.journal.get("traps") or [])] if p else []


def days_label(n: int) -> str:
    return f"{n // 30} 个月后" if n % 30 == 0 and n >= 30 else f"{n} 天后"


def due_soon(user_id: int) -> str:
    """下一条要回看的日期（没有就为空）。"""
    left = [e["review_on"] for e in entries(user_id) if not e["reviewed"] and not e["due"]]
    return min(left) if left else ""

