"""迭代日记：系统的每一次重要调整，背后都是对孩子的一次观察、对学习的一次理解（docs/DESIGN.md §5.50）。

日记不是管理员填的。每次有「为什么」的改动合并以后，由 Claude 用一个父亲的口吻写一篇草稿；管理员在后台读，
收下、改一改，或者不要。同一天的几件事合成一篇；只修 bug、调排版的不写。

日记是家里的私事，仓库是公开的，所以这里只有模块，没有内容。一篇日记是一个 Markdown 文件：

    ---
    day: 2026-10-07
    title: 弟弟也要能赢
    quote: 他们说，想玩火柴人大战。
    changes: 乐园上线，火柴人大战、闪电赛跑、星星守卫。
    prs: 14, 15
    tags: 游戏, 公平
    status: kept
    ---
    正文，空一行分段。

文件放在服务器的 DATA_DIR/diary/ 下，或在后台上传（上传的也存进这个目录）。打开后台日记页时读一遍目录：
新的文件进表；后台没改过的，文件变了跟着变；后台改过的（edited）和不要了的，文件不再覆盖。
"""
import re

from fastapi import HTTPException

from . import config, db

DIR = config.DATA_DIR / "diary"
REPO = "https://github.com/CNTWDev/AIStudy"
STATUS = {"draft": "草稿", "kept": "收下", "dropped": "不要"}
SLUG = re.compile(r"^[\w\-]{1,80}$")
FIELDS = ("day", "title", "quote", "changes", "prs", "tags", "status")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _list(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,，、\s]+", s or "") if x.strip()]


def parse(text: str) -> dict:
    """一篇日记文件 → {day, title, quote, changes, prs, tags, status, body}。格式不对抛 ValueError。"""
    m = re.match(r"\A﻿?---\s*\n(.*?)\n---\s*\n?(.*)\Z", text.replace("\r\n", "\n"), re.S)
    if not m:
        raise ValueError("开头要有 --- 包起来的 day、title 几行")
    head = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip() in FIELDS:
            head[k.strip()] = v.strip()
    if not DAY.match(head.get("day", "")):
        raise ValueError("day 要写成 2026-10-07 这样")
    if not head.get("title"):
        raise ValueError("缺 title")
    prs = [int(x.lstrip("#")) for x in _list(head.get("prs", "")) if x.lstrip("#").isdigit()]
    status = head.get("status", "draft")
    return {"day": head["day"], "title": head["title"][:100], "quote": head.get("quote", "")[:300],
            "changes": head.get("changes", "")[:500], "prs": prs, "tags": _list(head.get("tags", ""))[:8],
            "status": status if status in ("draft", "kept") else "draft", "body": m.group(2).strip()}


def _upsert(slug: str, e: dict) -> str:
    """一篇进表。返回 new / updated / kept（表里的不动）。"""
    row = db.one("SELECT id, edited, status, day, title, quote, body, changes, prs, tags FROM diary WHERE slug=?", slug)
    vals = (e["day"], e["title"], e["quote"], e["body"], e["changes"], db.jdump(e["prs"]), db.jdump(e["tags"]))
    if not row:
        db.insert("INSERT INTO diary(slug,day,title,quote,body,changes,prs,tags,status,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)", slug, *vals, e["status"], db.now(), db.now())
        return "new"
    if row["edited"] or row["status"] == "dropped":
        return "kept"
    if vals == (row["day"], row["title"], row["quote"], row["body"], row["changes"], row["prs"], row["tags"]):
        return "kept"
    db.run("UPDATE diary SET day=?,title=?,quote=?,body=?,changes=?,prs=?,tags=?,updated_at=? WHERE id=?",
           *vals, db.now(), row["id"])
    return "updated"


def sync() -> dict:
    """读一遍 DATA_DIR/diary/：返回 {new, updated, errors: [(文件名, 原因)]}。"""
    out = {"new": 0, "updated": 0, "errors": []}
    if not DIR.is_dir():
        return out
    for f in sorted(DIR.glob("*.md")):
        if not SLUG.match(f.stem):
            out["errors"].append((f.name, "文件名只用字母、数字、横线"))
            continue
        try:
            r = _upsert(f.stem, parse(f.read_text(encoding="utf-8")))
        except (ValueError, UnicodeDecodeError) as e:
            out["errors"].append((f.name, str(e)))
            continue
        if r in ("new", "updated"):
            out[r] += 1
    return out


def upload(name: str, data: bytes) -> str:
    """后台上传一篇：先检查格式，再存进 DATA_DIR/diary/，然后进表。返回 new / updated / kept。"""
    slug = re.sub(r"\.md$", "", (name or "").rsplit("/", 1)[-1])
    if not SLUG.match(slug):
        raise HTTPException(400, f"{name}：文件名只用字母、数字、横线，以 .md 结尾")
    try:
        text = data.decode("utf-8")
        e = parse(text)
    except (ValueError, UnicodeDecodeError) as err:
        raise HTTPException(400, f"{name}：{err}")
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{slug}.md").write_text(text, encoding="utf-8")
    return _upsert(slug, e)


def _row(r) -> dict:
    d = dict(r)
    d["prs"] = db.jload(d["prs"], [])
    d["tags"] = db.jload(d["tags"], [])
    d["paras"] = [p.strip() for p in re.split(r"\n\s*\n", d["body"]) if p.strip()]
    return d


def entries(status: str) -> list[dict]:
    return [_row(r) for r in db.q("SELECT * FROM diary WHERE status=? ORDER BY day, slug", status)]


def months(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """按月份分组，给时间线用：[('2026 年 10 月', [...])]。"""
    out: dict[str, list] = {}
    for r in rows:
        y, m, _ = r["day"].split("-")
        out.setdefault(f"{y} 年 {int(m)} 月", []).append(r)
    return list(out.items())


def get(eid: int) -> dict:
    r = db.one("SELECT * FROM diary WHERE id=?", eid)
    if not r:
        raise HTTPException(404, "没有这篇日记")
    return _row(r)


def neighbors(e: dict) -> tuple:
    """同一种状态里的上一篇、下一篇（按日期）。"""
    rows = entries(e["status"])
    i = next(i for i, r in enumerate(rows) if r["id"] == e["id"])
    return (rows[i - 1] if i else None), (rows[i + 1] if i + 1 < len(rows) else None)


def save(eid: int, form: dict) -> None:
    """后台改一改：改过的标 edited，文件再变也不覆盖。"""
    get(eid)
    day, title = str(form.get("day") or "").strip(), str(form.get("title") or "").strip()
    if not DAY.match(day) or not title:
        raise HTTPException(400, "日期和标题要写")
    db.run("UPDATE diary SET day=?,title=?,quote=?,body=?,changes=?,tags=?,edited=1,updated_at=? WHERE id=?",
           day, title[:100], str(form.get("quote") or "").strip()[:300], str(form.get("body") or "").strip(),
           str(form.get("changes") or "").strip()[:500], db.jdump(_list(str(form.get("tags") or ""))[:8]), db.now(), eid)


def set_status(eid: int, status: str) -> None:
    if status not in STATUS:
        raise HTTPException(400, "不认识的状态")
    get(eid)
    db.run("UPDATE diary SET status=?,updated_at=? WHERE id=?", status, db.now(), eid)
