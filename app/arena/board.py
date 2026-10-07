"""公平看板（管理后台「游戏公平」）：检验「每个水平的孩子，答对的机会都差不多」。

孩子按学业水平分 4 组（在学的教材里、当前学段及以前的知识点已掌握的比例，和游戏无关），每组对比：
预测答对率、实际答对率、每分钟经验值、打赢电脑的比例。各组实际答对率都接近目标，说明出题是公平的；
某组明显偏低 / 偏高，说明难度校准还不准（看下面每个题族、级别校准后的难度和样本数）。
"""
from datetime import timedelta

from .. import db, quickgen
from ..catalog import catalog, stage_rank
from .matches import GAMES

BANDS = ((0.25, "掌握 0–25%"), (0.5, "掌握 25–50%"), (0.75, "掌握 50–75%"), (1.01, "掌握 75–100%"))


def kid_band(kid: dict) -> str:
    rank = stage_rank(kid.get("grade") or "G3")
    packs = [e["pack_id"] for e in db.q("SELECT pack_id FROM enrollments WHERE user_id=? AND active=1", kid["id"])
             if e["pack_id"] in catalog.packs]
    ids = {k for p in packs for k in catalog.packs[p].kp_ids if stage_rank(catalog.kps[k]["stage"]) <= rank}
    if not ids:
        return BANDS[0][1]
    mastered = {r["kp_id"] for r in db.q("SELECT kp_id FROM mastery WHERE user_id=? AND status='mastered'", kid["id"])}
    share = len(ids & mastered) / len(ids)
    return next(label for cut, label in BANDS if share < cut)


def _family_name(f: str) -> str:
    if f in quickgen.FAMILIES:
        return quickgen.FAMILIES[f][2]
    if f == "words":
        return "英语单词（级别 = 年级段）"
    if f.startswith("terms:"):
        return "术语：" + f[6:]
    if f == "bank":
        return "题库短选择题"
    return f


def fairness(days: int = 30) -> dict:
    since = (db.today() - timedelta(days=days - 1)).isoformat()
    kids = {r["user_id"] for r in db.q("SELECT DISTINCT user_id FROM arena_answers WHERE created_at>=?", since)}
    groups = {label: {"band": label, "kids": 0, "answers": 0, "right": 0, "p_sum": 0.0, "xp": 0,
                      "matches": 0, "wins": 0, "seconds": 0} for _, label in BANDS}
    for uid in kids:
        u = db.one("SELECT * FROM users WHERE id=?", uid)
        if not u:
            continue
        g = groups[kid_band(dict(u))]
        g["kids"] += 1
        a = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(correct),0) AS c, COALESCE(SUM(p_pred),0) AS p, "
                   "COALESCE(SUM(xp),0) AS xp FROM arena_answers WHERE user_id=? AND created_at>=?", uid, since)
        g["answers"] += a["n"]
        g["right"] += a["c"]
        g["p_sum"] += a["p"]
        g["xp"] += a["xp"]
        mm = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN result='win' THEN 1 ELSE 0 END),0) AS w, "
                    "COALESCE(SUM(seconds),0) AS s FROM arena_matches WHERE user_id=? AND started_at>=? AND result<>'quit'",
                    uid, since)
        g["matches"] += mm["n"]
        g["wins"] += mm["w"]
        g["seconds"] += mm["s"]
    rows = []
    for g in groups.values():
        n = g["answers"]
        rows.append({**g, "acc": round(100 * g["right"] / n) if n else None,
                     "pred": round(100 * g["p_sum"] / n) if n else None,
                     "xp_per_min": round(g["xp"] / (g["seconds"] / 60)) if g["seconds"] >= 60 else None,
                     "win_rate": round(100 * g["wins"] / g["matches"]) if g["matches"] else None})
    cal = [{**dict(r), "name": _family_name(r["family"])}
           for r in db.q("SELECT family, level, b, n FROM arena_calib ORDER BY family, level")]
    return {"days": days, "target": round(GAMES["stickman"]["target"] * 100), "rows": rows, "calib": cal,
            "games": {g: round(v["target"] * 100) for g, v in GAMES.items()}}
