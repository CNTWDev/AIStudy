"""每日新闻：每天从高质量媒体的 RSS 里选一条当天最重要、和学习有关的新闻，按年级改写成分级英文短文给孩子读。

流程（后台每天跑一次，也可以在管理后台手动跑）：
1. 抓新闻源（content/news.json，可用 config/news.json 覆盖）最近一两天的标题和摘要；访问不了的源自动跳过，结果记在管理后台。
2. AI 当编辑：给小学（primary）、中学（secondary）各排出 3 条（第一条是今天的，后两条备选，管理员可以「换一篇」）。
3. 取事实材料：RSS 里带全文就用全文，否则服务器打开原文网页取正文。材料只给 AI 用，不给孩子看。
4. 按级别改写（L1–L4，见 content/news.json 的 levels）：用 AI 自己的话写，只用材料里的事实，附生词、背景、小题、讨论题。
   每个级别生成一次存进 contents（kind='news'），所有同级别的孩子共用。

版权：孩子读的都是改写后的新文章，页面上写明根据哪家媒体的报道改写并给原文链接；
只有公有领域（pd）和允许原样转载（cc-by-nd）的源，才提供「读原文」（放进站内阅读器，原样不改）。

孩子：/news 打开今天的新闻（放进站内阅读器：点词查、收藏进单词本、朗读、读后小题，读完算今天的英文阅读）。
"""
import html
import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import bank, config, db, llm, webpage
from .catalog import is_adult, stage_rank

log = logging.getLogger("aistudy.news")
JOB = "news"
BANDS = {"secondary": "中学", "primary": "小学"}
LICENSE_LABEL = {"pd": "公有领域", "cc-by-nd": "CC BY-ND，可原样转载", "copyright": "版权归原媒体"}
FREE_LICENSES = ("pd", "cc-by-nd")
MIN_FACTS = 400       # 事实材料少于这么多字就不用这条（AI 容易编）
FRESH_HOURS = (36, 72)  # 先找 36 小时内的新闻，太少就放宽到 72 小时
PER_SOURCE = 12
MAX_CANDIDATES = 90
RETRY_MINUTES = 60    # 抓取 / 选题失败后多久再试


def cfg() -> dict:
    for p in (config.BASE_DIR / "config" / "news.json", config.BASE_DIR / "content" / "news.json"):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                log.warning("news config %s is not valid JSON", p)
    return {"sources": [], "levels": {}}


def levels() -> dict:
    return cfg().get("levels") or {}


