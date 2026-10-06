"""学习内容库：书单（中文名著 / 英文分级读物）、分年级词表、每日计划模板。

数据在 content/*.json，可以直接编辑或增加；这里只负责读取，格式不全时尽量容错。
"""
import json
import re

from . import config

CONTENT_DIR = config.BASE_DIR / "content"


def _load(name: str) -> dict:
    p = CONTENT_DIR / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _grade_key(g: str) -> int:
    m = re.search(r"\d+", g or "")
    return int(m.group()) if m else 99


class Content:
    def __init__(self):
        self.books: dict[str, dict] = {}       # id -> book（中文 + 英文）
        self.zh_books: list[dict] = []
        self.en_levels: list[dict] = []
        self.word_lists: dict[str, dict] = {}
        self.plans: list[dict] = []
        self.for_kids: dict = {}

    def load(self):
        self.__init__()
        zh = _load("books_zh.json")
        for b in zh.get("books", []):
            if not b.get("id") or not b.get("title"):
                continue
            b = dict(b, lang="zh")
            b.setdefault("units", [])
            b["total_units"] = len(b["units"]) or int(b.get("total_units") or 0)
            b.setdefault("unit_name", "章")
            self.books[b["id"]] = b
            self.zh_books.append(b)
        self.zh_books.sort(key=lambda b: (_grade_key((b.get("grades") or ["G99"])[0]), b.get("type") != "required"))
        en = _load("books_en.json")
        for lv in en.get("levels", []):
            lv = dict(lv)
            books = []
            for i, b in enumerate(lv.get("books", [])):
                if not b.get("title"):
                    continue
                bid = b.get("id") or f"en-{lv.get('id', 'x').lower()}-{i + 1}"
                b = dict(b, id=bid, lang="en", level=lv.get("id"))
                b.setdefault("units", [])
                b["total_units"] = len(b["units"]) or int(b.get("total_units") or 0)
                b.setdefault("unit_name", "章")
                self.books[bid] = b
                books.append(b)
            lv["books"] = books
            self.en_levels.append(lv)
        self.for_kids = en.get("for_kids", {})
        for wl in _load("words_en.json").get("lists", []):
            if wl.get("id") and wl.get("words"):
                self.word_lists[wl["id"]] = wl
        self.plans = _load("plans.json").get("templates", [])
        return self

    def book(self, bid: str) -> dict | None:
        return self.books.get(bid)

    def zh_by_grade(self) -> list[tuple[str, list[dict]]]:
        groups: dict[str, list] = {}
        for b in self.zh_books:
            g = (b.get("grades") or ["其他"])[0]
            groups.setdefault(g, []).append(b)
        return sorted(groups.items(), key=lambda kv: _grade_key(kv[0]))


content = Content()
