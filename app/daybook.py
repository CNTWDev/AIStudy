"""我的日记：孩子自己的本子，每天点个心情、写几句或说一段，以后翻回来看（docs/DESIGN.md §5.57，讨论稿 docs/孩子的小日记-讨论稿.md）。

和学习无关，只练「每天记一点」这个习惯：点一个心情就算今天记了，写多写少都是同一个「记了」。
不算进今天清单的分母，不影响「全部做完」、连续天数和保底；不催、不提醒。

隐私（跟孩子说的话必须是真的）：
- 只有本人能打开。家长页、学习报告、周报、管理后台都没有能看内容的地方，家长只看到「这周记了几天」（marked）。
- 孩子自己点「给爸爸妈妈看这篇」，家长才能看这一天；随时能收回。
- 小助手（AI）不读：日记页不出现「问小助手」和划词查词，内容不发给任何大模型，学习计时也不算这里。
- 内容加密存放（Fernet），钥匙在服务器 DATA_DIR/secrets/daybook.key（或环境变量 DAYBOOK_KEY），不进仓库；直接翻表只看得到哪天记了。
- 不自动识别「危险内容」去通知谁。页面最下面一直有一行求助热线；心情连续几天很低时只对孩子本人多说一句，并提醒可以分享给家长。

地点只到城市：默认用家里的城市（家长账号上的，排行榜也用它），不弹定位授权；孩子点「我今天不在家」才定位一次，
服务器把坐标换成城市名，坐标不存。天气按「城市 + 日期」查一次（Open-Meteo），同城共用。

激励牛在稀有、专属：记一天和做完一项任务一样得一颗 ⭐（engine.total_stars）；去过的城市、遇到的天气变成本子上的邮戳；
累计（不是连续）记满 7 / 30 / 100 / 365 天解锁新封面。都从日记本身算出来，不另存（存了就是明文）。
"""
import json
import os
import re
import secrets
import time
from datetime import date, timedelta

from fastapi import HTTPException

from . import config, db

MOODS = [  # (存的值, 表情, 孩子看到的字)；顺序就是从高兴到难过
    ("great", "😄", "开心"), ("good", "🙂", "还不错"), ("meh", "😐", "一般"), ("low", "😟", "不太开心"), ("sad", "😢", "难过")]
