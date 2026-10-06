"""数据库迁移。

migrations/ 目录里按编号放 SQL 文件（0001_xxx.sql、0002_xxx.sql ...），每个文件在一个事务里执行，
执行过的记在 schema_migrations 表里，不会重复执行。以后要改表结构：新增一个编号更大的文件，
不要修改已经发布的旧文件。

  python -m app.cli migrate          # 执行所有未执行的迁移
  python -m app.cli migrate-status   # 查看哪些已执行、哪些待执行
"""
import re
from pathlib import Path

from . import config, db

MIGRATIONS_DIR = config.BASE_DIR / "migrations"
_PG_LOCK_ID = 7_310_442_118  # 防止多个进程同时迁移

_TYPES = {
    "postgres": {"ID": "SERIAL PRIMARY KEY", "FLOAT": "DOUBLE PRECISION"},
    "sqlite": {"ID": "INTEGER PRIMARY KEY AUTOINCREMENT", "FLOAT": "REAL"},
}


def _files() -> list[tuple[str, Path]]:
    out = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = re.match(r"(\d{4})_", p.name)
        if m:
            out.append((m.group(1), p))
    return out


def render(sql: str) -> str:
    types = _TYPES[db.DIALECT]
    return re.sub(r"\{\{(\w+)\}\}", lambda m: types[m.group(1)], sql)


def _ensure_table(t: "db.Tx") -> None:
    t.script("CREATE TABLE IF NOT EXISTS schema_migrations ("
             "version TEXT PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")


def status() -> list[dict]:
    with db.tx() as t:
        _ensure_table(t)
        done = {r["version"]: r["applied_at"] for r in t.q("SELECT version, applied_at FROM schema_migrations")}
    return [{"version": v, "name": p.name, "applied_at": done.get(v)} for v, p in _files()]


def pending() -> list[str]:
    return [s["name"] for s in status() if not s["applied_at"]]


def upgrade(verbose: bool = False) -> list[str]:
    applied = []
    with db.tx() as t:
        if db.IS_PG:
            t.one("SELECT pg_advisory_xact_lock(?) AS ok", _PG_LOCK_ID)
        _ensure_table(t)
        done = {r["version"] for r in t.q("SELECT version FROM schema_migrations")}
        for version, path in _files():
            if version in done:
                continue
            if verbose:
                print("执行迁移", path.name)
            t.script(render(path.read_text(encoding="utf-8")))
            t.run("INSERT INTO schema_migrations(version, name, applied_at) VALUES(?,?,?)", version, path.name, db.now())
            applied.append(path.name)
    return applied
