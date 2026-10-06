"""学习引擎：掌握度、间隔复习、诊断（向后回溯）、每日计划（补弱/回溯/复习/预习/阅读）。"""
import json
import random
import uuid
from datetime import date, timedelta

from . import db, llm
from .catalog import catalog, stage_rank

# ------------------------------------------------------------------ 掌握度

STATUS_LABEL = {"unknown": "未测", "weak": "薄弱", "learning": "学习中", "mastered": "已掌握"}


def _status(score: float, attempts: int, correct: int) -> str:
    if attempts == 0:
        return "unknown"
    if score >= 0.8 and correct >= 2:
        return "mastered"
    if score < 0.4:
        return "weak"
    return "learning"


def get_mastery(user_id: int) -> dict[str, dict]:
    return {r["kp_id"]: dict(r) for r in db.q("SELECT * FROM mastery WHERE user_id=?", user_id)}


def update_mastery(user_id: int, kp_id: str, correct: bool, weight=1.0, source="practice"):
    row = db.one("SELECT * FROM mastery WHERE user_id=? AND kp_id=?", user_id, kp_id)
    score = row["score"] if row else 0.3
    attempts = (row["attempts"] if row else 0) + 1
    ncorrect = (row["correct"] if row else 0) + (1 if correct else 0)
    if correct:
        score = score + (1 - score) * 0.35 * weight
    else:
        score = score - score * 0.45 * weight
    status = _status(score, attempts, ncorrect)
    db.run(
        "INSERT INTO mastery(user_id,kp_id,score,attempts,correct,status,source,updated_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id,kp_id) DO UPDATE SET score=excluded.score, attempts=excluded.attempts, "
        "correct=excluded.correct, status=excluded.status, source=excluded.source, updated_at=excluded.updated_at",
        user_id, kp_id, score, attempts, ncorrect, status, source, db.now())
    return status


def set_mastery(user_id: int, kp_id: str, score: float, status: str, source: str):
    db.run(
        "INSERT INTO mastery(user_id,kp_id,score,attempts,correct,status,source,updated_at) VALUES(?,?,?,1,?,?,?,?) "
        "ON CONFLICT(user_id,kp_id) DO UPDATE SET score=excluded.score, status=excluded.status, "
        "source=excluded.source, updated_at=excluded.updated_at",
        user_id, kp_id, score, 1 if status == "mastered" else 0, status, source, db.now())


# ------------------------------------------------------------------ 间隔复习卡片

INTERVALS = [1, 2, 4, 7, 15, 30, 60]


def add_card(user_id: int, kind: str, front: str, back: str, extra=None, kp_id=None, starred=0) -> int | None:
    front = front.strip()[:300]
    if not front:
        return None
    exist = db.one("SELECT id FROM cards WHERE user_id=? AND kind=? AND front=?", user_id, kind, front)
    if exist:
        if starred:
            db.run("UPDATE cards SET starred=1 WHERE id=?", exist["id"])
        return exist["id"]
    return db.insert(
        "INSERT INTO cards(user_id,kind,front,back,extra,kp_id,box,due,created_at,starred) VALUES(?,?,?,?,?,?,0,?,?,?)",
        user_id, kind, front, back, db.jdump(extra or {}), kp_id, (db.today() + timedelta(days=1)).isoformat(), db.now(), starred)


def review_card(user_id: int, card_id: int, grade: str):
    """grade: again(忘了) / hard(模糊) / good(记得)"""
    c = db.one("SELECT * FROM cards WHERE id=? AND user_id=?", card_id, user_id)
    if not c:
        return None
    box = c["box"]
    if grade == "again":
        box, lapses = 0, c["lapses"] + 1
        due = db.today()  # 今天再来一次
    else:
        lapses = c["lapses"]
        box = box + 1 if grade == "good" else max(box, 1)
        due = db.today() + timedelta(days=INTERVALS[min(box, len(INTERVALS) - 1)] if grade == "good" else 1)
    db.run("UPDATE cards SET box=?, due=?, lapses=?, reviews=reviews+1, last_review=? WHERE id=?",
           box, due.isoformat(), lapses, db.now(), card_id)
    if c["kp_id"]:
        update_mastery(user_id, c["kp_id"], grade == "good", weight=0.5, source="review")
    return {"box": box, "due": due.isoformat()}


