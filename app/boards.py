"""打卡榜和乐园榜（docs/DESIGN.md 5.29）：比坚持，不比水平。

- 学习榜四个：连续打卡天数、最长连续天数、本月打卡天数（每月 1 号重新数）、累计打卡天数。打卡沿用 app/streak.py 的规则
  （做完第 1 节才算；补签卡能保住连续，但补上的那天不加天数）。
- 两个范围：全站、我所在的城市（家长账号上的 city，「省·市」）。一个城市上榜的孩子少于 CITY_MIN 个就不单独开城市榜，
  免得别人猜出是谁。
- 孩子默认在榜上，家长打开隐私模式（auth.is_private）就不上任何榜；榜上只显示昵称（auth.public_name）。
- 乐园榜「本周答对」：这周（周一起）在乐园所有游戏里答对了几道题。每个人的题都按自己的水平出（app/arena/sources.py），
  预期答对的比例大家一样，比的是肯不肯答题，不是会多少；每周一重新数。还有「一共答对」（从开始玩到现在）。
  两个都能只看一款游戏（这款游戏里答对几道）。单款游戏也不比关卡、分数、时间。
  只数答对的，不数答了几道：乱点答错不算，刷不上去。
- 呈现（/mnt/project-files/aistudy/docs/排行榜呈现设计.md）：前 3 名领奖台、前 10 名；每个孩子只和上周同一天的自己比
  （「本周进步」榜按多出来的排，坚持得少的孩子也能拿金牌）；我的名次、比上周前进几名、再多少超过前一位 / 进前 10。
- 不上榜的孩子也能看榜，只是没有自己的名次；家长、管理员也能看（同样只看到昵称）。
- 按分数、正确率、通关数的跨家庭排名不做（比的是年龄和水平，小的孩子永远在后面）。
"""
import time
from datetime import date, timedelta

from . import auth, db, streak
from .arena import awards

METRICS = {"streak": "连续打卡", "best": "最长连续", "month": "本月打卡", "total": "累计打卡", "up": "本周进步",
           "game": "本周答对", "game_all": "一共答对", "game_up": "本周进步"}
UNITS = {"streak": "天", "best": "天", "month": "天", "total": "天", "up": "天", "game": "道", "game_all": "道", "game_up": "道"}
GAME = ("game", "game_all", "game_up")   # 乐园榜：只数答对的（乱点刷不上去）
UP = ("up", "game_up")                   # 进步榜：比上周同一天为止多了多少
TOP = 10        # 总榜只列前 10 名（领奖台 + 榜上之星），不列一条末尾总是同一批孩子的长名单
TOP_GAME = 50   # 单款游戏的榜：先看前 10 名，能展开到 50 名
CITY_MIN = 5
CACHE_S = 60    # 榜单 1 分钟算一次：玩一局回来就能看到名次变了
LIVE_S = 3      # 游戏页上那款游戏的榜几秒一算（live：只数这款游戏答对几道，一条 SQL），页面看着时一直在跳
_cache: dict = {}

# 直辖市、特别行政区：省和市是一个
SINGLE = ("北京", "上海", "天津", "重庆", "香港", "澳门")
PROVINCES = ("北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西",
             "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾", "内蒙古",
             "广西", "西藏", "宁夏", "新疆", "香港", "澳门", "海外")


def _settings(u: dict) -> dict:
    return db.jload(u.get("settings"), {}) or {}


def on_board(kid: dict) -> bool:
    """默认上榜；家长打开隐私模式（auth.is_private）就不上任何榜。家里的成人成员不上孩子的榜。"""
    return auth.with_other_kids(kid)


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


def _best(counted: dict[str, bool], used: set[str], end: date) -> int:
    """最长连续：和连续打卡一样数，补签卡补上的日子不断也不加。"""
    days = sorted(d for d, ok in counted.items() if ok and d <= end.isoformat())
    if not days:
        return 0
    d, best, n = date.fromisoformat(days[0][:10]), 0, 0
    while d <= end:
        k = d.isoformat()
        if counted.get(k):
            n += 1
            best = max(best, n)
        elif k not in used:
            n = 0
        d += timedelta(days=1)
    return best


def _at(counted: dict[str, bool], used: set[str], answers: list, d: date) -> dict:
    """到 d 这天为止的各项数字（d 是今天就是现在的，d 是 7 天前就是「上周这时候」的）。"""
    ds, mon = d.isoformat(), (d - timedelta(days=d.weekday())).isoformat()
    days = [x for x, ok in counted.items() if ok and x <= ds]
    out = {"streak": streak._walk({x: ok for x, ok in counted.items() if x <= ds}, used, d)[0], "best": _best(counted, used, d),
           "month": sum(1 for x in days if x[:7] == ds[:7]), "total": len(days), "week": sum(1 for x in days if x >= mon),
           "game": 0, "game_all": 0}
    for day, game, c in answers:   # 乐园：按游戏分开数，加起来就是所有游戏的
        if day > ds:
            continue
        w = c if day >= mon else 0
        out["game"] += w
        out["game_all"] += c
        if game:
            out[f"game:{game}"] = out.get(f"game:{game}", 0) + w
            out[f"game_all:{game}"] = out.get(f"game_all:{game}", 0) + c
    return out


