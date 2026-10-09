"""文字规整、缓存 key、长文切段。"""
import hashlib
import re
import unicodedata

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})


def normalize(text: str, lang: str = "en") -> str:
    """只做不影响读音的规整，保证同一句话（多个空格、弯引号、换行）得到同一个 key。"""
    t = unicodedata.normalize("NFC", text or "")
    if lang.startswith("en"):
        t = t.translate(_QUOTES)
    t = re.sub(r"\s+", " ", t).strip()
    if lang.startswith("en") and re.fullmatch(r"[A-Za-z][a-z'-]*", t):  # 单个单词不分大小写（Apple = apple）
        t = t.lower()
    return t


def cache_key(text: str, *, provider: str, model: str, voice: str, lang: str, speed: float, fmt: str) -> str:
    raw = "\x1f".join([provider, model, voice, lang, f"{speed:.2f}", fmt, text])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def split_text(text: str, max_chars: int = 400) -> list[str]:
    """把一段话按句子切成不超过 max_chars 的几块（尽量凑满，句子太长再按逗号、最后硬切）。"""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return [text] if text else []
    bits = re.split(r"([.!?。！？；;…][\"'”’」』）)]?\s*)", text)
    sents = [bits[i] + (bits[i + 1] if i + 1 < len(bits) else "") for i in range(0, len(bits), 2)]
    parts, cur = [], ""
    for s in filter(None, sents):
        while len(s) > max_chars:
            cut = max(s.rfind(c, 0, max_chars) for c in ",，、 ")
            cut = cut + 1 if cut > max_chars // 3 else max_chars
            if cur:
                parts.append(cur); cur = ""
            parts.append(s[:cut]); s = s[cut:]
        if cur and len(cur) + len(s) > max_chars:
            parts.append(cur); cur = ""
        cur += s
    if cur:
        parts.append(cur)
    return [p.strip() for p in parts if p.strip()]
