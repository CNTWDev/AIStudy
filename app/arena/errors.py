"""乐园里所有「这件事做不了」的错误：带 HTTP 状态码，路由统一转成 {"error": 文字, ...}（app/main.py）。"""


class ArenaError(Exception):
    def __init__(self, msg: str, status: int = 400, **extra):
        super().__init__(msg)
        self.status, self.extra = status, extra