def _counts(kid_id: int) -> dict:
    """现在的数字，加上上周同一天为止的（prev），和两个进步数（up、game_up）。"""
    counted, used = streak._days(kid_id), streak._used(kid_id)
    answers = [(r["d"], r["game"], int(r["c"] or 0)) for r in db.q(
        "SELECT substr(a.created_at, 1, 10) AS d, m.game, SUM(a.correct) AS c FROM arena_answers a "
        "LEFT JOIN arena_matches m ON m.id=a.match_id WHERE a.user_id=? GROUP BY substr(a.created_at, 1, 10), m.game", kid_id)]
    today = db.today()
    now, prev = _at(counted, used, answers, today), _at(counted, used, answers, today - timedelta(days=7))
    now["up"], now["game_up"] = now["week"] - prev["week"], now["game"] - prev["game"]
    for k in list(now):
        if k.startswith("game:"):
            now[f"game_up:{k[5:]}"] = now[k] - prev.get(k, 0)
    return {**now, "prev": prev}


def _roster() -> list[dict]:
    """上榜的孩子：只显示昵称、角色和城市（缓存 CACHE_S 秒；隐私模式、改昵称时 refresh 马上重来）。"""
    hit = _cache.get("r")
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    out = []
    for u in db.q("SELECT k.*, p.city AS family_city FROM users k JOIN users p ON p.id=k.parent_id "
                  "WHERE k.role='kid' AND k.status='active' ORDER BY k.id"):
        u = dict(u)
        if on_board(u):
            out.append({"id": u["id"], "name": auth.public_name(u), "avatar": awards.avatar(u), "city": u["family_city"] or ""})
    _cache["r"] = (time.time(), out)
    return out


def _entries() -> list[dict]:
    """上榜的孩子和他们的数字（缓存 CACHE_S 秒；日期变了马上重算）。"""
    today = db.today().isoformat()
    hit = _cache.get("v")
    if hit and hit[0] == today and time.time() - hit[1] < CACHE_S:
        return hit[2]
    out = [{**r, **_counts(r["id"])} for r in _roster()]
    _cache["v"] = (today, time.time(), out)
    return out


def refresh() -> None:
    _cache.clear()


def _rank(rows: list[dict], key: str, pick=None) -> list[dict]:
    """名次：数字一样名次一样（1、1、3）。0 的不排。pick：从每行取数字（默认 r[key]）。"""
    val = pick or (lambda r: r.get(key, 0))
    rows = sorted((r for r in rows if val(r) > 0), key=lambda r: (-val(r), r["id"]))
    out, prev, rank = [], None, 0
    for i, r in enumerate(rows, 1):
        if val(r) != prev:
            rank, prev = i, val(r)
        out.append({"id": r["id"], "name": r["name"], "avatar": r.get("avatar"), "value": val(r), "rank": rank})
    return out


def _standing(kid: dict | None, ranked: list[dict], n: int) -> tuple[list[dict], dict | None, int | None]:
    """前 n 名（自己那一行标 me）、我的名次、再多少超过前面一位。"""
    mine = next((r for r in ranked if kid and r["id"] == kid["id"]), None)
    higher = [r["value"] for r in ranked if mine and r["value"] > mine["value"]]
    ahead = min(higher) - mine["value"] + 1 if higher else None
    # 自己那一行用现在的昵称和角色：榜单缓存一分钟，刚改了昵称不能看到上下两个名字
    mine_now = {"name": auth.public_name(kid), "avatar": awards.avatar(kid)} if kid else {}
    top = [{**r, "me": True, **mine_now} if kid and r["id"] == kid["id"] else {**r, "me": False} for r in ranked[:n]]
    return top, mine, ahead


def games() -> list[dict]:
    """乐园榜能单独看的游戏（玩的时候要答题的都算）。"""
    from .arena.games import GAMES
    return [{"id": g.id, "name": g.name, "icon": g.icon} for g in GAMES.values()]


