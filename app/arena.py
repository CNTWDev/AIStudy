"""游戏乐园：按每个孩子自己的水平出题的游戏（第一款：火柴人大战，单机打电脑）。

公平的做法（详见 docs/DESIGN.md 5.12）：
- 每个孩子在每个知识点上有一个能力值 θ，每个（题族, 级别）有一个难度 b，答对概率 p = 1 / (1 + e^-(θ − b))。
- 出题时选让 p 最接近目标（火柴人 75%）的级别：学得快的孩子拿到难题，学得慢的拿到适合他的题，
  但大家答对的机会一样，所以经验值赚得多少看专注和发挥，不看本来会多少。
- θ 起点取自掌握度（BKT），之后每答一题在线更新；b 由全体孩子的作答在线校准（Elo 式）。
- 每次作答记下预测的 p，公平看板按水平分组对比「预测答对率」和「实际答对率」。

经济规则只在这里写一处：答对得经验值（期望 100，70–150 随机，8% 暴击翻倍），每连对 3 题攒一次必杀；
答错冷冻 5 秒，3 秒内连续瞎答冷冻递增（5 → 8 → 12 秒）；每天的游戏时长由家长设上限。
游戏里答题照常计入学习记录（attempts / events，mode = game），会更新掌握度，但不计入每天的学习打卡。
"""
import math
import random
import time
from datetime import datetime, timedelta

from . import db, engine, evidence, itemtypes, quickgen
from .catalog import catalog, stage_rank

MATH_PACK = "math-shanghai"
GAMES = {
    "stickman": {"name": "火柴人大战", "icon": "🥋", "target": 0.75, "max_seconds": 600,
                 "desc": "边打边答题：答对得经验值，换武器、放必杀。题目按你自己的水平出。"},
}
DEFAULT_GAME_MINUTES = 20
GAME_MINUTE_CHOICES = (0, 10, 20, 30, 45, 60)
FREEZE_SECONDS = (5, 8, 12)   # 答错冷冻；3 秒内连续瞎答逐级加长
FAST_MS = 3000
CRIT_RATE = 0.08
DAILY_SOFT_CAP = 5000          # 每天游戏经验值超过这个数后减半，防止只刷
PENDING_KEEP_S = 90            # 关掉答题面板再打开，90 秒内还是同一道题（不能换掉难题）
SPECIAL_EVERY = 3              # 连对几题攒一次必杀


class ArenaError(Exception):
    def __init__(self, msg: str, status: int = 400, **extra):
        super().__init__(msg)
        self.status, self.extra = status, extra


# ------------------------------------------------------------------ 能力值与难度

def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def logit(p: float) -> float:
    p = min(0.95, max(0.05, p))
    return math.log(p / (1 - p))


def prior_b(level: int) -> float:
    return 1.0 * (level - 3)


def calib(family: str) -> dict[int, tuple[float, int]]:
    rows = {r["level"]: (r["b"], r["n"]) for r in db.q("SELECT level, b, n FROM arena_calib WHERE family=?", family)}
    return {lv: rows.get(lv, (prior_b(lv), 0)) for lv in quickgen.LEVELS}


def _cur_rank(kid: dict) -> float:
    e = db.one("SELECT stage FROM enrollments WHERE user_id=? AND pack_id=? AND active=1", kid["id"], MATH_PACK)
    stage = e["stage"] if e else kid.get("grade") or "G3"
    r = stage_rank(stage)
    return r if r < 99 else 6.0


def ability(kid: dict, kp_id: str, mastery: dict | None = None) -> tuple[float, int]:
    row = db.one("SELECT theta, n FROM arena_ability WHERE user_id=? AND kp_id=?", kid["id"], kp_id)
    if row:
        return row["theta"], row["n"]
    m = (mastery or {}).get(kp_id) if mastery is not None else db.one(
        "SELECT * FROM mastery WHERE user_id=? AND kp_id=?", kid["id"], kp_id)
    if m and m.get("attempts"):
        p = evidence.now_p(kid["id"], dict(m))
    else:  # 没做过题：以前学段的按大概率会，当前学段的按一半
        gap = _cur_rank(kid) - stage_rank(catalog.kps[kp_id]["stage"])
        p = 0.8 if gap >= 1 else 0.5
    return logit(p), 0


