"""账号档案：每支队伍在每款游戏里打到哪了（所有游戏共用一张表 game_runs）。

队伍 = 一起玩的孩子（id 排好序连起来，team_key）：自己玩是一个人，姐弟一起是两个人，全家一起是几个人。
谁当什么角色都算同一队。每关记最好几颗星、纪录（用时或分数，看游戏的 record）、玩了几次、什么时候第一次过关。
- 过关才开下一关（打过的最高一关的下一关）。
- 世界：一个世界的最后一关过了算打通（要从第 1 个世界起连续打通），打通了解锁这个世界登记的东西。
比的是自己（或「我们」）和上次的自己，不和别人排名（docs/DESIGN.md 5.12 的原则）。
几个人第一次凑成一队时，队里打得最远的人打到的关都开放（reach）。
"""
from .. import db
from .games import Game


def team_key(ids) -> str:
    return ",".join(str(i) for i in sorted({int(i) for i in ids}))


def _rows(game: Game, team: str) -> dict:
    return {r["level"]: dict(r) for r in db.q("SELECT * FROM game_runs WHERE game=? AND team=?", game.id, team)}


def unlock_of(w: tuple):
    return {"id": w[2][0], "icon": w[2][1], "name": w[2][2], "how": w[2][3]} if len(w) > 2 else None


def reach(game: Game, kid_id: int) -> int:
    """这个孩子和任何人一起（或者自己）打过的最高一关。"""
    r = db.one("SELECT MAX(level) AS n FROM game_runs WHERE game=? AND cleared_at IS NOT NULL AND (',' || team || ',') LIKE ?",
               game.id, f"%,{int(kid_id)},%")
    return int(r["n"] or 0) if r else 0


def progress(game: Game, team: str) -> dict:
    """这支队伍的进度。开到哪一关：这队自己打过的，和队里打得最远的那个人打到的（和谁一起打的都算）取大的，再往后一关。
    所以几个人第一次凑成一队（比如公开房间里遇到的）不用从第 1 关打起，打得远的人带着大家往下打；
    每个人在哪个房间过的关都记在自己身上（reach），每天来都接着往下玩。"""
    rows = _rows(game, team)
    total = len(game.levels)
    cleared = {n for n, r in rows.items() if r["cleared_at"] and n <= total}
    ids = [x for x in team.split(",") if x]
    shared = max(reach(game, int(i)) for i in ids) if ids else 0
    upto = min(total, max(max(cleared, default=0), shared) + 1)
    levels = [{"n": lv["n"], "name": lv["name"], "boss": bool(lv.get("boss")), "world": game.world_of(lv["n"])["n"],
               "stars": (rows.get(lv["n"]) or {}).get("stars") or 0, "best": (rows.get(lv["n"]) or {}).get("best"),
               "open": lv["n"] <= upto, "cleared": lv["n"] in cleared} for lv in game.levels]
    done, worlds = 0, []
    for i, w in enumerate(game.worlds[:-(-total // game.per_world)]):   # 有关卡的世界
        last = min(total, (i + 1) * game.per_world)
        if done == i and last in cleared:
            done += 1
        ls = [lv for lv in levels if lv["world"] == i + 1]
        worlds.append({"n": i + 1, "name": w[0], "scene": w[1], "unlock": unlock_of(w), "done": i < done,
                       "stars": sum(lv["stars"] for lv in ls), "max_stars": 3 * len(ls)})
    return {"game": game.id, "levels": levels, "worlds": worlds, "upto": upto, "worlds_done": done, "total": total,
            "cleared": len(cleared), "stars": sum(lv["stars"] for lv in levels), "max_stars": 3 * total}


def record(game: Game, team: str, n: int, win: bool, stars: int, value) -> tuple[dict, dict]:
    """记一次打完这一关（过关或没过），返回记之前和记之后的进度。value：这次的成绩（用时或分数，只记过关的）。"""
    before = progress(game, team)
    r = db.one("SELECT * FROM game_runs WHERE game=? AND team=? AND level=?", game.id, team, n)
    now = db.now()
    best = r["best"] if r else None
    if win and value is not None and game.record:
        better = best is None or (value < best if game.record == "seconds" else value > best)
        best = value if better else best
    if r:
        db.run("UPDATE game_runs SET stars=?, best=?, plays=plays+1, wins=wins+?, cleared_at=COALESCE(cleared_at, ?), updated_at=? "
               "WHERE game=? AND team=? AND level=?", max(r["stars"], stars), best, 1 if win else 0, now if win else None, now,
               game.id, team, n)
    else:
        db.run("INSERT INTO game_runs(game, team, level, stars, best, plays, wins, cleared_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
               game.id, team, n, stars, best, 1, 1 if win else 0, now if win else None, now)
    return before, progress(game, team)
