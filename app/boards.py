"""打卡榜和乐园榜（docs/DESIGN.md 5.29）：比坚持，不比水平。

- 学习榜四个：连续打卡天数、最长连续天数、本月打卡天数（每月 1 号重新数）、累计打卡天数。打卡沿用 app/streak.py 的规则
  （做完第 1 节才算；补签卡能保住连续，但补上的那天不加天数）。
- 两个范围：全站、我所在的城市（家长账号上的 city，「省·市」）。一个城市上榜的孩子少于 CITY_MIN 个就不单独开城市榜，
  免得别人猜出是谁。
- 只有家长打开了「上排行榜」的孩子才在榜上（users.settings.board，默认不上），榜上只显示昵称（auth.public_name）。
- 乐园榜「本周答对」：这周（周一起）在乐园所有游戏里答对了几道题。每个人的题都按自己的水平出（app/arena/sources.py），
  预期答对的比例大家一样，比的是肯不肯答题，不是会多少；每周一重新数。还有「一共答对」（从开始玩到现在）。
  两个都能只看一款游戏（这款游戏里答对几道）。单款游戏也不比关卡、分数、时间。
  只数答对的，不数答了几道：乱点答错不算，刷不上去。
- 不上榜的孩子也能看榜，只是没有自己的名次；家长、管理员也能看（同样只看到昵称）。
- 按分数、正确率、通关数的跨家庭排名不做（比的是年龄和水平，小的孩子永远在后面）。
"""
import time
from datetime import date, timedelta

from . import auth, db, streak

METRICS = {"streak": "连续打卡", "best": "最长连续", "month": "本月打卡", "total": "累计打卡", "game": "本周答对", "game_all": "一共答对"}
UNITS = {"streak": "天", "best": "天", "month": "天", "total": "天", "game": "道", "game_all": "道"}
GAME = ("game", "game_all")   # 乐园榜：只数答对的（乱点刷不上去）
TOP = 10
CITY_MIN = 5
CACHE_S = 600   # 榜单 10 分钟算一次（每个孩子的打卡天数要从每天的记录里数）
_cache: dict = {}

# 直辖市、特别行政区：省和市是一个
SINGLE = ("北京", "上海", "天津", "重庆", "香港", "澳门")
PROVINCES = ("北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西",
             "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾", "内蒙古",
             "广西", "西藏", "宁夏", "新疆", "香港", "澳门", "海外")


def _settings(u: dict) -> dict:
    return db.jload(u.get("settings"), {}) or {}


def on_board(kid: dict) -> bool:
    return bool(_settings(kid).get("board"))


def set_on_board(kid_id: int, on: bool) -> None:
    u = db.one("SELECT settings FROM users WHERE id=?", kid_id)
    st = db.jload(u["settings"], {}) if u else {}
    st["board"] = bool(on)
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), kid_id)


def norm_city(province: str, city: str) -> str:
    """家长填的省和市 → 「省·市」。直辖市、香港、澳门、海外只有省；市去掉结尾的「市」，写法统一了才能分到一个榜。"""
    p = (province or "").strip()
    if p not in PROVINCES:
        return ""
    if p in SINGLE or p == "海外":
        return p
    c = "".join((city or "").split())[:12]
    for tail in ("市", "地区"):
        if c.endswith(tail) and len(c) > len(tail) + 1:
            c = c[:-len(tail)]
    return f"{p}·{c}" if c else p


def city_of(kid: dict) -> str:
    """孩子所在城市：跟着家长。"""
    p = db.one("SELECT city FROM users WHERE id=?", kid.get("parent_id")) if kid.get("parent_id") else None
    return (p["city"] or "") if p else ""


def set_city(parent_id: int, province: str, city: str) -> str:
    c = norm_city(province, city)
    db.run("UPDATE users SET city=? WHERE id=?", c, parent_id)
    return c


def city_label(c: str) -> str:
    return c.split("·")[-1] if c else ""


def _monday() -> str:
    t = db.today()
    return (t - timedelta(days=t.weekday())).isoformat()


def _best(counted: dict[str, bool], used: set[str]) -> int:
    """最长连续：和连续打卡一样数，补签卡补上的日子不断也不加。"""
    days = sorted(d for d, ok in counted.items() if ok)
    if not days:
        return 0
    d, end = date.fromisoformat(days[0][:10]), db.today()
    best = n = 0
    while d <= end:
        k = d.isoformat()
        if counted.get(k):
            n += 1
            best = max(best, n)
        elif k not in used:
            n = 0
        d += timedelta(days=1)
    return best


