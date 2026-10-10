"""把下载来的原文整理成书：去掉古腾堡的头尾和版权声明、去掉扉页、合并折行、诗歌保留分行、
全大写的标题和段首改成正常大小写、切章节、分页，最后做一遍质量检查。

切章的顺序：书里有目录（Contents）就按目录在正文里找各章标题，这最可靠；没有目录再按
「CHAPTER I / I. / 1. / 第一回」这类标题的规律切；都不行就整本当一章，按页读。
"""
import re
from difflib import SequenceMatcher

FORMAT = 2  # 整理规则的版本：规则改了就加一，已下载的书会自动按新规则重新整理

ROMAN = r"[IVXLC]+"
WORDNUM = (r"(?:(?:twenty|thirty|forty|fifty)-?)?(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
           r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty)")
ORDINAL = r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|\w+teenth|\w+tieth|\w+-\w+)"
NUM_ZH = r"[一二三四五六七八九十百千零〇两\d]+"
LABEL = r"(?:CHAPTER|Chapter|BOOK|Book|STORY|Story|PART|Part|ADVENTURE|Adventure|STAVE|Stave)"
# 「CHAPTER IV.」「Chapter 4: 标题」「PART ONE—标题」「THE FIRST CHAPTER」
EN_HEAD = re.compile(rf"^\s*{LABEL}\s+({ROMAN}|\d+|{WORDNUM})\b\s*[.:—–-]*\s*(.*)$", re.I)
ORD_HEAD = re.compile(rf"^\s*THE\s+{ORDINAL}\s+(CHAPTER|PART|BOOK)\s*\.?\s*$", re.I)
ROMAN_HEAD = re.compile(rf"^\s*({ROMAN})\s*[.:—–-]?\s*(.*)$")
ROMAN_LABEL = re.compile(rf"^\s*({ROMAN})(?:\s*[.:]\s*|\s{{2,}}|\s*$)(.*)$")   # 罗马数字后面要有点或两个空格：「C」「I Go」不算
NUM_HEAD = re.compile(r"^\s*(\d{1,3})\s*[.:)]?\s*(.*)$")
ZH_HEAD = re.compile(rf"^\s*(第{NUM_ZH}[回章篇节卷部])\s*(.*)$")
TOC_HEAD = re.compile(r"^\s*(table\s+of\s+)?contents\s*[.:]?\s*$", re.I)
# 不是故事正文的「章」：目录、插图目录、录入说明
JUNK_TITLE = re.compile(r"^(contents|table of contents|list of illustrations|illustrations|transcriber'?s? notes?|"
                        r"preparer'?s? notes?|editor'?s? notes?|note on the text|footnotes)$", re.I)
JUNK_PARA = re.compile(r"(project gutenberg|gutenberg\.org|\be-?texts?\b|\be-?books?\b|produced by|transcribed by|proofread|"
                       r"courtesy of|https?://|www\.|copyright|all rights reserved|printed (and bound )?in|"
                       r"first published|published by|illustrations? by|illustrated by|^by\s|^\[?note:)", re.I)
END_PARA = re.compile(r"^\W*(the end|finis|end of [\w\s]+|完|全书完|（完）)\W*$", re.I)
COMMON = set("""a an the and but or so of in on at to by with from as into upon was were is are am be been had has have
did do does could would should will shall may might must can it its he she they we you his her their our my your this that
there here then when what who how why where once upon time day night morning one two three little old long all no not
never very first last got went came saw said lost go come thought knew felt found heard looked looking now just after
before about over under again also only still even ever""".split())
ZH_JUNK = re.compile(r"(公有领域|公有領域|公共领域|公共領域|维基文库|維基文庫|维基百科|維基百科|Public domain|本作品|姊妹计划|姊妹計劃|"
                     r"Project Gutenberg|古登堡|古腾堡|上一篇|下一篇|返回目录|返回目錄)", re.I)
SMALL = {"a", "an", "the", "and", "but", "or", "nor", "for", "of", "in", "on", "at", "to", "by", "with", "from", "as",
         "into", "upon", "than"}


