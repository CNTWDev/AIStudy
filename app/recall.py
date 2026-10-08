"""复习要作答：卡片不再只让孩子自己点「记得 / 模糊」（全靠自觉），而是出成题、由系统判对错。

- 错题（mistake）：原题原样重做，选项齐全。隔天连续做对 2 次 → 换一道同知识点的「变式题」，
  也做对了才算过关、移出错题本（防止只是背住了这道题的答案）。做错了明天再来。
- 生词 / 短语 / 术语（word / phrase / term）：刚学的「看意思选单词」（四选一），熟一点的「看意思写单词」（拼写）。
- 知识点卡（kp）：从题库里取一道这个知识点的题来做。
- 实在出不了题的（意思还没查到的词、老的错题找不到原题、题库里没题的知识点）才退回自评。
"""
import random
import re
from datetime import timedelta

from . import bank, db, engine, itemtypes
from .catalog import catalog

WORD_KINDS = ("word", "phrase", "term")
PASS_STREAK = 2        # 隔天连续做对几次，换变式题
PASS_NO_VARIANT = 3    # 找不到变式题时，隔天连续做对几次算过关
SPELL_FROM_BOX = 2     # 复习过几轮（box）之后，从四选一升级为拼写
PASSED_DUE = "9999-12-31"
_LATIN = re.compile(r"[A-Za-z][A-Za-z '\-.]*")
_NO_MEANING = "（意思还没查到"


def _extra(c) -> dict:
    return db.jload(c["extra"], {}) or {}


def _item(item_id) -> dict | None:
    r = db.one("SELECT * FROM items WHERE id=?", item_id) if item_id else None
    return bank.row_to_item(r) if r else None


def _meaning(c) -> str:
    b = (c["back"] or "").strip()
    return "" if not b or b.startswith(_NO_MEANING) else b.split("\n")[0][:120]


# ------------------------------------------------------------------ 出题

def quiz(user_id: int, c) -> dict:
    """一张到期的卡 → 怎么考。返回 {mode: item / choice / spell / self, ...}，不含答案。"""
    kind, ex = c["kind"], _extra(c)
    if kind == "mistake":
        variant = bool(ex.get("variant"))
        it = _item(ex.get("variant") or ex.get("item_id"))
        if it:
            return {"mode": "item", "item": {**itemtypes.public(it), "id": it["id"]}, "variant": variant,
                    "last": None if variant else ex.get("my_answer"), "streak": ex.get("streak", 0)}
    elif kind in WORD_KINDS:
        q = _word_quiz(user_id, c)
        if q:
            return q
    elif kind == "kp" and c["kp_id"]:
        it = _kp_item(user_id, c["kp_id"])
        if it:
            return {"mode": "item", "item": {**itemtypes.public(it), "id": it["id"]}}
    return {"mode": "self"}


def _kp_item(user_id: int, kp_id: str) -> dict | None:
    """知识点卡：题库里这个知识点的题，没做过的优先，做错过的其次（不现场让 AI 出题，复习要快）。"""
    rows = [r for r in bank.candidates(user_id, kp_id) if r["type"] in itemtypes.TYPES]
    if not rows:
        return None
    rows.sort(key=lambda r: (r["done"] > 0 and r["ok"] > 0, r["done"] > 0, random.random()))
    return bank.row_to_item(rows[0])


def _distractors(user_id: int, c, latin: bool, n=3) -> list[str]:
    """干扰项：自己生词本里同类的词，不够再从词表里补。"""
    front = c["front"].strip().lower()
    mine = [r["front"] for r in db.q("SELECT front FROM cards WHERE user_id=? AND kind=? AND id<>?", user_id, c["kind"], c["id"])]
    pool = {w.strip() for w in mine if w.strip().lower() != front and bool(_LATIN.fullmatch(w.strip())) == latin}
    if len(pool) < n and latin:
        from .content import content
        for wl in content.word_lists.values():
            pool |= {w["w"] for w in wl.get("words", []) if w["w"].lower() != front and " " not in w["w"]}
            if len(pool) >= 40:
                break
    pool = sorted(pool)
    # 长度相近的更像，选得更认真
    pool.sort(key=lambda w: abs(len(w) - len(front)) + random.random() * 3)
    return pool[:n]


def _word_quiz(user_id: int, c) -> dict | None:
    meaning = _meaning(c)
    if not meaning:
        return None
    front, ex = c["front"].strip(), _extra(c)
    latin = bool(_LATIN.fullmatch(front)) and len(front) <= 40
    hint = " · ".join(x for x in (ex.get("pos", ""), ex.get("zh", "") if ex.get("zh") != meaning else "") if x)
    if latin and c["box"] >= SPELL_FROM_BOX:
        return {"mode": "spell", "q": meaning, "hint": hint, "say": front,
                "shape": f"{len(front)} 个字母 · 首字母 {front[0]}" if " " not in front else f"{len(front.split())} 个词 · 首字母 {front[0]}"}
    others = _distractors(user_id, c, latin)
    if len(others) < 3:
        return {"mode": "spell", "q": meaning, "hint": hint, "say": front,
                "shape": f"{len(front)} 个字母 · 首字母 {front[0]}"} if latin else None
    opts = others + [front]
    random.shuffle(opts)
    return {"mode": "choice", "q": meaning, "hint": hint, "options": opts}