def _counts(kid_id: int, today: str, month: str) -> dict:
    counted = streak._days(kid_id)
    days = [d for d, ok in counted.items() if ok]
    monday = _monday()
    out = {"streak": streak.streak(kid_id), "best": _best(counted, streak._used(kid_id)),
           "month": sum(1 for d in days if d.startswith(month) and d <= today), "total": len(days), "game": 0, "game_all": 0}
    # 乐园：按游戏分开数，加起来就是所有游戏的
    for r in db.q("SELECT m.game, COALESCE(SUM(CASE WHEN a.created_at>=? THEN a.correct ELSE 0 END), 0) AS w, "
                  "COALESCE(SUM(a.correct), 0) AS a FROM arena_answers a LEFT JOIN arena_matches m ON m.id=a.match_id "
                  "WHERE a.user_id=? GROUP BY m.game", monday, kid_id):
        w, a = int(r["w"] or 0), int(r["a"] or 0)
        out["game"] += w
        out["game_all"] += a
        if r["game"]:
            out[f"game:{r['game']}"], out[f"game_all:{r['game']}"] = w, a
    return out


def _entries() -> list[dict]:
    """上榜的孩子和他们的天数（缓存 CACHE_S 秒；日期变了马上重算）。"""
    today = db.today().isoformat()
    hit = _cache.get("v")
    if hit and hit[0] == today and time.time() - hit[1] < CACHE_S:
        return hit[2]
    out = []
    for u in db.q("SELECT k.*, p.city AS family_city FROM users k JOIN users p ON p.id=k.parent_id "
                  "WHERE k.role='kid' AND k.status='active' ORDER BY k.id"):
        u = dict(u)
        if not on_board(u):
            continue
        out.append({"id": u["id"], "name": auth.public_name(u), "city": u["family_city"] or "",
                    **_counts(u["id"], today, today[:7])})
    _cache["v"] = (today, time.time(), out)
    return out


def refresh() -> None:
    _cache.clear()


def _rank(rows: list[dict], key: str) -> list[dict]:
    """名次：天数一样名次一样（1、1、3）。0 天的不排。"""
    rows = sorted((r for r in rows if r.get(key, 0) > 0), key=lambda r: (-r[key], r["id"]))
    out, prev, rank = [], None, 0
    for i, r in enumerate(rows, 1):
        if r[key] != prev:
            rank, prev = i, r[key]
        out.append({"id": r["id"], "name": r["name"], "value": r[key], "rank": rank})
    return out


def games() -> list[dict]:
    """乐园榜能单独看的游戏（玩的时候要答题的都算）。"""
    from .arena.games import GAMES
    return [{"id": g.id, "name": g.name, "icon": g.icon} for g in GAMES.values()]


def board(kid: dict | None, metric: str = "streak", scope: str = "all", game: str = "") -> dict:
    """排行榜：前 TOP 名、我的名次、离前一名还差多少。scope：all 全站 / city 我的城市。
    game：乐园榜只看这一款游戏。kid 为 None：家长、管理员来看，没有「我」。"""
    metric = metric if metric in METRICS else "streak"
    gids = {g["id"] for g in games()}
    game = game if metric in GAME and game in gids else ""
    key = f"{metric}:{game}" if game else metric
    rows = _entries()
    city = city_of(kid) if kid else ""
    in_city = [r for r in rows if city and r["city"] == city]
    city_ok = len(in_city) >= CITY_MIN
    if scope == "city" and city_ok:
        rows = in_city
    else:
        scope = "all"
    ranked = _rank(rows, key)
    mine = next((r for r in ranked if kid and r["id"] == kid["id"]), None)
    ahead = None
    if mine:
        higher = [r["value"] for r in ranked if r["value"] > mine["value"]]
        ahead = min(higher) - mine["value"] + 1 if higher else None
    me_counts = _counts(kid["id"], db.today().isoformat(), db.today().isoformat()[:7]) if kid else {}
    top = [{**r, "me": bool(kid) and r["id"] == kid["id"]} for r in ranked[:TOP]]
    return {"metric": metric, "metrics": METRICS, "unit": UNITS[metric], "scope": scope, "city": city_label(city), "city_ok": city_ok,
            "top": top, "mine": mine, "ahead": ahead, "on_board": bool(kid) and on_board(kid), "counts": me_counts,
            "mine_value": me_counts.get(key, 0) if kid else 0, "game": game, "games": games(), "is_game": metric in GAME,
            "people": len(ranked), "joined": len(_entries()), "kid": bool(kid)}