# ================================================================== 去掉头尾

def strip_gutenberg(raw: str) -> str:
    t = _newlines(raw)
    m = re.search(r"^\*{3}\s*START OF (THE|THIS) PROJECT GUTENBERG[^*]{0,400}\*{3}", t, re.M | re.I)
    if m:
        t = t[m.end():]
    else:  # 很老的版本：头上是一大段 SMALL PRINT 许可
        m = re.search(r"\*END\*THE SMALL PRINT.*?\n", t, re.I)
        if m:
            t = t[m.end():]
    m = re.search(r"^\s*(\*{3}\s*END OF (THE|THIS) PROJECT GUTENBERG|End of (the )?Project Gutenberg|"
                  r"\*\*\*END OF)", t, re.M | re.I)
    if m:
        t = t[:m.start()]
    return t


def _newlines(t: str) -> str:
    return t.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")


def clean_raw(raw: str, *, gutenberg=False, fixes=()) -> str:
    t = strip_gutenberg(raw) if gutenberg else _newlines(raw)
    for a, b in fixes:   # 书单里给这本书配的修正（正则）：去掉插图说明、补上古腾堡丢掉的首字下沉字母……
        t = re.sub(a, b, t, flags=re.M)
    t = re.sub(r"\[(Illustration|Ilustration|Frontispiece|Picture|Image)[^\]]*\]", "", t, flags=re.S | re.I)
    t = re.sub(r"\[(Footnote|Transcriber'?s? Note|Note)[^\]]*\]", "", t, flags=re.S | re.I)
    t = re.sub(r"^\s*[_=~]{5,}\s*$", "", t, flags=re.M)   # 分隔线
    t = t.replace("\t", "    ")
    return t


# ================================================================== 分段

def _block_paras(block: str, lang: str) -> str:
    raw_lines = [ln.rstrip() for ln in block.split("\n") if ln.strip()]
    if not raw_lines:
        return ""
    lines = [ln.strip() for ln in raw_lines]
    if lang == "zh":   # 中文常常没有空行，每段开头空两格：按缩进分段，折行直接接上；「第一回」单独成段
        out, head, prev = [], False, ""
        uses_indent = any(re.match(r"[\u3000 ]{2,}|\t", x) for x in raw_lines)
        width = max(len(x) for x in lines)
        for raw_ln, ln in zip(raw_lines, lines):
            is_head = bool(ZH_HEAD.match(ln)) and len(ln) < 60
            if uses_indent:
                new = bool(re.match(r"[\u3000 ]{2,}|\t", raw_ln))
            else:   # \u6ca1\u6709\u7f29\u8fdb\uff1a\u4e0a\u4e00\u884c\u5728\u53e5\u672b\u6807\u70b9\u5904\u63d0\u524d\u7ed3\u675f\uff0c\u5c31\u662f\u65b0\u7684\u4e00\u6bb5\uff08\u4e00\u884c\u4e00\u6bb5\u7684 txt\uff09
                new = bool(re.search(r"[\u3002\uff01\uff1f\u201d\u300d\u300f\u2026\uff1a]$", prev)) and len(prev) < width - 2
            if out and not head and not is_head and not new:
                out[-1] += ln
            else:
                out.append(ln)
            head, prev = is_head, ln
        return "\n\n".join(out)
    if max(len(x) for x in lines) > 150:   # \u4e00\u884c\u5c31\u662f\u4e00\u6bb5\u7684 txt\uff08\u4e0d\u662f\u53e4\u817e\u5821\u90a3\u79cd\u6298\u884c\uff09
        return "\n\n".join(lines)
    indented = sum(1 for ln in raw_lines if re.match(r"\s{2,}", ln))
    verse = len(lines) >= 2 and (indented >= len(lines) * 0.6 or max(len(x) for x in lines[:-1]) < 48)
    if verse:  # 诗、歌、信：保留分行
        p = "\n".join(re.sub(r"\s{2,}", " ", x) for x in lines)
    else:
        p = " ".join(lines)
    return p