def due_cards(user_id: int, limit=50):
    return db.q("SELECT * FROM cards WHERE user_id=? AND due<=? ORDER BY box, due LIMIT ?",
                user_id, db.today().isoformat(), limit)


# ------------------------------------------------------------------ 题库

def load_seed_items(seed_dir):
    """把 seed/items_*.json 导入题库（幂等）。"""
    n = 0
    for path in sorted(seed_dir.glob("items_*.json")):
        for it in json.loads(path.read_text(encoding="utf-8")):
            kps = it.get("kp") or []
            if not kps:
                continue
            data = {k: v for k, v in it.items() if k not in ("id", "kp", "kid", "difficulty", "type")}
            db.run("INSERT INTO items(id,kp_id,kp_ids,type,difficulty,data,source,created_at) VALUES(?,?,?,?,?,?,?,?) "
                   "ON CONFLICT(id) DO NOTHING",
                   it["id"], kps[0], db.jdump(kps), it["type"], it.get("difficulty", 2), db.jdump(data), "seed", db.now())
            n += 1
    return n


def _item_row_to_dict(r) -> dict:
    d = db.jload(r["data"], {})
    d.update(id=r["id"], kp_id=r["kp_id"], kp_ids=db.jload(r["kp_ids"], []), type=r["type"], difficulty=r["difficulty"], source=r["source"])
    return d


def save_items(kp_id: str, items: list[dict], source="ai") -> list[dict]:
    out = []
    for it in items:
        iid = "AI-" + uuid.uuid4().hex[:10]
        data = {k: v for k, v in it.items() if k not in ("type", "difficulty", "id")}
        try:
            diff = int(it.get("difficulty", 2))
        except (TypeError, ValueError):
            diff = 2
        db.run("INSERT INTO items(id,kp_id,kp_ids,type,difficulty,data,source,created_at) VALUES(?,?,?,?,?,?,?,?)",
               iid, kp_id, db.jdump([kp_id]), it["type"], diff, db.jdump(data), source, db.now())
        out.append(_item_row_to_dict(db.one("SELECT * FROM items WHERE id=?", iid)))
    return out


def items_for(user_id: int, kp_id: str, n=3, purpose="practice", grade="G3") -> list[dict]:
    """取题：优先没做过的、难度从低到高；不够且配置了 AI 时现场出题并存入题库。"""
    rows = db.q(
        "SELECT i.*, (SELECT COUNT(*) FROM attempts a WHERE a.item_id=i.id AND a.user_id=?) AS done, "
        "(SELECT COUNT(*) FROM attempts a WHERE a.item_id=i.id AND a.user_id=? AND a.correct=1) AS ok "
        "FROM items i WHERE i.kp_id=? OR i.kp_ids LIKE ?", user_id, user_id, kp_id, f'%"{kp_id}"%')
    fresh = [r for r in rows if r["done"] == 0]
    redo = [r for r in rows if r["done"] > 0 and r["ok"] == 0]  # 做错过的题，换个时间再做
    pool = sorted(fresh, key=lambda r: r["difficulty"]) + redo
    if purpose == "diagnose":
        pool = sorted(rows, key=lambda r: (r["done"] > 0, abs(r["difficulty"] - 2)))
    picked = [_item_row_to_dict(r) for r in pool[:n]]
    if len(picked) < n and llm.enabled():
        kp = catalog.kp(kp_id)
        pack = catalog.packs[catalog.kp_pack[kp_id]]
        try:
            new = llm.generate_items(kp, pack, grade, n=max(3, n - len(picked)), purpose=purpose, user_id=user_id)
            picked += save_items(kp_id, new)[: n - len(picked)]
        except llm.LLMError:
            pass
    return picked[:n]


