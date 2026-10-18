"""每个孩子自己的答题对局（所有游戏共用）：开局 → 出题 / 作答（可以很多次）→ 结束（个人战报：专注值、练了什么、贴纸）。

一局游戏（房间里大家一起打的那一关）记在 sessions.py；联网时一局里每个人各有一个答题对局，题按各自的水平出。
游戏前端只管玩法；出题、判分、经验值、冷冻、时长、贴纸都在服务器，客户端改不了：
- 作答用服务器计时；关掉答题面板 90 秒内再打开还是同一道题（不能换掉难题）。
- 冷冻期间不出题；结束时客户端报的统计只用来写战报和发贴纸，数字都限定范围。
"""
import random
import time
from datetime import timedelta

from .. import db
from . import awards, progress, rules, sources
from .errors import ArenaError
from .games import GAMES, get


def max_seconds() -> dict:
    return {g: v.max_seconds for g, v in GAMES.items()}


def status(kid: dict) -> dict:
    return rules.status(kid, max_seconds())


def xp_today(kid_id: int) -> int:
    return (db.one("SELECT COALESCE(SUM(delta),0) AS s FROM arena_ledger WHERE user_id=? AND created_at>=? AND delta>0",
                   kid_id, db.today().isoformat()) or {}).get("s") or 0


def _match(kid: dict, match_id) -> dict:
    m = db.one("SELECT * FROM arena_matches WHERE id=? AND user_id=?", match_id, kid["id"])
    if not m:
        raise ArenaError("找不到这一局", 404)
    if m["ended_at"]:
        raise ArenaError("这一局已经结束了", 409)
    return {**dict(m), "state": db.jload(m["data"], {}) or {}}


def _save_state(match_id: int, st: dict):
    db.run("UPDATE arena_matches SET data=? WHERE id=?", db.jdump(st), match_id)


def ghost(kid_id: int) -> dict | None:
    """闪电赛跑里的「上次的我」：最近一局跑完的赛跑用了多久。"""
    for m in db.q("SELECT data, seconds FROM arena_matches WHERE user_id=? AND game='race' AND result IN ('win','lose') "
                  "ORDER BY id DESC LIMIT 5", kid_id):
        c = (db.jload(m["data"], {}) or {}).get("client") or {}
        if c.get("finish_s"):
            return {"finish_s": c["finish_s"], "right": (db.jload(m["data"], {}) or {}).get("right", 0)}
    return None


def start(kid: dict, game: str, session=None, resume=None) -> dict:
    """我自己的一局答题（每个孩子各一局，题按自己的学习情况出，见 sources.pick）。session：房间里正在玩的那一局（sessions.py），对局挂在它下面。
    resume：刷新、换设备回到这一局时，接着用原来的对局（还开着的话）。"""
    g = get(game)
    st = status(kid)
    why = rules.locked_reason(st)
    if why:
        raise ArenaError(why, 403)
    sid = None
    if session:
        s = db.one("SELECT s.id FROM game_sessions s JOIN room_members m ON m.room_id=s.room_id "
                   "WHERE s.id=? AND s.game=? AND m.user_id=? AND s.ended_at IS NULL", session, game, kid["id"])
        if not s:
            raise ArenaError("这一局已经结束了", 410)
        sid = s["id"]
        old = db.one("SELECT id, data FROM arena_matches WHERE id=? AND user_id=? AND session_id=? AND ended_at IS NULL", resume, kid["id"], sid) \
            if resume else None
        if old:
            return {"match_id": old["id"], "seconds_left": st["left"], "avatar": awards.avatar(kid), "ghost": ghost(kid["id"]) if game == "race" else None, "target": g.target}
    for m in db.q("SELECT id, game, started_at FROM arena_matches WHERE user_id=? AND ended_at IS NULL", kid["id"]):
        _close(m["id"], m["started_at"], "quit", {}, GAMES[m["game"]].max_seconds if m["game"] in GAMES else 600)
    mid = db.insert("INSERT INTO arena_matches(user_id, game, mode, started_at, data, session_id) VALUES(?,?,?,?,?,?)",
                    kid["id"], game, "solo" if not g.multi else "team", db.now(), db.jdump({"streak": 0, "fast_wrongs": 0}), sid)
    return {"match_id": mid, "seconds_left": st["left"], "avatar": awards.avatar(kid),
            "ghost": ghost(kid["id"]) if game == "race" else None, "target": g.target}