MOOD = {k: {"id": k, "emoji": e, "name": n} for k, e, n in MOODS}
LOW = {"low", "sad"}
TEXT_MAX = 2000          # 一段最多几个字
NOTES_MAX = 30           # 一天最多几段
VOICE_MAX = 3 * 1024 * 1024
VOICE_SECS = 90
VOICE_TYPES = {"audio/webm": "webm", "audio/mp4": "m4a", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/aac": "aac", "audio/wav": "wav"}
HOTLINE = "心里特别难受的时候，可以找爸爸妈妈、老师，或者打 12355（青少年服务热线）。"

# 封面：累计记了几天解锁（第一天就有第一本）
COVERS = [
    {"id": "sprout", "days": 1, "icon": "🌱", "name": "小芽本"},
    {"id": "rainbow", "days": 7, "icon": "🌈", "name": "彩虹本"},
    {"id": "stars", "days": 30, "icon": "🌌", "name": "星空本"},
    {"id": "rocket", "days": 100, "icon": "🚀", "name": "火箭本"},
    {"id": "crown", "days": 365, "icon": "👑", "name": "皇冠本"},
]

# 天气：WMO 天气代码 → 一种邮戳（kind）、一个词、一个图标
SKY_KINDS = {"sun": ("☀️", "晴天"), "cloud": ("⛅", "多云"), "fog": ("🌫️", "雾天"), "rain": ("🌧️", "雨天"),
             "snow": ("❄️", "雪天"), "storm": ("⛈️", "雷雨天")}


def _sky_kind(code: int) -> tuple[str, str]:
    if code == 0:
        return "sun", "晴"
    if code in (1, 2):
        return "cloud", "多云"
    if code == 3:
        return "cloud", "阴"
    if code in (45, 48):
        return "fog", "雾"
    if 51 <= code <= 57:
        return "rain", "小雨"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "rain", "雨" if code not in (61, 80) else "小雨"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow", "雪"
    if code >= 95:
        return "storm", "雷雨"
    return "cloud", "多云"


# 写不出来时的一个小问题（固定的，不用 AI；每天换一个，可以不理）
PROMPTS_PRIMARY = [
    "今天最好笑的一件事是什么？", "今天吃到什么好吃的了？", "今天和谁玩了？玩的什么？", "今天学会了一件什么新本事？",
    "今天有没有一件让你有点生气的事？", "如果今天能重来一次，你想改哪一件事？", "今天看到了什么好看的东西？",
    "今天你帮了谁，或者谁帮了你？", "今天最想对谁说一句话？", "今天天气怎么样？你喜欢吗？", "今天有没有一个小小的愿望？",
    "今天最累的时候是什么时候？", "今天听到了什么有意思的话？", "给今天打个比方：今天像一个什么？",
]
PROMPTS_TEEN = [
    "今天印象最深的一个瞬间？", "今天有没有一件事让你改变了想法？", "最近在想的一个问题。", "今天做得不错的一件事，哪怕很小。",
    "今天有点烦的事，写下来就放下了。", "最近喜欢的一首歌、一本书或一部剧？", "今天学到的一个没想到的东西。",
    "现在最想去的一个地方？为什么？", "今天和朋友聊了什么？", "如果可以给一年后的自己留一句话。", "今天最感谢的一个人或一件事。",
    "这周最想做成的一件事。", "今天哪件事花的时间比你想的多？", "一句今天想记住的话。",
]


# ------------------------------------------------------------------ 加密：内容只以密文落库

_fernet_obj = None


def _key_file():
    return config.DATA_DIR / "secrets" / "daybook.key"


def _fernet():
    """钥匙优先用环境变量 DAYBOOK_KEY；没有就用 DATA_DIR/secrets/daybook.key，第一次用时生成（只有服务器进程能读）。
    钥匙丢了日记就打不开了：备份 DATA_DIR 时一起备份。"""
    global _fernet_obj
    if _fernet_obj is None:
        from cryptography.fernet import Fernet
        key = os.environ.get("DAYBOOK_KEY", "").strip()
        if not key:
            f = _key_file()
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as out:
                    out.write(Fernet.generate_key().decode())
            key = f.read_text().strip()
        _fernet_obj = Fernet(key.encode())
    return _fernet_obj


def _seal(obj: dict) -> str:
    return _fernet().encrypt(json.dumps(obj, ensure_ascii=False).encode()).decode()


def _open(token: str) -> dict:
    if not token:
        return {}
    return json.loads(_fernet().decrypt(token.encode()).decode())


# ------------------------------------------------------------------ 读写一天

def _row(user_id: int, day: str):
    return db.one("SELECT * FROM daybook WHERE user_id=? AND day=?", user_id, day)


def _load(user_id: int, day: str) -> dict:
    r = _row(user_id, day)
    return _open(r["data"]) if r else {}


def _has(d: dict) -> bool:
    return bool(d.get("mood") or d.get("notes"))


def _save(user_id: int, day: str, d: dict) -> None:
    now = db.now()
    marked = 1 if _has(d) else 0
    db.run("INSERT INTO daybook(user_id,day,data,marked,shared,created_at,updated_at) VALUES(?,?,?,?,0,?,?) "
           "ON CONFLICT(user_id,day) DO UPDATE SET data=excluded.data, marked=excluded.marked, updated_at=excluded.updated_at",
           user_id, day, _seal(d), marked, now, now)
    if not marked:  # 什么都没有了（心情清掉、段落删光）：这一天不算记了，也不再分享
        db.run("UPDATE daybook SET shared=0 WHERE user_id=? AND day=?", user_id, day)


def _today() -> str:
    return db.today().isoformat()


def _view(user_id: int, day: str, d: dict, shared: bool = False) -> dict:
    notes = [{**n, "time": n.get("at", "")[11:16]} for n in d.get("notes", [])]
    return {"day": day, "mood": MOOD.get(d.get("mood", "")), "notes": notes, "city": d.get("city", ""),
            "sky": d.get("sky"), "shared": shared, "today": day == _today(), "label": day_label(day)}


def day_label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.year} 年 {d.month} 月 {d.day} 日 · 星期{'一二三四五六日'[d.weekday()]}"


