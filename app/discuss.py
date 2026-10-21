"""思辨题：没有标准答案的题不判对错，AI 陪学员把问题想透（docs/DESIGN.md §4.7）。

一次思辨（discussions 表的一行）：
  学员写下判断和理由 → AI 先把学员的观点说得更有力，回答学员的反问，再提一个挑战（反例、换个条件、站到另一方）
  → 学员接着想，最多 MAX_TURNS 轮，随时可以收尾 → 学员写下自己的结论和一个动作 → AI 对照第一段话说想法变了什么，
  标出结论接近题里哪几种看法、有没有自己的东西、在什么情况下会出问题。
不判对错、不进错题本、不算掌握度的对错证据。记下的是这次用到了哪些思考方式（课程包的 thinking_moves）：
第一段话里就用到的算「自己想到」，被挑战以后才用到的算「追问后想到」，攒起来就是学员的思考画像（profile）。
想完以后，题里的 twists 按 3 / 7 / 14 天排期：到时换一个条件再问一次（due）。没配置 AI 时跳过对话，直接写结论、看几种看法。
学员写的话里有自伤、轻生的信号（app/safety.py，或 AI 判断），这道题停下、给出求助渠道，这段话不存。
"""
from datetime import timedelta

from fastapi import HTTPException

from . import db, engine, llm, safety
from .catalog import catalog

MAX_TURNS = 4            # 学员最多说几轮，之后只能写结论
REVISIT_DAYS = (3, 7, 14)  # 第 1、2、3 次换条件再想，隔几天


def _pack(kp_id: str):
    return catalog.packs[catalog.kp_pack[kp_id]]


def _moves_of(kp_id: str) -> dict:
    return {m["id"]: m for m in _pack(kp_id).thinking_moves}


def _seen(raw, known: dict) -> list[dict]:
    """AI 说学员用到的思考方式：只认课程包里列了的，去重。"""
    out, ids = [], set()
    for m in raw or []:
        mid = str((m or {}).get("id", "")) if isinstance(m, dict) else str(m)
        if mid in known and mid not in ids:
            ids.add(mid)
            out.append({"id": mid, "name": known[mid]["name"], "kp": known[mid].get("kp", ""),
                        "note": str(m.get("note") or "") if isinstance(m, dict) else ""})
    return out


def _twist(user_id: int, item: dict, rethink) -> tuple[str, int | None]:
    """换条件再想：从上一次想完、已经到期的那条记录取出这次的条件。"""
    if not rethink:
        return "", None
    prev = db.one("SELECT * FROM discussions WHERE id=? AND user_id=? AND item_id=? AND done=1 AND next_due<>''",
                  rethink, user_id, item["id"])
    if not prev:
        raise HTTPException(409, "这道题现在不用再想了")
    n = db.one("SELECT COUNT(*) AS n FROM discussions WHERE user_id=? AND item_id=? AND done=1", user_id, item["id"])["n"]
    twists = item.get("twists") or []
    return (twists[n - 1] if 0 < n <= len(twists) else ""), prev["id"]


def handle(user_id: int, item: dict, kp_id: str, body: dict, grade: str) -> dict:
    out = _close(user_id, item, kp_id, body, grade) if body.get("conclusion") is not None else _turn(user_id, item, kp_id, body, grade)
    return {**out, "action_label": _pack(kp_id).discuss_action}


def _care(body: dict) -> dict:
    """先关心人：这一轮不回应题目、不存学员这段话，给出求助渠道。"""
    return {"discuss": True, "care": safety.CARE, "discussion_id": body.get("discussion_id"), "done": False,
            "can_continue": False, "ai": False}


def _turn(user_id, item, kp_id, body, grade) -> dict:
    text = str(body.get("answer") or "").strip()[:3000]
    if len(text) < 4:
        raise HTTPException(400, "先写下你的想法")
    if safety.crisis(text):
        return _care(body)
    if body.get("discussion_id"):
        row = db.one("SELECT * FROM discussions WHERE id=? AND user_id=? AND item_id=? AND done=0",
                     body["discussion_id"], user_id, item["id"])
        if not row:
            raise HTTPException(409, "这次思辨已经结束了")
        twist, turns, moves = row["twist"], db.jload(row["turns"], []), db.jload(row["moves"], {})
    else:
        twist, _ = _twist(user_id, item, body.get("rethink"))
        row, turns, moves = None, [], {}
    mine = sum(1 for t in turns if t.get("who") == "me")
    if mine >= MAX_TURNS:
        raise HTTPException(409, "想得差不多了，写下你的结论吧")
    turns.append({"who": "me", "text": text})
    mine += 1
    known = _moves_of(kp_id)
    reply, ai = {}, llm.enabled()
    if ai:
        try:
            reply = llm.discuss_turn(item, catalog.kp(kp_id), _pack(kp_id), grade, twist, turns, user_id=user_id)
        except llm.LLMError:
            ai = False
    if reply.get("care") is True:
        return _care(body)
    seen = _seen(reply.get("moves"), known)
    for m in seen:
        moves.setdefault(m["id"], "self" if mine == 1 else "prompted")
    if ai:
        turns.append({"who": "ai", **{k: str(reply.get(k) or "") for k in ("steelman", "answer", "challenge")}})
    now = db.now()
    if row:
        did = row["id"]
        db.run("UPDATE discussions SET turns=?, moves=?, updated_at=? WHERE id=?", db.jdump(turns), db.jdump(moves), now, did)
    else:
        did = db.insert("INSERT INTO discussions(user_id,item_id,kp_id,twist,turns,moves,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?)", user_id, item["id"], kp_id, twist, db.jdump(turns), db.jdump(moves), now, now)
    engine._touch_day(user_id)
    return {"discuss": True, "discussion_id": did, "turn": mine, "ai": ai,
            "steelman": turns[-1].get("steelman", "") if ai else "", "answer": turns[-1].get("answer", "") if ai else "",
            "challenge": turns[-1].get("challenge", "") if ai else "", "moves_now": seen,
            "can_continue": ai and mine < MAX_TURNS, "done": False}


