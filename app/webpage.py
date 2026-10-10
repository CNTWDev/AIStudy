"""贴链接就能读：网站帮孩子把网页文章抓下来，放进站内阅读器（点词就查、收藏进单词复习）。

安全：只抓 http/https 的公网地址（内网、本机、保留地址一律拒绝，每次跳转都重新检查），
限制大小和超时；只取正文文字，不执行网页里的任何脚本。
"""
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 3 * 1024 * 1024
TIMEOUT = 12
MAX_REDIRECTS = 4
UA = "Mozilla/5.0 (compatible; AIStudyReader/1.0; +https://github.com/CNTWDev/AIStudy)"


class FetchError(Exception):
    pass


def _check_url(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("请贴以 http:// 或 https:// 开头的网页链接")
    if u.port not in (None, 80, 443):
        raise FetchError("只支持普通网页（80 / 443 端口）")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchError("打不开这个网址：域名不存在或网络不通")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError("不能打开内网或本机地址")
    return url.strip()


def fetch(url: str, accept: tuple[str, ...] = ("html", "text")) -> tuple[str, str]:
    """返回 (最终网址, HTML 文本)。accept：允许的内容类型（Content-Type 里包含其中一个就行；抓 RSS 时加上 "xml"）。"""
    url = _check_url(url)
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": UA, "Accept": "text/html,application/xml,*/*"}) as c:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with c.stream("GET", url) as r:
                    if r.is_redirect:
                        url = _check_url(urljoin(url, r.headers.get("location", "")))
                        continue
                    if r.status_code >= 400:
                        raise FetchError(f"网页打不开（{r.status_code}）。有的网站需要登录或不让程序访问，可以改用「贴一段文字」")
                    ctype = r.headers.get("content-type", "")
                    if not any(a in ctype for a in accept):
                        raise FetchError("这个链接不是网页文章（可能是图片、PDF 或下载文件）")
                    buf = b""
                    for chunk in r.iter_bytes():
                        buf += chunk
                        if len(buf) > MAX_BYTES:
                            break
                    enc = r.encoding or "utf-8"
                    m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', buf[:4096], re.I)
                    if m:
                        enc = m.group(1).decode("ascii", "ignore") or enc
                    try:
                        return url, buf.decode(enc, errors="replace")
                    except LookupError:
                        return url, buf.decode("utf-8", errors="replace")
            except httpx.HTTPError:
                raise FetchError("网页打开超时或连接失败，稍后再试，或改用「贴一段文字」")
    raise FetchError("这个网址跳转太多次了")


SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "button", "svg", "iframe", "select",
        "figure", "figcaption", "template"}
VOID = {"meta", "br", "img", "input", "link", "hr", "source", "wbr", "area", "col", "embed", "track", "base"}
BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "dd", "td"}


class _Extract(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip: list[str] = []
        self.title = ""
        self.og_title = ""
        self.in_title = False
        self.blocks: list[tuple[str, int, str]] = []  # (tag, 在 article/main 里的深度, 文字)
        self.cur: list[str] | None = None
        self.cur_tag = ""
        self.main = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property") == "og:title":
            self.og_title = a.get("content") or ""
        if tag in VOID:
            if tag == "br" and self.cur is not None and not self.skip:
                self.cur.append("\n")
            return
        if self.skip:  # 在跳过的区域里：只记同名标签的嵌套层数
            if tag == self.skip[-1]:
                self.skip.append(tag)
            return
        if tag in SKIP or a.get("aria-hidden") == "true" or "display:none" in (a.get("style") or "").replace(" ", ""):
            self._flush()
            self.skip.append(tag)
            return
        if tag == "title":
            self.in_title = True
        if tag in ("article", "main"):
            self.main += 1
        if tag in BLOCK:
            self._flush()
            self.cur, self.cur_tag = [], tag

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag == "title":
            self.in_title = False
        if tag in BLOCK:
            self._flush()
        if tag in ("article", "main"):
            self.main = max(0, self.main - 1)

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip and self.cur is not None:
            self.cur.append(data)

    def _flush(self):
        if self.cur is not None:
            text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.cur)).strip()
            if text:
                self.blocks.append((self.cur_tag, self.main, text))
        self.cur = None


def extract(html: str) -> dict:
    """从 HTML 里取标题和正文段落。优先 article / main 里的内容；去掉太短的导航、按钮类碎片。"""
    p = _Extract()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001  网页写得再乱也不要报错，能取多少取多少
        pass
    p._flush()
    blocks = p.blocks
    if sum(len(t) for _, m, t in blocks if m) > 300:
        blocks = [b for b in blocks if b[1]]
    paras, seen = [], set()
    for tag, _, t in blocks:
        short = len(t) < (8 if re.search(r"[一-鿿]", t) else 30)
        if (tag in ("li", "td") and short) or (tag == "p" and len(t) < 4) or t in seen:
            continue
        seen.add(t)
        paras.append(t)
    title = (p.og_title or p.title or "").strip()
    title = re.split(r"\s+[|\-–—_]\s+", title)[0].strip() if len(title) > 20 else title
    body = "\n\n".join(paras)
    zh = len(re.findall(r"[一-鿿]", body))
    return {"title": title[:120] or "网页文章", "body": body[:20000], "lang": "zh" if zh > len(body) * 0.2 else "en"}


def article(url: str) -> dict:
    final, html = fetch(url)
    a = extract(html)
    if len(a["body"]) < 80:
        raise FetchError("没能从这个网页里找到正文（可能需要登录，或内容是图片 / 视频）。可以把文字复制下来，用「贴一段文字」")
    a["url"] = final
    return a
