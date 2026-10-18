"""对局记录（Record）：每一局一条 game_sessions，单机和联网都一样。

- 一局 = 一个房间里的一支队伍打一关（没有关卡的游戏 level 是 0）。开局时服务器建（随机种子也在这里定，大家一样），
  结束时记结果（win / lose / quit）、用了多久、几颗星、游戏报上来的数据。
- 每个孩子在这一局里的答题还是各自的乐园对局（arena_matches，session_id 指回这里）：题按各自的水平出，专注值也是各自的。
- 存档（state）也挂在这一局上：玩的时候每台设备每 2 秒存一次自己的状态，开房间的那台再存共享的世界；
  刷新页面、换设备时从这里接着玩（rooms.py）。一局结束存档就没用了。
"""
import random
import time
from datetime import datetime

from .. import db

ALIVE_S = 600          # 一局多久没人存档就当作没人玩了（大家都走开了）


def start(room_id: int, game: str, level: int, team: str) -> dict:
    """开一局：房间里上一局没结束的算退出。"""
    for s in db.q("SELECT id FROM game_sessions WHERE room_id=? AND ended_at IS NULL", room_id):
        close(s["id"], "quit")
    sid = db.insert("INSERT INTO game_sessions(game, level, room_id, team, seed, started_at, state, data) VALUES(?,?,?,?,?,?,?,?)",
                    game, level, room_id, team, random.randrange(1, 2 ** 31), db.now(), db.jdump({"players": {}, "world": None}), "{}")
    db.run("UPDATE game_rooms SET session_id=? WHERE id=?", sid, room_id)
    return get(sid)


def get(sid) -> dict | None:
    s = db.one("SELECT * FROM game_sessions WHERE id=?", sid) if sid else None
    if not s:
        return None
    return {**dict(s), "state": db.jload(s["state"], None) or {"players": {}, "world": None}}


def live(sid) -> dict | None:
    """还在玩的一局：没结束、最近有人存过档（刚开的也算）。太久没动静的顺手记成退出。"""
    s = get(sid)
    if not s or s["ended_at"]:
        return None
    last = max([p.get("at", 0) for p in s["state"]["players"].values()] or [0])
    if time.time() - (last or _ts(s["started_at"])) > ALIVE_S:
        close(s["id"], "quit")
        return None
    return s


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def elapsed(s: dict) -> float:
    """这一局开始到现在多少秒（服务器的钟）。"""
    return round(max(0.0, min(3600.0, _ts(db.now()) - _ts(s["started_at"]))), 1)


def _int(v, lo, hi, default=0):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def save(s: dict, kid_id: int, host: bool, msg: dict) -> None:
    """存档：我自己的状态（游戏自己定：位置、长度、道具……）、玩了多少秒、还欠几道题、倒下几次、我的答题对局；
    开房间的再存共享的世界。"""
    st = s["state"]
    try:
        el = max(0.0, min(3600.0, float(msg.get("el") or 0)))
    except (TypeError, ValueError):
        el = 0.0
    st["players"][str(kid_id)] = {"me": msg.get("me"), "need": _int(msg.get("need"), 0, 9), "deaths": _int(msg.get("deaths"), 0, 999),
                                  "match": _int(msg.get("match"), 0, 2 ** 62) or None, "el": el, "at": time.time()}
    if host and msg.get("w") is not None:
        st["world"] = msg.get("w")
    db.run("UPDATE game_sessions SET state=? WHERE id=? AND ended_at IS NULL", db.jdump(st), s["id"])


def resume_for(s: dict, kid_id: int) -> dict | None:
    """我回到这一局要的东西：我的存档、大家里玩得最久的时间、共享的世界、队友倒下几次。我没存过档就是 None。"""
    ps = s["state"]["players"]
    mine = ps.get(str(kid_id))
    if not mine:
        return None
    match = mine.get("match")
    if match and not db.one("SELECT id FROM arena_matches WHERE id=? AND user_id=? AND ended_at IS NULL", match, kid_id):
        match = None
    return {"el": max(p.get("el", 0) for p in ps.values()), "world": s["state"].get("world"), "me": mine.get("me"),
            "need": mine.get("need", 0), "deaths": mine.get("deaths", 0), "match": match,
            "peer_deaths": sum(p.get("deaths", 0) for k, p in ps.items() if k != str(kid_id))}


def close(sid: int, result: str, seconds: float | None = None, stars: int = 0, data: dict | None = None) -> None:
    s = get(sid)
    if not s or s["ended_at"]:
        return
    if seconds is None:
        seconds = elapsed(s)
    db.run("UPDATE game_sessions SET ended_at=?, result=?, seconds=?, stars=?, data=?, state=NULL WHERE id=? AND ended_at IS NULL",
           db.now(), result, round(seconds, 1), stars, db.jdump(data or {}), sid)


def focus(sid: int):
    """这一局大家答题的专注值（matches.focus 的算法，把这一局所有人的题放在一起）。"""
    from .matches import focus_of
    return focus_of(db.q("SELECT a.correct, a.p_pred, a.ms FROM arena_answers a JOIN arena_matches m ON m.id=a.match_id "
                         "WHERE m.session_id=?", sid))["index"]
