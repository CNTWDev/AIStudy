"""贴纸和角色：只奖励真实发生的事（答对、坚持、点亮知识点、打赢一局），不靠运气，也不能用经验值买。

- 贴纸（STICKERS）：达成条件就自动贴到孩子的贴纸墙上（arena_awards），一张只得一次。
  一半和游戏有关（第一次打赢、连对 10 题），一半和真实学习有关（连续学习 7 天、点亮 10 个知识点），
  这样「学得好」在乐园里也看得见。
- 角色（AVATARS）：几个一开始就有，其余由贴纸解锁；孩子在乐园首页选一个，所有游戏里都用它（users.settings.avatar）。
- 乐园等级：按乐园里累计答对的题数算，只升不降。
前端画角色用 app/static/play/art.js，这里只登记 id、名字和解锁条件。
"""
import math

from .. import db, engine

STICKERS = {
    # key: (图标, 名字, 怎么得)
    "hello": ("👋", "你好乐园", "第一次进乐园玩一局"),
    "first_win": ("🏆", "第一场胜利", "第一次在火柴人大战里打赢电脑"),
    "streak5": ("🔥", "连对 5 题", "一局里连续答对 5 题"),
    "streak10": ("☄️", "连对 10 题", "一局里连续答对 10 题"),
    "focus": ("💎", "超级专注", "一局专注指数 120 以上（至少 8 题），比平时的自己更投入"),
    "sharp": ("🎯", "神准", "一局答对率 90% 以上（至少 8 题）"),
    "calm": ("🧘", "不慌不忙", "一局答了 10 题以上，一次都没有瞎答"),
    "r50": ("🥉", "答对 50 题", "在乐园里累计答对 50 题"),
    "r200": ("🥈", "答对 200 题", "在乐园里累计答对 200 题"),
    "r500": ("🥇", "答对 500 题", "在乐园里累计答对 500 题"),
    "race_win": ("⚡", "闪电冠军", "在闪电赛跑里跑赢「上次的我」"),
    "guard5": ("🏰", "城堡守卫", "在星星守卫里守住第 5 波"),
    "boss": ("🐲", "屠龙勇士", "在星星守卫里打败大怪兽"),
    "done_first": ("✅", "先学后玩", "做完今天全部任务以后来乐园"),
    "days7": ("📅", "坚持一周", "连续学习 7 天"),
    "days30": ("🌈", "坚持一个月", "连续学习 30 天"),
    "lit3": ("🌟", "点亮 3 个", "真正掌握 3 个知识点"),
    "lit10": ("✨", "点亮 10 个", "真正掌握 10 个知识点"),
    "lit30": ("🌌", "点亮 30 个", "真正掌握 30 个知识点"),
    "redo": ("📝", "认真订正", "把一份试卷的每道题都在线重做完"),
    "helper": ("🤝", "出题小帮手", "分享的卷子改编成新题，帮别的同学练习了 10 次"),
}

AVATARS = {
    # id: (名字, 解锁需要的贴纸；None = 一开始就有)
    "mint": ("薄荷", None),
    "sunny": ("小太阳", None),
    "berry": ("莓莓", None),
    "sky": ("天天", None),
    "cap": ("棒球帽", "first_win"),
    "ninja": ("小忍者", "streak10"),
    "cat": ("猫猫", "days7"),
    "astro": ("宇航员", "r200"),
    "wizard": ("魔法师", "lit10"),
    "dino": ("小恐龙", "boss"),
    "crown": ("小国王", "days30"),
    "robot": ("机器人", "r500"),
}
DEFAULT_AVATAR = "mint"


def got(kid_id: int) -> dict[str, str]:
    return {r["key"]: r["created_at"] for r in db.q("SELECT key, created_at FROM arena_awards WHERE user_id=?", kid_id)}


def give(kid_id: int, keys) -> list[dict]:
    """发贴纸（已经有的跳过），返回新得到的。"""
    have = got(kid_id)
    new = []
    for k in keys:
        if k in STICKERS and k not in have:
            db.run("INSERT INTO arena_awards(user_id, key, created_at) VALUES(?,?,?) ON CONFLICT(user_id, key) DO NOTHING",
                   kid_id, k, db.now())
            have[k] = db.now()
            ico, name, how = STICKERS[k]
            new.append({"key": k, "icon": ico, "name": name, "how": how,
                        "avatar": next((a for a, (_, need) in AVATARS.items() if need == k), None)})
    return new


