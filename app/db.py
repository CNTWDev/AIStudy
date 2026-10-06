"""数据库访问层。

生产用 PostgreSQL（DATABASE_URL=postgresql://...），本地开发和自动测试可以用 SQLite
（DATABASE_URL=sqlite:///data/aistudy.db）。业务代码统一写 `?` 占位符和两种数据库都支持的 SQL，
这里负责转换。表结构只由 migrations/ 目录里的迁移脚本维护（见 app/migrate.py）。
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config


def _parse_url(url: str) -> tuple[str, str]:
    if url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
        return "postgres", url.replace("postgresql+psycopg://", "postgresql://", 1)
    if url.startswith("sqlite:///"):
        p = Path(url[len("sqlite:///"):])
        if not p.is_absolute():
            p = config.BASE_DIR / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return "sqlite", str(p)
    raise RuntimeError(f"不认识的 DATABASE_URL：{url}（应以 postgresql:// 或 sqlite:/// 开头）")


DIALECT, _TARGET = _parse_url(config.DATABASE_URL)
IS_PG = DIALECT == "postgres"


# ------------------------------------------------------------------ 连接

class _Row(dict):
    """统一的行对象：既能 row["col"]，也能 dict(row)。"""


if IS_PG:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    _pool: "ConnectionPool | None" = None
    _pool_lock = threading.Lock()

    def _get_pool() -> "ConnectionPool":
        global _pool
        if _pool is None:
            with _pool_lock:
                if _pool is None:
                    _pool = ConnectionPool(_TARGET, min_size=1, max_size=config.DB_POOL_SIZE,
                                           kwargs={"row_factory": dict_row}, open=True,
                                           check=ConnectionPool.check_connection)
        return _pool

    @contextmanager
    def _connection():
        with _get_pool().connection() as c:   # 退出时自动 commit；出异常自动 rollback
            yield c

    def _sql(sql: str) -> str:
        return sql.replace("%", "%%").replace("?", "%s")

    def close() -> None:
        global _pool
        if _pool is not None:
            _pool.close()
            _pool = None
else:
    _local = threading.local()

    def _sqlite_conn() -> sqlite3.Connection:
        c = getattr(_local, "conn", None)
        if c is None:
            c = sqlite3.connect(_TARGET, check_same_thread=False, timeout=30)
            c.row_factory = lambda cur, row: _Row(zip([d[0] for d in cur.description], row))
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            _local.conn = c
        return c

    @contextmanager
    def _connection():
        c = _sqlite_conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise

    def _sql(sql: str) -> str:
        return sql

    def close() -> None:
        c = getattr(_local, "conn", None)
        if c is not None:
            c.close()
            _local.conn = None


class Tx:
    """一个事务里的多条语句：with db.tx() as t: t.run(...); t.one(...)"""

    def __init__(self, c):
        self.c = c

    def q(self, sql: str, *args) -> list:
        return [_Row(r) for r in self.c.execute(_sql(sql), args).fetchall()]

    def one(self, sql: str, *args):
        r = self.c.execute(_sql(sql), args).fetchone()
        return _Row(r) if r is not None else None

    def run(self, sql: str, *args) -> int:
        return self.c.execute(_sql(sql), args).rowcount

    def insert(self, sql: str, *args) -> int:
        return self.c.execute(_sql(sql + " RETURNING id"), args).fetchone()["id"]

    def script(self, sql: str) -> None:
        if IS_PG:
            self.c.execute(sql)
        else:
            self.c.executescript(sql)


@contextmanager
def tx():
    with _connection() as c:
        yield Tx(c)


def q(sql: str, *args) -> list:
    with tx() as t:
        return t.q(sql, *args)


def one(sql: str, *args):
    with tx() as t:
        return t.one(sql, *args)


def run(sql: str, *args) -> int:
    """执行 UPDATE / DELETE / 不需要返回 id 的 INSERT，返回影响行数。"""
    with tx() as t:
        return t.run(sql, *args)


def insert(sql: str, *args) -> int:
    """执行 INSERT，返回新行的 id（表必须有自增 id 列）。"""
    with tx() as t:
        return t.insert(sql, *args)


def greatest(a: str, b: str) -> str:
    """两个值取大的 SQL 表达式（PostgreSQL 叫 GREATEST，SQLite 叫 MAX）。"""
    return f"GREATEST({a}, {b})" if IS_PG else f"MAX({a}, {b})"


def init() -> None:
    """应用启动时调用：执行尚未执行的数据库迁移。"""
    from . import migrate
    migrate.upgrade()


# ------------------------------------------------------------------ 工具

def now() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).isoformat(timespec="seconds")


def today() -> date:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def jload(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def jdump(o) -> str:
    return json.dumps(o, ensure_ascii=False)
