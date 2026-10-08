"""题库自己长、自己把关：不靠人一道道导题、审题。后台每隔一会儿跑一小批（run_once），管理员只看被拦下来的题。

- 同型题分组（near_key）：去掉数字、标点、空格后，同一个知识点下题干和选项几乎一样的题算一组（换了数字、改了几个字的同一道题）。
  一组里做过一道，其它的就不算「新题」：先出真正没见过的题；做错过的那组，换个时间优先出同组换了数字的题，而不是原题再来一遍。
- 改编入库（promote）：家里拍的卷子（家长同意分享的）上的题，AI 改编成考同一个知识点、难度相当的新题，
  先是待校对（status='pending'），校对通过才进公共题库。原卷、孩子的作答都不公开；改编的题记下是谁的卷子带来的（出题小帮手贴纸）。
- 校对答案（verify）：不给参考答案，让 AI 独立做一遍（可以在 config/llm.toml 的 [tasks.verify] 换另一个模型）。
  做出来一样就算校对过；不一样、或者 AI 认为题目本身有问题，就暂停使用，进管理后台「被标记有问题」。
- 难度校准（calibrate）：做的人够多以后，按真实作答算难度：同一道题，要看是谁做的——
  b = 做这道题的人的平均水平 − logit(这道题的正确率)，再分成 1-5 级。难度只用来给每个人配「踮踮脚够得着」的题，
  不拿来给孩子排名。
"""
import logging
import math
import os
import re
import threading
import time
from datetime import timedelta

from . import bank, db, itemtypes, llm
from .catalog import catalog

log = logging.getLogger("aistudy.bankflow")

SIM = 0.8              # 题干骨架的相似度（二字组 Jaccard）超过这个就算同一组
VERIFY_BATCH = 20      # 每轮最多校对几道
VARIANT_BATCH = 10     # 每轮最多改编几道
DAILY_AI = 300         # 后台任务每天最多调用几次 AI（公益网站，控制成本）
MIN_ATTEMPTS = 8       # 至少有这么多次作答、3 个不同的人做过，才按作答校准难度
INTERVAL = 15 * 60     # 后台每隔多久跑一轮（秒）
LEVELS = (-1.5, -0.5, 0.5, 1.5)  # b 的分界：1 很容易 … 5 很难
JOB = "bank"


# ------------------------------------------------------------------ 同型题分组

def skeleton(it: dict) -> str:
    text = str(it.get("q") or "") + "|" + "|".join(map(str, it.get("options") or []))
    text = re.sub(r"\d+(\.\d+)?", "#", text.lower())
    return re.sub(r"[\s\W_]+", "", text)