def check_answer(item: dict, answer) -> bool | None:
    t = item["type"]
    if t == "short":
        return None  # 自评
    if answer is None or str(answer).strip() == "":
        return False
    if t == "mcq":
        try:
            return int(answer) == int(item["answer"])
        except (TypeError, ValueError):
            return False
    if t == "num":
        try:
            val = float(str(answer).replace(",", "").split()[0])
            target = float(item["answer"])
        except (TypeError, ValueError, IndexError):
            return False
        tol = float(item.get("tol") or 0) or max(abs(target) * 0.01, 1e-9)
        return abs(val - target) <= tol + 1e-12
    if t == "fill":
        accepted = item["answer"] if isinstance(item["answer"], list) else [item["answer"]]
        norm = lambda s: "".join(str(s).lower().split()).strip("。.!！")
        return norm(answer) in {norm(a) for a in accepted}
    return False


def record_attempt(user_id: int, item: dict | None, kp_id: str, mode: str, correct: bool, answer=""):
    db.run("INSERT INTO attempts(user_id,item_id,kp_id,mode,correct,answer,created_at) VALUES(?,?,?,?,?,?,?)",
           user_id, item["id"] if item else None, kp_id, mode, 1 if correct else 0, str(answer)[:500], db.now())
    if mode == "diagnose" and correct:
        set_mastery(user_id, kp_id, 0.8, "mastered", "diagnose")
        status = "mastered"
    else:
        status = update_mastery(user_id, kp_id, correct, source=mode)
    if item and not correct and mode in ("practice", "diagnose"):
        # 错题自动进错题本（以卡片形式参与间隔复习）
        ans = item.get("answer")
        if item["type"] == "mcq":
            try:
                ans = item["options"][int(ans)]
            except (TypeError, ValueError, IndexError, KeyError):
                pass
        elif item["type"] == "short":
            ans = item.get("model", "")
        elif isinstance(ans, list):
            ans = " / ".join(map(str, ans))
        back = f"{ans}{(' ' + item.get('unit', '')) if item.get('unit') else ''}\n{item.get('explain', '')}"
        add_card(user_id, "mistake", item["q"], back.strip(), {"item_id": item["id"], "zh": item.get("zh", ""), "my_answer": str(answer)}, kp_id)
    _touch_day(user_id, 2)
    return status


# ------------------------------------------------------------------ 诊断（向后回溯）

MAX_DIAG = 18
MAX_DEPTH = 3


def enrollment_stage(user_id: int, pack_id: str) -> str | None:
    r = db.one("SELECT stage FROM enrollments WHERE user_id=? AND pack_id=?", user_id, pack_id)
    return r["stage"] if r else None


def start_diagnosis(user_id: int, pack_id: str, stage: str) -> int:
    """从当前学段（及上一学段）里选核心知识点做探测；做错就沿「必须」前置往回查，最多 3 级。"""
    pack = catalog.packs[pack_id]
    idx = pack.stages.index(stage) if stage in pack.stages else len(pack.stages) - 1
    window = pack.stages[max(0, idx - 1): idx + 1]
    cands = [k for k in catalog.pack_kps(pack_id) if k["stage"] in window]
    has_items = {r["kp_id"] for r in db.q("SELECT DISTINCT kp_id FROM items")}
    # 高频 > 有现成题 > 当前学段；每个板块至少一个
    cands.sort(key=lambda k: (not k.get("hot"), k["id"] not in has_items, k["stage"] != stage))
    queue, seen_strand = [], set()
    for k in cands:
        if k["strand"] not in seen_strand:
            queue.append(k["id"]); seen_strand.add(k["strand"])
    for k in cands:
        if len(queue) >= 8:
            break
        if k["id"] not in queue:
            queue.append(k["id"])
    state = {"queue": [{"kp": q, "depth": 0, "from": None} for q in queue], "done": [], "current": None}
    return db.insert("INSERT INTO diag_sessions(user_id,pack_id,state,created_at) VALUES(?,?,?,?)",
                  user_id, pack_id, db.jdump(state), db.now())