def _close(user_id, item, kp_id, body, grade) -> dict:
    conclusion = str(body.get("conclusion") or "").strip()[:2000]
    action = str(body.get("action") or "").strip()[:1000]
    if len(conclusion) < 4:
        raise HTTPException(400, "写下你现在的结论")
    if safety.crisis(conclusion + "\n" + action):
        return _care(body)
    row = db.one("SELECT * FROM discussions WHERE id=? AND user_id=? AND item_id=? AND done=0",
                 body.get("discussion_id"), user_id, item["id"])
    if not row:
        raise HTTPException(409, "这次思辨已经结束了")
    turns, moves = db.jload(row["turns"], []), db.jload(row["moves"], {})
    known = _moves_of(kp_id)
    r = {}
    if llm.enabled():
        try:
            r = llm.discuss_close(item, catalog.kp(kp_id), _pack(kp_id), grade, row["twist"], turns, conclusion, action,
                                  user_id=user_id)
        except llm.LLMError:
            r = {}
    if r.get("care") is True:
        return _care(body)
    for m in _seen(r.get("moves"), known):
        moves.setdefault(m["id"], "prompted")
    near = {int(i) for i in r.get("near") or [] if str(i).isdigit()}
    views = [{**p, "near": (i + 1) in near} for i, p in enumerate(item.get("perspectives") or [])]
    # 下一次换条件再想：第 n 次想完，排第 n 个 twist
    n = db.one("SELECT COUNT(*) AS n FROM discussions WHERE user_id=? AND item_id=? AND done=1", user_id, item["id"])["n"] + 1
    twists = item.get("twists") or []
    nxt = None
    if n <= len(twists) and n <= len(REVISIT_DAYS):
        nxt = {"due": (db.today() + timedelta(days=REVISIT_DAYS[n - 1])).isoformat(), "twist": twists[n - 1]}
    now = db.now()
    db.run("UPDATE discussions SET next_due='' WHERE user_id=? AND item_id=? AND done=1", user_id, item["id"])
    db.run("UPDATE discussions SET moves=?, conclusion=?, action=?, done=1, next_due=?, updated_at=? WHERE id=?",
           db.jdump(moves), conclusion, action, nxt["due"] if nxt else "", now, row["id"])
    engine._touch_day(user_id)
    return {"discuss": True, "done": True, "discussion_id": row["id"], "correct": None,
            "shift": str(r.get("shift") or ""), "own": str(r.get("own") or ""), "watch": str(r.get("watch") or ""),
            "perspectives": views, "conclusion": conclusion, "action": action,
            "moves": [{"id": k, "name": known[k]["name"], "kp": known[k].get("kp", ""), "how": v} for k, v in moves.items() if k in known],
            "next": nxt}


def due(user_id: int) -> list[dict]:
    """到期该换条件再想的题。"""
    rows = db.q("SELECT d.*, i.data, i.type FROM discussions d JOIN items i ON i.id=d.item_id "
                "WHERE d.user_id=? AND d.done=1 AND d.next_due<>'' AND d.next_due<=? ORDER BY d.next_due",
                user_id, db.today().isoformat())
    paused = engine.Paused(user_id).kps   # 退掉的课先不再问，选回来接着排
    out = []
    for r in rows:
        if not catalog.kp(r["kp_id"]) or r["kp_id"] in paused:
            continue
        it = db.jload(r["data"], {})
        n = db.one("SELECT COUNT(*) AS n FROM discussions WHERE user_id=? AND item_id=? AND done=1", user_id, r["item_id"])["n"]
        twists = it.get("twists") or []
        out.append({"id": r["id"], "item_id": r["item_id"], "kp_id": r["kp_id"], "kp_name": catalog.kp(r["kp_id"])["name"],
                    "q": it.get("q", ""), "twist": twists[n - 1] if 0 < n <= len(twists) else "", "conclusion": r["conclusion"]})
    return out


def upcoming(user_id: int) -> list[dict]:
    paused = engine.Paused(user_id).kps
    return [r for r in db.q("SELECT kp_id, next_due FROM discussions WHERE user_id=? AND done=1 AND next_due>? ORDER BY next_due",
                            user_id, db.today().isoformat()) if r["kp_id"] not in paused]


def profile(user_id: int) -> list[dict]:
    """思考画像：每门列了思考方式的课，每种思考方式自己想到几次、追问后想到几次。"""
    rows = db.q("SELECT kp_id, moves FROM discussions WHERE user_id=? AND done=1", user_id)
    by_pack: dict[str, dict] = {}
    for r in rows:
        if r["kp_id"] not in catalog.kp_pack:
            continue
        counts = by_pack.setdefault(catalog.kp_pack[r["kp_id"]], {})
        for mid, how in db.jload(r["moves"], {}).items():
            c = counts.setdefault(mid, {"self": 0, "prompted": 0})
            c["self" if how == "self" else "prompted"] += 1
    out = []
    for pid, counts in by_pack.items():
        pack = catalog.packs[pid]
        total = sum(1 for r in rows if catalog.kp_pack.get(r["kp_id"]) == pid)
        out.append({"pack": pack.subject_name, "discussions": total,
                    "moves": [{**m, **counts.get(m["id"], {"self": 0, "prompted": 0})} for m in pack.thinking_moves]})
    return out
