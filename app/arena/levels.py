"""闯关：一张地图，5 个世界 × 6 关，每个世界的第 6 关是大怪兽。

越往后越难的是游戏本身：电脑血更厚、更快、更凶、会格挡，大怪兽带护盾（要答对几题才能破盾）。
题目难度不跟关卡涨，一直按孩子自己的水平出（matches.GAMES 的目标答对率），所以公平还在。
- 过关才开下一关，进度一直保存（arena_levels）。
- 每关最多 3 颗星：过关 1 颗、专注指数 ≥ 90 1 颗、血量剩一半以上 1 颗。没拿满可以回去再打。
- 每打通一个世界解锁一样新东西（WORLDS 的 unlock），地图上能看到下一样是什么。
- 关卡参数只在这里算，开局时发给前端；赢没赢由前端报，星星和解锁在服务器算。
现在只有火柴人大战有关卡（LEVELED）。详见 docs/DESIGN.md 5.15。
"""
from .. import db

PER_WORLD = 6
STAR_FOCUS = 90          # 第 2 颗星：专注指数
STAR_HP = 50             # 第 3 颗星：剩下的血量

WORLDS = {
    "stickman": [
        # (名字, 场景, 打通后解锁 (id, 图标, 名字, 说明))
        ("草地擂台", "day", ("shield", "🛡️", "护盾", "装备店里可以买护盾了")),
        ("黄昏山谷", "sunset", ("sword", "🗡️", "剑", "装备店里可以买剑了")),
        ("糖果城堡", "candy", ("laser", "🔫", "激光", "装备店里可以买激光了")),
        ("星空屋顶", "night", ("super", "💥", "超级必杀", "必杀伤害更高、打得更远")),
        ("火山之巅", "lava", ("belt", "🥋", "大师腰带", "打通全部 30 关")),
    ],
}
LEVELED = tuple(WORLDS)
SHOP_START = ("stick",)      # 一开始就能买的装备


def count(game: str) -> int:
    return len(WORLDS[game]) * PER_WORLD