def board(kid: dict | None, metric: str = "streak", scope: str = "all", game: str = "") -> dict:
    """排行榜。scope：all 全站 / city 我的城市；game：乐园榜只看这一款游戏；kid 为 None：家长、管理员来看，没有「我」。
    top：前 TOP 名（单款游戏 TOP_GAME 名）；mine：我的名次；moved：比上周前进了几名；ahead：再多少超过前面一位；
    to_top：再多少进前 10；week：这周和上周同一天为止（学习榜是打卡天数，乐园榜是答对几道）。"""
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
    top, mine, ahead = _standing(kid, ranked, TOP_GAME if game else TOP)
    to_top = moved = None
    if mine:
        if mine["rank"] > TOP:
            to_top = ranked[TOP - 1]["value"] - mine["value"] + 1
        if metric not in UP:   # 名次变化：拿上周同一天为止的数字排一次
            was = next((r["rank"] for r in _rank(rows, key, lambda r: r["prev"].get(key, 0)) if r["id"] == kid["id"]), None)
            moved = (was - mine["rank"]) if was else "new"   # 上周这时候还没上榜：新上榜
    elif kid and on_board(kid) and len(ranked) >= TOP:
        to_top = ranked[TOP - 1]["value"] + 1
    me = _counts(kid["id"]) if kid else {}
    gk = f"game:{game}" if game else "game"
    week = ({"now": me.get(gk, 0), "prev": me["prev"].get(gk, 0)} if metric in GAME else
            {"now": me["week"], "prev": me["prev"]["week"]}) if kid else None
    if week:
        week["left"] = 7 - db.today().weekday()   # 这周还剩几天（连今天）
    return {"metric": metric, "metrics": METRICS, "unit": UNITS[metric], "scope": scope, "city": city_label(city), "city_ok": city_ok,
            "top": top, "mine": mine, "ahead": ahead, "to_top": to_top, "moved": moved, "week": week, "top_n": TOP,
            "on_board": bool(kid) and on_board(kid), "counts": me, "mine_value": me.get(key, 0) if kid else 0,
            "game": game, "games": games(), "is_game": metric in GAME, "is_up": metric in UP,
            "people": len(ranked), "joined": len(_entries()), "kid": bool(kid), "me_name": auth.public_name(kid) if kid else ""}


def live(kid: dict, game: str) -> dict:
    """游戏页上这款游戏的榜：本周答对、一共答对（arena_game.html，页面看着的时候每几秒拉一次，play/gboard.js 让它跳）。
    只数这款游戏里答对几道：一条 SQL，每款游戏缓存 LIVE_S 秒，几个孩子同时看也只算一次；谁上榜、叫什么跟 _roster
    （隐私模式不上、只显示昵称）。和 board() 同样的名次规则，只是不等那 1 分钟的缓存。"""
    today, hit = db.today().isoformat(), _cache.get(("live", game))
    if hit and hit[0] == today and time.time() - hit[1] < LIVE_S:
        rows = hit[2]
    else:
        sums = {r["user_id"]: (int(r["w"] or 0), int(r["t"] or 0)) for r in db.q(
            "SELECT a.user_id, SUM(CASE WHEN substr(a.created_at, 1, 10) >= ? THEN a.correct ELSE 0 END) AS w, SUM(a.correct) AS t "
            "FROM arena_answers a JOIN arena_matches m ON m.id=a.match_id WHERE m.game=? GROUP BY a.user_id", _monday(), game)}
        rows = [{**r, "game": sums.get(r["id"], (0, 0))[0], "game_all": sums.get(r["id"], (0, 0))[1]} for r in _roster()]
        _cache[("live", game)] = (today, time.time(), rows)
    out = {}
    for metric in ("game", "game_all"):
        top, mine, ahead = _standing(kid, _rank(rows, metric), TOP_GAME)
        out[metric] = {"metric": metric, "top": top, "mine": mine, "ahead": ahead, "on_board": on_board(kid), "joined": len(rows)}
    return out


def nudge(kid: dict, park: bool = False) -> str:
    """今天页（学习榜）、乐园首页（乐园榜）上的一句话：说离下一个目标最近的那件事。隐私模式不说。"""
    if not on_board(kid):
        return ""
    b = board(kid, "game" if park else "streak")
    w, unit = b["week"], ("道" if park else "天")
    verb = "答对" if park else "坚持"
    name = "乐园榜" if park else "学习榜"
    m = b["mine"]
    if m and m["rank"] <= 3:
        return f"{name}第 {m['rank']} 名 {['🥇', '🥈', '🥉'][m['rank'] - 1]}"
    if b["to_top"] and b["to_top"] <= (3 if not park else 20):
        return f"再{verb} {b['to_top']} {unit}，进{name}前 {TOP}"
    if w and w["now"] > w["prev"]:
        return f"这周比上周多{'答对' if park else '打卡'} {w['now'] - w['prev']} {unit} 👍"
    if m and isinstance(b["moved"], int) and b["moved"] > 0:
        return f"{name}第 {m['rank']} 名，比上周前进 {b['moved']} 名"
    if m and b["ahead"]:
        return f"再{verb} {b['ahead']} {unit}，{name}前进一名"
    return f"{name}：看看你排第几"
