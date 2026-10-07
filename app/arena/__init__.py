"""游戏乐园：按每个孩子自己的水平出题的小游戏（详见 docs/DESIGN.md 5.12）。

分层（从下往上）：
- fair.py     能力值 θ 和难度 b（IRT / Elo 在线校准）、按目标答对率选级别。只管数学。
- sources.py  题从哪来：数学口算（现场生成）、英语单词和课本术语、题库里的短选择题；答完怎么记进学习记录。
- rules.py    经验值、冷冻、每天能玩多久、学习换游戏时间、什么时候解锁。
- awards.py   贴纸、角色、乐园等级（只奖励真实发生的事）。
- matches.py  一局的流程：开局 → 出题 / 作答 → 结束（战报、贴纸）；登记有哪些游戏（GAMES）。
- board.py    公平看板（管理后台）。
游戏前端（app/static/play/）只写玩法，答题面板、引擎、角色画法共用。加一款游戏：写玩法 JS，在 GAMES 里登记目标答对率。
"""
from .awards import AVATARS, STICKERS, set_avatar
from .board import fairness, kid_band
from .fair import elo_step, logit, pick_level, prior_b, sigmoid
from .matches import GAMES, ArenaError, answer, end, hub, parent_summary, question, start, status
from .rules import (DAILY_SOFT_CAP, DEFAULT_GAME_MINUTES, FREEZE_SECONDS, GAME_MINUTE_CHOICES, UNLOCK_CHOICES,
                    freeze_seconds, game_minutes, game_unlock, locked_reason, roll_xp, set_game_minutes, set_game_unlock)
from .sources import SOURCES, available as question_sources

__all__ = ["AVATARS", "STICKERS", "set_avatar", "fairness", "kid_band", "elo_step", "logit", "pick_level", "prior_b",
           "sigmoid", "GAMES", "ArenaError", "answer", "end", "hub", "parent_summary", "question", "start", "status",
           "DAILY_SOFT_CAP", "DEFAULT_GAME_MINUTES", "FREEZE_SECONDS", "GAME_MINUTE_CHOICES", "UNLOCK_CHOICES",
           "freeze_seconds", "game_minutes", "game_unlock", "locked_reason", "roll_xp", "set_game_minutes", "set_game_unlock",
           "SOURCES", "question_sources"]
