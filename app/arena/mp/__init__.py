"""联网游戏（docs/DESIGN.md 5.24）：同一个家庭的孩子各用自己的设备，进一个房间一起玩。

分层：
- base.py    每款游戏登记什么：角色、关卡文件、三颗星的条件（MpGame）。加游戏就加一个子类 + 关卡 + 前端玩法。
- rooms.py   房间（开房间、加入、选角色、选关）、队伍纪录（每关最好成绩和星星，打通一关开下一关）、过关、贴纸。
- bus.py     实时消息转发（WebSocket 进来的消息转给房间里的其他人；多进程时走 PostgreSQL LISTEN / NOTIFY）。
- duo.py / bubble.py / snake.py   三款游戏。
出题判分仍然是乐园的 matches.py（每个孩子一局，game=游戏 id）：谁倒下谁答题，题按他自己的水平出。
前端：app/static/play/net.js（连接、插值）、mpshell.js（大厅、房间、复活答题、结算）、<游戏>.js（玩法）。
"""
from . import bubble, duo, snake  # noqa: F401  （登记游戏）
from .base import REGISTRY, MpGame, get, register

__all__ = ["REGISTRY", "MpGame", "get", "register"]