def page(user_id: int, day: str) -> dict | None:
    r = _row(user_id, day)
    if not r:
        return None
    return _view(user_id, day, _open(r["data"]), bool(r["shared"]))


def _before(user_id: int) -> tuple[set, set, int]:
    """现在已经有的邮戳（天气种类、城市）和记了几天：存一笔之前先看一眼，存完再比，就知道新得了什么。"""
    skies, cities, n = set(), set(), 0
    for r in db.q("SELECT data FROM daybook WHERE user_id=? AND marked=1", user_id):
        d = _open(r["data"])
        n += 1
        if d.get("sky"):
            skies.add(d["sky"]["kind"])
        if d.get("city"):
            cities.add(d["city"])
    return skies, cities, n


def _changed(user_id: int, was: tuple[set, set, int]) -> dict:
    now = _before(user_id)
    new = {"stamps": [], "cover": None, "first_today": False}
    for k in sorted(now[0] - was[0]):
        new["stamps"].append({"kind": "sky", "id": k, "icon": SKY_KINDS[k][0], "name": f"{SKY_KINDS[k][1]}章"})
    for c in sorted(now[1] - was[1]):
        new["stamps"].append({"kind": "city", "id": c, "icon": "📮", "name": f"{c}章"})
    if now[2] > was[2]:
        new["first_today"] = True
        new["cover"] = next((c for c in COVERS if was[2] < c["days"] <= now[2]), None)
    new["days"] = now[2]
    return new


def _edit_today(user, fn) -> dict:
    """改今天这一页：带上城市和天气，存回去，返回新得到的东西（第一次记今天 +1 ⭐、邮戳、封面）。"""
    uid, day = user["id"], _today()
    was = _before(uid)
    d = _load(uid, day)
    fn(d)
    if _has(d):
        d.setdefault("city", home_city(user))
        if not d.get("sky") and d.get("city"):
            s = sky(d["city"], day)
            if s:
                d["sky"] = s
    _save(uid, day, d)
    return {"page": _view(uid, day, d, bool((_row(uid, day) or {}).get("shared"))), "new": _changed(uid, was)}


def set_mood(user, mood: str) -> dict:
    if mood and mood not in MOOD:
        raise HTTPException(400, "选一个心情")

    def fn(d):
        if mood:
            d["mood"] = mood
        else:
            d.pop("mood", None)
    return _edit_today(user, fn)


def add_note(user, text: str) -> dict:
    text = (text or "").strip()[:TEXT_MAX]
    if not text:
        raise HTTPException(400, "写一点再存")

    def fn(d):
        notes = d.setdefault("notes", [])
        if len(notes) >= NOTES_MAX:
            raise HTTPException(400, "今天已经写了好多段啦，明天接着写")
        notes.append({"id": secrets.token_hex(6), "at": db.now(), "text": text})
    return _edit_today(user, fn)


def add_voice(user, data: bytes, mime: str, secs: int) -> dict:
    mime = (mime or "").split(";")[0].strip().lower()
    if mime not in VOICE_TYPES:
        raise HTTPException(400, "这种录音格式存不了")
    if not data or len(data) > VOICE_MAX:
        raise HTTPException(400, "录音太长了，说短一点再试试")
    vid = secrets.token_hex(12)

    def fn(d):
        notes = d.setdefault("notes", [])
        if len(notes) >= NOTES_MAX:
            raise HTTPException(400, "今天已经写了好多段啦，明天接着写")
        f = _voice_file(user["id"], vid)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(_fernet().encrypt(data))
        notes.append({"id": secrets.token_hex(6), "at": db.now(), "voice": vid, "mime": mime,
                      "secs": max(1, min(VOICE_SECS, int(secs or 0)))})
    return _edit_today(user, fn)


def _voice_file(user_id: int, vid: str):
    if not re.fullmatch(r"[0-9a-f]{24}", vid or ""):
        raise HTTPException(404, "没有这段录音")
    return config.DATA_DIR / "daybook" / str(int(user_id)) / f"{vid}.bin"


