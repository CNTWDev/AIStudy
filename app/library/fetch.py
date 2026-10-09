"""从网上下载公版原文：古腾堡（纯文本）、维基文库（MediaWiki 接口）、任意纯文本地址。

服务器访问不了某个来源时，管理员可以在后台上传 txt（见 Library.import_text）。
古腾堡的镜像可以用环境变量 GUTENBERG_BASE 指定。"""
import csv
import html
import io
import os
import re

GUTENBERG = os.environ.get("GUTENBERG_BASE", "https://www.gutenberg.org").rstrip("/")
WIKISOURCE = os.environ.get("WIKISOURCE_BASE", "https://zh.wikisource.org").rstrip("/")
UA = {"User-Agent": "beejoy-library/1.0 (family reading app; public-domain texts only)"}


class FetchError(Exception):
    pass


def _get(url: str, **params) -> "httpx.Response":
    import httpx
    try:
        r = httpx.get(url, params=params or None, headers=UA, timeout=60, follow_redirects=True)
    except httpx.HTTPError as e:
        raise FetchError(f"连不上 {url.split('/')[2]}：{e}") from e
    if r.status_code >= 400:
        raise FetchError(f"{url} 返回 {r.status_code}")
    return r


def gutenberg_text(gid: int) -> str:
    for url in (f"{GUTENBERG}/cache/epub/{gid}/pg{gid}.txt", f"{GUTENBERG}/files/{gid}/{gid}-0.txt",
                f"{GUTENBERG}/ebooks/{gid}.txt.utf-8"):
        try:
            r = _get(url)
        except FetchError:
            continue
        r.encoding = "utf-8"
        return r.text
    raise FetchError(f"古腾堡 #{gid} 下载失败")


def gutenberg_title(raw: str) -> str:
    m = re.search(r"^Title:\s*(.+)$", raw[:5000], re.M)
    return m.group(1).strip() if m else ""


def gutenberg_find(title: str, lang: str) -> int | None:
    """按书名在古腾堡目录（pg_catalog.csv）里找编号。"""
    r = _get(f"{GUTENBERG}/cache/epub/feeds/pg_catalog.csv")
    want = _key(title)
    for row in csv.DictReader(io.StringIO(r.text)):
        if row.get("Type") == "Text" and lang in (row.get("Language") or "") and _key(row.get("Title", "")).startswith(want):
            return int(row["Text#"])
    return None


def _key(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


def wikisource_html(page: str) -> str:
    r = _get(f"{WIKISOURCE}/w/api.php", action="parse", page=page, prop="text", format="json", formatversion="2",
             redirects="1")
    d = r.json()
    if "error" in d:
        raise FetchError(f"维基文库没有「{page}」：{d['error'].get('info', '')}")
    return d["parse"]["text"]


def html_to_text(h: str) -> str:
    h = re.sub(r"<(script|style|table)[^>]*>.*?</\1>", "", h, flags=re.S | re.I)
    h = re.sub(r'<(div|span)[^>]*class="[^"]*(mw-editsection|reference|noprint|ws-noexport|header)[^"]*"[^>]*>.*?</\1>', "",
               h, flags=re.S | re.I)
    h = re.sub(r"<sup[^>]*>.*?</sup>", "", h, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</(p|div|h\d|li|dd|dt)>", "\n\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    return html.unescape(h)


def wikisource_book(page: str, subpages: list[str] | None = None) -> str:
    """一本书在维基文库通常是目录页 + 子页面（《朝花夕拾/狗·猫·鼠》）。按目录里出现的顺序把子页面拼起来，
    每个子页面前面加「第 N 篇 标题」，方便切章。没有子页面就直接用这一页。"""
    index = wikisource_html(page)
    if subpages is None:
        links = re.findall(r'href="/wiki/([^"#?]+)"', index)
        seen, subpages = set(), []
        from urllib.parse import unquote
        for href in links:
            t = unquote(href).replace("_", " ")
            if t.startswith(page + "/") and t not in seen:
                seen.add(t); subpages.append(t)
    if not subpages:
        return html_to_text(index)
    parts = []
    for i, sp in enumerate(subpages, 1):
        body = html_to_text(wikisource_html(sp))
        parts.append(f"第{i}篇 {sp.split('/')[-1]}\n\n{body}")
    return "\n\n".join(parts)


def fetch_raw(book: dict) -> tuple[str, dict]:
    """返回 (原文, 来源说明)。"""
    src = book.get("source") or {}
    t = src.get("type")
    if t == "gutenberg":
        gid = int(src["id"])
        raw = gutenberg_text(gid)
        match = src.get("match") or book["title"]
        if match and _key(match) not in _key(gutenberg_title(raw) or match):  # 编号对不上：按书名重新找
            alt = gutenberg_find(match, book.get("lang", "en"))
            if not alt:
                raise FetchError(f"古腾堡 #{gid} 是《{gutenberg_title(raw)}》，不是《{match}》，目录里也没找到")
            gid, raw = alt, gutenberg_text(alt)
        return raw, {"type": "gutenberg", "id": gid, "url": f"{GUTENBERG}/ebooks/{gid}", "gutenberg": True}
    if t == "wikisource":
        raw = wikisource_book(src["page"], src.get("subpages"))
        return raw, {"type": "wikisource", "url": f"{WIKISOURCE}/wiki/{src['page']}"}
    if t == "url":
        r = _get(src["url"])
        return r.text, {"type": "url", "url": src["url"], "gutenberg": "gutenberg" in src["url"]}
    raise FetchError("这本书没有可下载的来源（不是公版书，或者需要管理员上传原文）")