def diag_next(user_id: int, sid: int, grade: str):
    s = db.one("SELECT * FROM diag_sessions WHERE id=? AND user_id=?", sid, user_id)
    if not s or s["status"] != "running":
        return None
    state = db.jload(s["state"])
    if state.get("current"):
        return state["current"]
    while state["queue"] and len(state["done"]) < MAX_DIAG:
        nxt = state["queue"].pop(0)
        if any(d["kp"] == nxt["kp"] for d in state["done"]):
            continue
        items = items_for(user_id, nxt["kp"], n=1, purpose="diagnose", grade=grade)
        cur = {**nxt, "item": items[0] if items else None, "n": len(state["done"]) + 1}
        state["current"] = cur
        db.run("UPDATE diag_sessions SET state=? WHERE id=?", db.jdump(state), sid)
        return cur
    finish_diagnosis(user_id, sid, state)
    return None


def diag_answer(user_id: int, sid: int, answer, self_rating: str | None = None):
    s = db.one("SELECT * FROM diag_sessions WHERE id=? AND user_id=?", sid, user_id)
    state = db.jload(s["state"])
    cur = state.get("current")
    if not cur:
        return None
    item = cur.get("item")
    if item and item["type"] != "short":
        ok = check_answer(item, answer)
    else:  # 没有题或简答题：孩子自评
        ok = self_rating == "know"
    if item:
        record_attempt(user_id, item, cur["kp"], "diagnose", bool(ok), answer if answer is not None else self_rating)
    else:
        db.run("INSERT INTO attempts(user_id,item_id,kp_id,mode,correct,answer,created_at) VALUES(?,?,?,?,?,?,?)",
               user_id, None, cur["kp"], "diagnose-self", 1 if ok else 0, self_rating or "", db.now())
        if ok:
            set_mastery(user_id, cur["kp"], 0.7, "learning", "diagnose-self")
        else:
            update_mastery(user_id, cur["kp"], False, source="diagnose-self")
    state["done"].append({"kp": cur["kp"], "ok": bool(ok), "depth": cur["depth"], "from": cur["from"]})
    if not ok and cur["depth"] < MAX_DEPTH:
        for p in catalog.prereqs(cur["kp"], required_only=True):
            if not any(d["kp"] == p["id"] for d in state["done"]):
                state["queue"].insert(0, {"kp": p["id"], "depth": cur["depth"] + 1, "from": cur["kp"]})
    state["current"] = None
    db.run("UPDATE diag_sessions SET state=? WHERE id=?", db.jdump(state), sid)
    return {"ok": ok, "item": item}


def finish_diagnosis(user_id: int, sid: int, state=None):
    if state is None:
        state = db.jload(db.one("SELECT state FROM diag_sessions WHERE id=?", sid)["state"])
    # 答对的点，其「必须」前置视为大概率已掌握（未测过的才标）
    m = get_mastery(user_id)
    for d in state["done"]:
        if d["ok"]:
            for p, _ in catalog.ancestors(d["kp"], depth=2):
                if p["id"] not in m:
                    set_mastery(user_id, p["id"], 0.65, "learning", "inferred")
    state["current"] = None
    db.run("UPDATE diag_sessions SET state=?, status='done', finished_at=? WHERE id=?", db.jdump(state), db.now(), sid)


def diag_report(sid: int):
    s = db.one("SELECT * FROM diag_sessions WHERE id=?", sid)
    state = db.jload(s["state"])
    weak = [d for d in state["done"] if not d["ok"]]
    ok = [d for d in state["done"] if d["ok"]]
    # 根源：做错的点中，其必须前置都已通过（或没有前置）的——这是该先补的地方
    failed = {d["kp"] for d in weak}
    roots = [d for d in weak if not any(p["id"] in failed for p in catalog.prereqs(d["kp"], True))]
    return {"session": dict(s), "weak": weak, "ok": ok, "roots": roots}


# ------------------------------------------------------------------ 每日计划

def _touch_day(user_id: int, minutes: int):
    day = db.today().isoformat()
    db.run("INSERT INTO days(user_id,day,plan,minutes) VALUES(?,?,'[]',?) "
           "ON CONFLICT(user_id,day) DO UPDATE SET minutes=days.minutes+?", user_id, day, minutes, minutes)