def _grams(s: str) -> set:
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def similar(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def assign_near(item_id: str, it: dict | None = None, kp_id: str = ""):
    """给一道题找它的同型组：同一个知识点下骨架相似的题共用一个 near_key（组里最早那道题的 id）。"""
    if it is None or not kp_id:
        r = db.one("SELECT * FROM items WHERE id=?", item_id)
        if not r:
            return
        it, kp_id = bank.row_to_item(r), kp_id or r["kp_id"]
    key = item_id
    if kp_id:
        sk = skeleton(it)
        for r in db.q("SELECT i.id, i.data, i.near_key FROM items i JOIN item_kps x ON x.item_id=i.id "
                      "WHERE x.kp_id=? AND i.id<>? AND i.near_key IS NOT NULL ORDER BY i.created_at LIMIT 400", kp_id, item_id):
            if similar(sk, skeleton(db.jload(r["data"], {}))) >= SIM:
                key = r["near_key"]
                break
    db.run("UPDATE items SET near_key=? WHERE id=?", key, item_id)


def sync_near(limit=5000):
    """启动时给老题补上同型组（幂等）。"""
    for r in db.q("SELECT id FROM items WHERE near_key IS NULL ORDER BY created_at LIMIT ?", limit):
        assign_near(r["id"])


# ------------------------------------------------------------------ 校对答案

def _grade_of(r) -> str:
    kp = catalog.kp(r["kp_id"]) or {}
    return r["grade"] or kp.get("stage") or catalog.default_grade


def verify_batch(limit=VERIFY_BATCH) -> dict:
    out = {"checked": 0, "ok": 0, "held": 0}
    # 简答题是对照参考答案自评的，不校对
    db.run("UPDATE items SET verified=2 WHERE verified=0 AND type NOT IN ('mcq','num','fill')")
    rows = db.q("SELECT * FROM items WHERE verified=0 AND status IN ('active','pending') AND kp_id<>'' "
                "ORDER BY CASE WHEN status='pending' THEN 0 ELSE 1 END, n_attempts DESC, created_at LIMIT ?", limit)
    for r in rows:
        kp = catalog.kp(r["kp_id"])
        if not kp:
            continue
        it = bank.row_to_item(r)
        try:
            data = llm.solve_item(it, catalog.packs[kp["pack"]], _grade_of(r))
        except llm.LLMError as e:
            log.warning("verify stopped: %s", e)
            break
        out["checked"] += 1
        if data.get("ok") is False:
            ok, note = False, f"AI 校对：题目可能有问题（{str(data.get('problem') or '')[:120]}）"
        elif "answer" not in data:
            continue
        else:
            ok = bool(itemtypes.check(it, data.get("answer")))
            mine = itemtypes.display({**it, "answer": data.get("answer")})
            note = "" if ok else f"AI 校对：独立做出来是「{mine[:80]}」，和参考答案「{itemtypes.display(it)[:80]}」对不上"
        if ok:
            out["ok"] += 1
            db.run("UPDATE items SET verified=1, verify_note='', status=CASE WHEN status='pending' THEN 'active' ELSE status END, "
                   "updated_at=? WHERE id=?", db.now(), r["id"])
        else:
            out["held"] += 1
            # 孩子试卷上的原题不停用（只影响他自己的订正），记下来给管理员看
            st = "review" if r["source"] != "paper" else r["status"]
            db.run("UPDATE items SET verified=-1, verify_note=?, status=?, updated_at=? WHERE id=?", note, st, db.now(), r["id"])
        _spend(1)
    return out


# ------------------------------------------------------------------ 家里的卷子改编入库

def promote_batch(limit=VARIANT_BATCH) -> dict:
    out = {"tried": 0, "made": 0}
    rows = db.q(
        "SELECT i.*, p.user_id AS owner FROM items i JOIN paper_items pi ON pi.item_id=i.id JOIN papers p ON p.id=pi.paper_id "
        "WHERE i.source='paper' AND p.shared=1 AND pi.flagged=0 AND i.kp_id<>'' AND i.status='active' "
        "AND COALESCE(i.verified,0)<>-1 AND i.gen_meta NOT LIKE '%variant_tried%' "
        "AND NOT EXISTS (SELECT 1 FROM items v WHERE v.variant_of=i.id) ORDER BY p.id, pi.seq LIMIT ?", limit)
    for r in rows:
        kp = catalog.kp(r["kp_id"])
        if not kp:
            continue
        grade = _grade_of(r)
        try:
            v = llm.make_variant(bank.row_to_item(r), kp, catalog.packs[kp["pack"]], grade)
        except llm.LLMError as e:
            log.warning("promote stopped: %s", e)
            break
        _spend(1)
        out["tried"] += 1
        meta = {**db.jload(r["gen_meta"], {}), "variant_tried": 1}
        db.run("UPDATE items SET gen_meta=? WHERE id=?", db.jdump(meta), r["id"])
        if not v or bank.item_hash(v) == r["qhash"]:
            continue
        saved = bank.save_items(r["kp_id"], [v], source="variant", purpose="practice", grade=grade, status="pending",
                                meta=bank.gen_meta("variant", variant_of=r["id"]))
        for s in saved:
            if s["source"] == "variant":
                db.run("UPDATE items SET variant_of=?, contributor_id=? WHERE id=? AND variant_of IS NULL", r["id"], r["owner"], s["id"])
                out["made"] += 1
    return out


def contributed(user_id: int) -> dict:
    """这个孩子的卷子改编出了几道新题、别的同学练了几次。"""
    r = db.one("SELECT COUNT(*) AS n FROM items WHERE contributor_id=? AND status='active'", user_id)
    u = db.one("SELECT COUNT(*) AS n FROM attempts a JOIN items i ON i.id=a.item_id WHERE i.contributor_id=? AND a.user_id<>?",
               user_id, user_id)
    return {"items": r["n"] if r else 0, "uses": u["n"] if u else 0}


# ------------------------------------------------------------------ 难度校准

def level_of(b: float) -> int:
    return 1 + sum(b > x for x in LEVELS)


def calibrate(min_n=MIN_ATTEMPTS) -> int:
    """按真实作答校准难度。游戏里限时作答、原卷批改不算（条件不一样）。返回校准了几道题。"""
    modes = "('practice','diagnose','probe','paper','preview','review','check')"
    ability = {}
    for r in db.q(f"SELECT user_id, kp_id, COUNT(*) AS n, SUM(correct) AS ok FROM attempts WHERE mode IN {modes} "
                  "GROUP BY user_id, kp_id"):
        ability[(r["user_id"], r["kp_id"])] = _logit((r["ok"] or 0) + 1, r["n"] + 2)
    per_item: dict = {}
    for r in db.q(f"SELECT item_id, user_id, kp_id, COUNT(*) AS n, SUM(correct) AS ok FROM attempts "
                  f"WHERE item_id IS NOT NULL AND mode IN {modes} GROUP BY item_id, user_id, kp_id"):
        g = per_item.setdefault(r["item_id"], {"n": 0, "ok": 0, "users": set(), "theta": 0.0})
        g["n"] += r["n"]
        g["ok"] += r["ok"] or 0
        g["users"].add(r["user_id"])
        g["theta"] += r["n"] * ability.get((r["user_id"], r["kp_id"]), 0.0)
    n = 0
    for iid, g in per_item.items():
        if g["n"] < min_n or len(g["users"]) < 3:
            continue
        b = g["theta"] / g["n"] - _logit(g["ok"] + 0.5, g["n"] + 1)
        db.run("UPDATE items SET b=?, level=? WHERE id=?", round(b, 3), level_of(b), iid)
        n += 1
    return n


def _logit(k: float, n: float) -> float:
    p = min(0.97, max(0.03, k / n))
    return math.log(p / (1 - p))


def level(r) -> int:
    """一道题的难度级别（1-5）：校准过的用校准值，没有就用出题时 AI 估的难度（1-3 → 2-4）。"""
    lv = r["level"] if "level" in r.keys() else None
    return lv or min(5, max(1, (r["difficulty"] or 2) + 1))


# ------------------------------------------------------------------ 后台任务

def _job():
    db.run("INSERT INTO jobs(name) VALUES(?) ON CONFLICT(name) DO NOTHING", JOB)
    return db.one("SELECT * FROM jobs WHERE name=?", JOB)


def _spend(n: int):
    db.run("UPDATE jobs SET ai_calls=ai_calls+? WHERE name=?", n, JOB)


def budget() -> int:
    j = _job()
    today = db.today().isoformat()
    if j["day"] != today:
        db.run("UPDATE jobs SET day=?, ai_calls=0 WHERE name=?", today, JOB)
        return DAILY_AI
    return max(0, DAILY_AI - (j["ai_calls"] or 0))


def _lease(seconds=600) -> bool:
    """多个应用进程时只让一个跑：抢到租约的才跑。"""
    _job()
    now = db.now()
    until = _later(seconds)
    return db.run("UPDATE jobs SET locked_until=? WHERE name=? AND (locked_until='' OR locked_until IS NULL OR locked_until<?)",
                  until, JOB, now) == 1


def _later(seconds: int) -> str:
    from datetime import datetime
    now = datetime.fromisoformat(db.now())
    return (now + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def run_once(force=False) -> dict:
    if not _lease():
        return {"skipped": "另一个进程正在跑"}
    res = {}
    try:
        j = _job()
        if llm.enabled():
            left = budget()
            if left:
                res["variants"] = promote_batch(min(VARIANT_BATCH, left))
            left = budget()
            if left:
                res["verify"] = verify_batch(min(VERIFY_BATCH, left))
        last = db.jload(j["last_result"], {}) or {}
        if force or last.get("calibrated_on") != db.today().isoformat():
            res["calibrated"] = calibrate()
            res["calibrated_on"] = db.today().isoformat()
        else:
            res["calibrated_on"] = last.get("calibrated_on")
    finally:
        db.run("UPDATE jobs SET locked_until='', last_run=?, last_result=? WHERE name=?", db.now(), db.jdump(res), JOB)
    return res


def stats() -> dict:
    one = lambda sql, *a: (db.one(sql, *a) or {}).get("n") or 0  # noqa: E731
    j = _job()
    return {"pending": one("SELECT COUNT(*) AS n FROM items WHERE status='pending'"),
            "verified": one("SELECT COUNT(*) AS n FROM items WHERE verified=1 AND status='active'"),
            "todo": one("SELECT COUNT(*) AS n FROM items WHERE verified=0 AND status='active' AND type IN ('mcq','num','fill')"),
            "held": one("SELECT COUNT(*) AS n FROM items WHERE verified=-1 AND status='review'"),
            "variants": one("SELECT COUNT(*) AS n FROM items WHERE source='variant' AND status='active'"),
            "groups": one("SELECT COUNT(*) AS n FROM (SELECT near_key FROM items WHERE status='active' AND near_key IS NOT NULL "
                          "GROUP BY near_key HAVING COUNT(*)>1) g"),
            "calibrated": one("SELECT COUNT(*) AS n FROM items WHERE level IS NOT NULL AND status='active'"),
            "levels": {r["level"]: r["n"] for r in db.q("SELECT level, COUNT(*) AS n FROM items WHERE level IS NOT NULL "
                                                         "AND status='active' GROUP BY level")},
            "last_run": j["last_run"], "last": db.jload(j["last_result"], {}), "ai_today": j["ai_calls"] if j["day"] == db.today().isoformat() else 0,
            "ai_limit": DAILY_AI}


def start_background():
    """应用启动时开一个后台线程，每隔 INTERVAL 跑一轮。设 AISTUDY_JOBS=0 关掉（测试里就关掉了）。"""
    if os.environ.get("AISTUDY_JOBS", "1") == "0":
        return

    def loop():
        time.sleep(60)
        while True:
            try:
                run_once()
            except Exception:  # 后台任务出错不能影响网站
                log.exception("bank job failed")
            time.sleep(INTERVAL)

    threading.Thread(target=loop, name="bankflow", daemon=True).start()