def elo_step(theta: float, n: int, b: float, nb: int, correct: bool) -> tuple[float, float]:
    """答完一题：能力值和难度各往「意外」的方向挪一步。新孩子步子大（很快定位），答得多了就稳定；题目难度的步子更小。"""
    p = sigmoid(theta - b)
    y = 1.0 if correct else 0.0
    k_theta = max(0.25, 1.2 / math.sqrt(1 + n / 3))
    k_b = max(0.02, 0.4 / math.sqrt(1 + nb))
    return theta + k_theta * (y - p), b - k_b * (y - p)


def update_ability(kid_id: int, kp_id: str, family: str, level: int, correct: bool, theta: float, n: int, b: float, nb: int):
    theta2, b2 = elo_step(theta, n, b, nb, correct)
    db.run("INSERT INTO arena_ability(user_id,kp_id,theta,n,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,kp_id) "
           "DO UPDATE SET theta=excluded.theta, n=excluded.n, updated_at=excluded.updated_at",
           kid_id, kp_id, theta2, n + 1, db.now())
    db.run("INSERT INTO arena_calib(family,level,b,n) VALUES(?,?,?,?) ON CONFLICT(family,level) "
           "DO UPDATE SET b=excluded.b, n=excluded.n", family, level, b2, nb + 1)


# ------------------------------------------------------------------ 出题

def kp_pool(kid: dict, target: float = 0.75) -> list[tuple[str, float]]:
    """能出题的知识点和权重：不超过孩子当前学段；学习中、薄弱的多出（按水平出题，所以薄弱也答得上），
    掌握了的偶尔复习；离当前学段越远权重越低。"""
    rank = _cur_rank(kid)
    mastery = engine.get_mastery(kid["id"])
    taught = engine.taught_set(kid["id"])
    out = []
    for fam, (kp_id, _, _) in quickgen.FAMILIES.items():
        kp = catalog.kps.get(kp_id)
        if not kp:
            continue
        r = stage_rank(kp["stage"])
        if r > rank:
            continue
        status = (mastery.get(kp_id) or {}).get("status") or "unknown"
        w = {"learning": 3.0, "weak": 2.0, "mastered": 1.2}.get(status)
        if w is None:
            w = 1.0 if r < rank else (1.5 if kp_id in taught else 0.4)
        # 这个知识点最合适的一级也离目标答对率很远（太难或太简单）：少出，换别的知识点
        theta, _ = ability(kid, kp_id, mastery)
        levels = calib(fam)
        gap = min(abs(sigmoid(theta - levels[lv][0]) - target) for lv in levels)
        w *= max(0.05, 1 - 2.5 * gap)
        out.append((kp_id, w / (1 + 0.35 * (rank - r))))
    return out or [(quickgen.FAMILIES["add10"][0], 1.0)]


def pick_level(theta: float, levels: dict[int, tuple[float, int]], target: float, rng: random.Random) -> int:
    """选级别：在答对概率刚好高于、刚好低于目标的两级之间按比例混着出，平均下来答对概率正好是目标；
    目标超出这个题族的范围（最简单的也太难，或最难的也太简单）就出最接近的一级。"""
    ps = {lv: sigmoid(theta - b) for lv, (b, _) in levels.items()}
    easy = [lv for lv in ps if ps[lv] >= target]
    hard = [lv for lv in ps if ps[lv] < target]
    if not easy or not hard:
        return min(ps, key=lambda lv: abs(ps[lv] - target))
    e, h = min(easy, key=lambda lv: ps[lv]), max(hard, key=lambda lv: ps[lv])
    w = (target - ps[h]) / (ps[e] - ps[h])
    return e if rng.random() < w else h


