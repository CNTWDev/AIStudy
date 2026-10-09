"""把下载来的原文整理成书：去掉古腾堡的头尾和版权声明、合并折行、切章节、分页。"""
import re

ROMAN = r"[IVXLC]+"
NUM_ZH = r"[一二三四五六七八九十百千零〇两\d]+"
EN_HEAD = re.compile(rf"^\s*(CHAPTER|Chapter|BOOK|Book|STORY|Story|PART|Part)\s+({ROMAN}|\d+|[A-Z][A-Za-z-]+)\b[.:]?\s*(.*)$")
ROMAN_HEAD = re.compile(rf"^\s*({ROMAN})\.?\s*(.*)$")
ZH_HEAD = re.compile(rf"^\s*(第{NUM_ZH}[回章篇节卷])\s*(.*)$")


def strip_gutenberg(raw: str) -> str:
    t = raw.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    m = re.search(r"^\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*\s*$", t, re.M | re.I)
    if m:
        t = t[m.end():]
    m = re.search(r"^\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG", t, re.M | re.I)
    if m:
        t = t[:m.start()]
    t = re.sub(r"^(Produced by|Transcribed by|E-text prepared by).*?\n\n", "", t, flags=re.S | re.M)
    t = re.sub(r"\[Illustration[^\]]*\]", "", t)
    t = re.sub(r"^\s*\[Footnote[^\]]*\]\s*$", "", t, flags=re.M)
    return t


def paragraphs(text: str, lang: str) -> list[str]:
    """空行分段；段内的折行合并（英文加空格，中文直接接上）。"""
    out = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lang == "zh":
            p = "".join(lines)
        else:
            p = " ".join(lines)
            p = re.sub(r"_(.+?)_", r"\1", p)  # 古腾堡用 _斜体_
        p = re.sub(r"\s{2,}", " ", p).strip()
        if p and not re.fullmatch(r"[*\s.·—-]+", p):
            out.append(p)
    return out


def _is_heading(p: str, lang: str, mode: str) -> tuple[bool, str]:
    if len(p) > 90:
        return False, ""
    if lang == "zh":
        m = ZH_HEAD.match(p)
        return (True, p) if m else (False, "")
    m = EN_HEAD.match(p)
    if m:
        return True, p
    if mode in ("roman", "auto2"):
        m = ROMAN_HEAD.match(p)
        if m and (not m.group(2) or m.group(2)[:1].isupper()) and len(p) < 70:
            return True, p
    if mode == "titles":  # 故事集：全大写的短标题，或首字母大写、没有句末标点的短行
        if len(p) < 60 and not re.search(r"[.!?,;:\"”]$", p) and (p.isupper() or re.fullmatch(r"(The |A |An |How |Why |What )?[A-Z][\w'’-]*(\s+[\w'’-]+){0,8}", p)):
            return True, p
    return False, ""


def _split_with(paras: list[str], lang: str, mode: str, min_chars: int) -> list[dict]:
    chs, cur = [], None
    i = 0
    while i < len(paras):
        p = paras[i]
        ok, title = _is_heading(p, lang, mode)
        if ok:
            # 「CHAPTER I.」下一行是短标题时合并成一个标题
            nxt = paras[i + 1] if i + 1 < len(paras) else ""
            if lang == "en" and re.fullmatch(r"\s*(CHAPTER|Chapter|BOOK|PART|STORY)\s+\S+\.?\s*", p) and nxt and len(nxt) < 70 \
                    and not re.search(r"[.!?,;\"”]$", nxt) and not _is_heading(nxt, lang, mode)[0]:
                title = f"{p.rstrip('.')}: {nxt}"
                i += 1
            cur = {"title": _tidy_title(title, lang), "paras": []}
            chs.append(cur)
        elif cur is not None:
            cur["paras"].append(p)
        i += 1
    # 目录里的标题会切出很多空章：去掉正文太短的
    return [c for c in chs if sum(len(x) for x in c["paras"]) >= min_chars]


def _tidy_title(t: str, lang: str) -> str:
    t = t.strip().strip(".")
    if lang == "en" and t.isupper():
        t = t.title().replace("'S ", "'s ").replace("’S ", "’s ")
    return t[:80]


def split_chapters(paras: list[str], lang: str, mode: str = "") -> list[dict]:
    """依次试几种切法，用切出章节最多且合理的那种；都不行就整本当一章（之后按页读）。"""
    total = sum(len(p) for p in paras)
    min_chars = 150 if mode == "titles" else 400
    tries = [mode] if mode else []
    tries += ["chapter", "roman"] if lang == "en" else ["chapter"]
    best = []
    for m in tries:
        chs = _split_with(paras, lang, m, min_chars)
        covered = sum(len(x) for c in chs for x in c["paras"])
        if len(chs) >= 2 and covered >= total * 0.6 and len(chs) > len(best):
            best = chs
        if best and m == mode:
            break
    if not best:
        best = [{"title": "", "paras": paras}]
    return best


def paginate(paras: list[str], lang: str, size: int) -> list[list[int]]:
    """按字数分页，返回每页的 [起始段, 结束段)。一段太长也不拆，宁可那一页长一点。"""
    pages, start, n = [], 0, 0
    for i, p in enumerate(paras):
        w = len(p.split()) if lang == "en" else len(p)
        if n and n + w > size * 1.25:
            pages.append([start, i]); start, n = i, 0
        n += w
    if start < len(paras):
        pages.append([start, len(paras)])
    return pages


def build_book(raw: str, lang: str, *, gutenberg=False, split="", page_size=0, convert="") -> dict:
    text = strip_gutenberg(raw) if gutenberg else raw.replace("\r\n", "\n")
    if convert == "t2s":
        text = to_simplified(text)
    paras = paragraphs(text, lang)
    if not paras:
        raise ValueError("没有正文")
    chs = split_chapters(paras, lang, split)
    size = page_size or (250 if lang == "en" else 600)
    out, n_pages = [], 0
    for c in chs:
        pages = paginate(c["paras"], lang, size)
        out.append({"title": c["title"], "paras": c["paras"], "pages": pages})
        n_pages += len(pages)
    words = sum(len(p.split()) if lang == "en" else len(p) for c in chs for p in c["paras"])
    return {"chapters": out, "n_pages": n_pages, "words": words}


def to_simplified(text: str) -> str:
    try:
        from opencc import OpenCC
    except ImportError:  # 没装就保留原文（繁体）
        return text
    return OpenCC("t2s").convert(text)
