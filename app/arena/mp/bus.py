"""联网游戏的实时消息转发：一个房间里谁发了消息，转给房间里的其他人。服务器只转发、不模拟游戏。

服务器默认开 2 个进程（install.sh --workers 2），两个孩子的连接可能落在不同进程上，所以：
- PostgreSQL：用数据库自带的 LISTEN / NOTIFY 在进程之间转发（不用另装 Redis）。每个进程一条监听连接、一条发送连接，
  每条消息都绕数据库走一圈（同一进程里的也一样），路径只有一条，简单可靠。延迟几毫秒。
- SQLite（开发、测试，只有一个进程）：直接在进程里转发。
消息是 JSON 文本，NOTIFY 一条最多 8000 字节。前端每条大多只有几十字节（位置、事件），最大的是重连时补发的整个世界（几 KB），超过 6000 的直接丢掉。
"""
import asyncio
import itertools
import json
import logging
import secrets

from ... import db

log = logging.getLogger("aistudy.mp")
CHANNEL = "aistudy_mp"
MAX_MSG = 6000
MAX_NOTIFY = 7900   # PostgreSQL NOTIFY 的上限是 8000 字节（转义以后可能比原文长）
_ids = itertools.count(1)


class Conn:
    """一个 WebSocket 连接：记下它属于哪个事件循环，跨线程投递时用。"""

    def __init__(self, ws, room: int, user_id: int):
        self.ws, self.room, self.user_id, self.id = ws, room, user_id, next(_ids)
        self.loop = asyncio.get_running_loop()

    def send(self, text: str) -> None:
        async def go():
            try:
                await self.ws.send_text(text)
            except Exception:   # 对方刚断开：不管
                pass
        try:
            if asyncio.get_running_loop() is self.loop:
                self.loop.create_task(go())
                return
        except RuntimeError:
            pass
        asyncio.run_coroutine_threadsafe(go(), self.loop)


class LocalBus:
    """同一个进程里转发（SQLite / 单进程）。"""

    def __init__(self):
        self.rooms: dict[int, dict[int, Conn]] = {}

    async def join(self, c: Conn):
        self.rooms.setdefault(c.room, {})[c.id] = c

    async def leave(self, c: Conn):
        self.rooms.get(c.room, {}).pop(c.id, None)
        if not self.rooms.get(c.room):
            self.rooms.pop(c.room, None)

    async def publish(self, room: int, sender: int, text: str):
        self.deliver(room, sender, text)

    def deliver(self, room: int, sender: int, text: str):
        for c in list(self.rooms.get(room, {}).values()):
            if c.id != sender:
                c.send(text)


class PgBus(LocalBus):
    """多进程：发出去走 NOTIFY，每个进程 LISTEN 到以后转给自己这边的连接。"""

    def __init__(self):
        super().__init__()
        self.listener = self.pub = self.task = None
        self.lock = asyncio.Lock()
        self.tag = secrets.token_hex(8)   # 认出自己发的消息（不同进程的连接编号会重复）

    async def _ensure(self):
        if self.task and not self.task.done():
            return
        import psycopg
        await self._close()
        self.listener = await psycopg.AsyncConnection.connect(db._TARGET, autocommit=True)
        await self.listener.execute(f"LISTEN {CHANNEL}")
        self.pub = await psycopg.AsyncConnection.connect(db._TARGET, autocommit=True)
        self.task = asyncio.get_running_loop().create_task(self._listen(self.listener))

    async def _close(self):
        for c in (self.listener, self.pub):
            if c is not None:
                try:
                    await c.close()
                except Exception:
                    pass
        self.listener = self.pub = None

    async def _listen(self, conn):
        try:
            async for n in conn.notifies():
                try:
                    env = json.loads(n.payload)
                    self.deliver(int(env["r"]), env["s"] if env.get("t") == self.tag else 0, env["m"])
                except Exception:
                    log.exception("mp: 坏消息")
        except Exception:
            log.warning("mp: 监听断了，下次有人连进来时重连", exc_info=True)

    async def join(self, c: Conn):
        async with self.lock:
            await self._ensure()
        await super().join(c)

    async def publish(self, room: int, sender: int, text: str):
        payload = json.dumps({"r": room, "s": sender, "t": self.tag, "m": text}, ensure_ascii=False)
        if len(payload.encode()) > MAX_NOTIFY:
            log.warning("mp: 消息太长，丢掉（%d 字节）", len(payload.encode()))
            return
        async with self.lock:
            try:
                await self._ensure()
                await self.pub.execute("SELECT pg_notify(%s, %s)", (CHANNEL, payload))
            except Exception:
                log.warning("mp: 发送失败，重连后再发一次", exc_info=True)
                if self.task:
                    self.task.cancel()
                self.task = None
                await self._ensure()
                await self.pub.execute("SELECT pg_notify(%s, %s)", (CHANNEL, payload))


_local = LocalBus()
_pg: dict = {}


def bus() -> LocalBus:
    """PostgreSQL 每个事件循环一个（数据库连接不能跨循环用；正式运行时一个进程只有一个循环）。"""
    if not db.IS_PG:
        return _local
    loop = asyncio.get_running_loop()
    b = _pg.get(loop)
    if b is None:
        for old in [k for k in _pg if k.is_closed()]:
            _pg.pop(old, None)
        b = _pg[loop] = PgBus()
    return b
