"""书库：内置公版名著。

- 书单在 content/library.json（书名、作者、级别、建议年级、来源、推荐理由）；还在版权期的好书只列书名（not_pd）。
- 原文由服务器自己下载（python -m app.cli library fetch），整理好存成 data/library/<书的 id>.json：
  {"chapters": [{"title", "paras": [...], "pages": [[起始段, 结束段), ...]}], "n_pages", "words", "source", "fetched_at"}
- 页码是全书连续编号（从 1 开始），孩子的进度、每天读几页都按页算。
"""
import json
import os
import threading
import time
from pathlib import Path

from .. import config
from .fetch import FetchError, fetch_raw
from .text import build_book

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

    def text(self, bid: str) -> dict | None:
        p = self.path(bid)
        if not p.exists():
            return None
        m = p.stat().st_mtime
        hit = self._cache.get(bid)
        if hit and hit[0] == m:
            return hit[1]
        d = json.loads(p.read_text(encoding="utf-8"))
        self._cache[bid] = (m, d)
        return d

    def ready(self, bid: str) -> bool:
        return self.path(bid).exists()

    def shelf(self, lang: str = "") -> list[dict]:
        """有原文的书（孩子书架上能读的）。"""
        out = []
        for b in self.books.values():
            if lang and b["lang"] != lang:
                continue
            t = self.text(b["id"])
            if t:
                out.append({**b, "n_pages": t["n_pages"], "n_chapters": len(t["chapters"]), "words": t["words"]})
        return out

    def status(self) -> list[dict]:
        out = []
        for b in self.books.values():
            t = self.text(b["id"])
            out.append({**b, "ready": bool(t), "n_pages": t["n_pages"] if t else 0,
                        "n_chapters": len(t["chapters"]) if t else 0, "words": t["words"] if t else 0,
                        "fetched_at": t.get("fetched_at", "") if t else "", "error": t.get("error", "") if t else "",
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
    def _save(self, bid: str, raw: str, source: dict) -> dict:
        b = self.books[bid]
        built = build_book(raw, b["lang"], gutenberg=bool(source.get("gutenberg")), split=b.get("split", ""),
                           page_size=int(b.get("page_words") or b.get("page_chars") or 0),
                           convert=(b.get("source") or {}).get("convert", ""))
        built.update(source={k: v for k, v in source.items() if k != "gutenberg"},
                     fetched_at=time.strftime("%Y-%m-%d %H:%M"), title=b["title"])
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = self.path(bid).with_suffix(".tmp")
        tmp.write_text(json.dumps(built, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path(bid))
        return built

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
            if only_missing and self.ready(bid):
                continue
            try:
                d = self.fetch(bid)
                ok += 1
                log(f"  [ OK ] {bid}：{len(d['chapters'])} 章 · {d['n_pages']} 页")
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
                    self.jobs[bid] = f"完成：{len(d['chapters'])} 章 · {d['n_pages']} 页"
                except Exception as e:  # noqa: BLE001
                    self.jobs[bid] = f"失败：{e}"
        threading.Thread(target=run, daemon=True).start()


library = Library().load()