def right_total(kid_id: int) -> int:
    return (db.one("SELECT COALESCE(SUM(correct),0) AS s FROM arena_answers WHERE user_id=?", kid_id) or {}).get("s") or 0


def level(kid_id: int) -> dict:
    """乐园等级：第 n 级需要累计答对 5·n·(n−1) 题（0、10、30、60、100……），越往后越慢。"""
    return level_of(right_total(kid_id))


def level_of(r: int) -> dict:
    n = int((1 + math.sqrt(1 + 0.8 * r)) / 2)
    lo, hi = 5 * n * (n - 1), 5 * (n + 1) * n
    return {"level": n, "right": r, "to_next": hi - r, "pct": round(100 * (r - lo) / max(1, hi - lo))}


def learning_keys(kid_id: int) -> list[str]:
    """和真实学习有关的贴纸：连续学习天数、真正掌握的知识点数。"""
    keys = []
    st = engine.streak(kid_id)
    keys += [k for k, n in (("days7", 7), ("days30", 30)) if st >= n]
    lit = (db.one("SELECT COUNT(*) AS n FROM mastery WHERE user_id=? AND status='mastered'", kid_id) or {}).get("n") or 0
    keys += [k for k, n in (("lit3", 3), ("lit10", 10), ("lit30", 30)) if lit >= n]
    # 奖励认真做完，不奖励上传张数（张数会招来重复、拍不清的卷子）
    for r in db.q("SELECT report FROM papers WHERE user_id=? AND status='done'", kid_id):
        rep = db.jload(r["report"], {}) or {}
        if rep.get("total") and rep.get("answered", 0) >= rep["total"]:
            keys.append("redo")
            break
    from .. import bankflow
    if bankflow.contributed(kid_id)["uses"] >= 10:
        keys.append("helper")
    return keys


def after_match(kid_id: int, game: str, result: str, st: dict, stats: dict, status: dict, focus: dict | None = None) -> list[dict]:
    keys = ["hello"]
    if focus and focus["index"] is not None and focus["answered"] >= 8 and focus["index"] >= 120:
        keys.append("focus")
    if game == "stickman" and result == "win":
        keys.append("first_win")
    if game == "race" and result == "win":
        keys.append("race_win")
    if game == "defense":
        if (stats.get("wave") or 0) >= 5:
            keys.append("guard5")
        if stats.get("boss"):
            keys.append("boss")
    best = st.get("best_streak", 0)
    keys += [k for k, n in (("streak5", 5), ("streak10", 10)) if best >= n]
    answered, right = st.get("answered", 0), st.get("right", 0)
    if answered >= 8 and right / answered >= 0.9:
        keys.append("sharp")
    if answered >= 10 and not st.get("guesses"):
        keys.append("calm")
    total = right_total(kid_id)
    keys += [k for k, n in (("r50", 50), ("r200", 200), ("r500", 500)) if total >= n]
    if status.get("total") and status.get("done", 0) >= status["total"]:
        keys.append("done_first")
    return give(kid_id, keys + learning_keys(kid_id))


def avatar(kid: dict) -> str:
    a = (db.jload(kid.get("settings"), {}) or {}).get("avatar")
    return a if a in AVATARS else DEFAULT_AVATAR


def avatars(kid_id: int) -> list[dict]:
    have = got(kid_id)
    return [{"id": a, "name": name, "need": need, "need_name": STICKERS[need][1] if need else "",
             "need_how": STICKERS[need][2] if need else "", "open": need is None or need in have}
            for a, (name, need) in AVATARS.items()]


def set_avatar(kid_id: int, a: str) -> bool:
    if a not in AVATARS:
        return False
    need = AVATARS[a][1]
    if need and need not in got(kid_id):
        return False
    u = db.one("SELECT settings FROM users WHERE id=?", kid_id)
    st = db.jload(u["settings"], {}) if u else {}
    st["avatar"] = a
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), kid_id)
    return True


def wall(kid_id: int) -> list[dict]:
    have = got(kid_id)
    return [{"key": k, "icon": ico, "name": name, "how": how, "got": k in have, "at": (have.get(k) or "")[:10]}
            for k, (ico, name, how) in STICKERS.items()]


def refresh_learning(kid_id: int) -> list[dict]:
    """打开乐园首页时补发学习类贴纸（学习发生在别的页面）。"""
    return give(kid_id, learning_keys(kid_id))