def tidy_en(p: str) -> str:
    p = re.sub(r"_(.+?)_", r"\1", p, flags=re.S)       # 古腾堡用 _斜体_
    p = re.sub(r"=(\S.*?\S|\S)=", r"\1", p)             # =粗体=
    p = re.sub(r"[ \t]*--+[ \t]*", "—", p)               # -- 是破折号
    p = re.sub(r"[  ]{2,}", " ", p)
    p = re.sub(r" +([,;:!?])", r"\1", p)
    return _fix_caps(p).strip()


def _fix_caps(p: str) -> str:
    """整段全大写 → 句子大小写；段首几个全大写的词（老书的首字装饰，ONCE upon a time）→ 首字母大写。"""
    letters = [c for c in p if c.isalpha()]
    if len(letters) >= 25 and sum(c.isupper() for c in letters) > 0.8 * len(letters):
        return _sentence_case(p)
    m = re.match(r"^([\"'“‘(\[]*)((?:[A-Z][A-Z'’.-]+|[A-Z])(?:[ ,]+(?:[A-Z][A-Z'’.-]+|I)){0,3})(?=[\s,;:!?—-]+[\"'“‘]?[A-Za-z]*[a-z])", p)
    if m and len(m.group(2)) >= 2 and not re.fullmatch(rf"{ROMAN}|I", m.group(2)):
        words = re.split(r"(\W+)", m.group(2))
        lower_rest = words[0].lower() in COMMON or words[0] == "I"
        out = []
        for i, w in enumerate(words):
            if not w or not w[0].isalpha() or w == "I":
                out.append(w)
            elif i == 0:
                out.append(w[:1] + w[1:].lower())
            else:   # 第一个词是常用词（THE WIND / I WAS）后面就小写；不是（SQUIRE TRELAWNEY）多半是名字，首字母大写
                out.append(w.lower() if lower_rest or w.lower() in COMMON else w[:1] + w[1:].lower())
        p = p[:m.start(2)] + "".join(out) + p[m.end(2):]
    return p


def _sentence_case(p: str) -> str:
    s = p.lower()
    s = re.sub(r"(^|[.!?]\s+|[\"“‘(]\s*|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), s)
    s = re.sub(r"\bi\b", "I", s)
    s = re.sub(r"\b(mr|mrs|dr|st)\.\s+([a-z])", lambda m: m.group(1).capitalize() + ". " + m.group(2).upper(), s)
    return s


def smart_title(t: str, lang: str = "en") -> str:
    t = re.sub(r"\s+", " ", t).strip().strip(".:—–- ").strip()
    if lang != "en" or not t:
        return t[:80]
    t = tidy_en(t) if "--" in t or "_" in t else t
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) > 0.8 * len(letters):
        words = t.split(" ")
        out = []
        for i, w in enumerate(words):
            lw = w.lower()
            core = re.sub(r"\W", "", w)
            if re.fullmatch(ROMAN, core) and len(core) > 1:
                out.append(w)
            elif i and lw.strip("\"'“‘(") in SMALL:
                out.append(lw)
            else:
                out.append(re.sub(r"[a-z]", lambda m: m.group(0).upper(), lw, count=1))
        t = " ".join(out)
    return t[:80]


def paragraphs(text: str, lang: str) -> list[str]:
    """空行分段；段内的折行合并（英文加空格，中文直接接上），诗歌保留分行。"""
    out = []
    for block in re.split(r"\n\s*\n", text):
        got = _block_paras(block, lang)
        for p in got.split("\n\n"):
            if lang == "en":
                p = tidy_en(p)
            else:   # 中文：去掉缩进和多余空格（夹着英文单词的空格留着）；标题里的全角空格换成一个空格
                p = p.strip(" \u3000")
                p = re.sub(r"[ \u3000]+", " " if ZH_HEAD.match(p) else "", p) if not re.search(r"[A-Za-z] [A-Za-z]", p) \
                    else re.sub(r"\s{2,}", " ", p)
            if p and not re.fullmatch(r"[*\s.·—–_~-]+", p) and not (lang == "zh" and ZH_JUNK.search(p) and len(p) < 400):
                out.append(p)
    return out


