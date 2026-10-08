"""跟自己比：个人最好（PB）、「上周的我」、最长专注、每周进步卡。

原则：只跟自己比，不做排行榜；正确率不到 80% 的一组不算纪录（免得为了快乱答）；
速度只比「每道答对的题平均用时」，而且只在单词复习、错题重做这类已经学过的内容和热身上比。
"""
from datetime import timedelta

from . import db, sprint

KINDS = {"warmup": "热身", "words": "单词复习", "mistakes": "错题重做", "review": "知识点回顾",
         "practice": "知识点练习", "read": "阅读"}
SPEED_KINDS = {"warmup", "words", "mistakes", "review"}
MIN_ACC = 0.8


def _valid(r) -> bool:
    return r["n_items"] > 0 and r["n_right"] / r["n_items"] >= MIN_ACC


def _per_item(r) -> float | None:
    return r["ms_active"] / r["n_right"] if r["n_right"] and r["ms_active"] else None


def personal_best(user_id: int, kind: str, before_id: int | None = None) -> dict:
    """这个孩子某类任务的 PB：答对最多、最长连对、每题最快（只算正确率≥80% 的组）；以及所有任务里最长的一次专注。"""
    cond = " AND id<?" if before_id else ""
    args = (before_id,) if before_id else ()
    rows = db.q(f"SELECT * FROM runs WHERE user_id=? AND kind=?{cond}", user_id, kind, *args)
    valid = [r for r in rows if _valid(r)]
    speeds = [s for s in (_per_item(r) for r in valid if r["n_right"] >= 3) if s]
    focus = db.one(f"SELECT MAX(ms_active) AS m FROM runs WHERE user_id=?{cond}", user_id, *args)["m"] or 0
    return {"right": max((r["n_right"] for r in valid), default=0),
            "combo": max((r["best_combo"] for r in rows), default=0),
            "speed": int(min(speeds)) if speeds and kind in SPEED_KINDS else None,
            "focus": int(focus), "runs": len(rows)}


def ghost(user_id: int, kind: str) -> int | None:
    """「上周的我」：过去 7 天（不含今天）这类任务每道答对的题平均用时（毫秒）。"""
    today = db.today()
    rows = db.q("SELECT * FROM runs WHERE user_id=? AND kind=? AND day>=? AND day<?", user_id, kind,
                (today - timedelta(days=7)).isoformat(), today.isoformat())
    right = sum(r["n_right"] for r in rows)
    ms = sum(r["ms_active"] for r in rows if r["n_right"])
    return int(ms / right) if right >= 3 and ms else None


def record(user_id: int, kind: str, n_items: int, n_right: int, ms_active: int, ms_total: int, best_combo: int) -> dict:
    """记一组，返回破了哪些 PB。"""
    kind = kind if kind in KINDS else "practice"
    n_items = max(0, min(int(n_items or 0), 500))
    n_right = max(0, min(int(n_right or 0), n_items))
    ms_active = max(0, min(int(ms_active or 0), 4 * 3600_000))
    ms_total = max(ms_active, min(int(ms_total or 0), 8 * 3600_000))
    best_combo = max(0, min(int(best_combo or 0), n_items))
    old = personal_best(user_id, kind)
    rid = db.insert("INSERT INTO runs(user_id,kind,day,n_items,n_right,ms_active,ms_total,best_combo,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", user_id, kind, db.today().isoformat(), n_items, n_right, ms_active,
                    ms_total, best_combo, db.now())
    row = {"n_items": n_items, "n_right": n_right, "ms_active": ms_active}
    pbs = []
    if old["runs"]:  # 第一次做不算破纪录，先立一个
        if _valid(row) and n_right > old["right"]:
            pbs.append({"key": "right", "label": f"{KINDS[kind]}答对最多：{n_right} 题"})
        if best_combo >= 3 and best_combo > old["combo"]:
            pbs.append({"key": "combo", "label": f"最长连对：{best_combo} 题"})
        sp = _per_item(row)
        if kind in SPEED_KINDS and _valid(row) and n_right >= 3 and sp and old["speed"] and sp < old["speed"]:
            pbs.append({"key": "speed", "label": f"{KINDS[kind]}每题最快：{sp / 1000:.1f} 秒"})
    if ms_active >= 5 * 60_000 and ms_active > old["focus"] and db.one(  # 专注跨所有任务比：以前做过任何一组就算
            "SELECT id FROM runs WHERE user_id=? AND id<?", user_id, rid):
        pbs.append({"key": "focus", "label": f"最长专注：{ms_active // 60000} 分 {ms_active // 1000 % 60} 秒"})
    db.run("UPDATE runs SET pbs=? WHERE id=?", db.jdump(pbs), rid)
    return {"pbs": pbs, "pb": personal_best(user_id, kind), "first": not old["runs"]}