def next_question(kid: dict, game: str, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    pool = kp_pool(kid, GAMES[game]["target"])
    kp_id = rng.choices([k for k, _ in pool], weights=[w for _, w in pool])[0]
    fam = quickgen.BY_KP[kp_id]
    theta, _ = ability(kid, kp_id)
    levels = calib(fam)
    lv = pick_level(theta, levels, GAMES[game]["target"], rng)
    item = quickgen.generate(fam, lv, rng)
    item["p"] = round(sigmoid(theta - levels[lv][0]), 4)
    return item


def public_item(it: dict) -> dict:
    t = itemtypes.of(it)
    keypad = it.get("keypad") or ("dec" if t.id == "num" else "")
    return {"id": it["id"], "q": it["q"], "type": t.id, "widget": t.widget, "unit": it.get("unit", ""),
            "options": it.get("options"), "keypad": keypad, "topic": quickgen.FAMILIES[it["family"]][2]}


# ------------------------------------------------------------------ 每天的游戏时长

def game_minutes(kid: dict) -> int:
    st = db.jload(kid.get("settings"), {}) or {}
    try:
        return int(st.get("game_minutes", DEFAULT_GAME_MINUTES))
    except (TypeError, ValueError):
        return DEFAULT_GAME_MINUTES


def set_game_minutes(kid_id: int, minutes) -> None:
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return
    minutes = min(GAME_MINUTE_CHOICES, key=lambda m: abs(m - minutes))
    u = db.one("SELECT settings FROM users WHERE id=?", kid_id)
    st = db.jload(u["settings"], {}) if u else {}
    st["game_minutes"] = minutes
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), kid_id)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def seconds_today(kid_id: int) -> int:
    day = db.today().isoformat()
    now = _ts(db.now())
    total = 0
    for m in db.q("SELECT game, started_at, ended_at, seconds FROM arena_matches WHERE user_id=? AND started_at>=?",
                  kid_id, day):
        if m["ended_at"]:
            total += m["seconds"] or 0
        else:
            cap = GAMES.get(m["game"], {}).get("max_seconds", 600)
            total += int(min(cap, max(0, (now - _ts(m["started_at"])).total_seconds())))
    return total


def remaining_seconds(kid: dict) -> int:
    return max(0, game_minutes(kid) * 60 - seconds_today(kid["id"]))


# ------------------------------------------------------------------ 经验值

def xp_total(kid_id: int) -> int:
    return (db.one("SELECT COALESCE(SUM(delta),0) AS s FROM arena_ledger WHERE user_id=?", kid_id) or {}).get("s") or 0


def xp_today(kid_id: int) -> int:
    return (db.one("SELECT COALESCE(SUM(delta),0) AS s FROM arena_ledger WHERE user_id=? AND created_at>=? AND delta>0",
                   kid_id, db.today().isoformat()) or {}).get("s") or 0


def roll_xp(rng: random.Random, earned_today: int) -> tuple[int, bool]:
    """答对一题的经验值：70–150，期望 100；8% 暴击翻倍；今天赚得太多就减半。"""
    xp = int(round(rng.triangular(70, 150, 80) / 10) * 10)
    crit = rng.random() < CRIT_RATE
    if crit:
        xp *= 2
    if earned_today >= DAILY_SOFT_CAP:
        xp //= 2
    return xp, crit


def freeze_seconds(fast_wrongs: int, fast: bool) -> int:
    return FREEZE_SECONDS[min(fast_wrongs - 1, len(FREEZE_SECONDS) - 1)] if fast and fast_wrongs > 0 else FREEZE_SECONDS[0]


# ------------------------------------------------------------------ 一局

def _match(kid: dict, match_id) -> dict:
    m = db.one("SELECT * FROM arena_matches WHERE id=? AND user_id=?", match_id, kid["id"])
    if not m:
        raise ArenaError("找不到这一局", 404)
    if m["ended_at"]:
        raise ArenaError("这一局已经结束了", 409)
    return {**dict(m), "state": db.jload(m["data"], {}) or {}}


def _save_state(match_id: int, st: dict):
    db.run("UPDATE arena_matches SET data=? WHERE id=?", db.jdump(st), match_id)


def start(kid: dict, game: str) -> dict:
    if game not in GAMES:
        raise ArenaError("没有这个游戏", 404)
    left = remaining_seconds(kid)
    if left <= 0:
        mins = game_minutes(kid)
        raise ArenaError("今天的游戏时间用完了，明天再来！" if mins else "家长设置了暂时不玩游戏。", 403)
    # 没结束的旧局算作退出
    for m in db.q("SELECT id, started_at FROM arena_matches WHERE user_id=? AND ended_at IS NULL", kid["id"]):
        _close(m["id"], m["started_at"], "quit", {})
    mid = db.insert("INSERT INTO arena_matches(user_id, game, mode, started_at, data) VALUES(?,?,?,?,?)",
                    kid["id"], game, "solo", db.now(), db.jdump({"streak": 0, "fast_wrongs": 0}))
    return {"match_id": mid, "seconds_left": left, "xp_total": xp_total(kid["id"])}


def question(kid: dict, match_id, rng: random.Random | None = None) -> dict:
    m = _match(kid, match_id)
    st, now = m["state"], time.time()
    if st.get("freeze_until", 0) > now:
        raise ArenaError("冷冻中，等一下再答", 423, freeze_ms=int((st["freeze_until"] - now) * 1000))
    pend = st.get("pending")
    if not pend or now - pend["issued"] > PENDING_KEEP_S:
        pend = {"item": next_question(kid, m["game"], rng), "issued": now}
        st["pending"] = pend
        _save_state(m["id"], st)
    return {"item": public_item(pend["item"])}