def _is_prose(p: str, lang: str) -> bool:
    if lang == "zh":
        return len(p) >= 30 and bool(re.search(r"[。！？，]", p)) and not JUNK_PARA.search(p)
    words = p.split()
    lower = sum(c.islower() for c in p)
    return len(words) >= 8 and lower > 0.6 * sum(c.isalpha() for c in p) and not JUNK_PARA.search(p)


def drop_front(paras: list[str], lang: str) -> list[str]:
    """去掉扉页：书名、作者、出版社、献词、录入说明……一直到第一段真正的正文。"""
    for i, p in enumerate(paras[:60]):
        if _is_prose(p, lang):
            return paras[i:]
    return paras


def drop_back(paras: list[str]) -> list[str]:
    while paras and (END_PARA.match(paras[-1]) or JUNK_PARA.search(paras[-1]) and len(paras[-1]) < 300):
        paras = paras[:-1]
    return paras


# ================================================================== 切章：按目录

def _key(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


def _same(a: str, b: str) -> bool:
    """目录和正文的写法常有一点出入（fireman / firemen）：短的要完全一样，长的差一两个字母也算。"""
    if a == b:
        return True
    if len(b) < 8 or abs(len(a) - len(b)) > 3:
        return False
    sm = SequenceMatcher(None, a, b)
    return sm.quick_ratio() >= 0.9 and sm.ratio() >= 0.9


def _strip_label(s: str) -> tuple[str, str]:
    """「CHAPTER IV. The Shadow」→ ("CHAPTER IV", "The Shadow")；没有编号就返回 ("", 原文)。"""
    s = s.strip()
    for rx in (EN_HEAD, ROMAN_LABEL, NUM_HEAD):
        m = rx.match(s)
        if m:
            rest = m.group(m.lastindex).strip()
            if rx is ROMAN_LABEL and rest and not re.match(r"[\W]*[A-Z“\"']", rest):
                continue   # 「I went…」不是罗马数字标题
            if rx is NUM_HEAD and rest and not re.match(r"[\W]*[A-Z“\"']", rest):
                continue
            return s[:len(s) - len(rest)].strip(" .:—–-"), rest
    m = ORD_HEAD.match(s)
    if m:
        return s, ""
    m = re.match(rf"^({WORDNUM})(?:\s{{2,}}|[.:]\s+|\s*$)(.*)$", s, re.I)   # 「ONE  PLAYING PILGRIMS」
    if m:
        return m.group(1), m.group(2).strip()
    return "", s


def _toc_entries(lines: list[str]) -> tuple[list[str], int] | None:
    limit = min(len(lines), max(400, len(lines) // 5))
    start = next((i for i in range(limit) if TOC_HEAD.match(lines[i])), None)
    if start is None:  # 没写「Contents」的目录：开头一串「I. 标题」「II. 标题」……
        numbered = re.compile(rf"^\s*(({LABEL}\s+)?({ROMAN}|\d{{1,3}})[.:)]?)\s+[\"“']?[A-Z]")
        for i in range(limit):
            run = [j for j in range(i, min(i + 8, limit)) if numbered.match(lines[j])]
            if len(run) >= 4 and run[0] == i and run[3] - i <= 7:
                start = i - 1
                break
        if start is None:
            return None
    entries, j, blanks, prev_indent = [], start + 1, 0, 0
    while j < len(lines):
        s = lines[j].strip()
        if not s:
            blanks += 1
            if blanks >= 3 and entries:
                break
            j += 1
            continue
        if len(s) > 110 or (len(s.split()) > 14 and re.search(r"[.!?]\s*$", s) and not re.search(r"\.\s*\.", s)):
            break   # 进入正文了
        indent = len(lines[j]) - len(lines[j].lstrip())
        cont = blanks == 0 and entries and indent > prev_indent and not _strip_label(s)[0] and \
            (re.search(r"[:,;—-]$", entries[-1]) or s[:1].islower())
        prev_indent = indent
        s = re.sub(r"(\s*\.\s*){2,}\s*\d*\s*$", "", s)        # 点线 + 页码
        s = re.sub(r"\s{2,}(\d+|[ivxlc]+)\s*$", "", s)          # 页码
        s = re.sub(r"^\s*page\s*$", "", s, flags=re.I)
        if cont:
            entries[-1] += " " + s
        elif s:
            entries.append(s)
        blanks = 0
        j += 1
    return (entries, j) if len(entries) >= 2 else None


MARKER = re.compile(rf"^\s*({LABEL}\b.{{0,50}}|{ROMAN}\.?|\d{{1,3}}\.?|THE\s+{ORDINAL}\s+(CHAPTER|PART|BOOK)\.?)\s*$", re.I)


def _split_by_toc(lines: list[str], lang: str) -> list[dict] | None:
    got = _toc_entries(lines)
    if not got:
        return None
    entries, pos = got
    found = []   # (起始行, 正文开始行, 目录里的写法, 正文里的写法)
    useful = 0
    for e in entries:
        lab, rest = _strip_label(e)
        want = _key(rest) or _key(e)
        if not want or (not rest and re.fullmatch(rf"{LABEL}", lab or "", re.I)):
            continue
        if not rest and re.match(r"^\s*(PART|Part|BOOK|Book)\b", e):
            continue  # 「PART ONE」这种分部标记，不当一章
        useful += 1
        for i in range(pos, len(lines)):
            s = lines[i].strip()
            if not s or len(s) > 120 or (i and lines[i - 1].strip() and not MARKER.match(lines[i - 1])):
                continue
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            cands = [(s, i + 1)]
            if nxt and len(nxt) < 90:
                cands.append((s + " " + nxt, i + 2))
            hit = None
            for text, end in cands:
                l2, r2 = _strip_label(text)
                if _same(_key(r2) or _key(text), want) or _key(text) in (want, _key(e)):
                    hit = (text, end)
                    break
            if hit:
                begin = i
                k = i - 1   # 往回吃掉标题前面的「1」「CHAPTER I」「THE FIRST CHAPTER」「PART ONE—…」
                while k >= pos - 1 and k >= 0:
                    if not lines[k].strip():
                        k -= 1
                        continue
                    if MARKER.match(lines[k]) and len(lines[k].strip()) < 70 and begin - k < 8:
                        begin = k
                        k -= 1
                        continue
                    break
                found.append((begin, hit[1], e, hit[0]))
                pos = hit[1]
                break
    if useful < 2 or len(found) < max(2, useful * 0.6):
        return None
    chs = []
    for n, (begin, body, e, seen) in enumerate(found):
        stop = found[n + 1][0] if n + 1 < len(found) else len(lines)
        paras = paragraphs("\n".join(lines[body:stop]), lang)
        chs.append({"title": _pick_title(e, seen, lang), "paras": paras})
    return chs


def _pick_title(toc: str, seen: str, lang: str) -> str:
    l1, r1 = _strip_label(toc)
    l2, r2 = _strip_label(seen)
    if _key(seen) == _key(r1):   # 「I Go to Bristol」：正文标题本身就是目录里去掉编号的部分
        l2, r2 = "", seen
    rest = r2 if r2 and not r2.isupper() else (r1 if r1 and not r1.isupper() else (r2 or r1))
    return smart_title(rest or l2 or l1 or toc, lang)


# ================================================================== 切章：按标题规律

def _heading(p: str, lang: str, mode: str) -> tuple[bool, str, str]:
    """返回 (是不是标题, 编号部分, 标题文字)。"""
    if len(p) > 90 or "\n" in p:
        return False, "", ""
    if lang == "zh":
        m = ZH_HEAD.match(p)
        return (True, m.group(1), m.group(2)) if m else (False, "", "")
    if mode == "chapter":
        m = EN_HEAD.match(p)
        if m:
            return True, p[:m.start(2)].strip(" .:—–-"), m.group(2)
        if ORD_HEAD.match(p):
            return True, p, ""
        if re.fullmatch(r"(CONCLUSION|EPILOGUE|PROLOGUE|PREFACE|INTRODUCTION|FOREWORD|AFTERWORD)\.?", p, re.I):
            return True, "", p
    if mode == "roman":
        m = ROMAN_HEAD.match(p)
        if m and len(p) < 80 and not re.search(r"[,;]$", p) and (
                not m.group(2) or (re.match(r"[\"“']?[A-Z]", m.group(2)) and p[len(m.group(1)):][:1] in ".:—–-")):
            return True, m.group(1), m.group(2)
    if mode == "number":
        m = NUM_HEAD.match(p)
        if m and int(m.group(1)) < 200 and (not m.group(2) or (m.group(2)[:1].isupper() and len(p) < 80)) and not re.search(r"[,;]$", p):
            return True, m.group(1), m.group(2)
    if mode == "titles":  # 故事集：全大写的短标题，或首字母大写、没有句末标点的短行
        if len(p) < 60 and not re.search(r"[.!?,;:\"”’]$", p) and len(p.split()) <= 9 and \
                (p.isupper() or re.fullmatch(r"[A-Z][\w'’-]*(\s+[\w'’-]+){0,8}", p) and sum(w[:1].isupper() for w in p.split()) >= len(p.split()) / 2):
            return True, "", p
    return False, "", ""


def _split_with(paras: list[str], lang: str, mode: str, min_chars: int) -> list[dict]:
    chs, cur = [], None
    i = 0
    while i < len(paras):
        p = paras[i]
        ok, lab, title = _heading(p, lang, mode)
        if ok and not JUNK_TITLE.match(title.strip(" .")):
            # 「CHAPTER I.」下一段是短标题时合并
            nxt = paras[i + 1] if i + 1 < len(paras) else ""
            if not title and nxt and len(nxt) < 80 and "\n" not in nxt and not re.search(r"[.!?,;\"”]$", nxt) \
                    and not _heading(nxt, lang, mode)[0]:
                title = nxt
                i += 1
            cur = {"title": smart_title(p if lang == "zh" else (title or lab or p), lang), "paras": []}
            chs.append(cur)
        elif ok:
            cur = None   # 目录、插图目录：后面的内容丢掉，直到下一章
        elif cur is not None:
            cur["paras"].append(p)
        i += 1
    # 目录里的标题会切出很多空章：去掉正文太短的
    return [c for c in chs if sum(len(x) for x in c["paras"]) >= min_chars]


INNER_HEAD = re.compile(rf"^\s*(CHAPTER|Chapter|STORY|Story|ADVENTURE|Adventure|STAVE|Stave)\s+({ROMAN}|\d+|{WORDNUM})\b\s*[.:—–-]*\s*(.*)$")


def _refine(ch: dict, lang: str) -> list[dict]:
    out = [{"title": ch["title"], "paras": []}]
    for i, p in enumerate(ch["paras"]):
        m = INNER_HEAD.match(p) if len(p) < 90 and "\n" not in p else None
        if m and i > 0:
            out.append({"title": smart_title(m.group(3) or p, lang), "paras": []})
        else:
            out[-1]["paras"].append(p)
    return out


def split_chapters(paras: list[str], lang: str, mode: str = "") -> list[dict]:
    """依次试几种切法，用切出章节最多且合理的那种；都不行就整本当一章（之后按页读）。"""
    total = sum(len(p) for p in paras)
    min_chars = 150 if mode == "titles" else 400
    tries = [mode] if mode else []
    tries += ["chapter", "roman", "number"] if lang == "en" else ["chapter"]
    best = []
    for m in tries:
        chs = _split_with(paras, lang, m, min_chars)
        covered = sum(len(x) for c in chs for x in c["paras"])
        if len(chs) >= 2 and covered >= total * 0.6 and len(chs) > len(best):
            best = chs
        if best and m == mode:
            break
    if not best:
        best = [{"title": "", "paras": drop_front(paras, lang)}]
    return best


# ================================================================== 成书

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


def _finish(chs: list[dict], lang: str) -> list[dict]:
    out = []
    for c in chs:
        if JUNK_TITLE.match(c["title"] or "-"):
            continue
        paras = [p for p in drop_back(c["paras"]) if not re.fullmatch(rf"({ROMAN}|\d{{1,3}})\.?", p)]
        if paras and lang == "en":
            paras[0] = _fix_caps(paras[0])
        if not paras:
            continue
        if out and sum(len(p) for p in paras) < 200 and not out[-1].get("_short"):
            out[-1]["paras"] += paras  # 太短的「章」（分部标题页之类）并到上一章
            continue
        out.append({"title": c["title"], "paras": paras})
    return out


def build_book(raw: str, lang: str, *, gutenberg=False, split="", page_size=0, convert="", fixes=()) -> dict:
    text = clean_raw(raw, gutenberg=gutenberg, fixes=fixes)
    if convert == "t2s":
        text = to_simplified(text)
    lines = text.split("\n")
    toc = None if split == "none" or lang != "en" else _split_by_toc(lines, lang)
    paras = paragraphs(text, lang)
    if not paras:
        raise ValueError("没有正文")
    if toc:   # 按目录切；目录不全时，某一章里还藏着「CHAPTER X」标题的，再切开
        chs = [x for c in toc for x in _refine(c, lang)]
    elif split == "none":
        chs = [{"title": "", "paras": drop_front(paras, lang)}]
    else:
        chs = split_chapters(paras, lang, split)
    chs = _finish(chs, lang)
    if not chs:
        raise ValueError("没有正文")
    size = page_size or (250 if lang == "en" else 600)
    out, n_pages = [], 0
    for c in chs:
        pages = paginate(c["paras"], lang, size)
        out.append({"title": c["title"], "paras": c["paras"], "pages": pages})
        n_pages += len(pages)
    words = sum(len(p.split()) if lang == "en" else len(p) for c in chs for p in c["paras"])
    book = {"chapters": out, "n_pages": n_pages, "words": words, "format": FORMAT}
    book["problems"] = check(book, lang)
    return book


def check(book: dict, lang: str, min_words: int = 0) -> list[str]:
    """质量检查。返回的问题里以「!」开头的是严重问题：这本书不给孩子看。"""
    probs = []
    paras = [p for c in book["chapters"] for p in c["paras"]]
    alltext = "\n".join(paras)
    need = min_words or (600 if lang == "en" else 800)
    if book["words"] < need:
        probs.append(f"!正文太短（{book['words']} {'词' if lang == 'en' else '字'}），可能没下载到正文")
    if re.search(r"<(div|span|p|a|table|html)\b|&(amp|nbsp|lt|gt);", alltext[:200000]):
        probs.append("!正文里有网页代码，下载到的可能是网页而不是原文")
    if lang == "zh" and len(re.findall(r"[一-鿿]", alltext)) < len(alltext) * 0.5:
        probs.append("!中文书里汉字太少，可能下载错了")
    if lang == "en" and re.search(r"[一-鿿]{20}", alltext):
        probs.append("!英文书里有大段中文，可能下载错了")
    junk = sum(1 for p in paras if re.search(r"project gutenberg|gutenberg\.org", p, re.I))
    if junk:
        probs.append(f"还有 {junk} 段古腾堡的说明文字")
    caps = sum(1 for p in paras if len(p) > 40 and sum(ch.isupper() for ch in p) > 0.6 * max(1, sum(ch.isalpha() for ch in p)))
    if caps > max(3, len(paras) * 0.02):
        probs.append(f"有 {caps} 段全是大写字母")
    if len(book["chapters"]) == 1 and book["n_pages"] > 40:
        probs.append("没切出章节，整本是一章（按页读不受影响）")
    return probs


def to_simplified(text: str) -> str:
    try:
        from opencc import OpenCC
    except ImportError:  # 没装就保留原文（繁体）
        return text
    return OpenCC("t2s").convert(text)
