"""书库：内置公版名著。

- 书单在 content/library.json（书名、作者、级别、建议年级、来源、推荐理由）；还在版权期的好书只列书名（not_pd）。
- 原文由服务器自己下载（python -m app.cli library fetch），整理好存成 data/library/<书的 id>.json：
  {"chapters": [{"title", "paras": [...], "pages": [[起始段, 结束段), ...]}], "n_pages", "words", "source", "fetched_at"}
- 页码是全书连续编号（从 1 开始），孩子的进度、每天读几页都按页算。
- 下载到的原文另存一份在 data/library/raw/<id>.txt：整理规则（text.FORMAT）升级后直接用它重新整理，不用重新下载。
- 整理完会做质量检查（text.check）。有严重问题（下载失败、只拿到网页、正文太短）的书孩子那边不显示，只在管理后台看得到。
"""
import json
import os
import threading
import time
from pathlib import Path

from .. import config
from .fetch import FetchError, fetch_raw
from .text import FORMAT, build_book

CATALOG = config.BASE_DIR / "content" / "library.json"
DATA = Path(os.environ.get("LIBRARY_DIR", config.DATA_DIR / "library"))


class Library:
    def __init__(self):
        self.books: dict[str, dict] = {}
        self.not_pd: list[dict] = []
        self.levels: dict[str, str] = {}
        self._cache: dict[str, tuple[float, dict]] = {}
        self.jobs: dict[str, str] = {}   # 正在下载的书：id → 状态文字

    def load(self):
        d = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {}
        self.books = {b["id"]: b for b in d.get("books", []) if b.get("id") and b.get("title")}
        self.not_pd = d.get("not_pd", [])
        self.levels = d.get("levels", {})
        self._cache.clear()
        return self

    # ---------------------------------------------------------------- 原文
    def path(self, bid: str) -> Path:
        return DATA / f"{bid}.json"

    def raw_path(self, bid: str) -> Path:
        return DATA / "raw" / f"{bid}.txt"

    def text(self, bid: str) -> dict | None:
        p = self.path(bid)
        if not p.exists():
            return None
        m = p.stat().st_mtime
        hit = self._cache.get(bid)
        if hit and hit[0] == m:
            return hit[1]
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("format", 1) < FORMAT and bid in self.books and self.raw_path(bid).exists():
            try:  # 整理规则升级了：用存着的原文重新整理
                return self.rebuild(bid)
            except Exception:  # noqa: BLE001  重新整理失败就先用旧的
                pass
        self._cache[bid] = (m, d)
        return d

    def ready(self, bid: str) -> bool:
        """下载过（不管好不好）。"""
        return self.path(bid).exists()

    def available(self, bid: str) -> bool:
        """孩子能读：下载好了，而且没有严重问题。"""
        t = self.text(bid)
        return bool(t) and bid in self.books and not severe(t)

    def outdated(self, bid: str) -> bool:
        """用旧规则整理的、又没存原文的书：要重新下载一次才能用上新排版。"""
        t = self.text(bid)
        return bool(t) and t.get("format", 1) < FORMAT

    def shelf(self, lang: str = "") -> list[dict]:
        """孩子书架上能读的书。下载失败、内容不对的不放上来。"""
        out = []
        for b in self.books.values():
            if lang and b["lang"] != lang:
                continue
            t = self.text(b["id"])
            if t and not severe(t):
                out.append({**b, "n_pages": t["n_pages"], "n_chapters": len(t["chapters"]), "words": t["words"]})
        return out

    def status(self) -> list[dict]:
        out = []
        for b in self.books.values():
            t = self.text(b["id"])
            out.append({**b, "ready": bool(t), "n_pages": t["n_pages"] if t else 0,
                        "n_chapters": len(t["chapters"]) if t else 0, "words": t["words"] if t else 0,
                        "fetched_at": t.get("fetched_at", "") if t else "", "problems": (t or {}).get("problems", []),
                        "ok": bool(t) and not severe(t), "outdated": bool(t) and t.get("format", 1) < FORMAT,
                        "src": (t or {}).get("source", {}), "job": self.jobs.get(b["id"], ""),
                        "chapters": [c["title"] for c in t["chapters"]][:200] if t else []})
        return out

    def page(self, bid: str, n: int) -> dict | None:
        """第 n 页（全书连续编号，从 1 开始）：所在章、这页的段落、是不是章的第一页 / 最后一页。"""
        t = self.text(bid)
        if not t or n < 1:
            return None
        k = n
        for ci, c in enumerate(t["chapters"]):
            if k <= len(c["pages"]):
                a, b = c["pages"][k - 1]
                return {"n": n, "total": t["n_pages"], "chapter": ci + 1, "n_chapters": len(t["chapters"]),
                        "chapter_title": c["title"] or f"第 {ci + 1} 部分", "paras": c["paras"][a:b],
                        "first": k == 1, "last": k == len(c["pages"]), "page_in_chapter": k, "chapter_pages": len(c["pages"])}
            k -= len(c["pages"])
        return None

    def chapter_start(self, bid: str, ch: int) -> int:
        t = self.text(bid)
        return 1 + sum(len(c["pages"]) for c in t["chapters"][:ch - 1]) if t else 1

    def chapter_text(self, bid: str, ch: int, limit: int = 8000) -> str:
        t = self.text(bid)
        if not t or not 1 <= ch <= len(t["chapters"]):
            return ""
        return "\n\n".join(t["chapters"][ch - 1]["paras"])[:limit]

    # ---------------------------------------------------------------- 下载 / 上传
    def _build(self, bid: str, raw: str, gutenberg: bool) -> dict:
        b = self.books[bid]
        built = build_book(raw, b["lang"], gutenberg=gutenberg, split=b.get("split", ""),
                           page_size=int(b.get("page_words") or b.get("page_chars") or 0),
                           convert=(b.get("source") or {}).get("convert", ""), fixes=b.get("fixes") or ())
        if b.get("min_words"):
            from .text import check
            built["problems"] = check(built, b["lang"], int(b["min_words"]))
        return built

    def _write(self, bid: str, built: dict):
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = self.path(bid).with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(built, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path(bid))
        self._cache.pop(bid, None)

    def _save(self, bid: str, raw: str, source: dict) -> dict:
        b = self.books[bid]
        gut = bool(source.get("gutenberg"))
        built = self._build(bid, raw, gut)
        built.update(source={k: v for k, v in source.items() if k != "gutenberg"}, gutenberg=gut,
                     fetched_at=time.strftime("%Y-%m-%d %H:%M"), title=b["title"])
        rp = self.raw_path(bid)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.with_suffix(".tmp").write_text(raw, encoding="utf-8")
        rp.with_suffix(".tmp").replace(rp)
        self._write(bid, built)
        return built

    def rebuild(self, bid: str) -> dict:
        """用存着的原文按现在的规则重新整理（不用重新下载）。"""
        old = json.loads(self.path(bid).read_text(encoding="utf-8")) if self.path(bid).exists() else {}
        raw = self.raw_path(bid).read_text(encoding="utf-8")
        gut = old.get("gutenberg", (old.get("source") or {}).get("type") == "gutenberg" or "PROJECT GUTENBERG" in raw[:20000].upper())
        built = self._build(bid, raw, gut)
        built.update({k: old[k] for k in ("source", "fetched_at") if k in old}, gutenberg=gut, title=self.books[bid]["title"])
        self._write(bid, built)
        return built

    def rebuild_all(self, log=print) -> tuple[int, int]:
        ok = bad = 0
        for bid in self.books:
            if not self.raw_path(bid).exists():
                continue
            try:
                d = self.rebuild(bid)
                ok += 1
                log(f"  [ OK ] {bid}：{len(d['chapters'])} 章 · {d['n_pages']} 页" + _probs(d))
            except Exception as e:  # noqa: BLE001
                bad += 1
                log(f"  [FAIL] {bid}：{e}")
        return ok, bad

    def fetch(self, bid: str) -> dict:
        b = self.books.get(bid)
        if not b:
            raise FetchError("书单里没有这本书")
        if not b.get("pd", True):
            raise FetchError("这本书还在版权期，不下载原文")
        raw, src = fetch_raw(b)
        return self._save(bid, raw, src)

    def import_text(self, bid: str, raw: str, note: str = "管理员上传") -> dict:
        if bid not in self.books:
            raise FetchError("书单里没有这本书")
        if not raw.strip():
            raise FetchError("文件是空的")
        return self._save(bid, raw, {"type": "upload", "note": note,
                                     "gutenberg": "PROJECT GUTENBERG" in raw[:20000].upper()})

    def fetch_all(self, only_missing=True, ids=None, log=print) -> tuple[int, int]:
        ok = bad = 0
        for bid in ids or list(self.books):
            if only_missing and self.available(bid) and not self.outdated(bid):
                continue   # 已经好好的；没下载、下载坏了、旧规则整理又没存原文的，都重新下
            if not self.books.get(bid, {}).get("pd", True):
                continue
            try:
                d = self.fetch(bid)
                ok += 1
                log(f"  [ OK ] {bid}：{len(d['chapters'])} 章 · {d['n_pages']} 页" + _probs(d))
            except Exception as e:  # noqa: BLE001  一本失败不影响别的
                bad += 1
                log(f"  [FAIL] {bid}：{e}")
        return ok, bad

    def fetch_background(self, ids: list[str]):
        def run():
            for bid in ids:
                self.jobs[bid] = "正在下载…"
                try:
                    d = self.fetch(bid)
                    self.jobs[bid] = f"完成：{len(d['chapters'])} 章 · {d['n_pages']} 页" + _probs(d)
                except Exception as e:  # noqa: BLE001
                    self.jobs[bid] = f"失败：{e}"
        threading.Thread(target=run, daemon=True).start()


def severe(t: dict) -> bool:
    return any(p.startswith("!") for p in t.get("problems", []))


def _probs(d: dict) -> str:
    return ("  ⚠ " + "；".join(p.lstrip("!") for p in d["problems"])) if d.get("problems") else ""


library = Library().load()
