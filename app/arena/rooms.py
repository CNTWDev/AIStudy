"""房间、成员和设备（所有游戏共用）：单机是一个人的房间，联网是几个人的房间，开局、存档、过关、换设备都是同一套。

- 房间里放 1～N 个人（game_rooms + room_members，N 是游戏登记的 players）。开房间的人是房主：选关、开局、报结果，
  共享的东西由他那台设备算。房主走了，由最早进来的人接任；人都走了房间就关了。
- 单机游戏每个孩子每款游戏一个房间，一直留着（刷新、换设备回来接着玩）；联网游戏只和自己家里的兄弟姐妹玩，
  一个孩子开房间，其他人在自己的设备上加入，乐园首页会提示「X 在等你」。
- 设备：一个账号在一个房间里同一时间只有一台设备在玩（room_members.device，前端每个页面一个设备号，刷新不变）。
  所有改房间的请求都带设备号，不是当前那台的一律拒绝（409 moved），那台就知道「已经换到另一台设备上了」。
  另一台设备进来时：原来那台正在玩（最近几秒存过档）就先问要不要换过来（elsewhere），点了才换（take）；
  原来那台没在玩就直接换过来。
- 一局怎么开、怎么记、存档怎么接着玩：sessions.py；每支队伍打到哪了：progress.py。
"""
from datetime import datetime, timedelta

from .. import db
from . import awards, progress, rules, sessions
from .errors import ArenaError
from .games import GAMES, Game, get

ROOM_ALIVE_S = 90          # 联网房间多久没动静就当作没人了（页面开着时每 20 秒报一次）
PLAYING_S = 8              # 一台设备多久没存档就不算「正在玩」（玩的时候每 2 秒存一次）
MIN_SECONDS = 5            # 少于这个时间的过关不算（防止乱报）
MAX_SAVE = 16000           # 一次存档最多多少字节


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