def frontier(user_id: int, pack_id: str, stage: str, mastery: dict) -> list[dict]:
    """预习候选：前置都已掌握/学习中、自己还没学的知识点，限当前学段及下一学段。"""
    pack = catalog.packs[pack_id]
    idx = pack.stages.index(stage) if stage in pack.stages else 0
    window = set(pack.stages[idx: idx + 2])
    out = []
    for k in catalog.pack_kps(pack_id):
        if k["stage"] not in window or k["id"] in mastery and mastery[k["id"]]["status"] != "unknown":
            continue
        reqs = catalog.prereqs(k["id"], required_only=True)
        if all(mastery.get(p["id"], {}).get("status") in ("mastered", "learning") for p in reqs):
            out.append(k)
    out.sort(key=lambda k: (stage_rank(k["stage"]), not k.get("hot")))
    return out


def build_plan(user_id: int) -> list[dict]:
    user = db.one("SELECT * FROM users WHERE id=?", user_id)
    budget = user["daily_minutes"] or 60
    mastery = get_mastery(user_id)
    enrolls = db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", user_id)
    tasks: list[dict] = []

    ncards = len(due_cards(user_id, 200))
    if ncards:
        tasks.append({"type": "review", "title": f"复习到期卡片 {ncards} 张", "why": "间隔复习：今天不复习就会开始忘",
                      "minutes": min(15, 3 + ncards // 3), "url": "/review"})

    weak_all, back_all, pre_all, diag_needed = [], [], [], []
    for e in enrolls:
        pack_id, stage = e["pack_id"], e["stage"]
        if pack_id not in catalog.packs:
            continue
        diag = db.one("SELECT id FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='done'", user_id, pack_id)
        if not diag:
            diag_needed.append(pack_id)
        kp_ids = set(catalog.packs[pack_id].kp_ids)
        weak = [mastery[k] for k in kp_ids if k in mastery and
                (mastery[k]["status"] == "weak" or mastery[k]["status"] == "learning" and mastery[k]["score"] < 0.6)]
        weak.sort(key=lambda m: m["score"])
        for w in weak[:3]:
            kp = catalog.kp(w["kp_id"])
            # 回溯：薄弱点的必须前置里，有没掌握的就先补前置
            gap = [p for p, _ in catalog.ancestors(w["kp_id"], depth=3)
                   if mastery.get(p["id"], {}).get("status") in (None, "weak", "unknown")]
            if gap:
                g = gap[0]
                back_all.append({"type": "backfill", "kp": g["id"], "for": w["kp_id"],
                                 "title": f"补前置：{g['name']}", "why": f"「{kp['name']}」要用到它", "minutes": 10, "pack": pack_id})
            else:
                weak_all.append({"type": "weak", "kp": w["kp_id"], "title": f"攻克：{kp['name']}",
                                 "why": f"掌握度 {int(w['score'] * 100)}%，{'刚学' if w['status'] == 'learning' else '薄弱'}", "minutes": 12, "pack": pack_id})
        fr = frontier(user_id, pack_id, stage, mastery)[:2] if diag else []  # 诊断前不安排预习
        for k in fr:
            pre_all.append({"type": "preview", "kp": k["id"], "title": f"预习：{k['name']}",
                            "why": "前置已具备" + ("，高频考点" if k.get("hot") else ""), "minutes": 8, "pack": pack_id})

    for p in diag_needed[:1]:
        pk = catalog.packs[p]
        tasks.append({"type": "diagnose", "pack": p, "title": f"{pk.subject_name}摸底诊断（约 10 分钟）",
                      "why": "先找到真正的薄弱点，计划才准", "minutes": 12, "url": f"/diagnose/{p}"})

    # 比例约 50% 补弱 / 20% 回溯 / 20% 复习(上面) / 10% 预习，按时间预算截断
    def interleave(*lists):
        out, i = [], 0
        while any(i < len(l) for l in lists):
            for l in lists:
                if i < len(l):
                    out.append(l[i])
            i += 1
        return out

    for t in interleave(weak_all, back_all) + pre_all[:2]:
        t["url"] = f"/learn/{t['kp']}?task={t['type']}"
        tasks.append(t)

    langs = {catalog.packs[e["pack_id"]].lang for e in enrolls if e["pack_id"] in catalog.packs} - {""}
    for lang in sorted(langs):
        tasks.append({"type": "reading", "lang": lang, "title": "英文阅读 15 分钟" if lang == "en" else "中文阅读 15 分钟",
                      "why": "大量输入：不懂的词点一下就查，收藏后自动进复习", "minutes": 15, "url": f"/reading?lang={lang}"})

    out, used = [], 0
    for t in tasks:
        if used + t["minutes"] > budget and out and t["type"] not in ("review", "reading"):
            continue
        t["id"] = f"t{len(out)}"
        t["done"] = False
        out.append(t)
        used += t["minutes"]
    return out


def today_plan(user_id: int, rebuild=False) -> dict:
    day = db.today().isoformat()
    row = db.one("SELECT * FROM days WHERE user_id=? AND day=?", user_id, day)
    plan = db.jload(row["plan"], []) if row else []
    if rebuild or not plan:
        old_done = {t["title"] for t in plan if t.get("done")}
        plan = build_plan(user_id)
        for t in plan:
            t["done"] = t["title"] in old_done
        db.run("INSERT INTO days(user_id,day,plan) VALUES(?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET plan=excluded.plan",
               user_id, day, db.jdump(plan))
        row = db.one("SELECT * FROM days WHERE user_id=? AND day=?", user_id, day)
    return {"day": day, "plan": plan, "minutes": row["minutes"], "checked_in": row["checked_in"], "reflection": row["reflection"]}


def mark_task(user_id: int, task_id: str, done=True):
    day = db.today().isoformat()
    row = db.one("SELECT plan FROM days WHERE user_id=? AND day=?", user_id, day)
    if not row:
        return
    plan = db.jload(row["plan"], [])
    for t in plan:
        if t["id"] == task_id:
            t["done"] = done
    db.run("UPDATE days SET plan=? WHERE user_id=? AND day=?", db.jdump(plan), user_id, day)


def mark_task_by(user_id: int, **match):
    day = db.today().isoformat()
    row = db.one("SELECT plan FROM days WHERE user_id=? AND day=?", user_id, day)
    if not row:
        return
    plan = db.jload(row["plan"], [])
    for t in plan:
        if all(t.get(k) == v for k, v in match.items()):
            t["done"] = True
    db.run("UPDATE days SET plan=? WHERE user_id=? AND day=?", db.jdump(plan), user_id, day)


# ------------------------------------------------------------------ 统计

def streak(user_id: int) -> int:
    days = {r["day"] for r in db.q("SELECT day FROM days WHERE user_id=? AND (checked_in=1 OR minutes>0)", user_id)}
    d, n = db.today(), 0
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def pack_summary(user_id: int, pack_id: str, mastery=None) -> dict:
    mastery = mastery if mastery is not None else get_mastery(user_id)
    ids = catalog.packs[pack_id].kp_ids
    c = {"unknown": 0, "weak": 0, "learning": 0, "mastered": 0}
    for k in ids:
        c[mastery.get(k, {}).get("status", "unknown")] += 1
    c["total"] = len(ids)
    return c


def calendar(user_id: int, weeks=8) -> list[dict]:
    start = db.today() - timedelta(days=weeks * 7 - 1)
    rows = {r["day"]: r for r in db.q("SELECT * FROM days WHERE user_id=? AND day>=?", user_id, start.isoformat())}
    out = []
    for i in range(weeks * 7):
        d = (start + timedelta(days=i)).isoformat()
        r = rows.get(d)
        plan = db.jload(r["plan"], []) if r else []
        done = sum(1 for t in plan if t.get("done"))
        out.append({"day": d, "minutes": r["minutes"] if r else 0, "checked": bool(r and r["checked_in"]),
                    "done": done, "total": len(plan)})
    return out


def seed_vocab(user_id: int, pack_id: str, seed_dir) -> int:
    """把 seed/vocab_*.json 里属于该教材包的术语卡加入孩子的复习卡片（幂等）。"""
    kp_ids = set(catalog.packs[pack_id].kp_ids)
    n = 0
    for path in sorted(seed_dir.glob("vocab_*.json")):
        for v in json.loads(path.read_text(encoding="utf-8")):
            if v.get("kp") in kp_ids:
                if add_card(user_id, "term", v["front"], v["back"], {"example": v.get("example", ""), "deck": v.get("deck", "")}, v["kp"]):
                    n += 1
    return n
