"""乐园的规则：只在这里写一处，所有游戏共用。

- 经验值（游戏里的能量）：答对得 70–150（期望 100），8% 暴击翻倍；当天赚得太多就减半，防止只刷。
  经验值只在一局里当能量、换装备用，不攒成可以买东西的「积分」，也不进学习打卡。
- 答错冷冻 5 秒；3 秒内连续瞎答，冷冻逐级加长（5 → 8 → 12 秒）。
- 每天能玩多久：家长设基础时长；孩子今天的任务全部做完、点亮新知识点，再奖励几分钟（学习换游戏时间）。
  也可以选「不限时」：不封顶、不倒计时，只看「什么时候可以玩」。
- 什么时候能玩：家长选「随时」「做完一半任务」或「做完全部任务」（默认做完一半；今天没有任务就随时能玩）。
"""
import random
import time
from datetime import datetime

from .. import db, explore
from ..catalog import is_adult

DEFAULT_GAME_MINUTES = 20
UNLIMITED = -1            # 家长选「不限时」：存成 -1
GAME_MINUTE_CHOICES = (0, 10, 20, 30, 45, 60, UNLIMITED)
UNLIMITED_SECONDS = 24 * 3600   # 不限时的孩子，「还剩多少秒」按一整天算，各游戏照常用自己的单局上限
UNLOCK_CHOICES = {"free": "随时都能玩", "half": "做完今天一半的任务后才能玩", "all": "做完今天全部任务后才能玩"}
DEFAULT_UNLOCK = "half"
BONUS_ALL_DONE = 10       # 今天的任务全部做完：多玩 10 分钟
BONUS_PER_LIT = 2         # 今天每点亮一个知识点：多玩 2 分钟
BONUS_LIT_MAX = 6
BONUS_SPRINT_PER = 20     # 冲刺每 20 分多玩 1 分钟
BONUS_SPRINT_MAX = 10

FREEZE_SECONDS = (5, 8, 12)
FAST_MS = 3000
CRIT_RATE = 0.08
DAILY_SOFT_CAP = 5000
PENDING_KEEP_S = 90       # 关掉答题面板再打开，90 秒内还是同一道题（不能换掉难题）
SPECIAL_EVERY = 3         # 连对几题攒一次必杀 / 大招


# ------------------------------------------------------------------ 家长设置

def _settings(kid: dict) -> dict:
    return db.jload(kid.get("settings"), {}) or {}


def game_minutes(kid: dict) -> int:
    """每天能玩几分钟（UNLIMITED = 不限时）。成人学习者默认不玩（不显示乐园），家长明确设置过的以设置为准。"""
    default = 0 if is_adult(kid.get("grade")) else DEFAULT_GAME_MINUTES
    try:
        m = int(_settings(kid).get("game_minutes", default))
        return m if m >= 0 else UNLIMITED
    except (TypeError, ValueError):
        return default


def game_unlock(kid: dict) -> str:
    v = _settings(kid).get("game_unlock", DEFAULT_UNLOCK)
    return v if v in UNLOCK_CHOICES else DEFAULT_UNLOCK


def _save_setting(kid_id: int, key: str, value):
    u = db.one("SELECT settings FROM users WHERE id=?", kid_id)
    st = db.jload(u["settings"], {}) if u else {}
    st[key] = value
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), kid_id)


def set_game_minutes(kid_id: int, minutes) -> None:
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return
    if minutes < 0:
        minutes = UNLIMITED
    else:
        minutes = min((m for m in GAME_MINUTE_CHOICES if m >= 0), key=lambda m: abs(m - minutes))
    _save_setting(kid_id, "game_minutes", minutes)


def set_game_unlock(kid_id: int, rule) -> None:
    if rule in UNLOCK_CHOICES:
        _save_setting(kid_id, "game_unlock", rule)


def public_rooms(kid: dict) -> bool:
    """能不能进全站的公开房间（和别的家庭的孩子一起玩）。家长可以关掉；关掉以后只能进家里的和用密码进的房间。"""
    return bool(_settings(kid).get("public_rooms", True))


def set_public_rooms(kid_id: int, on: bool) -> None:
    _save_setting(kid_id, "public_rooms", bool(on))


# ------------------------------------------------------------------ 今天能不能玩、能玩多久