def _ago(seconds: int) -> str:
    return (datetime.fromisoformat(db.now()) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _locked(u: dict) -> str:
    return rules.locked_reason(rules.status(u, {g: v.max_seconds for g, v in GAMES.items()}))


# ------------------------------------------------------------------ 房间和成员

def _room(room_id) -> dict:
    try:
        rid = int(room_id)
    except (TypeError, ValueError):
        raise ArenaError("找不到这个房间", 404)
    r = db.one("SELECT * FROM game_rooms WHERE id=?", rid)
    if not r or r["status"] == "closed":
        raise ArenaError("这个房间已经关了", 404)
    return dict(r)


def _members(room_id: int) -> list[dict]:
    return [dict(m) for m in db.q("SELECT * FROM room_members WHERE room_id=? ORDER BY joined_at, user_id", room_id)]


def _membership(kid: dict, room_id) -> tuple[dict, dict]:
    r = _room(room_id)
    m = db.one("SELECT * FROM room_members WHERE room_id=? AND user_id=?", r["id"], kid["id"])
    if not m:
        raise ArenaError("你不在这个房间里", 403)
    return r, dict(m)


def member_room(kid: dict, room_id) -> dict:
    return _membership(kid, room_id)[0]


def _on_device(kid: dict, room_id, device: str) -> tuple[dict, dict]:
    """这台设备是不是我现在在玩的那台：不是就拒绝（我在另一台设备上接着玩了）。"""
    r, m = _membership(kid, room_id)
    if m["device"] and m["device"] != device:
        raise ArenaError("已经换到另一台设备上玩了", 409, moved=True)
    return r, m


def _host_only(r: dict, kid: dict, what: str) -> None:
    if r["host_id"] != kid["id"]:
        raise ArenaError(f"开房间的人来{what}", 403)


def touch(room_id: int) -> None:
    db.run("UPDATE game_rooms SET seen_at=? WHERE id=?", db.now(), room_id)


def _claim(room_id: int, kid_id: int, device: str) -> None:
    db.run("UPDATE room_members SET device=?, seen_at=NULL WHERE room_id=? AND user_id=?", device[:40], room_id, kid_id)


def _add(room_id: int, kid_id: int, role: str, device: str) -> None:
    db.run("INSERT INTO room_members(room_id, user_id, role, joined_at, device) VALUES(?,?,?,?,?)",
           room_id, kid_id, role, db.now(), device[:40])


def _leave_multi(kid: dict) -> None:
    """开新的联网房间、加入别人的房间之前：离开我在别的联网房间里的位置（一个孩子同时只在一个联网房间里）。"""
    for r in db.q("SELECT r.id FROM game_rooms r JOIN room_members m ON m.room_id=r.id "
                  "WHERE m.user_id=? AND r.status<>'closed' AND r.multi=1", kid["id"]):
        _remove(_room(r["id"]), kid["id"])


def _remove(r: dict, kid_id: int) -> None:
    db.run("DELETE FROM room_members WHERE room_id=? AND user_id=?", r["id"], kid_id)
    rest = _members(r["id"])
    s = sessions.live(r["session_id"])
    if s:
        sessions.close(s["id"], "quit")
    if not rest:
        db.run("UPDATE game_rooms SET status='closed' WHERE id=?", r["id"])
    elif r["host_id"] == kid_id:   # 房主走了：最早进来的人接任
        db.run("UPDATE game_rooms SET host_id=? WHERE id=?", rest[0]["user_id"], r["id"])


def _free_role(g: Game, taken: list[str]) -> str:
    return next((k for k in g.roles if k not in taken), next(iter(g.roles), ""))


def create(kid: dict, game: str, role: str = "", device: str = "") -> dict:
    """单机：回到我这款游戏的房间（没有就开一个）。联网：开一个新房间，等家里的兄弟姐妹加入。"""
    g = get(game)
    if not g.multi:
        r = db.one("SELECT id FROM game_rooms WHERE game=? AND host_id=? AND status<>'closed' ORDER BY id DESC", game, kid["id"])
        if r:
            return enter(kid, r["id"], device)
        rid = db.insert("INSERT INTO game_rooms(game, family_id, host_id, multi, level, status, created_at, seen_at) VALUES(?,?,?,?,?,?,?,?)",
                        game, family_of(kid) or 0, kid["id"], 0, 1, "open", db.now(), db.now())
        _add(rid, kid["id"], "", device)
        return room_info(kid, rid)
    if not family_of(kid):
        raise ArenaError("联网游戏要和家里的兄弟姐妹一起玩", 403)
    if not siblings(kid):
        raise ArenaError("家里还没有别的小伙伴的账号，请爸爸妈妈在家长页添加。", 403)
    _leave_multi(kid)
    rid = db.insert("INSERT INTO game_rooms(game, family_id, host_id, multi, level, status, created_at, seen_at) VALUES(?,?,?,?,?,?,?,?)",
                    game, family_of(kid), kid["id"], 1, 1, "open", db.now(), db.now())
    _add(rid, kid["id"], role if role in g.roles else _free_role(g, []), device)
    return room_info(kid, rid)


def enter(kid: dict, room_id, device: str = "") -> dict:
    """进房间：刷新、换设备、加入都走这里。不在房间里就加入；我在另一台设备上正在玩就先问（elsewhere = 第几关），
    不然这台就是我在玩的设备。"""
    r = _room(room_id)
    m = db.one("SELECT * FROM room_members WHERE room_id=? AND user_id=?", r["id"], kid["id"])
    if not m:
        g = get(r["game"])
        ms = _members(r["id"])
        if not r["multi"] or r["family_id"] != family_of(kid):
            raise ArenaError("只能加入自己家里的房间", 403)
        if len(ms) >= g.players[1]:
            raise ArenaError("这个房间已经满了", 409)
        if r["seen_at"] < _ago(ROOM_ALIVE_S):
            raise ArenaError("这个房间已经没人了，自己开一个吧", 410)
        _leave_multi(kid)
        _add(r["id"], kid["id"], _free_role(g, [x["role"] for x in ms]), device)
        touch(r["id"])
        return room_info(kid, r["id"])
    if m["device"] and m["device"] != device and m["seen_at"] and m["seen_at"] >= _ago(PLAYING_S):
        s = sessions.live(r["session_id"])
        if s:
            return {**room_info(kid, r["id"]), "elsewhere": s["level"] or True}
    _claim(r["id"], kid["id"], device)
    return room_info(kid, r["id"])


def take(kid: dict, room_id, device: str = "") -> dict:
    """在这台设备上接着玩：原来那台下次存档时就知道换走了。"""
    r = member_room(kid, room_id)
    _claim(r["id"], kid["id"], device)
    return room_info(kid, r["id"])


def leave(kid: dict, room_id, device: str = "") -> None:
    r, _ = _on_device(kid, room_id, device)
    _remove(r, kid["id"])


def swap_roles(kid: dict, room_id, device: str = "") -> dict:
    """房主点「换角色」：每个人换成下一个角色（两个人就是互换）。"""
    r, _ = _on_device(kid, room_id, device)
    _host_only(r, kid, "换角色")
    roles = list(get(r["game"]).roles)
    if roles:
        for m in _members(r["id"]):
            nxt = roles[(roles.index(m["role"]) + 1) % len(roles)] if m["role"] in roles else roles[0]
            db.run("UPDATE room_members SET role=? WHERE room_id=? AND user_id=?", nxt, r["id"], m["user_id"])
    return room_info(kid, r["id"])


def team_of(room_id: int) -> str:
    return progress.team_key(m["user_id"] for m in _members(room_id))


def set_level(kid: dict, room_id, n, device: str = "") -> dict:
    r, _ = _on_device(kid, room_id, device)
    _host_only(r, kid, "选关")
    g = get(r["game"])
    lv = g.level(n)
    if lv["n"] > progress.progress(g, team_of(r["id"]))["upto"]:
        raise ArenaError("这一关还没解锁，先打通前面的关", 403)
    db.run("UPDATE game_rooms SET level=? WHERE id=?", lv["n"], r["id"])
    return room_info(kid, r["id"])


def room_info(kid: dict, room_id) -> dict:
    r = member_room(kid, room_id)
    g = get(r["game"])
    users = {u["id"]: dict(u) for u in db.q("SELECT * FROM users WHERE id IN (SELECT user_id FROM room_members WHERE room_id=?)", r["id"])}
    members = [{**member(users[m["user_id"]]), "role": m["role"], "host": m["user_id"] == r["host_id"],
                "locked": _locked(users[m["user_id"]])} for m in _members(r["id"]) if m["user_id"] in users]
    team = progress.team_key(m["id"] for m in members)
    full = len(members) >= g.players[0]
    prog = progress.progress(g, team) if g.levels and full else None
    s = sessions.live(r["session_id"])
    play = None
    if s:
        play = {"session": s["id"], "n": s["level"], "seed": s["seed"],
                "stage": {**(g.stage(s["level"], progress.progress(g, s["team"])) if s["level"] else {}), **g.team_setup(s["team"])},
                "resume": sessions.resume_for(s, kid["id"])}
    return {"id": r["id"], "game": r["game"], "level": r["level"], "members": members,
            "me": next(m for m in members if m["id"] == kid["id"]), "host": kid["id"] == r["host_id"], "full": full,
            "progress": prog, "play": play}


# ------------------------------------------------------------------ 大厅、邀请

def my_room(kid: dict, game: str):
    """打开游戏页面时：我已经在的这款游戏的房间（刷新了、换了设备），没有就是 None。"""
    r = db.one("SELECT r.* FROM game_rooms r JOIN room_members m ON m.room_id=r.id WHERE r.game=? AND m.user_id=? "
               "AND r.status<>'closed' ORDER BY r.id DESC", game, kid["id"])
    if not r or (r["multi"] and r["seen_at"] < _ago(ROOM_ALIVE_S)):
        return None
    return r["id"]


def playing(room_id) -> bool:
    """这个房间里有一局正在玩（游戏没解锁时也让接着玩完这一局，比如刷新了）。"""
    r = db.one("SELECT session_id FROM game_rooms WHERE id=?", room_id) if room_id else None
    return bool(r and r["session_id"] and sessions.live(r["session_id"]))


def lobby(kid: dict, game: str) -> dict:
    """进一款联网游戏时看到的：家里的小伙伴、谁开了房间在等我、和每个小伙伴一起打到哪了。"""
    g = get(game)
    sibs = siblings(kid)
    rooms = []
    if g.multi and family_of(kid):
        for r in db.q("SELECT * FROM game_rooms WHERE game=? AND family_id=? AND multi=1 AND status<>'closed' AND seen_at>=? "
                      "ORDER BY id DESC", game, family_of(kid), _ago(ROOM_ALIVE_S)):
            ms = _members(r["id"])
            ids = [m["user_id"] for m in ms]
            if kid["id"] in ids or len(ms) < g.players[1]:
                host = next((m for m in ms if m["user_id"] == r["host_id"]), {})
                rooms.append({"id": r["id"], "mine": kid["id"] in ids, "level": r["level"], "role": host.get("role", ""),
                              "host": next((s for s in sibs if s["id"] == r["host_id"]), None)})
    teams = {str(s["id"]): progress.progress(g, progress.team_key((kid["id"], s["id"]))) for s in sibs} if g.levels and g.multi else {}
    return {"siblings": sibs, "rooms": rooms, "locked": _locked(kid), "teams": teams}


def invites(kid: dict) -> list[dict]:
    """乐园首页的提示：家里有人开了联网游戏的房间在等我。"""
    if not family_of(kid):
        return []
    sibs = {s["id"]: s for s in siblings(kid)}
    out = []
    for r in db.q("SELECT * FROM game_rooms WHERE family_id=? AND multi=1 AND status<>'closed' AND seen_at>=? AND host_id<>? "
                  "ORDER BY id DESC", family_of(kid), _ago(ROOM_ALIVE_S), kid["id"]):
        g = GAMES.get(r["game"])
        ids = [m["user_id"] for m in _members(r["id"])]
        if g and r["host_id"] in sibs and kid["id"] not in ids and len(ids) < g.players[1]:
            out.append({"id": r["id"], "game": g.id, "name": g.name, "icon": g.icon, "host": sibs[r["host_id"]]})
    return out


# ------------------------------------------------------------------ 一局：开局、存档、结束

def start(kid: dict, room_id, n, device: str = "") -> dict:
    """房主开局：人到齐了、都还能玩、这一关解锁了，就开一局（sessions.start），返回房间信息（play 里是这一局）。"""
    r, _ = _on_device(kid, room_id, device)
    _host_only(r, kid, "开始")
    g = get(r["game"])
    info = room_info(kid, r["id"])
    if not info["full"]:
        raise ArenaError("人还没到齐", 409)
    locked = next((m for m in info["members"] if m["locked"]), None)
    if locked:
        raise ArenaError(f"{locked['name']}：{locked['locked']}" if g.multi else locked["locked"], 403)
    level = 0
    if g.levels:
        level = g.level(n)["n"]
        upto = info["progress"]["upto"]
        if level > upto:
            raise ArenaError(f"先打过第 {upto} 关，才能打第 {level} 关", 403)
        db.run("UPDATE game_rooms SET level=? WHERE id=?", level, r["id"])
    sessions.start(r["id"], g.id, level, team_of(r["id"]))
    touch(r["id"])
    return room_info(kid, r["id"])


def _session(r: dict, session_id) -> dict:
    s = sessions.live(r["session_id"])
    if not s or str(s["id"]) != str(session_id):
        raise ArenaError("这一局已经结束了", 410, over=True)
    return s


def save(kid: dict, room_id, device: str, msg: dict) -> dict:
    """玩的时候每 2 秒存一次档（sessions.save）。end：这一局不玩了（只有房主说了算，记成退出）。"""
    r, _ = _on_device(kid, room_id, device)
    s = _session(r, msg.get("session"))
    host = r["host_id"] == kid["id"]
    if msg.get("end"):
        if host:
            sessions.close(s["id"], "quit")
        return {"ok": True}
    sessions.save(s, kid["id"], host, msg)
    db.run("UPDATE room_members SET seen_at=? WHERE room_id=? AND user_id=?", db.now(), r["id"], kid["id"])
    touch(r["id"])
    return {"ok": True}


def finish(kid: dict, room_id, device: str, session_id, result: str, report: dict) -> dict:
    """房主报这一局的结果：记进这一局（sessions.close）和队伍进度（progress.record），算星星、开下一关、发贴纸。"""
    r, _ = _on_device(kid, room_id, device)
    _host_only(r, kid, "报结果")
    s = _session(r, session_id)
    g = get(r["game"])
    result = result if result in ("win", "lose", "draw", "quit") else "quit"
    win, n = result == "win", s["level"]
    lv = g.level(n) if n else {}
    rep = g.clean(lv, report or {})
    if not rep["seconds"]:
        rep["seconds"] = sessions.elapsed(s)
    if win and n and rep["seconds"] < MIN_SECONDS:
        raise ArenaError("用时不对")
    checks = g.checks(lv, rep, sessions.focus(s["id"])) if win else {}
    stars = sum(1 for k, _ in g.stars if checks.get(k))
    out = {"n": n, "result": result, **rep, "stars": stars, "checks": checks, "stickers": {}}
    if n:
        value = rep.get(g.record) if g.record else None
        before, after = progress.record(g, s["team"], n, win, stars, value)
        b, a = before["levels"][n - 1], after["levels"][n - 1]
        unlocked = g.worlds[after["worlds_done"] - 1] if after["worlds_done"] > before["worlds_done"] else None
        nxt = n + 1 if win and n < after["total"] else None
        out.update(value=value, prev_best=b["best"], best=a["best"], best_stars=a["stars"], first_clear=win and not b["cleared"],
                   new_best=win and (a["best"] != b["best"] if g.record else stars > b["stars"]),
                   unlocked=progress.unlock_of(unlocked) if unlocked else None, next=nxt,
                   all_clear=after["cleared"] == after["total"], progress=after)
        if win:
            keys = g.run_stickers(rep, before, after)
            out["stickers"] = {uid: awards.give(int(uid), keys) for uid in s["team"].split(",")} if keys else {}
            db.run("UPDATE game_rooms SET level=? WHERE id=?", nxt or n, r["id"])
    sessions.close(s["id"], result, rep["seconds"], stars, {**rep, "checks": checks})
    return out