def _stickman(n: int, cleared_worlds: int) -> dict:
    """第 n 关（1 起）的参数。t 从 0 到 1：第 1 关最弱，第 30 关最强。"""
    w, k = divmod(n - 1, PER_WORLD)
    t = (n - 1) / (count("stickman") - 1)
    boss = k == PER_WORLD - 1
    weapon = ["fist", "stick", "stick", "sword", "sword"][w]
    p = {
        "ai_hp": round((70 + 110 * t) * (1.4 if boss else 1)),
        "ai_dmg": round(0.6 + 0.65 * t, 2),          # 电脑打人的伤害倍数
        "ai_speed": round(0.65 + 0.45 * t, 2),       # 走路速度
        "ai_aggr": round(0.35 + 0.4 * t, 2),         # 多想出手
        "ai_dodge": round(0.4 + 2.6 * t, 2),         # 看到你出拳时，每秒跳开的机会
        "ai_guard": round(0.3 * t, 2),               # 挨打时格挡（伤害减到 4 成）的机会
        "ai_think": round(6 - 2.5 * t, 1),           # 电脑答题要几秒
        "ai_weapon": "laser" if boss and w == 4 else weapon,
        "ai_shield": 40 if (boss and w >= 2) or w >= 3 else 0,
        "start_energy": 260 if n == 1 else 200 if n == 2 else 120,
        "regen": 6 if n <= 2 else 0,                 # 前两关能量会慢慢回一点（到 100 为止），让孩子先学会玩
        "seconds": 180 if boss else 150,
        "boss": {"pips": 3 + w // 2, "window": 8} if boss else None,
        "shop": list(SHOP_START) + [WORLDS["stickman"][i][2][0] for i in range(min(cleared_worlds, 3))],
        "super": cleared_worlds >= 4,
    }
    return p


def stage(game: str, n: int, cleared_worlds: int = 0) -> dict:
    w, k = divmod(n - 1, PER_WORLD)
    name, scene, unlock = WORLDS[game][w]
    out = {"n": n, "world": w + 1, "world_name": name, "k": k + 1, "boss": k == PER_WORLD - 1, "scene": scene,
           "unlock": {"id": unlock[0], "icon": unlock[1], "name": unlock[2], "how": unlock[3]}, "last": n == count(game)}
    if game == "stickman":
        out["params"] = _stickman(n, cleared_worlds)
    return out


def _rows(kid_id: int, game: str) -> dict:
    return {r["level"]: dict(r) for r in db.q("SELECT * FROM arena_levels WHERE user_id=? AND game=?", kid_id, game)}


def _cleared(rows: dict) -> set:
    return {n for n, r in rows.items() if r["cleared_at"]}


def _worlds_done(cleared: set, game: str) -> int:
    """从第 1 个世界起，连续打通了几个（大怪兽那关过了就算打通）。"""
    n = 0
    while n < len(WORLDS[game]) and (n + 1) * PER_WORLD in cleared:
        n += 1
    return n


def open_upto(cleared: set, game: str) -> int:
    """能打到第几关：打过的最高一关的下一关。"""
    return min(count(game), max(cleared, default=0) + 1)


def progress(kid_id: int, game: str) -> dict:
    rows = _rows(kid_id, game)
    cleared = _cleared(rows)
    upto = open_upto(cleared, game)
    done = _worlds_done(cleared, game)
    worlds = []
    for w, (name, scene, unlock) in enumerate(WORLDS[game]):
        levels = []
        for k in range(PER_WORLD):
            n = w * PER_WORLD + k + 1
            r = rows.get(n) or {}
            levels.append({"n": n, "stars": r.get("stars", 0), "open": n <= upto, "cleared": n in cleared,
                           "boss": k == PER_WORLD - 1})
        worlds.append({"n": w + 1, "name": name, "scene": scene, "levels": levels, "done": w < done,
                       "unlock": {"id": unlock[0], "icon": unlock[1], "name": unlock[2], "how": unlock[3]},
                       "stars": sum(lv["stars"] for lv in levels)})
    return {"game": game, "upto": upto, "worlds": worlds, "worlds_done": done, "total": count(game),
            "cleared": len(cleared), "stars": sum(r["stars"] for r in rows.values()), "max_stars": 3 * count(game)}


def check_start(kid_id: int, game: str, n) -> dict:
    """开局：选的关能不能打。没选就打能打的最高一关。返回这一关的参数。"""
    cleared = _cleared(_rows(kid_id, game))
    upto = open_upto(cleared, game)
    try:
        n = int(n) if n not in (None, "") else upto
    except (TypeError, ValueError):
        n = upto
    if n < 1 or n > count(game):
        n = upto
    if n > upto:
        from .matches import ArenaError
        raise ArenaError(f"先打过第 {upto} 关，才能打第 {n} 关", 403)
    return stage(game, n, _worlds_done(cleared, game))


def checks(result: str, focus_index, hp_left) -> dict:
    """三颗星各自达成没有。后两颗要先过关。"""
    win = result == "win"
    return {"win": win, "focus": win and focus_index is not None and focus_index >= STAR_FOCUS,
            "hp": win and (hp_left or 0) >= STAR_HP}


def stars_of(result: str, focus_index, hp_left) -> int:
    return sum(checks(result, focus_index, hp_left).values())


def finish(kid_id: int, game: str, n: int, result: str, focus_index, hp_left) -> dict:
    """一局结束：记星星、过关，返回给战报用的结果（这次几颗星、是不是新纪录、解锁了什么、下一关）。"""
    rows = _rows(kid_id, game)
    before = _cleared(rows)
    worlds_before = _worlds_done(before, game)
    r = rows.get(n)
    stars = stars_of(result, focus_index, hp_left)
    best = r["stars"] if r else 0
    win = result == "win"
    now = db.now()
    if r:
        db.run("UPDATE arena_levels SET stars=?, plays=plays+1, wins=wins+?, cleared_at=COALESCE(cleared_at, ?), updated_at=? "
               "WHERE user_id=? AND game=? AND level=?", max(best, stars), 1 if win else 0, now if win else None, now,
               kid_id, game, n)
    else:
        db.run("INSERT INTO arena_levels(user_id, game, level, stars, plays, wins, cleared_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
               kid_id, game, n, stars, 1, 1 if win else 0, now if win else None, now)
    after = before | ({n} if win else set())
    worlds_after = _worlds_done(after, game)
    unlocked = None
    if worlds_after > worlds_before:
        u = WORLDS[game][worlds_after - 1][2]
        unlocked = {"id": u[0], "icon": u[1], "name": u[2], "how": u[3]}
    upto = open_upto(after, game)
    total = sum(max(x["stars"], stars if x["level"] == n else 0) for x in rows.values()) + (0 if r else stars)
    return {"n": n, "stars": stars, "checks": checks(result, focus_index, hp_left), "best": max(best, stars), "new_best": stars > best, "first_clear": win and n not in before,
            "unlocked": unlocked, "next": n + 1 if win and n < count(game) and n + 1 <= upto else None,
            "total_stars": total, "cleared": len(after), "worlds_done": worlds_after, "all_clear": len(after) >= count(game)}


def sticker_keys(game: str, res: dict) -> list[str]:
    if game != "stickman":
        return []
    keys = []
    if res["worlds_done"] >= 1:
        keys.append("sm_w1")
    if res["total_stars"] >= 30:
        keys.append("sm_stars")
    if res["all_clear"]:
        keys.append("sm_master")
    return keys
