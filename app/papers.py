"""试卷：拍照 / 粘贴导入 → AI 拆题、对应知识点、读出原卷批改 → 孩子在线重做 → 诊断报告。

- 原卷上老师判错的题：直接进错题本，并计入掌握度（这是真实考试的证据）。
- 在线重做：自动判分（选择 / 数值 / 填空），简答题看参考答案自评；可以点「这道题还不会」。
- 诊断：按知识点汇总原卷丢分和重做结果，指出要补的前置知识点；薄弱点会自动进入每天的任务。
"""
import base64
import uuid

from . import bank, config, db, engine, itemtypes, llm
from .catalog import catalog, stage_rank

MAX_IMAGES = 8
MAX_IMAGE_BYTES = 6 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
PAPER_DIR = config.DATA_DIR / "papers"


def candidates(pack_id: str, stage: str) -> list[dict]:
    """给 AI 选的候选知识点：当前学段往前两段、往后一段。"""
    r = stage_rank(stage)
    pack = catalog.packs[pack_id]
    return [catalog.kps[k] for k in pack.kp_ids if r - 2 <= stage_rank(catalog.kps[k]["stage"]) <= r + 1]


_normalize = itemtypes.normalize
_num = itemtypes._num


def create(user_id: int, pack_id: str, *, title="", exam_date="", images=None, text="", created_by=None, shared=True) -> int:
    """images: [(media_type, bytes)]。先让 AI 解析（失败就什么都不保存），再入库。
    shared：家长同意的话，题目会由 AI 改编成新题进公共题库（原卷和作答不公开，见 app/bankflow.py）。"""
    kid = db.one("SELECT * FROM users WHERE id=?", user_id)
    pack = catalog.packs[pack_id]
    stage = engine.enrollment_stage(user_id, pack_id) or catalog.default_stage(pack_id, kid["grade"])
    cands = candidates(pack_id, stage)
    imgs = [(mt, base64.b64encode(b).decode()) for mt, b in (images or [])]
    data = llm.parse_paper(pack, kid["grade"], cands, images=imgs, text=text, user_id=user_id)
    qs = data.get("questions", [])
    if not qs:
        raise llm.LLMError("没有从卷子里认出题目。照片请拍清楚、拍正，一页一张；也可以改用粘贴文字")
    valid = set(pack.kp_ids)
    now = db.now()
    with db.tx() as t:
        pid = t.insert(
            "INSERT INTO papers(user_id,pack_id,title,exam_date,source,raw_text,notes,status,created_by,created_at,shared) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)", user_id, pack_id, (title or data.get("title") or "试卷")[:80], exam_date[:10],
            "photo" if images else "text", text[:20000], str(data.get("notes") or "")[:500], "ready", created_by, now,
            1 if shared else 0)
        for i, q in enumerate(qs):
            kp = q.get("kp_id") if q.get("kp_id") in valid else None
            it = _normalize(q)
            iid = "PP-" + uuid.uuid4().hex[:10]
            body = {k: v for k, v in it.items() if k != "type"}
            t.run("INSERT INTO items(id,kp_id,kp_ids,type,difficulty,data,source,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  iid, kp or "", db.jdump([kp] if kp else []), it["type"], 2, db.jdump(body), "paper", now)
            t.run("UPDATE items SET qhash=?, lang=?, purpose='paper' WHERE id=?", bank.item_hash(it), bank.kp_lang(kp) if kp else "", iid)
            if kp:
                t.run("INSERT INTO item_kps(item_id, kp_id, role) VALUES(?,?,'main')", iid, kp)
            page = q.get("page")
            t.run("INSERT INTO paper_items(paper_id,seq,label,item_id,kp_id,points,page,orig,orig_answer) VALUES(?,?,?,?,?,?,?,?,?)",
                  pid, i + 1, str(q.get("label") or i + 1)[:20], iid, kp, _num(q.get("score")),
                  int(page) if str(page).isdigit() else None,
                  q.get("marked") if q.get("marked") in ("right", "wrong", "partial") else "",
                  str(q.get("student_answer") or "")[:300])
    from . import bankflow
    for r in db.q("SELECT item_id, kp_id FROM paper_items WHERE paper_id=?", pid):
        bankflow.assign_near(r["item_id"], kp_id=r["kp_id"] or "")
    names = []
    if images:
        d = PAPER_DIR / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        for n, (mt, b) in enumerate(images, 1):
            name = f"{n}.{IMAGE_TYPES.get(mt, 'jpg')}"
            (d / name).write_bytes(b)
            names.append(name)
        db.run("UPDATE papers SET images=? WHERE id=?", db.jdump(names), pid)
    # 原卷批改结果：判错 / 扣分的题进错题本，并计入掌握度；卷子上考到的知识点 = 学校已经教过（进度自动往前走）
    engine.mark_taught(user_id, [pi["kp_id"] for pi in rows(pid) if pi["kp_id"]])
    for pi in rows(pid):
        if pi["orig"] in ("right", "wrong", "partial") and pi["kp_id"]:
            engine.record_attempt(user_id, pi["item"], pi["kp_id"], "exam", pi["orig"] == "right",
                                  pi["orig_answer"], touch=False)
        elif pi["orig"] in ("wrong", "partial"):
            it = pi["item"]
            engine.add_card(user_id, "mistake", it["q"], (engine.answer_display(it) + "\n" + it.get("explain", "")).strip(),
                            {"item_id": it["id"], "my_answer": pi["orig_answer"]})
    return pid


def get(user_id: int, paper_id: int):
    return db.one("SELECT * FROM papers WHERE id=? AND user_id=?", paper_id, user_id)


def rows(paper_id: int) -> list[dict]:
    out = []
    for r in db.q("SELECT * FROM paper_items WHERE paper_id=? ORDER BY seq", paper_id):
        item = db.one("SELECT * FROM items WHERE id=?", r["item_id"])
        out.append({**dict(r), "item": engine._item_row_to_dict(item) if item else None,
                    "kp": catalog.kp(r["kp_id"]) if r["kp_id"] else None})
    return [r for r in out if r["item"]]


def answer(user_id: int, paper: dict, pi_id: int, body: dict) -> dict:
    pi = db.one("SELECT * FROM paper_items WHERE id=? AND paper_id=?", pi_id, paper["id"])
    if not pi:
        return {"error": "没有这道题"}
    it = engine._item_row_to_dict(db.one("SELECT * FROM items WHERE id=?", pi["item_id"]))
    reveal = itemtypes.reveal(it)
    if body.get("flag"):  # 孩子 / 家长觉得 AI 给的答案不对：这题不计入诊断
        db.run("UPDATE paper_items SET flagged=1 WHERE id=?", pi_id)
        return {"ok": True, "flagged": True}
    if body.get("dont_know"):
        correct, ans, dk = False, "", True
    elif it["type"] == "short":
        if "self" not in body:
            return {"reveal": True, **reveal}
        correct, ans, dk = body["self"] == "ok", body.get("answer", ""), False
    else:
        correct, ans, dk = bool(engine.check_answer(it, body.get("answer"))), body.get("answer", ""), False
    if pi["kp_id"]:
        engine.record_attempt(user_id, it, pi["kp_id"], "paper", correct, ans, dont_know=dk, ms=body.get("ms"))
    elif not correct:
        engine.add_card(user_id, "mistake", it["q"], (reveal["answer"] + "\n" + reveal["explain"]).strip(),
                        {"item_id": it["id"], "my_answer": "（还不会）" if dk else str(ans)})
    db.run("UPDATE paper_items SET answer=?, correct=?, dont_know=?, answered_at=? WHERE id=?",
           str(ans)[:500], 1 if correct else 0, 1 if dk else 0, db.now(), pi_id)
    left = db.one("SELECT COUNT(*) AS n FROM paper_items WHERE paper_id=? AND answered_at IS NULL AND flagged=0", paper["id"])["n"]
    return {"correct": correct, "dont_know": dk, "left": left, **reveal}


def report(user_id: int, paper: dict) -> dict:
    """按知识点汇总：原卷丢分、重做对错、还要补的前置。"""
    rs = [r for r in rows(paper["id"]) if not r["flagged"]]
    m = engine.get_mastery(user_id)
    by_kp: dict = {}
    for r in rs:
        k = r["kp_id"] or ""
        g = by_kp.setdefault(k, {"kp": r["kp"], "n": 0, "orig_wrong": 0, "lost": 0.0, "orig_right": 0, "redo_right": 0, "redo_wrong": 0,
                                 "dont_know": 0, "labels": []})
        g["n"] += 1
        g["labels"].append(r["label"])
        if r["orig"] == "right":
            g["orig_right"] += 1
        if r["orig"] in ("wrong", "partial"):
            g["orig_wrong"] += 1
            g["lost"] += r["points"] or 0
        if r["answered_at"]:
            if r["dont_know"]:
                g["dont_know"] += 1
            elif r["correct"]:
                g["redo_right"] += 1
            else:
                g["redo_wrong"] += 1
    groups = []
    for k, g in by_kp.items():
        g["status"] = m.get(k, {}).get("status", "unknown") if k else ""
        # 重做还错 / 还不会 → 最该补；原卷错但重做对了 → 粗心或已经会了
        g["need"] = g["redo_wrong"] + g["dont_know"] + (g["orig_wrong"] if not (g["redo_right"] or g["redo_wrong"] or g["dont_know"]) else 0)
        g["gaps"] = [p for p, _ in catalog.ancestors(k, depth=2)
                     if m.get(p["id"], {}).get("status") in (None, "unknown", "weak")][:3] if k and g["need"] else []
        groups.append(g)
    groups.sort(key=lambda g: (-g["need"], -g["lost"], -g["orig_wrong"]))
    answered = [r for r in rs if r["answered_at"]]
    orig_marked = [r for r in rs if r["orig"]]
    return {
        "total": len(rs), "answered": len(answered),
        "redo_right": sum(1 for r in answered if r["correct"]),
        "dont_know": sum(1 for r in answered if r["dont_know"]),
        "orig_marked": len(orig_marked), "orig_wrong": sum(1 for r in orig_marked if r["orig"] != "right"),
        "lost": sum((r["points"] or 0) for r in orig_marked if r["orig"] != "right"),
        "groups": groups,
        "good": [g for g in groups if not g["need"] and not g["orig_wrong"] and (g["redo_right"] or g["orig_right"])],
        "careless": [g for g in groups if g["orig_wrong"] and g["redo_right"] and not g["redo_wrong"] and not g["dont_know"]],
    }


def finish(user_id: int, paper: dict) -> dict:
    rep = report(user_id, paper)
    slim = {k: v for k, v in rep.items() if k not in ("groups", "careless", "good")}
    slim["weak_kps"] = [g["kp"]["id"] for g in rep["groups"] if g["kp"] and g["need"]]
    db.run("UPDATE papers SET status='done', finished_at=?, report=? WHERE id=?", db.now(), db.jdump(slim), paper["id"])
    engine.mark_task_by(user_id, type="paper")
    return rep


def open_papers(user_id: int) -> list:
    return db.q("SELECT p.id, p.title, (SELECT COUNT(*) FROM paper_items i WHERE i.paper_id=p.id AND i.answered_at IS NULL "
                "AND i.flagged=0) AS left_n FROM papers p WHERE p.user_id=? AND p.status='ready' ORDER BY p.id", user_id)