def question(kid: dict, match_id, rng: random.Random | None = None) -> dict:
    m = _match(kid, match_id)
    st, now = m["state"], time.time()
    if st.get("freeze_until", 0) > now:
        raise ArenaError("冷冻中，等一下再答", 423, freeze_ms=int((st["freeze_until"] - now) * 1000))
    pend = st.get("pending")
    if not pend or now - pend["issued"] > rules.PENDING_KEEP_S:
        it = sources.next_item(kid, st, get(m["game"]).target, rng or random.Random())
        pend = {"item": it, "issued": now}
        st["pending"] = pend
        _save_state(m["id"], st)
    return {"item": sources.public(pend["item"])}


@db.atomic_user
def answer(kid: dict, match_id, item_id: str, ans, dont_know=False, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    m = _match(kid, match_id)
    st, now = m["state"], time.time()
    pend = st.get("pending")
    if not pend or pend["item"]["id"] != item_id:
        raise ArenaError("这道题已经过期了，再点一次答题", 409)
    it = pend["item"]
    ms = int((now - pend["issued"]) * 1000)  # 用服务器计时，不信客户端
    correct = sources.settle(kid, st, it, ans, dont_know, ms)   # 学习记录（不计入当天打卡）、能力值
    dim, fam, lv = it["dim"], it["family"], it["level"]
    out = {"correct": correct, "answer": sources.itemtypes.display(it), "explain": it.get("explain", ""), "xp": 0}
    if correct:
        xp, crit = rules.roll_xp(rng, xp_today(kid["id"]))
        st["streak"] = st.get("streak", 0) + 1
        st["best_streak"] = max(st.get("best_streak", 0), st["streak"])
        st["fast_wrongs"] = 0
        st["xp"] = st.get("xp", 0) + xp
        st["right"] = st.get("right", 0) + 1
        db.run("INSERT INTO arena_ledger(user_id,delta,reason,match_id,created_at) VALUES(?,?,?,?,?)",
               kid["id"], xp, "answer", m["id"], db.now())
        out.update(xp=xp, crit=crit, streak=st["streak"], special=st["streak"] % rules.SPECIAL_EVERY == 0)
    else:
        fast = ms < rules.FAST_MS and not dont_know
        st["fast_wrongs"] = st.get("fast_wrongs", 0) + 1 if fast else 0
        if fast:
            st["guesses"] = st.get("guesses", 0) + (1 if st["fast_wrongs"] >= 2 else 0)
        st["streak"] = 0
        sec = rules.freeze_seconds(st["fast_wrongs"], fast) if get(m["game"]).freeze else 0
        st["freeze_until"] = now + sec
        out.update(freeze_ms=sec * 1000, streak=0, guessing=fast and st["fast_wrongs"] >= 2, dont_know=bool(dont_know))
    st["answered"] = st.get("answered", 0) + 1
    st["ms"] = st.get("ms", 0) + ms
    st.setdefault("dims", {})
    d = st["dims"].setdefault(dim, [0, 0, it.get("topic", "")])
    d[0] += 1
    d[1] += 1 if correct else 0
    st.pop("pending", None)
    _save_state(m["id"], st)
    db.run("INSERT INTO arena_answers(user_id,match_id,kp_id,family,level,q,p_pred,correct,ms,xp,created_at) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?)", kid["id"], m["id"], dim, fam, lv, it["q"][:200], it.get("p"),
           1 if correct else 0, ms, out["xp"], db.now())
    return out


def _close(match_id: int, started_at: str, result: str, stats: dict, cap: int = 600) -> int:
    secs = int(min(cap, max(0, (rules._ts(db.now()) - rules._ts(started_at)).total_seconds())))
    row = db.one("SELECT data FROM arena_matches WHERE id=?", match_id)
    st = db.jload(row["data"], {}) if row else {}
    st.pop("pending", None)
    st["client"] = stats
    db.run("UPDATE arena_matches SET ended_at=?, seconds=?, result=?, data=? WHERE id=?",
           db.now(), secs, result, db.jdump(st), match_id)
    return secs


STAT_KEYS = ("zero_energy_s", "specials", "hp_left", "ai_hp_left", "wave", "boss", "finish_s", "castle_hp",
             "monsters", "combo", "distance")


def _clean_stats(stats: dict) -> dict:
    out = {}
    for k in STAT_KEYS + ("weapons",):
        v = (stats or {}).get(k)
        if isinstance(v, bool):
            out[k] = int(v)
        elif isinstance(v, (int, float)):
            out[k] = max(0, min(10_000, int(v)))
        elif isinstance(v, list):
            out[k] = [str(x)[:20] for x in v[:10]]
    return out


def end(kid: dict, match_id, result: str, stats: dict | None = None) -> dict:
    m = _match(kid, match_id)
    result = result if result in ("win", "lose", "draw", "quit") else "quit"
    clean = _clean_stats(stats or {})
    secs = _close(m["id"], m["started_at"], result, clean, get(m["game"]).max_seconds)
    st = m["state"]
    answered, right = st.get("answered", 0), st.get("right", 0)
    f = focus(m["id"])
    tips = []
    if f["index"] is not None:
        if f["index"] >= 110:
            tips.append(f"专注值 {f['index']}：比平时的你多答对了 {f['extra']} 道，这局很投入！")
        elif f["index"] >= 90:
            tips.append(f"专注值 {f['index']}：发挥稳定，和平时的你一样。")
        else:
            tips.append(f"专注值 {f['index']}：比平时的你少答对了 {-f['extra']} 道，下局看清题目再答。")
    if f["guesses"] >= 2:
        tips.append(f"有 {f['guesses']} 道题 3 秒内就答错了，像是在猜。认真答，冷冻会少很多。")
    if clean.get("zero_energy_s", 0) >= 20:
        tips.append(f"有 {clean['zero_energy_s']} 秒能量是 0，能量快没时早点去答题。")
    if not answered:
        tips.append("这局没答题。能量是从答题来的，试试边玩边答。")
    # 这局在哪些内容上进步了：按能力维度汇总答对几题
    topics = [{"topic": t, "n": n, "right": r} for n, r, t in (st.get("dims") or {}).values() if t]
    topics.sort(key=lambda x: -x["n"])
    st_now = status(kid)
    new = awards.after_match(kid["id"], get(m["game"]).match_stickers(result, clean), st, st_now, f)
    return {"result": result, "seconds": secs, "answered": answered, "right": right,
            "accuracy": round(right / answered * 100) if answered else 0, "xp": st.get("xp", 0), "focus": f["index"],
            "best_streak": st.get("best_streak", 0), "seconds_left": st_now["left"], "tips": tips,
            "topics": topics[:4], "stickers": new, "level": awards.level(kid["id"])}


def focus(match_id: int) -> dict:
    """专注指数 = 实际答对题数 ÷ 预期答对题数 × 100（预期 = 每题出题时预测的答对概率之和）。
    每道题都按孩子自己的水平出，预期答对的概率大家一样，所以比的不是会多少，而是这一局有多投入：
    100 是正常发挥，高于 100 是比平时的自己更专注。答题少于 3 道不算（太少，看不出来）。3 秒内答错算「猜」。"""
    return focus_of(db.q("SELECT correct, p_pred, ms FROM arena_answers WHERE match_id=?", match_id))


def focus_of(rows) -> dict:
    exp = sum(r["p_pred"] or 0 for r in rows)
    right = sum(r["correct"] for r in rows)
    guesses = sum(1 for r in rows if not r["correct"] and (r["ms"] or 0) < rules.FAST_MS)
    ok = len(rows) >= 3 and exp >= 1
    return {"index": round(100 * right / exp) if ok else None, "extra": round(right - exp) if ok else 0,
            "answered": len(rows), "guesses": guesses}


def _week(kid_id: int) -> dict:
    week = (db.today() - timedelta(days=6)).isoformat()
    w = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(correct),0) AS c, COALESCE(SUM(p_pred),0) AS p "
               "FROM arena_answers WHERE user_id=? AND created_at>=?", kid_id, week)
    secs = (db.one("SELECT COALESCE(SUM(seconds),0) AS s FROM arena_matches WHERE user_id=? AND started_at>=?",
                   kid_id, week) or {}).get("s") or 0
    return {"answered": w["n"], "right": w["c"], "minutes": round(secs / 60),
            "focus": round(100 * w["c"] / w["p"]) if w["n"] >= 5 and w["p"] >= 1 else None}


def hub(kid: dict) -> dict:
    new = awards.refresh_learning(kid["id"])
    st = status(kid)
    return {"games": [g.card() for g in GAMES.values()], "status": st, "locked": rules.locked_reason(st), "level": awards.level(kid["id"]),
            "avatar": awards.avatar(kid), "avatars": awards.avatars(kid["id"]), "wall": awards.wall(kid["id"]),
            "new_stickers": new, "week": _week(kid["id"]),
            "ghost": ghost(kid["id"]),
            "stages": {g.id: progress.progress(g, str(kid["id"])) for g in GAMES.values() if g.levels and not g.multi}}


def parent_summary(kid: dict) -> dict:
    w = _week(kid["id"])
    st = status(kid)
    have = awards.got(kid["id"])
    return {"minutes": st["base"], "rule": st["rule_name"], "week_minutes": w["minutes"], "week_answered": w["answered"],
            "week_right": w["right"], "week_focus": w["focus"], "level": awards.level(kid["id"])["level"], "stickers": len(have)}