def answer(kid: dict, match_id, item_id: str, ans, dont_know=False, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    m = _match(kid, match_id)
    st, now = m["state"], time.time()
    pend = st.get("pending")
    if not pend or pend["item"]["id"] != item_id:
        raise ArenaError("这道题已经过期了，再点一次答题", 409)
    it = pend["item"]
    ms = int((now - pend["issued"]) * 1000)  # 用服务器计时，不信客户端
    correct = (not dont_know) and bool(itemtypes.check(it, ans))
    kp_id, fam, lv = it["kp_id"], it["family"], it["level"]
    # 学习记录：照常更新掌握度（不计入当天打卡）
    engine.record_attempt(kid["id"], it, kp_id, "game", correct, "" if dont_know else ans, dont_know=dont_know,
                          touch=False, ms=ms)
    theta, n = ability(kid, kp_id)
    b, nb = calib(fam)[lv]
    update_ability(kid["id"], kp_id, fam, lv, correct, theta, n, b, nb)
    out = {"correct": correct, "answer": itemtypes.display(it), "explain": it.get("explain", ""), "xp": 0}
    if correct:
        xp, crit = roll_xp(rng, xp_today(kid["id"]))
        st["streak"] = st.get("streak", 0) + 1
        st["fast_wrongs"] = 0
        st["xp"] = st.get("xp", 0) + xp
        st["right"] = st.get("right", 0) + 1
        db.run("INSERT INTO arena_ledger(user_id,delta,reason,match_id,created_at) VALUES(?,?,?,?,?)",
               kid["id"], xp, "answer", m["id"], db.now())
        out.update(xp=xp, crit=crit, streak=st["streak"], special=st["streak"] % SPECIAL_EVERY == 0)
    else:
        fast = ms < FAST_MS and not dont_know
        st["fast_wrongs"] = st.get("fast_wrongs", 0) + 1 if fast else 0
        st["streak"] = 0
        sec = freeze_seconds(st["fast_wrongs"], fast)
        st["freeze_until"] = now + sec
        out.update(freeze_ms=sec * 1000, streak=0, guessing=fast and st["fast_wrongs"] >= 2)
    st["answered"] = st.get("answered", 0) + 1
    st["ms"] = st.get("ms", 0) + ms
    st.pop("pending", None)
    _save_state(m["id"], st)
    db.run("INSERT INTO arena_answers(user_id,match_id,kp_id,family,level,q,p_pred,correct,ms,xp,created_at) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?)", kid["id"], m["id"], kp_id, fam, lv, it["q"][:200], it.get("p"),
           1 if correct else 0, ms, out["xp"], db.now())
    out["xp_total"] = xp_total(kid["id"])
    return out


def _close(match_id: int, started_at: str, result: str, stats: dict, cap: int = 600) -> int:
    secs = int(min(cap, max(0, (_ts(db.now()) - _ts(started_at)).total_seconds())))
    row = db.one("SELECT data FROM arena_matches WHERE id=?", match_id)
    st = db.jload(row["data"], {}) if row else {}
    st.pop("pending", None)
    st["client"] = stats
    db.run("UPDATE arena_matches SET ended_at=?, seconds=?, result=?, data=? WHERE id=?",
           db.now(), secs, result, db.jdump(st), match_id)
    return secs


def _clean_stats(stats: dict) -> dict:
    out = {}
    for k in ("zero_energy_s", "specials", "weapons", "hp_left", "ai_hp_left"):
        v = (stats or {}).get(k)
        if isinstance(v, (int, float)):
            out[k] = max(0, min(10_000, int(v)))
        elif isinstance(v, list):
            out[k] = [str(x)[:20] for x in v[:10]]
    return out


def end(kid: dict, match_id, result: str, stats: dict | None = None) -> dict:
    m = _match(kid, match_id)
    result = result if result in ("win", "lose", "draw", "quit") else "quit"
    clean = _clean_stats(stats or {})
    secs = _close(m["id"], m["started_at"], result, clean, GAMES[m["game"]]["max_seconds"])
    st = m["state"]
    answered, right = st.get("answered", 0), st.get("right", 0)
    tips = []
    if clean.get("zero_energy_s", 0) >= 20:
        tips.append(f"有 {clean['zero_energy_s']} 秒能量是 0，能量快没时早点去答题。")
    if answered and right / answered < 0.6:
        tips.append("这局答错的多一些，慢一点看清题目再答，冷冻时间就少了。")
    if answered >= 4 and right / answered >= 0.8:
        tips.append("答题又快又准，专注力很棒！")
    if not answered:
        tips.append("这局没答题。经验值是从答题来的，试试边打边答。")
    return {"result": result, "seconds": secs, "answered": answered, "right": right,
            "accuracy": round(right / answered * 100) if answered else 0, "xp": st.get("xp", 0),
            "xp_total": xp_total(kid["id"]), "seconds_left": remaining_seconds(kid), "tips": tips}


def hub(kid: dict) -> dict:
    week = (db.today() - timedelta(days=6)).isoformat()
    w = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(correct),0) AS c FROM arena_answers WHERE user_id=? AND created_at>=?",
               kid["id"], week)
    return {"games": GAMES, "xp_total": xp_total(kid["id"]), "minutes": game_minutes(kid),
            "seconds_today": seconds_today(kid["id"]), "seconds_left": remaining_seconds(kid),
            "week_answered": w["n"], "week_right": w["c"]}