# ------------------------------------------------------------------ 判对错、排期

def answer(user_id: int, card_id: int, body: dict) -> dict:
    c = db.one("SELECT * FROM cards WHERE id=? AND user_id=?", card_id, user_id)
    if not c:
        return {"error": "没有这张卡"}
    if c["kind"] in WORD_KINDS:
        return _answer_word(user_id, c, body)
    item = _item(body.get("item_id"))
    if not item:
        return {"error": "没有这道题"}
    ex = _extra(c)
    if c["kind"] == "mistake":
        if item["id"] not in (ex.get("item_id"), ex.get("variant")):
            return {"error": "这道题不是这张卡的"}
    elif c["kind"] != "kp" or item["id"] not in {r["id"] for r in bank.candidates(user_id, c["kp_id"])}:
        return {"error": "这道题不是这张卡的"}
    reveal = {"answer": itemtypes.display(item), "explain": item.get("explain", ""), "points": item.get("points", []),
              "redo": True}  # 前端：不再显示「已放进错题本」
    if item["type"] == "mcq":
        reveal["answer_index"] = item.get("answer")
    dk = bool(body.get("dont_know"))
    if dk:
        correct = False
    elif itemtypes.of(item).self_rated:
        if "self" not in body:  # 简答题：先写，再看参考答案，再对照要点自己判
            return {"reveal": True, **reveal}
        correct = body["self"] == "ok"
    else:
        correct = bool(itemtypes.check(item, body.get("answer")))
    kp_id = c["kp_id"] or item.get("kp_id")
    if kp_id and catalog.kp(kp_id):  # 计入掌握度（做错不会再生成一张错题卡：同一题干已经有了）
        engine.record_attempt(user_id, item, kp_id, "redo" if c["kind"] == "mistake" else "review", correct,
                              body.get("answer", ""), dont_know=dk, ms=body.get("ms"))
    fmt = itemtypes.fmt(item["type"])
    if c["kind"] != "mistake":
        engine.schedule_card(user_id, c, "good" if correct else "again", fmt=fmt)
        return {"correct": correct, "dont_know": dk, **reveal}
    return {"correct": correct, "dont_know": dk, **reveal, **_after_mistake(user_id, c, ex, item, correct, fmt)}


def _after_mistake(user_id: int, c, ex: dict, item: dict, correct: bool, fmt: str) -> dict:
    today = db.today().isoformat()
    on_variant = item["id"] == ex.get("variant")
    if not correct:
        ex.update(streak=0, streak_day="")
        ex.pop("variant", None)  # 变式题做错了：说明还没真懂，回到原题重新来
        engine.schedule_card(user_id, c, "again", due=db.today() + timedelta(days=1), extra=ex, fmt=fmt)
        return {"next": "tomorrow"}
    if ex.get("streak_day") != today:  # 同一天做对两次只算一次
        ex["streak"] = ex.get("streak", 0) + 1
        ex["streak_day"] = today
    if on_variant:
        ex["passed"] = today
        engine.schedule_card(user_id, c, "good", due=PASSED_DUE, extra=ex, fmt=fmt)
        return {"passed": True}
    if ex["streak"] >= PASS_STREAK:
        v = _variant(user_id, c, ex)
        if v:
            ex["variant"] = v["id"]
            engine.schedule_card(user_id, c, "good", extra=ex, fmt=fmt)
            return {"variant_next": True, "streak": ex["streak"]}
        if ex["streak"] >= PASS_NO_VARIANT:
            ex["passed"] = today
            engine.schedule_card(user_id, c, "good", due=PASSED_DUE, extra=ex, fmt=fmt)
            return {"passed": True}
    engine.schedule_card(user_id, c, "good", extra=ex, fmt=fmt)
    return {"streak": ex["streak"], "need": (PASS_STREAK if c["kp_id"] else PASS_NO_VARIANT) - ex["streak"]}


def _variant(user_id: int, c, ex: dict) -> dict | None:
    """同一知识点的另一道题（不是原题、不是同一组换数字的题）：题库里没有就请 AI 出（出过的存进题库）。"""
    if not c["kp_id"] or not catalog.kp(c["kp_id"]):
        return None
    orig = _item(ex.get("item_id")) or {}
    near = db.one("SELECT near_key FROM items WHERE id=?", orig.get("id")) if orig else None
    for it in engine.items_for(user_id, c["kp_id"], n=3):
        r = db.one("SELECT near_key FROM items WHERE id=?", it["id"])
        if it["id"] != orig.get("id") and not (near and near["near_key"] and r and r["near_key"] == near["near_key"]):
            return it
    return None


def _answer_word(user_id: int, c, body: dict) -> dict:
    given = str(body.get("answer") or "").strip()
    norm = lambda s: " ".join(s.lower().replace("’", "'").split()).strip(".!?。")
    correct = bool(given) and not body.get("dont_know") and norm(given) == norm(c["front"])
    engine.schedule_card(user_id, c, "good" if correct else "again", fmt="recall" if body.get("mode") == "spell" else "choice")
    if c["kp_id"]:
        engine.update_mastery(user_id, c["kp_id"], correct, weight=0.6, source="review",
                              fmt="recall" if body.get("mode") == "spell" else "choice", kind="review")
    ex = _extra(c)
    return {"correct": correct, "answer": c["front"], "meaning": c["back"], "example": ex.get("example", "")}