def voice(user_id: int, vid: str, day: str | None = None) -> tuple[bytes, str]:
    """一段录音（解密后的声音和格式）。day 给了就只在那一天里找（家长看分享的那一天时用）。"""
    rows = db.q("SELECT data FROM daybook WHERE user_id=?" + (" AND day=?" if day else ""), user_id, *([day] if day else []))
    for r in rows:
        for n in _open(r["data"]).get("notes", []):
            if n.get("voice") == vid:
                f = _voice_file(user_id, vid)
                if f.exists():
                    return _fernet().decrypt(f.read_bytes()), n.get("mime", "audio/webm")
    raise HTTPException(404, "没有这段录音")


def delete_note(user_id: int, day: str, note_id: str) -> None:
    """删一段（哪天的都能删，这是孩子自己的本子）。"""
    d = _load(user_id, day)
    notes = d.get("notes", [])
    keep = [n for n in notes if n.get("id") != note_id]
    if len(keep) == len(notes):
        raise HTTPException(404, "没有这一段")
    for n in notes:
        if n.get("id") == note_id and n.get("voice"):
            f = _voice_file(user_id, n["voice"])
            if f.exists():
                f.unlink()
    d["notes"] = keep
    _save(user_id, day, d)


def share(user_id: int, day: str, on: bool) -> bool:
    r = _row(user_id, day)
    if not r or not r["marked"]:
        raise HTTPException(404, "这天还没有记")
    db.run("UPDATE daybook SET shared=? WHERE user_id=? AND day=?", 1 if on else 0, user_id, day)
    return bool(on)


# ------------------------------------------------------------------ 城市和天气

def home_city(user) -> str:
    """家里的城市（只到市）：孩子跟着家长账号上填的；自己学的成人用自己的。"""
    from . import boards
    c = boards.city_of(dict(user)) if user["parent_id"] else (user["city"] or "")
    return boards.city_label(c)


def clean_city(name: str) -> str:
    c = "".join((name or "").split())[:12]
    for tail in ("特别行政区", "市", "地区", "盟"):
        if c.endswith(tail) and len(c) > len(tail) + 1:
            c = c[:-len(tail)]
    return c


def set_city(user, city: str) -> dict:
    """今天在哪个城市（孩子手动改，或「我今天不在家」定位换来的城市名）。只影响今天这一页。"""
    city = clean_city(city)
    if not city:
        raise HTTPException(400, "写一下城市的名字")

    def fn(d):
        d["city"] = city
        d.pop("sky", None)
    out = _edit_today(user, fn)
    if not _has(_load(user["id"], _today())):   # 只改了城市还不算记了：天气先查好，等孩子点心情时带上
        out["sky"] = sky(city, _today())
    return out


def city_from_point(lat: float, lon: float) -> str:
    """坐标 → 城市名。坐标只在这里用一下，不落库、不写日志。"""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, "没拿到位置")
    name = _reverse(round(lat, 3), round(lon, 3))
    if not name:
        raise HTTPException(503, "没认出是哪个城市，自己写一下城市的名字吧")
    return clean_city(name)


def _http_off() -> bool:
    return os.environ.get("AISTUDY_WEATHER", "on") == "off"


UA = {"User-Agent": "BeeJoy/1.0 (https://beejoy.ai)"}


def _reverse(lat: float, lon: float) -> str:
    if _http_off():
        return ""
    import httpx
    try:
        r = httpx.get("https://nominatim.openstreetmap.org/reverse", headers=UA, timeout=6,
                      params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 10, "accept-language": "zh-CN"})
        a = r.json().get("address", {})
    except (httpx.HTTPError, ValueError):
        return ""
    return a.get("city") or a.get("state_district") or a.get("county") or a.get("province") or a.get("state") or ""


_places: dict[str, tuple[float, float] | None] = {}
_failed: dict[str, float] = {}   # 城市 → 上次没查到的时间：10 分钟内不再试，别让孩子每存一下都等


