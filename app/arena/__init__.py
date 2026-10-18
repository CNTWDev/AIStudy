"""游戏乐园：按每个孩子自己的水平出题的小游戏（详见 docs/DESIGN.md 5.12）。

乐园是一个小小的游戏平台：游戏只写玩法，其余的都由平台提供，单机、两人联网、全家一起玩是同一套（docs/DESIGN.md 5.26）。
分层（从下往上）：
- （能力值 θ 和难度 b 在 app/ability.py：全站共用一个水平，练习和游戏的作答都算。）
- sources.py   题从哪来：数学口算（现场生成）、英语单词和课本术语、题库里的短选择题；答完怎么记进学习记录。
- rules.py     经验值、冷冻、每天能玩多久、学习换游戏时间、什么时候解锁。
- awards.py    贴纸、角色、乐园等级（只奖励真实发生的事）。
- games/       游戏登记：一款游戏一个类（几个人、角色、关卡、星星、贴纸），关卡文件在 games/levels/。
- progress.py  账号档案：每支队伍（一个人或几个人）在每款游戏里打到哪了。
- sessions.py  对局记录：每一局谁、哪一关、结果、用时、星星，还有玩的时候的存档。
- rooms.py     房间（1～N 人）、成员、设备（换设备、刷新接着玩）、开局和报结果。
- bus.py       联网时的实时消息转发（WebSocket，多进程时走 PostgreSQL LISTEN / NOTIFY）。
- matches.py   每个孩子自己的答题对局：出题、判分、个人战报。
- board.py     公平看板（管理后台）。
前端（app/static/play/）：shell.js 是所有游戏共用的外壳（大厅、房间、开局、答题、存档、结算），net.js 是联网连接，
<游戏>.js 只写玩法；答题面板、引擎、角色画法共用。
"""
from .awards import AVATARS, STICKERS, set_avatar
from .board import fairness, kid_band
from ..ability import elo_step, logit, pick_level, prior_b, sigmoid
from .errors import ArenaError
from .games import GAMES
from .matches import answer, end, hub, parent_summary, pause, question, start, status
from .rules import (DAILY_SOFT_CAP, DEFAULT_GAME_MINUTES, FREEZE_SECONDS, GAME_MINUTE_CHOICES, UNLIMITED, UNLOCK_CHOICES,
                    freeze_seconds, game_minutes, game_unlock, locked_reason, public_rooms, roll_xp, set_game_minutes,
                    set_game_unlock, set_public_rooms)
from .sources import SOURCES

__all__ = ["AVATARS", "STICKERS", "set_avatar", "fairness", "kid_band", "elo_step", "logit", "pick_level", "prior_b",
           "sigmoid", "GAMES", "ArenaError", "answer", "end", "hub", "parent_summary", "pause", "question", "start", "status",
           "DAILY_SOFT_CAP", "DEFAULT_GAME_MINUTES", "FREEZE_SECONDS", "GAME_MINUTE_CHOICES", "UNLIMITED", "UNLOCK_CHOICES",
           "freeze_seconds", "game_minutes", "game_unlock", "locked_reason", "public_rooms", "roll_xp", "set_game_minutes", "set_game_unlock",
           "set_public_rooms",
           "SOURCES"]
