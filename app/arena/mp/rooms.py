"""联网游戏的房间和队伍纪录（所有联网游戏共用）。

- 只和自己家里的兄弟姐妹玩：一个孩子开房间（选游戏、选角色），兄弟姐妹在自己的设备上加入；乐园首页会提示「X 在等你」。
- 队伍 = 这两个孩子（谁当什么角色都算同一队）。每关记队伍最好成绩和星星，打通一关开下一关，每次都能接着往后闯。
  比的是「我们俩」和上次的我们，不和别人排名（docs/DESIGN.md 5.12 的原则）。
- 谁死了谁做题、题按他自己的水平出：前端用他自己的乐园对局（matches.py，game=<游戏 id>，不冷冻）出题判分。
- 开房间的孩子报过关（两个人的设备都在场时由他统一报），服务器检查、记纪录、算星星、发贴纸。
"""
from datetime import datetime, timedelta

from ... import db
from .. import awards, rules
from ..matches import GAMES, ArenaError
from .base import get

ROOM_ALIVE_S = 90          # 房间多久没动静就当作没人了（页面开着时每 20 秒报一次）
MIN_SECONDS = 5            # 少于这个时间的过关不算（防止乱报）


def family_of(kid: dict):
    return kid.get("parent_id") if kid.get("role") == "kid" else None


def member(u: dict) -> dict:
    return {"id": u["id"], "name": u["name"], "avatar": awards.avatar(u)}


def siblings(kid: dict) -> list[dict]:
    fam = family_of(kid)
    if not fam:
        return []
    return [member(dict(u)) for u in db.q("SELECT * FROM users WHERE parent_id=? AND role='kid' AND id<>? AND status='active' "
                                          "ORDER BY id", fam, kid["id"])]