def _fetch_sky(city: str) -> dict | None:
    """Open-Meteo：城市名 → 坐标 → 今天的天气。查不到返回 None。"""
    if _http_off():
        return None
    import httpx
    try:
        if city not in _places:
            g = httpx.get("https://geocoding-api.open-meteo.com/v1/search", timeout=4,
                          params={"name": city, "count": 1, "language": "zh", "format": "json"}).json()
            res = (g.get("results") or [None])[0]
            _places[city] = (res["latitude"], res["longitude"]) if res else None
        p = _places[city]
        if not p:
            return None
        w = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=4,
                      params={"latitude": p[0], "longitude": p[1], "timezone": "auto", "forecast_days": 1,
                              "daily": "weather_code,temperature_2m_max,temperature_2m_min"}).json()["daily"]
        return {"code": int(w["weather_code"][0]), "hi": round(w["temperature_2m_max"][0]), "lo": round(w["temperature_2m_min"][0])}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None


def sky(city: str, day: str) -> dict | None:
    """这个城市这一天的天气快照 {kind, icon, word, hi, lo}：同城的人共用，一天只查一次。只查今天（翻旧的日记用当时存的）。"""
    if not city:
        return None
    r = db.one("SELECT data FROM sky WHERE city=? AND day=?", city, day)
    if r:
        return db.jload(r["data"], None)
    if day != _today() or time.time() - _failed.get(city, 0) < 600:
        return None
    raw = _fetch_sky(city)
    if not raw:
        _failed[city] = time.time()
        return None
    kind, word = _sky_kind(raw["code"])
    s = {"kind": kind, "icon": SKY_KINDS[kind][0], "word": word, "hi": raw["hi"], "lo": raw["lo"]}
    db.run("INSERT INTO sky(city,day,data) VALUES(?,?,?) ON CONFLICT(city,day) DO NOTHING", city, day, db.jdump(s))
    return s


# ------------------------------------------------------------------ 本子：日历、回看、邮戳、封面

def days(user_id: int) -> int:
    """一共记了几天（累计，断了不清零）。每一天和做完一项任务一样算一颗 ⭐。"""
    return db.one("SELECT COUNT(*) AS n FROM daybook WHERE user_id=? AND marked=1", user_id)["n"]


def marked_today(user_id: int) -> bool:
    return bool(db.one("SELECT 1 AS x FROM daybook WHERE user_id=? AND day=? AND marked=1", user_id, _today()))


def today_mood(user_id: int) -> dict:
    """今天页上那个小入口：今天记了没有、点的什么心情（只给孩子本人的页面）。"""
    p = page(user_id, _today())
    return {"marked": bool(p and (p["mood"] or p["notes"])), "mood": p["mood"] if p else None}


