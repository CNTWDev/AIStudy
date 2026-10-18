"""打卡榜和乐园榜（docs/DESIGN.md 5.29）：比坚持，不比水平。

- 三个榜：连续打卡天数、本月打卡天数（每月 1 号重新数）、累计打卡天数。打卡沿用 app/streak.py 的规则
  （做完第 1 节才算；补签卡能保住连续，但补上的那天不加天数）。
- 两个范围：全站、我所在的城市（家长账号上的 city，「省·市」）。一个城市上榜的孩子少于 CITY_MIN 个就不单独开城市榜，
  免得别人猜出是谁。
- 只有家长打开了「上排行榜」的孩子才在榜上（users.settings.board，默认不上），榜上只显示昵称（auth.public_name）。
- 乐园榜「本周答对」：这周（周一起）在乐园所有游戏里答对了几道题。每个人的题都按自己的水平出（app/arena/sources.py），
  预期答对的比例大家一样，比的是肯不肯答题，不是会多少；每周一重新数。
- 不上榜的孩子也能看榜，只是没有自己的名次。
- 按分数、正确率、通关数的跨家庭排名不做（比的是年龄和水平，小的孩子永远在后面）。
"""
import time
from datetime import timedelta

from . import auth, db, streak

METRICS = {"streak": "连续打卡", "month": "本月打卡", "total": "累计打卡", "game": "本周答对"}
UNITS = {"streak": "天", "month": "天", "total": "天", "game": "道"}
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


def _counts(kid_id: int, today: str, month: str) -> dict:
    counted = streak._days(kid_id)
    days = [d for d, ok in counted.items() if ok]
    game = db.one("SELECT COALESCE(SUM(correct), 0) AS c FROM arena_answers WHERE user_id=? AND created_at>=?", kid_id, _monday())
    return {"streak": streak.streak(kid_id), "month": sum(1 for d in days if d.startswith(month) and d <= today),
            "total": len(days), "game": int(game["c"] or 0)}


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


def _rank(rows: list[dict], metric: str) -> list[dict]:
    """名次：天数一样名次一样（1、1、3）。0 天的不排。"""
    rows = sorted((r for r in rows if r[metric] > 0), key=lambda r: (-r[metric], r["id"]))
    out, prev, rank = [], None, 0
    for i, r in enumerate(rows, 1):
        if r[metric] != prev:
            rank, prev = i, r[metric]
        out.append({"id": r["id"], "name": r["name"], "value": r[metric], "rank": rank})
    return out


def board(kid: dict, metric: str = "streak", scope: str = "all") -> dict:
    """孩子看的打卡榜：前 TOP 名、我的名次、离前一名还差几天。scope：all 全站 / city 我的城市。"""
    metric = metric if metric in METRICS else "streak"
    rows = _entries()
    city = city_of(kid)
    in_city = [r for r in rows if city and r["city"] == city]
    city_ok = len(in_city) >= CITY_MIN
    if scope == "city" and city_ok:
        rows = in_city
    else:
        scope = "all"
    ranked = _rank(rows, metric)
    mine = next((r for r in ranked if r["id"] == kid["id"]), None)
    ahead = None
    if mine:
        higher = [r["value"] for r in ranked if r["value"] > mine["value"]]
        ahead = min(higher) - mine["value"] + 1 if higher else None
    me_counts = _counts(kid["id"], db.today().isoformat(), db.today().isoformat()[:7])
    top = [{**r, "me": r["id"] == kid["id"]} for r in ranked[:TOP]]
    return {"metric": metric, "metrics": METRICS, "unit": UNITS[metric], "scope": scope, "city": city_label(city), "city_ok": city_ok,
            "top": top, "mine": mine, "ahead": ahead, "on_board": on_board(kid), "counts": me_counts,
            "people": len(ranked)}