# ------------------------------------------------------------------ 读 RSS / Atom

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def strip_html(s: str) -> str:
    """HTML 片段变成纯文本，段落之间空一行。"""
    s = re.sub(r"(?is)<(script|style|figure|figcaption)\b.*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|blockquote)>", "\n\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    paras = [re.sub(r"[ \t\r\f\v\xa0]+", " ", p).strip() for p in s.split("\n")]
    return "\n\n".join(p for p in paras if p)


def _date(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(text: str) -> list[dict]:
    """RSS 2.0 / RSS 1.0 (RDF) / Atom 都认。返回 [{title, url, published(datetime|None), summary, content, categories}]。"""
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError:
        return []
    out = []
    for e in root.iter():
        if _local(e.tag) not in ("item", "entry"):
            continue
        d = {"title": "", "url": "", "published": None, "summary": "", "content": "", "categories": []}
        for c in e:
            t, val = _local(c.tag), (c.text or "")
            if t == "title":
                d["title"] = strip_html(val).replace("\n", " ")
            elif t == "link":
                href = c.get("href")
                if href and c.get("rel", "alternate") == "alternate":
                    d["url"] = href.strip()
                elif not href and val.strip():
                    d["url"] = val.strip()
            elif t in ("pubdate", "published", "updated", "date") and not d["published"]:
                d["published"] = _date(val)
            elif t in ("description", "summary"):
                d["summary"] = strip_html(val)
            elif t in ("encoded", "content"):
                d["content"] = strip_html(val)
            elif t == "category":
                d["categories"].append((c.get("term") or val).strip())
        if d["title"] and d["url"].startswith("http"):
            out.append(d)
    return out


def fetch_source(src: dict) -> tuple[list[dict], str]:
    try:
        _, text = webpage.fetch(src["url"], accept=("xml", "rss", "atom", "text", "html"))
    except webpage.FetchError as e:
        return [], str(e)
    except Exception as e:  # noqa: BLE001  一个源出错不影响别的源
        return [], f"{type(e).__name__}: {e}"
    items = parse_feed(text)
    return items, "" if items else "没读到新闻条目（不是 RSS / Atom，或者是空的）"


def collect(now: datetime | None = None) -> tuple[list[dict], dict]:
    """抓所有新闻源，返回 (候选新闻, 每个源的状态)。"""
    now = now or datetime.now(timezone.utc)
    srcs = [s for s in cfg().get("sources", []) if s.get("url") and not s.get("off")]
    status, pool = {}, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for src, (items, err) in zip(srcs, ex.map(fetch_source, srcs)):
            dated = [i for i in items if i["published"]]
            newest = max((i["published"] for i in dated), default=None)
            status[src["id"]] = {"name": src["name"], "ok": not err, "error": err[:200], "n": len(items),
                                 "newest": newest.isoformat(timespec="minutes") if newest else ""}
            for it in items[:PER_SOURCE]:
                pool.append({**it, "source_id": src["id"], "source_name": src["name"],
                             "license": src.get("license", "copyright"), "topics": src.get("topics", [])})
    for hours in FRESH_HOURS:
        cands = [c for c in pool if c["published"] is None or now - c["published"] <= timedelta(hours=hours)]
        if len(cands) >= 10:
            break
    seen, out = set(), []
    for c in sorted(cands, key=lambda c: c["published"] or now, reverse=True):
        key = re.sub(r"\W+", "", c["title"].lower())[:60]
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out[:MAX_CANDIDATES], status


# ------------------------------------------------------------------ 选题、取事实材料

def facts_for(c: dict) -> str:
    text = c.get("content") or ""
    if len(text) < 1500:
        try:
            body = webpage.article(c["url"])["body"]
            if len(body) > len(text):
                text = body
        except webpage.FetchError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("news: fetch article failed %s", c.get("url"))
    if len(text) < MIN_FACTS:
        text = "\n\n".join(x for x in (c.get("summary"), text) if x)
    return text[:8000] if len(text) >= MIN_FACTS else ""


def _plain(c: dict) -> dict:
    """候选新闻存进数据库时的样子（日期变字符串，全文截短）。"""
    p = c.get("published")
    d = {**c, "published": p.isoformat(timespec="minutes") if isinstance(p, datetime) else (p or "")}
    d["content"] = (c.get("content") or "")[:8000]
    return d


def _save_pick(day: str, band: str, c: dict, facts: str, alts: list[dict]) -> int:
    db.run("DELETE FROM news_picks WHERE day=? AND band=?", day, band)
    return db.insert(
        "INSERT INTO news_picks(day,band,source_id,source_name,license,title,url,published,summary,facts,why,topic,subjects,alts,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        day, band, c.get("source_id", ""), c.get("source_name", ""), c.get("license", "copyright"), c["title"][:300],
        c["url"][:1000], c.get("published") or "", (c.get("summary") or "")[:1000], facts, c.get("why", "")[:300],
        c.get("topic", "")[:40], db.jdump(c.get("subjects") or []), db.jdump([_plain(a) for a in alts]), db.now())


def _first_usable(ranked: list[dict]) -> tuple[dict | None, str, list[dict]]:
    for i, c in enumerate(ranked):
        facts = facts_for(c)
        if facts:
            return c, facts, ranked[i + 1:]
    return None, "", []


def pick_today(force: bool = False) -> dict:
    """给今天选新闻（已经选好的不重选，force=True 全部重选）。返回 {band: pick_id 或错误, sources: 源状态}。"""
    day = db.today().isoformat()
    todo = [b for b in BANDS if force or not db.one("SELECT id FROM news_picks WHERE day=? AND band=?", day, b)]
    if not todo:
        return {"day": day, "done": True}
    cands, status = collect()
    res = {"day": day, "sources": status, "candidates": len(cands)}
    if not cands:
        res["error"] = "所有新闻源都没抓到最近的新闻（网络不通，或源地址失效）"
        return res
    brief = [{"i": i, "source": c["source_name"], "title": c["title"], "summary": c["summary"], "topics": c["topics"]}
             for i, c in enumerate(cands)]
    ranks = llm.news_pick(brief, day)
    for band in todo:
        ranked = []
        for x in ranks.get(band) or []:
            try:
                c = cands[int(x.get("i"))]
            except (TypeError, ValueError, IndexError):
                continue
            ranked.append({**c, "why": x.get("why", ""), "topic": x.get("topic", ""), "subjects": x.get("subjects") or []})
        c, facts, alts = _first_usable(ranked)
        if not c:
            res[band] = "AI 选出的新闻都取不到足够的事实材料"
            continue
        res[band] = _save_pick(day, band, _plain(c), facts, alts)
    return res


def swap(pick_id: int) -> int | None:
    """管理员「换一篇」：用这条的备选里下一条能用的替换（同一天、同一组）。"""
    p = db.one("SELECT * FROM news_picks WHERE id=?", pick_id)
    if not p:
        return None
    c, facts, alts = _first_usable(db.jload(p["alts"], []))
    if not c:
        return None
    return _save_pick(p["day"], p["band"], c, facts, alts)


# ------------------------------------------------------------------ 级别、文章

def level_for(user) -> str:
    lv = levels()
    want = (db.jload(user.get("settings"), {}) or {}).get("news_level")
    if want in lv:
        return want
    grade = user.get("grade") or ""
    year = 99 if is_adult(grade) else stage_rank(grade)
    for lid, L in sorted(lv.items(), key=lambda kv: kv[1].get("max_year", 99)):
        if year <= L.get("max_year", 99):
            return lid
    return next(iter(lv), "L3")


def band_of(level: str) -> str:
    return levels().get(level, {}).get("band", "secondary")


def latest_pick(band: str):
    return db.one("SELECT * FROM news_picks WHERE band=? AND day<=? ORDER BY day DESC, id DESC LIMIT 1",
                  band, db.today().isoformat())


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def article(pick, level: str, user_id=None) -> tuple[dict, int]:
    """这条新闻在这个级别的改写稿：库里有就用，没有就让 AI 写一篇存下来（同一时间只写一次）。"""
    topic = f"news:{pick['id']}"
    sql = "SELECT id, body FROM contents WHERE kind='news' AND topic=? AND grade=? AND status='active' ORDER BY id LIMIT 1"
    row = db.one(sql, topic, level)
    if row:
        return db.jload(row["body"], {}), row["id"]
    with _locks_guard:
        lock = _locks.setdefault(f"{topic}/{level}", threading.Lock())
    with lock:
        row = db.one(sql, topic, level)
        if row:
            return db.jload(row["body"], {}), row["id"]
        body = llm.news_write(dict(pick), levels()[level], pick["day"])
        if not body.get("body"):
            raise llm.LLMError("AI 没写出文章，请稍后再试")
        cid = bank.save_content("news", body, "", "en", level, topic=topic, title=body.get("title", ""),
                                meta=bank.gen_meta("news_write", source=pick["source_name"], url=pick["url"]),
                                created_by=user_id)
        return body, cid


def levels_in_use() -> set[str]:
    return {level_for(u) for u in db.q("SELECT grade, settings FROM users WHERE role='kid'")}


def open_for(user) -> int | None:
    """孩子打开今天的新闻：返回阅读记录 id（第一次打开时建一条）；还没有新闻返回 None。"""
    level = level_for(user)
    pick = latest_pick(band_of(level))
    if not pick:
        return None
    source = f"news:{pick['id']}:{level}"
    r = db.one("SELECT id FROM readings WHERE user_id=? AND source=?", user["id"], source)
    if r:
        return r["id"]
    body, cid = article(pick, level, user_id=user["id"])
    return db.insert("INSERT INTO readings(user_id,lang,title,body,source,level,questions,content_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     user["id"], "en", body.get("title") or pick["title"], body.get("body", ""), source, level,
                     db.jdump(body.get("questions", [])), cid, db.now())


def today_title(user) -> str | None:
    """每日计划用：今天（或最近一天）给这个孩子的新闻标题；没有就 None。不调用 AI。"""
    pick = latest_pick(band_of(level_for(user)))
    fresh = (db.today() - timedelta(days=2)).isoformat()
    return pick["title"] if pick and pick["day"] >= fresh else None


def reading_extra(r) -> dict | None:
    """阅读页上新闻多出来的部分：来源和原文链接、读前背景、生词、和课本的联系、讨论题、换难度。"""
    m = re.fullmatch(r"news:(\d+):(\w+)", r["source"] or "")
    if not m:
        return None
    pick = db.one("SELECT * FROM news_picks WHERE id=?", int(m.group(1)))
    c = db.one("SELECT body FROM contents WHERE id=?", r["content_id"]) if r["content_id"] else None
    body = db.jload(c["body"], {}) if c else {}
    lv = levels()
    return {"level": m.group(2), "levels": [{"id": k, "name": v["name"], "who": v["who"]} for k, v in lv.items()],
            "pick": dict(pick) if pick else None, "license": LICENSE_LABEL.get(pick["license"], "") if pick else "",
            "free": bool(pick) and pick["license"] in FREE_LICENSES,
            "glossary": body.get("glossary") or [], "background": body.get("background", ""),
            "links": [x for x in body.get("links") or [] if x.get("point")], "discuss": (body.get("discuss") or None) if lv.get(m.group(2), {}).get("discuss") else None}


# ------------------------------------------------------------------ 后台定时

def _job():
    db.run("INSERT INTO jobs(name) VALUES(?) ON CONFLICT(name) DO NOTHING", JOB)
    return db.one("SELECT * FROM jobs WHERE name=?", JOB)


def _lease(seconds=900) -> bool:
    _job()
    now = datetime.fromisoformat(db.now())
    return db.run("UPDATE jobs SET locked_until=? WHERE name=? AND (locked_until='' OR locked_until IS NULL OR locked_until<?)",
                  (now + timedelta(seconds=seconds)).isoformat(timespec="seconds"), JOB, now.isoformat(timespec="seconds")) == 1


def status() -> dict:
    j = _job()
    return {"last_run": j["last_run"], "last": db.jload(j["last_result"], {}) or {}}


def run_daily(force: bool = False) -> dict:
    """每天一次：到了设定的钟点（默认早上 6 点）就选今天的新闻，再把用得到的级别都先写好。
    已经做完的不重复做；失败了隔一小时再试。force=True：管理员手动重选。"""
    if not llm.enabled():
        return {"skipped": "AI 没开"}
    now = datetime.fromisoformat(db.now())
    last = status()["last"]
    if not force:
        if now.hour < int(cfg().get("hour", 6)):
            return {"skipped": "还没到时间"}
        day = db.today().isoformat()
        if last.get("day") == day and last.get("error") and last.get("tried_at", "") > (now - timedelta(minutes=RETRY_MINUTES)).isoformat():
            return {"skipped": "刚失败过，稍后再试"}
    if not _lease():
        return {"skipped": "另一个进程正在跑"}
    res = {}
    try:
        res = pick_today(force=force)
        if res.get("done") and last.get("day") == res["day"]:
            res = {**last, **res}  # 保留上次抓取时各个源的状态
        res["written"] = []
        for level in sorted(levels_in_use()):
            pick = latest_pick(band_of(level))
            if pick and pick["day"] == db.today().isoformat():
                try:
                    article(pick, level)
                    res["written"].append(level)
                except llm.LLMError as e:
                    res.setdefault("write_errors", {})[level] = str(e)[:200]
    except llm.LLMError as e:
        res["error"] = str(e)[:300]
    except Exception as e:  # noqa: BLE001  后台任务出错不能影响网站
        log.exception("news job failed")
        res["error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        res.setdefault("day", db.today().isoformat())
        res["tried_at"] = now.isoformat(timespec="seconds")
        db.run("UPDATE jobs SET locked_until='', last_run=?, last_result=? WHERE name=?", db.now(), db.jdump(res), JOB)
    return res


def run_async(force: bool = False):
    threading.Thread(target=run_daily, kwargs={"force": force}, name="news-manual", daemon=True).start()


def start_background():
    """应用启动时开一个后台线程，每 20 分钟看一眼要不要选今天的新闻。设 AISTUDY_JOBS=0 关掉（测试里就关掉了）。"""
    if os.environ.get("AISTUDY_JOBS", "1") == "0":
        return

    def loop():
        time.sleep(90)
        while True:
            try:
                run_daily()
            except Exception:  # noqa: BLE001
                log.exception("news job failed")
            time.sleep(20 * 60)

    threading.Thread(target=loop, name="news", daemon=True).start()
