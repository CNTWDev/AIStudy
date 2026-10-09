"""索引：key → 一条记录（文字、语言、声音、语速、文件、状态、播放次数）。

用的项目实现 Index 这几个方法即可（AIStudy 用自己的数据库，见 app/tts_web.py）；
这里自带两个现成的：SQLiteIndex（单独一个 sqlite 文件）和 MemoryIndex（测试用）。
记录字段：key text lang voice model speed chars fmt status(pending/ready/failed) bytes error
          hits created_by created_at last_played_at
"""
import sqlite3
import threading
import time
from typing import Protocol


class Index(Protocol):
    def get(self, key: str) -> dict | None: ...
    def add(self, rec: dict) -> None: ...          # key 已存在时忽略
    def update(self, key: str, **fields) -> None: ...
    def hit(self, key: str) -> None: ...            # 播放次数 +1、记下最后播放时间
    def generated_chars(self, since: float, user=None) -> int: ...  # since 之后新生成（非 pending）的字数


class MemoryIndex:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.lock = threading.Lock()

    def get(self, key):
        r = self.rows.get(key)
        return dict(r) if r else None

    def add(self, rec):
        with self.lock:
            self.rows.setdefault(rec["key"], dict({"hits": 0, "status": "pending", "created_at": time.time()}, **rec))

    def update(self, key, **fields):
        with self.lock:
            if key in self.rows:
                self.rows[key].update(fields)

    def hit(self, key):
        with self.lock:
            if key in self.rows:
                self.rows[key]["hits"] = self.rows[key].get("hits", 0) + 1
                self.rows[key]["last_played_at"] = time.time()

    def generated_chars(self, since, user=None):
        return sum(r.get("chars", 0) for r in self.rows.values() if r.get("generated_at", 0) >= since
                   and (user is None or r.get("generated_by") == user))


_SCHEMA = """CREATE TABLE IF NOT EXISTS tts_clips(
  key TEXT PRIMARY KEY, text TEXT NOT NULL, lang TEXT, voice TEXT, model TEXT, speed REAL, chars INTEGER DEFAULT 0,
  fmt TEXT, status TEXT DEFAULT 'pending', bytes INTEGER DEFAULT 0, error TEXT DEFAULT '', hits INTEGER DEFAULT 0,
  created_by TEXT, created_at REAL, generated_by TEXT, generated_at REAL, last_played_at REAL)"""
_COLS = {"text", "lang", "voice", "model", "speed", "chars", "fmt", "status", "bytes", "error", "hits", "created_by",
         "created_at", "generated_by", "generated_at", "last_played_at"}


class SQLiteIndex:
    """独立使用时的索引（一个 sqlite 文件）。"""

    def __init__(self, path: str):
        self.path = str(path)
        with self._c() as c:
            c.execute(_SCHEMA)

    def _c(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def get(self, key):
        with self._c() as c:
            r = c.execute("SELECT * FROM tts_clips WHERE key=?", (key,)).fetchone()
        return dict(r) if r else None

    def add(self, rec):
        rec = {k: v for k, v in rec.items() if k in _COLS or k == "key"}
        rec.setdefault("created_at", time.time())
        with self._c() as c:
            c.execute(f"INSERT OR IGNORE INTO tts_clips({','.join(rec)}) VALUES({','.join('?' * len(rec))})",
                      tuple(rec.values()))

    def update(self, key, **fields):
        fields = {k: v for k, v in fields.items() if k in _COLS}
        if fields:
            with self._c() as c:
                c.execute(f"UPDATE tts_clips SET {','.join(k + '=?' for k in fields)} WHERE key=?",
                          (*fields.values(), key))

    def hit(self, key):
        with self._c() as c:
            c.execute("UPDATE tts_clips SET hits=hits+1, last_played_at=? WHERE key=?", (time.time(), key))

    def generated_chars(self, since, user=None):
        sql, args = "SELECT COALESCE(SUM(chars),0) FROM tts_clips WHERE generated_at>=?", [since]
        if user is not None:
            sql += " AND generated_by=?"; args.append(str(user))
        with self._c() as c:
            return int(c.execute(sql, args).fetchone()[0])