def team(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def progress(game: str, a_id: int, b_id: int) -> dict:
    """这两个孩子在这款游戏里一起打到第几关、每关最好几颗星和最好成绩。"""
    g = get(game)
    a, b = team(a_id, b_id)
    agg = "MIN(seconds)" if g.lower_is_better else "MAX(score)"
    best = {r["level"]: r for r in db.q(f"SELECT level, MAX(stars) AS stars, {agg} AS best FROM mp_runs "
                                         "WHERE game=? AND a_id=? AND b_id=? GROUP BY level", game, a, b)}
    upto = 1
    for lv in g.levels:
        if (best.get(lv["n"]) or {}).get("stars"):
            upto = min(len(g.levels), lv["n"] + 1)
    items = []
    for lv in g.levels:
        r = best.get(lv["n"]) or {}
        items.append({"n": lv["n"], "name": lv["name"], "stars": r.get("stars") or 0, "best": round(r.get("best") or 0, 1),
                      "open": lv["n"] <= upto, "world": g.world_of(lv["n"])["n"]})
    return {"levels": items, "upto": upto, "stars": sum(i["stars"] for i in items), "max_stars": 3 * len(items),
            "cleared": sum(1 for i in items if i["stars"])}


# ------------------------------------------------------------------ 房间

def _alive_since() -> str:
    return (datetime.fromisoformat(db.now()) - timedelta(seconds=ROOM_ALIVE_S)).isoformat(timespec="seconds")


def _room(room_id) -> dict:
    try:
        rid = int(room_id)
    except (TypeError, ValueError):
        raise ArenaError("找不到这个房间", 404)
    r = db.one("SELECT * FROM mp_rooms WHERE id=?", rid)
    if not r or r["status"] == "closed":
        raise ArenaError("这个房间已经关了", 404)
    return dict(r)


def member_room(kid: dict, room_id) -> dict:
    r = _room(room_id)
    if kid["id"] not in (r["host_id"], r["guest_id"]):
        raise ArenaError("你不在这个房间里", 403)
    return r


def touch(room_id: int) -> None:
    db.run("UPDATE mp_rooms SET seen_at=? WHERE id=?", db.now(), room_id)


def _close_mine(kid_id: int) -> None:
    db.run("UPDATE mp_rooms SET status='closed' WHERE host_id=? AND status<>'closed'", kid_id)


def create_room(kid: dict, game: str, role: str = "") -> dict:
    g = get(game)
    if not family_of(kid):
        raise ArenaError("联网游戏要和家里的兄弟姐妹一起玩", 403)
    if not siblings(kid):
        raise ArenaError("家里还没有别的小伙伴的账号，请爸爸妈妈在家长页添加。", 403)
    role = role if role in g.roles else next(iter(g.roles))
    _close_mine(kid["id"])
    rid = db.insert("INSERT INTO mp_rooms(game, family_id, host_id, host_role, level, status, created_at, seen_at) "
                    "VALUES(?,?,?,?,?,?,?,?)", game, family_of(kid), kid["id"], role, 1, "open", db.now(), db.now())
    return room_info(kid, rid)


def join(kid: dict, room_id) -> dict:
    r = _room(room_id)
    if r["family_id"] != family_of(kid):
        raise ArenaError("只能加入自己家里的房间", 403)
    if r["host_id"] == kid["id"]:
        return room_info(kid, r["id"])
    if r["guest_id"] and r["guest_id"] != kid["id"]:
        raise ArenaError("这个房间已经满了", 409)
    if r["seen_at"] < _alive_since():
        raise ArenaError("这个房间已经没人了，自己开一个吧", 410)
    _close_mine(kid["id"])
    db.run("UPDATE mp_rooms SET guest_id=?, status='ready', seen_at=? WHERE id=?", kid["id"], db.now(), r["id"])
    return room_info(kid, r["id"])


def leave(kid: dict, room_id) -> None:
    r = member_room(kid, room_id)
    if r["host_id"] == kid["id"]:
        db.run("UPDATE mp_rooms SET status='closed' WHERE id=?", r["id"])
    else:
        db.run("UPDATE mp_rooms SET guest_id=NULL, status='open' WHERE id=?", r["id"])


def _locked(u: dict) -> str:
    return rules.locked_reason(rules.status(u, {g: v["max_seconds"] for g, v in GAMES.items()}))


def other_role(game: str, role: str) -> str:
    return next((r for r in get(game).roles if r != role), role)


def room_info(kid: dict, room_id) -> dict:
    r = member_room(kid, room_id)
    host = dict(db.one("SELECT * FROM users WHERE id=?", r["host_id"]))
    guest = dict(db.one("SELECT * FROM users WHERE id=?", r["guest_id"])) if r["guest_id"] else None
    members = [{**member(host), "role": r["host_role"], "host": True, "locked": _locked(host)}]
    if guest:
        members.append({**member(guest), "role": other_role(r["game"], r["host_role"]), "host": False, "locked": _locked(guest)})
    return {"id": r["id"], "game": r["game"], "status": r["status"], "level": r["level"], "members": members,
            "me": next(m for m in members if m["id"] == kid["id"]), "host": kid["id"] == r["host_id"],
            "progress": progress(r["game"], host["id"], guest["id"]) if guest else None}


def set_role(kid: dict, room_id, role: str) -> dict:
    r = member_room(kid, room_id)
    if r["host_id"] != kid["id"]:
        raise ArenaError("开房间的人才能换角色", 403)
    if role in get(r["game"]).roles:
        db.run("UPDATE mp_rooms SET host_role=? WHERE id=?", role, r["id"])
    return room_info(kid, r["id"])


def set_level(kid: dict, room_id, n) -> dict:
    r = member_room(kid, room_id)
    if r["host_id"] != kid["id"]:
        raise ArenaError("开房间的人来选关", 403)
    lv = get(r["game"]).level(n)
    if r["guest_id"] and lv["n"] > progress(r["game"], r["host_id"], r["guest_id"])["upto"]:
        raise ArenaError("这一关还没解锁，先一起打通前面的关", 403)
    db.run("UPDATE mp_rooms SET level=? WHERE id=?", lv["n"], r["id"])
    return room_info(kid, r["id"])


def lobby(kid: dict, game: str) -> dict:
    """进一款联网游戏时看到的：家里的小伙伴、谁开了房间在等我、我自己开的房间。"""
    g = get(game)
    sibs = siblings(kid)
    rooms = []
    if family_of(kid):
        for r in db.q("SELECT * FROM mp_rooms WHERE game=? AND family_id=? AND status<>'closed' AND seen_at>=? ORDER BY id DESC",
                      game, family_of(kid), _alive_since()):
            if r["host_id"] == kid["id"] or r["guest_id"] in (None, kid["id"]):
                rooms.append({"id": r["id"], "mine": r["host_id"] == kid["id"], "host_role": r["host_role"], "level": r["level"],
                              "host": next((s for s in sibs if s["id"] == r["host_id"]), None), "joined": r["guest_id"] == kid["id"]})
    return {"game": g.describe(), "siblings": sibs, "rooms": rooms, "locked": _locked(kid), "levels": g.public_levels(),
            "teams": {str(s["id"]): progress(game, kid["id"], s["id"]) for s in sibs}}


def invites(kid: dict) -> list[dict]:
    """乐园首页的提示：家里有人开了联网游戏的房间在等我。"""
    if not family_of(kid):
        return []
    sibs = {s["id"]: s for s in siblings(kid)}
    out = []
    for r in db.q("SELECT * FROM mp_rooms WHERE family_id=? AND status='open' AND seen_at>=? AND host_id<>? ORDER BY id DESC",
                  family_of(kid), _alive_since(), kid["id"]):
        if r["host_id"] in sibs and r["game"] in GAMES:
            out.append({"id": r["id"], "game": r["game"], "name": GAMES[r["game"]]["name"], "icon": GAMES[r["game"]]["icon"],
                        "host": sibs[r["host_id"]]})
    return out


# ------------------------------------------------------------------ 过关

def finish(kid: dict, room_id, n, report: dict) -> dict:
    """开房间的孩子报过关：记一条队伍纪录，算星星，开下一关，两个人都发贴纸。"""
    r = member_room(kid, room_id)
    if r["host_id"] != kid["id"] or not r["guest_id"]:
        raise ArenaError("开房间的人来报过关", 403)
    g = get(r["game"])
    lv = g.level(n)
    rep = g.clean(lv, report or {})
    if not MIN_SECONDS <= rep["seconds"] <= 3600:
        raise ArenaError("用时不对")
    a, b = team(r["host_id"], r["guest_id"])
    before = progress(r["game"], a, b)
    if lv["n"] > before["upto"]:
        raise ArenaError("这一关还没解锁", 403)
    prev = before["levels"][lv["n"] - 1]["best"]
    checks = g.checks(lv, rep)
    stars = sum(1 for k, _ in g.stars if checks.get(k))
    db.insert("INSERT INTO mp_runs(game, room_id, a_id, b_id, level, seconds, deaths, score, stars, data, created_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?)", r["game"], r["id"], a, b, lv["n"], rep["seconds"], rep["deaths"], rep["score"],
              stars, db.jdump(rep), db.now())
    after = progress(r["game"], a, b)
    value = rep["seconds"] if g.lower_is_better else rep["score"]
    better = (value < prev) if g.lower_is_better else (value > prev)
    keys = ["mp1"] + (["mp_clean"] if rep["deaths"] == 0 else []) + (["mp_all"] if after["cleared"] == len(g.levels) else [])
    stickers = {str(uid): awards.give(uid, keys) for uid in (a, b)}
    nxt = lv["n"] + 1 if lv["n"] < len(g.levels) else None
    db.run("UPDATE mp_rooms SET level=? WHERE id=?", nxt or lv["n"], r["id"])
    return {"n": lv["n"], **rep, "stars": stars, "checks": checks, "value": value, "prev_best": prev,
            "new_best": not prev or better, "best": value if (not prev or better) else prev,
            "next": nxt, "progress": after, "stickers": stickers}