def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def paused_seconds(st: dict) -> float:
    """这一局暂停了多少秒（还在暂停的算到现在）。"""
    at = st.get("paused_at")
    return (st.get("paused_s") or 0) + (max(0.0, time.time() - at) if at else 0)


def seconds_today(kid_id: int, max_seconds: dict | None = None) -> int:
    day = db.today().isoformat()
    now = _ts(db.now())
    total = 0
    for m in db.q("SELECT game, started_at, ended_at, seconds, data FROM arena_matches WHERE user_id=? AND started_at>=?",
                  kid_id, day):
        if m["ended_at"]:
            total += m["seconds"] or 0
        else:
            cap = (max_seconds or {}).get(m["game"], 600)
            paused = paused_seconds(db.jload(m["data"], {}) or {})
            total += int(min(cap, max(0, (now - _ts(m["started_at"])).total_seconds() - paused)))
    return total


def _tasks_today(kid_id: int) -> tuple[int, int]:
    row = db.one("SELECT plan FROM days WHERE user_id=? AND day=?", kid_id, db.today().isoformat())
    plan = db.jload(row["plan"], []) if row else []
    return sum(1 for t in plan if t.get("done")), len(plan)


def sprint_points(kid_id: int) -> int:
    from .. import sprint   # sprint 用到乐园的出题，这里晚一点导入，避免循环
    return sprint.points_today(kid_id)


def status(kid: dict, max_seconds: dict | None = None) -> dict:
    """今天的游戏状态：基础时长 + 学习奖励、用了多少、还剩多少、是否解锁（还差几项任务）。
    不限时：unlimited=True，不算奖励时间，left 固定是一整天（不倒计时），base 仍为真值表示能玩。"""
    base = game_minutes(kid)
    unlimited = base == UNLIMITED
    done, total = _tasks_today(kid["id"])
    rule = game_unlock(kid)
    need = 0
    if base and total:
        if rule == "half":
            need = max(0, (total + 1) // 2 - done)
        elif rule == "all":
            need = max(0, total - done)
    timed = base and not unlimited
    lit = len(explore.lit_today(kid["id"])) if timed else 0
    bonus_all = BONUS_ALL_DONE if timed and total and done >= total else 0
    bonus_lit = min(BONUS_LIT_MAX, lit * BONUS_PER_LIT) if timed else 0
    bonus_sprint = min(BONUS_SPRINT_MAX, sprint_points(kid["id"]) // BONUS_SPRINT_PER) if timed else 0
    used = seconds_today(kid["id"], max_seconds)
    if unlimited:
        minutes, left = None, UNLIMITED_SECONDS
    else:
        minutes = base + bonus_all + bonus_lit + bonus_sprint
        left = max(0, minutes * 60 - used)
    return {"base": base, "bonus_all": bonus_all, "bonus_lit": bonus_lit, "bonus_sprint": bonus_sprint, "lit": lit, "minutes": minutes,
            "used": used, "left": left, "rule": rule, "rule_name": UNLOCK_CHOICES[rule], "need": need,
            "done": done, "total": total, "unlocked": base != 0 and need == 0,
            "unlimited": unlimited, "can_bonus_all": bool(timed and total and done < total)}


def locked_reason(st: dict) -> str:
    if not st["base"]:
        return "家长设置了暂时不玩游戏。"
    if st["need"]:
        return f"再完成 {st['need']} 项今天的任务就能玩啦！"
    if st["left"] <= 0:
        return "今天的游戏时间用完了，明天再来！" + ("（今天的任务全做完还能多玩 10 分钟）" if st["can_bonus_all"] else "")
    return ""


# ------------------------------------------------------------------ 经验值和冷冻

def roll_xp(rng: random.Random, earned_today: int) -> tuple[int, bool]:
    """答对一题的经验值：70–150，期望 100；8% 暴击翻倍；今天赚得太多就减半。"""
    xp = int(round(rng.triangular(70, 150, 80) / 10) * 10)
    crit = rng.random() < CRIT_RATE
    if crit:
        xp *= 2
    if earned_today >= DAILY_SOFT_CAP:
        xp //= 2
    return xp, crit


def freeze_seconds(fast_wrongs: int, fast: bool) -> int:
    return FREEZE_SECONDS[min(fast_wrongs - 1, len(FREEZE_SECONDS) - 1)] if fast and fast_wrongs > 0 else FREEZE_SECONDS[0]