def _week(user_id: int, start, end) -> dict:
    rows = db.q("SELECT * FROM runs WHERE user_id=? AND day>=? AND day<?", user_id, start.isoformat(), end.isoformat())
    att = db.q("SELECT correct, ms FROM attempts WHERE user_id=? AND created_at>=? AND created_at<?",
               user_id, start.isoformat(), end.isoformat())
    timed = [a["ms"] for a in att if a["correct"] and a["ms"]]
    return {"runs": len(rows), "pbs": sum(len(db.jload(r["pbs"], [])) for r in rows),
            "focus": sum(r["ms_active"] for r in rows) // 60000,
            "attempts": len(att), "acc": round(100 * sum(1 for a in att if a["correct"]) / len(att)) if att else None,
            "sec": round(sum(timed) / len(timed) / 1000, 1) if len(timed) >= 3 else None,
            "lit": db.one("SELECT COUNT(*) AS n FROM lights WHERE user_id=? AND day>=? AND day<?", user_id,
                          start.isoformat(), end.isoformat())["n"],
            "sprint": sprint.week(user_id, start.isoformat(), end.isoformat()),
            "days": db.one("SELECT COUNT(*) AS n FROM days WHERE user_id=? AND day>=? AND day<? AND (minutes>0 OR checked_in=1)",
                           user_id, start.isoformat(), end.isoformat())["n"]}


def weekly(user_id: int) -> dict | None:
    """每周进步卡：上周（周一到周日）和再上一周比。上周没学就不出卡。"""
    today = db.today()
    this_mon = today - timedelta(days=today.weekday())
    last_mon = this_mon - timedelta(days=7)
    w1, w0 = _week(user_id, last_mon, this_mon), _week(user_id, last_mon - timedelta(days=7), last_mon)
    if not (w1["days"] or w1["attempts"]):
        return None
    lines = []
    if w1["sec"] and w0["sec"] and w1["sec"] < w0["sec"]:
        lines.append(f"答对一题平均快了 {w0['sec'] - w1['sec']:.1f} 秒（{w0['sec']} → {w1['sec']} 秒）")
    if w1["acc"] is not None and w0["acc"] is not None and w1["acc"] > w0["acc"]:
        lines.append(f"正确率提高了 {w1['acc'] - w0['acc']} 个百分点（{w0['acc']}% → {w1['acc']}%）")
    if w1["lit"]:
        lines.append(f"新点亮 {w1['lit']} 个知识点")
    if w1["pbs"]:
        lines.append(f"破了 {w1['pbs']} 次个人纪录")
    if w1["focus"]:
        lines.append(f"专注学习 {w1['focus']} 分钟" + (f"（比再上周多 {w1['focus'] - w0['focus']} 分钟）" if w1["focus"] > w0["focus"] else ""))
    if w1["sprint"]:
        lines.append(f"冲刺了 {w1['sprint']} 分" + (f"（比再上周多 {w1['sprint'] - w0['sprint']} 分）" if w1["sprint"] > w0["sprint"] else ""))
    lines.append(f"学习了 {w1['days']} 天，做题 {w1['attempts']} 道")
    return {"from": last_mon.isoformat(), "to": (this_mon - timedelta(days=1)).isoformat(), "lines": lines,
            "this": w1, "prev": w0, "monday": today.weekday() == 0}


def _mmss(ms: int) -> str:
    return f"{ms // 60000}:{ms // 1000 % 60:02d}"


def summary(user_id: int) -> dict:
    """「我的纪录」：每类任务的 PB 一行，加上最长专注。没做过的类不显示。"""
    rows = []
    for kind, label in KINDS.items():
        pb = personal_best(user_id, kind)
        if not pb["runs"]:
            continue
        bits = []
        if pb["right"] and kind != "read":
            bits.append(f"答对最多 {pb['right']} 题")
        if pb["speed"]:
            bits.append(f"每题最快 {pb['speed'] / 1000:.1f} 秒")
        if pb["combo"] >= 3:
            bits.append(f"最长连对 {pb['combo']}")
        if bits:
            rows.append({"kind": kind, "label": label, "text": " · ".join(bits)})
    focus = db.one("SELECT MAX(ms_active) AS m FROM runs WHERE user_id=?", user_id)["m"] or 0
    return {"rows": rows, "focus": _mmss(int(focus)) if focus >= 60_000 else ""}