def month(user_id: int, ym: str) -> dict:
    """一个月的日历：每天显示那天的心情（没记的空着）。"""
    try:
        first = date.fromisoformat(ym + "-01")
    except ValueError:
        first = db.today().replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    got = {r["day"]: _open(r["data"]) for r in db.q("SELECT day, data FROM daybook WHERE user_id=? AND day>=? AND day<? AND marked=1",
                                                    user_id, first.isoformat(), nxt.isoformat())}
    cells = [None] * first.weekday()
    d, today = first, db.today()
    while d < nxt:
        x = got.get(d.isoformat())
        cells.append({"day": d.isoformat(), "n": d.day, "mood": MOOD.get(x.get("mood", "")) if x else None,
                      "has": bool(x), "today": d == today, "future": d > today})
        d += timedelta(days=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    return {"ym": first.isoformat()[:7], "title": f"{first.year} 年 {first.month} 月", "cells": cells, "n": len(got),
            "prev": prev.isoformat()[:7], "next": nxt.isoformat()[:7] if nxt <= today else ""}


LOOKBACK = [(365, "去年的今天"), (30, "一个月前的今天"), (7, "一周前的今天")]


def lookback(user_id: int) -> dict | None:
    """日记最大的回报：翻到以前的自己。去年的今天 > 一个月前 > 一周前，有哪个给哪个。"""
    t = db.today()
    for n, label in LOOKBACK:
        day = (t - timedelta(days=n)).isoformat()
        p = page(user_id, day)
        if p and (p["mood"] or p["notes"]):
            return {**p, "ago": label}
    return None


def book(user_id: int) -> dict:
    """本子本身：记了几天、解锁的封面、收到的邮戳。"""
    skies, cities = {}, {}
    n = 0
    for r in db.q("SELECT day, data FROM daybook WHERE user_id=? AND marked=1 ORDER BY day", user_id):
        d = _open(r["data"])
        n += 1
        if d.get("sky"):
            skies.setdefault(d["sky"]["kind"], r["day"])
        if d.get("city"):
            cities.setdefault(d["city"], r["day"])
    covers = [{**c, "open": n >= c["days"]} for c in COVERS]
    nxt = next((c for c in COVERS if c["days"] > n), None)
    stamps = ([{"icon": SKY_KINDS[k][0], "name": SKY_KINDS[k][1] + "章", "first": skies[k]} for k in SKY_KINDS if k in skies] +
              [{"icon": "📮", "name": c + "章", "first": day} for c, day in cities.items()])
    return {"days": n, "covers": covers, "next": nxt, "to_next": nxt["days"] - n if nxt else 0, "stamps": stamps,
            "sky_left": [{"icon": v[0], "name": v[1] + "章"} for k, v in SKY_KINDS.items() if k not in skies]}


def cover_of(user, bk: dict) -> dict:
    """现在用的封面：孩子自己挑的（解锁了才算），没挑就用最新解锁的。"""
    st = db.jload(user["settings"], {}) or {}
    opened = [c for c in bk["covers"] if c["open"]] or bk["covers"][:1]
    return next((c for c in opened if c["id"] == st.get("daybook_cover")), opened[-1])


def set_cover(user_id: int, cover: str) -> None:
    n = days(user_id)
    if not any(c["id"] == cover and n >= c["days"] for c in COVERS):
        raise HTTPException(400, "这个封面还没解锁")
    u = db.one("SELECT settings FROM users WHERE id=?", user_id)
    st = db.jload(u["settings"], {}) or {}
    st["daybook_cover"] = cover
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), user_id)


def prompt(user_id: int, teen: bool) -> str:
    pool = PROMPTS_TEEN if teen else PROMPTS_PRIMARY
    return pool[(db.today().toordinal() + user_id) % len(pool)]


def low_streak(user_id: int) -> bool:
    """最近记的几天心情都很低（至少 3 天、两周以内）：只对孩子本人多说一句，不通知任何人。"""
    since = (db.today() - timedelta(days=14)).isoformat()
    rows = db.q("SELECT data FROM daybook WHERE user_id=? AND day>=? AND marked=1 ORDER BY day DESC LIMIT 5", user_id, since)
    moods = [m for m in (_open(r["data"]).get("mood") for r in rows) if m]
    return len(moods) >= 3 and all(m in LOW for m in moods[:5])


# ------------------------------------------------------------------ 家长那边只有这些

def week_days(user_id: int) -> int:
    """这一周（周一起）记了几天：家长页、周报上唯一和日记有关的数。"""
    t = db.today()
    mon = (t - timedelta(days=t.weekday())).isoformat()
    return db.one("SELECT COUNT(*) AS n FROM daybook WHERE user_id=? AND day>=? AND marked=1", user_id, mon)["n"]


def days_between(user_id: int, start: str, end: str) -> int:
    return db.one("SELECT COUNT(*) AS n FROM daybook WHERE user_id=? AND day>=? AND day<? AND marked=1", user_id, start, end)["n"]


def shared_pages(user_id: int) -> list[dict]:
    """孩子自己点了分享的那几天（新的在前）。"""
    return [_view(user_id, r["day"], _open(r["data"]), True)
            for r in db.q("SELECT day, data FROM daybook WHERE user_id=? AND shared=1 AND marked=1 ORDER BY day DESC", user_id)]


def shared_count(user_id: int) -> int:
    return db.one("SELECT COUNT(*) AS n FROM daybook WHERE user_id=? AND shared=1 AND marked=1", user_id)["n"]


def is_shared(user_id: int, day: str) -> bool:
    return bool(db.one("SELECT 1 AS x FROM daybook WHERE user_id=? AND day=? AND shared=1 AND marked=1", user_id, day))