def parent_summary(kid: dict) -> dict:
    week = (db.today() - timedelta(days=6)).isoformat()
    w = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(correct),0) AS c FROM arena_answers WHERE user_id=? AND created_at>=?",
               kid["id"], week)
    secs = (db.one("SELECT COALESCE(SUM(seconds),0) AS s FROM arena_matches WHERE user_id=? AND started_at>=?",
                   kid["id"], week) or {}).get("s") or 0
    return {"minutes": game_minutes(kid), "week_minutes": round(secs / 60), "week_answered": w["n"],
            "week_right": w["c"], "xp_total": xp_total(kid["id"])}


# ------------------------------------------------------------------ 公平看板（管理后台）

BANDS = ((0.25, "掌握 0–25%"), (0.5, "掌握 25–50%"), (0.75, "掌握 50–75%"), (1.01, "掌握 75–100%"))


def kid_band(kid: dict) -> str:
    """水平分组：数学里当前学段及以前、游戏覆盖的知识点中已掌握的比例（和游戏无关的学业水平）。"""
    rank = _cur_rank(kid)
    ids = [kp for kp, _, _ in quickgen.FAMILIES.values() if kp in catalog.kps and stage_rank(catalog.kps[kp]["stage"]) <= rank]
    if not ids:
        return BANDS[0][1]
    st = {r["kp_id"]: r["status"] for r in db.q(
        f"SELECT kp_id, status FROM mastery WHERE user_id=? AND kp_id IN ({','.join('?' * len(ids))})", kid["id"], *ids)}
    share = sum(st.get(k) == "mastered" for k in ids) / len(ids)
    return next(label for cut, label in BANDS if share < cut)


def fairness(days: int = 30) -> dict:
    """按水平分组：预测答对率 vs 实际答对率、每分钟经验值、打电脑的胜率。各组实际答对率都接近目标，才说明出题是公平的。"""
    since = (db.today() - timedelta(days=days - 1)).isoformat()
    kids = {r["user_id"] for r in db.q("SELECT DISTINCT user_id FROM arena_answers WHERE created_at>=?", since)}
    groups = {label: {"band": label, "kids": 0, "answers": 0, "right": 0, "p_sum": 0.0, "xp": 0, "ms": 0,
                      "matches": 0, "wins": 0, "seconds": 0} for _, label in BANDS}
    for uid in kids:
        u = db.one("SELECT * FROM users WHERE id=?", uid)
        if not u:
            continue
        g = groups[kid_band(dict(u))]
        g["kids"] += 1
        a = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(correct),0) AS c, COALESCE(SUM(p_pred),0) AS p, "
                   "COALESCE(SUM(xp),0) AS xp, COALESCE(SUM(ms),0) AS ms FROM arena_answers WHERE user_id=? AND created_at>=?",
                   uid, since)
        g["answers"] += a["n"]
        g["right"] += a["c"]
        g["p_sum"] += a["p"]
        g["xp"] += a["xp"]
        g["ms"] += a["ms"]
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
    cal = [{**dict(r), "name": quickgen.FAMILIES[r["family"]][2] if r["family"] in quickgen.FAMILIES else r["family"]}
           for r in db.q("SELECT family, level, b, n FROM arena_calib ORDER BY family, level")]
    return {"days": days, "target": round(GAMES["stickman"]["target"] * 100), "rows": rows,
            "calib": cal}
