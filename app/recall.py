"""复习要作答：卡片不再只让孩子自己点「记得 / 模糊」（全靠自觉），而是出成题、由系统判对错。

- 错题（mistake）：做错的原题只再做一次（第二天，选项齐全、标出上次选的），为的是改掉这一个错；
  之后都换「变式题」——同一个知识点、孩子没做过的题（换情境、换问法，不是换了数字的同一道）：
  隔 2 天、再隔 4 天各一道，在不同的日子做对 2 道就过关。点了「还不会」的不做原题（答案当时已经看过了），直接从变式题开始。
  做错了明天再来（换一道新的变式题）。过关以后 RECHECK_DAYS 天再用一道新的变式题回头看：还会就真的掌握了（不再出现）；
  忘了就重新回到错题本里练。同一道题做对一次证据就拿到了，再做练的是记住答案（docs/DESIGN.md 5.46）。
  变式题打开错题本时在后台备好（prepare_variants）；还没备好的卡今天先不出（mode=later），下次打开再做。
- 生词 / 短语 / 术语（word / phrase / term）：单词包里今天的新词第一次出现是一张学习卡（learn：先猜意思 → 揭晓、听读音、看例句），
  不当场考：隔开几个别的词以后再回来认一次（check：听读音、看英文选意思）。马上考只是在考刚才那几秒的印象；
  隔开了还认得出才是真的从记忆里取出来了（docs/DESIGN.md 5.44）。前几次复习按 1/2/4/7 天（YOUNG_STEPS），
  一次比一次难：「看意思选单词」（四选一）/ 放回句子里（补洞，有例句才出）→「看意思写单词」（拼写）→
  和「听音写单词」（听写，不给意思）、自己把词拼进句子里轮换着来（app/words.py）；之后交给记忆模型排期。
  要拼的题前几轮用字母块（这个词的字母 + 几个容易混的），再往后自己打，打不出来也能要字母块（_pad）。
  同一个词一次只考一个方向，两个方向分在不同的日子。
- 知识点卡（kp）：从题库里取一道这个知识点、孩子没做过的题来做（做对过的题不再出）。
- 实在出不了题的（意思还没查到的词、老的错题找不到原题、题库里没题的知识点）才退回自评。
"""
import logging
import random
import re
import threading
from datetime import timedelta

from . import bank, db, engine, evidence, itemtypes, llm, words
from .catalog import catalog

WORD_KINDS = ("word", "phrase", "term")
PASS_STREAK = 2        # 变式题在不同的日子做对几道算过关
PASS_NO_VARIANT = 3    # 出不了变式题时（没有知识点的老错题、没配置 AI）：原题隔天做对几次算过关
VARIANT_GAPS = (2, 4)  # 原题改对以后隔几天出第一道变式题；变式题做对第 1 道以后隔几天出第 2 道
LATER_MAX_DAYS = 3     # 变式题一直备不出来（AI 出错）：卡片过期这么多天以后，退回做原题 / 自评，不让它一直挂着
SPELL_FROM_BOX = 2     # 复习过几轮（box）之后，从四选一升级为拼写
TYPE_FROM_BOX = 5      # 拼写、听写、拼进句子：这之前用字母块拼（词的字母 + 几个容易混的），之后自己打；打的时候也能要字母块
DICTATE_FROM_BOX = 3   # 再往后，拼写和听写轮换（box 为奇数时听写）
PASSED_DUE = "9999-12-31"
RECHECK_DAYS = 21      # 过关后隔多久回头看一眼
YOUNG_STEPS = (1, 2, 4, 7)   # 单词前几次答对后隔几天再来（box 0→1 隔 1 天，1→2 隔 2 天……）；之后按记忆模型
_LATIN = re.compile(r"[A-Za-z][A-Za-z '\-.]*")
_NO_MEANING = "（意思还没查到"
log = logging.getLogger(__name__)


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
    """一张到期的卡 → 怎么考。返回 {mode: item / choice / spell / dictate / self, ...}，不含答案。"""
    kind, ex = c["kind"], _extra(c)
    if kind == "mistake":
        it, variant = _item(ex.get("item_id")), False
        if _on_variants(c, ex):
            v = _variant(user_id, c, ex)
            if v:
                it, variant = v, True
            elif llm.enabled() and not _overdue(c):
                return {"mode": "later"}   # 变式题后台还在出：今天先做别的，不拿原题凑
        if it:
            return {"mode": "item", "item": {**itemtypes.public(it), "id": it["id"]}, "variant": variant,
                    "recheck": bool(ex.get("passed")), "last": None if variant else ex.get("my_answer"), "streak": ex.get("streak", 0)}
    elif kind in WORD_KINDS:
        q = _word_quiz(user_id, c)
        if q:
            return q
    elif kind == "kp" and c["kp_id"]:
        it = _kp_item(user_id, c["kp_id"])
        if it:
            return {"mode": "item", "item": {**itemtypes.public(it), "id": it["id"]}}
        if llm.enabled() and not _overdue(c) and catalog.kp(c["kp_id"]):
            return {"mode": "later"}   # 这个点的题都做对过了：后台出新题，下次再考
    return {"mode": "self"}


def _overdue(c) -> bool:
    return (c["due"] or "") < (db.today() - timedelta(days=LATER_MAX_DAYS)).isoformat()


def _judged(r) -> bool:
    return r["type"] in itemtypes.TYPES and itemtypes.of(r["type"]).judge != "open"


def _kp_item(user_id: int, kp_id: str) -> dict | None:
    """知识点卡：题库里这个知识点孩子没做过的题，其次是做错过、还没做对的（不现场让 AI 出题，复习要快；
    缺题由 prepare_variants 在后台出）。做对过的题不再出；没配置 AI 的部署出不了新题，才拿它们来用。"""
    rows = [r for r in bank.candidates(user_id, kp_id) if _judged(r)]
    if llm.enabled():
        rows = [r for r in rows if not bank.retired(r)]
    if not rows:
        return None
    rows.sort(key=lambda r: (bank.retired(r), r["done"] > 0 or bool(r["gdone"]), random.random()))
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


CLOZE_BOX = 1          # 复习过一轮以后，有例句的词放回句子里选（补洞）
CLOZE_SPELL_FROM = 4   # 再往后，拼写、听写之外也要自己把词拼进句子里
USE_FROM = 4           # 再往后，每隔一轮考一道用法题（app/words.py 的用法卡）


def _grade(user_id: int) -> str:
    u = db.one("SELECT grade FROM users WHERE id=?", user_id)
    return (u and u["grade"]) or ""


def _cloze(user_id: int, c, spell=False) -> dict | None:
    front, ex = c["front"].strip(), _extra(c)
    it = words.cloze_item({"w": front, "zh": _meaning(c), "pos": ex.get("pos", "")}, words.band(_grade(user_id)), random.Random(),
                          words.pool_for(user_id, ex.get("list")), spell=spell)
    if not it:
        return None
    if spell:
        return _pad(c, {"mode": "cloze_spell", "q": it["q"], "hint": it["hint"], "shape": f"{len(front)} 个字母"})
    return {"mode": "cloze", "q": it["q"], "options": it["options"]}


def _meanings(user_id: int, c, meaning: str, n: int = 3) -> list[str]:
    """几个意思（含正确的），打乱：从同一个包里别的词的中文里挑干扰项。"""
    front, ex = c["front"].strip().lower(), _extra(c)
    others = list({w["zh"] for w in words.pool_for(user_id, ex.get("list")) if w.get("zh") and w["zh"] != meaning and w["w"].lower() != front})
    random.shuffle(others)
    opts = others[:n - 1] + [meaning]
    random.shuffle(opts)
    return opts if len(opts) >= n else []


def _learn(user_id: int, c) -> dict:
    """新词第一次见：先猜意思（有例句就看着句子猜），再揭晓（意思、读音、例句）。猜错不算：猜是为了让后面的揭晓记得更牢
    （Richland 等 2009）。揭晓完不当场考：check 是隔开几个别的词以后回来认的那一下（听读音、看英文选意思，换一组干扰项），
    算数的是它。认意思出不了（同包的词太少）退回看意思选单词；都出不了，看过就算学了，明天再考。"""
    front, ex, meaning = c["front"].strip(), _extra(c), _meaning(c)
    got = words.examples([front], words.band(_grade(user_id))).get(front.lower()) or []
    sample = got[0] if got else None
    opts = _meanings(user_id, c, meaning, 4)
    check = {"mode": "meaning", "q": front, "say": front, "options": opts} if opts else None
    if not check:
        q = _word_quiz(user_id, c, plain=True)
        check = q if q and q["mode"] == "choice" else None
    grade = _grade(user_id)
    return {"mode": "learn", "word": front, "meaning": meaning, "pos": ex.get("pos", ""), "guess": _meanings(user_id, c, meaning),
            "example": {"en": words._MARK.sub(lambda m: m.group(1), sample["en"]), "mark": front, "zh": sample.get("zh", "")} if sample else None,
            "check": check, "usage": words.usage_view(front, words.band(grade), grade), "known": _known_check(user_id, c),
            "line": ex.get("line", ""), "root": ex.get("root", "")}


def _known_check(user_id: int, c) -> dict | None:
    """学习卡上点「我认识」：马上出一道更难的（看中文拼出来；拼不了的看中文选英文）。对了就算认识，不当新词学。"""
    front, meaning = c["front"].strip(), _meaning(c)
    if not meaning:
        return None
    if front.isalpha() and 2 <= len(front) <= 14:
        return _pad(c, {"mode": "spell", "q": meaning, "hint": _extra(c).get("pos", ""), "say": front,
                        "shape": f"{len(front)} 个字母 · 首字母 {front[0]}"})
    q = _word_quiz(user_id, c, plain=True)
    return q if q and q["mode"] == "choice" else None


def push_quiz(user_id: int, c) -> dict | None:
    """抓词里把学过的词往上推一级：按这个词现在到了哪一级（words.ladder），出比它高一级的题。
    认得 → 看意思选单词（或放回句子里选）；想得起 → 拼出来；拼得出 → 用法题（选搭配、句型、找错词），没有就自己拼进句子里。"""
    front = c["front"].strip()
    if not _meaning(c) or not _LATIN.fullmatch(front):
        return None
    lv = words.level_of(user_id, front)
    q = None
    if lv >= 4:
        q = _use(user_id, c) or (_cloze(user_id, c, spell=True) if front.isalpha() else None)
    elif lv >= 2 and front.isalpha():
        q = _pad(c, {"mode": "spell", "q": _meaning(c), "hint": _extra(c).get("pos", ""), "say": front,
                     "shape": f"{len(front)} 个字母 · 首字母 {front[0]}"})
    q = q or _cloze(user_id, c) or _word_quiz(user_id, c, plain=True)
    return {**q, "push": 1} if q else None


def _use(user_id: int, c) -> dict | None:
    """用法题（用法卡里写好的）：选搭配 / 句型补全 / 找错词。"""
    it = words.use_item(c["front"].strip(), words.band(_grade(user_id)), random.Random())
    if not it:
        return None
    opts = list(it["options"])
    random.shuffle(opts)
    return {"mode": "use", "kind": it["kind"], "q": it["q"], "options": opts}


def _pad(c, q: dict) -> dict:
    """要自己拼的题（拼写、听写、拼进句子）带一盘字母块（letters）：tiles 为真时直接用字母块拼（前几轮），
    否则自己打，想不起来可以要字母块。用了字母块拼对记成 tiles（「想得起」），自己打对才算「拼得出」。"""
    front = c["front"].strip()
    if front.isalpha() and len(front) <= 14:
        q.update(letters=words.letter_pad(front, random.Random(), tiles=True), tiles=c["box"] < TYPE_FROM_BOX)
    return q


def _word_quiz(user_id: int, c, plain: bool = False) -> dict | None:
    meaning = _meaning(c)
    if not meaning:
        return None
    front, ex = c["front"].strip(), _extra(c)
    latin = bool(_LATIN.fullmatch(front)) and len(front) <= 40
    if latin and not plain:
        if ex.get("new") and not c["reviews"]:
            return _learn(user_id, c)
        if ex.get("check") and not c["reviews"]:   # 往前补的词（抓词里的「认一认」）：认得就算认识，不认得再学
            opts = _meanings(user_id, c, meaning, 4)
            if opts:
                return {"mode": "meaning", "q": front, "say": front, "options": opts, "probe": 1}
        if c["box"] >= USE_FROM and not c["box"] % 4:   # 拼得出以后：隔一轮考一次会不会用（用法题），没有就照常
            q = _use(user_id, c)
            if q:
                return q
        q = (_cloze(user_id, c) if c["box"] == CLOZE_BOX else
             _cloze(user_id, c, spell=True) if c["box"] >= CLOZE_SPELL_FROM and not c["box"] % 2 and front.isalpha() else None)
        if q:
            return q
    hint = " · ".join(x for x in (ex.get("pos", ""), ex.get("zh", "") if ex.get("zh") != meaning else "") if x)
    if latin and c["box"] >= SPELL_FROM_BOX and not plain:
        shape = f"{len(front)} 个字母" if " " not in front else f"{len(front.split())} 个词"
        if c["box"] >= DICTATE_FROM_BOX and c["box"] % 2:  # 听写：只听读音，不给意思和首字母
            return _pad(c, {"mode": "dictate", "say": front, "shape": shape})
        return _pad(c, {"mode": "spell", "q": meaning, "hint": hint, "say": front, "shape": f"{shape} · 首字母 {front[0]}"})
    others = _distractors(user_id, c, latin)
    if len(others) < 3:
        return _pad(c, {"mode": "spell", "q": meaning, "hint": hint, "say": front,
                        "shape": f"{len(front)} 个字母 · 首字母 {front[0]}"}) if latin else None
    opts = others + [front]
    random.shuffle(opts)
    return {"mode": "choice", "q": meaning, "hint": hint, "options": opts}


# ------------------------------------------------------------------ 判对错、排期

def answer(user_id: int, card_id: int, body: dict) -> dict:
    card = db.one("SELECT * FROM cards WHERE id=? AND user_id=?", card_id, user_id)
    item = _item(body.get("item_id")) if card and card["kind"] == "mistake" else None
    if item and itemtypes.of(item).judge == "open" and item["id"] == _extra(card).get("item_id"):
        return _discuss_card(user_id, card, item, body)
    return _answer_command(user_id, card_id, body)


def _discuss_card(user_id: int, card, item: dict, body: dict) -> dict:
    """错题卡上的题现在是思辨题（以前按简答题判错进了错题本）：没有对错，不该留在错题本。
    这次照思辨题的方式想一遍（AI 陪着想，app/discuss.py），想完就把这张卡拿掉。"""
    from . import discuss
    kp_id = card["kp_id"] or item.get("kp_id")
    u = db.one("SELECT grade FROM users WHERE id=?", user_id)
    res = discuss.handle(user_id, item, kp_id, body, u["grade"] if u else "")
    if res.get("done"):
        db.run("DELETE FROM cards WHERE id=? AND user_id=?", card["id"], user_id)
    return {**res, "redo": True}


# ------------------------------------------------------------------ 变式题提前准备

_prep: dict[int, threading.Thread] = {}
_prep_lock = threading.Lock()


def _on_variants(c, ex: dict) -> bool:
    """这张错题卡现在该做变式题了：原题已经改过一次，或者当时点的是「还不会」，或者是过关后的回头看。
    没挂知识点的老错题出不了变式题，一直做原题。"""
    if not c["kp_id"] or not catalog.kp(c["kp_id"]):
        return False
    return bool(ex.get("fixed") or ex.get("dont_know") or ex.get("my_answer") == DONT_KNOW or ex.get("passed"))


DONT_KNOW = "（还不会）"   # engine.record_attempt 记在错题卡上的「上次的答案」


def _needs_item(user_id: int, c) -> bool:
    """这张卡（这次或下次）要出的题，题库里还没有：错题卡缺变式题（还在改原题的也先备上，下次就要用），
    知识点卡缺没做过的题。"""
    if c["kind"] == "mistake":
        return bool(c["kp_id"]) and bool(catalog.kp(c["kp_id"])) and _variant(user_id, c, _extra(c)) is None
    return c["kind"] == "kp" and bool(c["kp_id"]) and bool(catalog.kp(c["kp_id"])) and _kp_item(user_id, c["kp_id"]) is None


def _ensure_variant(user_id: int, c) -> None:
    """题库里还没有能出的题，才请 AI 出题（存进题库，以后别的孩子也能用）。已经有了就什么都不做。
    原题那一组（换了数字的同一道）不算，出的题要换个样子。"""
    if not llm.enabled() or not _needs_item(user_id, c):
        return
    group = _group_ids(_extra(c).get("item_id")) if c["kind"] == "mistake" else set()
    engine.items_for(user_id, c["kp_id"], n=1, purpose="diagnose", exclude=group)


def prepare_variants(user_id: int, cards) -> None:
    """打开错题重做、复习时，后台为要换新题的卡把题准备好：孩子做到这张卡时不用等 AI 现场出题。"""
    for c in cards:
        if not llm.enabled() or not _needs_item(user_id, c):
            continue
        with _prep_lock:
            if c["id"] in _prep:
                continue
            t = threading.Thread(target=_prep_run, args=(user_id, c), name=f"variant-{c['id']}", daemon=True)
            _prep[c["id"]] = t
        t.start()


def _prep_run(user_id: int, c) -> None:
    try:
        _ensure_variant(user_id, c)
    except Exception:  # noqa: BLE001  后台准备失败不影响做题：提交时没有变式题就照常往下走
        log.exception("variant prep failed: card %s", c["id"])
    finally:
        with _prep_lock:
            _prep.pop(c["id"], None)
        if not db.IS_PG:
            db.close()


@db.atomic_user
def _answer_command(user_id: int, card_id: int, body: dict) -> dict:
    c = db.one("SELECT * FROM cards WHERE id=? AND user_id=?", card_id, user_id)
    if not c:
        return {"error": "没有这张卡"}
    if c["kind"] in WORD_KINDS:
        return _answer_word(user_id, c, body)
    item = _item(body.get("item_id"))
    if not item:
        return {"error": "没有这道题"}
    ex = _extra(c)
    # 错题卡：原题，或者这个知识点的任何一道题（变式题是出卡时现挑的）；知识点卡：这个知识点的题
    if c["kind"] == "mistake" and item["id"] == ex.get("item_id"):
        pass
    elif not c["kp_id"] or c["kind"] not in ("mistake", "kp") or item["id"] not in {r["id"] for r in bank.candidates(user_id, c["kp_id"])}:
        return {"error": "这道题不是这张卡的"}
    reveal = {**itemtypes.reveal(item), "redo": True}  # 前端：不再显示「已放进错题本」
    if body.get("dont_know"):
        reveal["steps"] = itemtypes.walk(item)   # 「还不会」：一步一步一起做这道（app.js walkThrough）
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
    original = item["id"] == ex.get("item_id")
    if ex.get("passed"):  # 过关后的回头看
        if correct:
            ex["kept"] = today
            engine.schedule_card(user_id, c, "good", due=PASSED_DUE, extra=ex, fmt=fmt)
            return {"passed": True, "kept": True}
        ex.pop("passed", None)
        ex.pop("kept", None)
    variants = _on_variants(c, ex)
    if original and not variants and c["kp_id"] and catalog.kp(c["kp_id"]):
        ex["fixed"] = today   # 原题改过一次了：以后都换变式题
    ex.pop("variant", None)   # 以前的版本记过「定好的那道变式题」，现在每次现挑
    if not correct:
        ex.update(streak=0, streak_day="")
        engine.schedule_card(user_id, c, "again", due=db.today() + timedelta(days=1), extra=ex, fmt=fmt)
        return {"next": "tomorrow"}
    if not variants and ex.get("fixed") == today:   # 原题改对了：隔两天换一道新题来考
        engine.schedule_card(user_id, c, "good", due=db.today() + timedelta(days=VARIANT_GAPS[0]), extra=ex, fmt=fmt)
        return {"variant_next": True, "streak": 0}
    if ex.get("streak_day") != today:  # 同一天做对两次只算一次
        ex["streak"] = ex.get("streak", 0) + 1
        ex["streak_day"] = today
    need = (PASS_STREAK if variants else PASS_NO_VARIANT) - ex["streak"]
    if need <= 0:
        return _pass(user_id, c, ex, today, fmt)
    engine.schedule_card(user_id, c, "good", due=db.today() + timedelta(days=VARIANT_GAPS[min(ex["streak"], len(VARIANT_GAPS) - 1)]),
                         extra=ex, fmt=fmt)
    return {"streak": ex["streak"], "need": need}


def _pass(user_id: int, c, ex: dict, today: str, fmt: str) -> dict:
    """过关：先不移出，RECHECK_DAYS 天后回头再做一次原题。"""
    ex["passed"] = today
    due = db.today() + timedelta(days=RECHECK_DAYS)
    engine.schedule_card(user_id, c, "good", due=due, extra=ex, fmt=fmt)
    return {"passed": True, "recheck_on": due.isoformat()}


def _group_ids(item_id) -> set:
    """一道题和它同一组（换了数字的同一道题）的所有题。"""
    r = db.one("SELECT near_key FROM items WHERE id=?", item_id) if item_id else None
    if not r:
        return {item_id} if item_id else set()
    return {item_id} | ({x["id"] for x in db.q("SELECT id FROM items WHERE near_key=?", r["near_key"])} if r["near_key"] else set())


def _variant(user_id: int, c, ex: dict) -> dict | None:
    """同一知识点的另一道题，换个样子考：不是原题、不是同一组换数字的题、这个孩子没做对过的；没做过的优先，
    和原题题型不一样的优先。仅从已准备好的题库选择，AI 准备在事务外完成（prepare_variants）。"""
    if not c["kp_id"] or not catalog.kp(c["kp_id"]):
        return None
    orig = _item(ex.get("item_id")) or {}
    group = _group_ids(orig.get("id"))
    fmt0 = itemtypes.fmt(orig["type"]) if orig.get("type") in itemtypes.TYPES else ""
    rows = [r for r in bank.candidates(user_id, c["kp_id"]) if r["id"] not in group and _judged(r) and not bank.retired(r)]
    if not rows:
        return None
    # 复习要判对错：思辨题另有「换条件再想」（app/discuss.py）
    rows.sort(key=lambda r: (r["done"] > 0 or bool(r["gdone"]), itemtypes.fmt(r["type"]) == fmt0, r["other"] or 0, r["id"]))
    return bank.row_to_item(rows[0])


# 复习页的题型 → 单词等级里的哪一级（app/words.py 的 TIERS）
_TIER = {"meaning": "read", "choice": "reverse", "spell": "spell", "dictate": "dictate", "cloze": "cloze", "cloze_spell": "cloze", "use": "use"}
KNOWN_DAYS = 7         # 「我认识」验过了、往前补的词认出来了：算认识，这么多天以后再来看一眼


def _due(c, correct: bool):
    """答对后下次哪天：前几次按 YOUNG_STEPS（1/2/4/7 天），之后（或答错）交给记忆模型（答错默认今天再来）。"""
    return db.today() + timedelta(days=YOUNG_STEPS[c["box"]]) if correct and c["box"] < len(YOUNG_STEPS) else None


def _known(user_id: int, c, fmt: str, box: int = 3) -> None:
    """认识这个词（「我认识」验过了、往前补的词认出来了）：不当新词学，当作已经过了前几轮（box），KNOWN_DAYS 天以后再来看一眼。
    「我认识」验的是拼出来，算过了 1/2/4 天三轮（单词等级算到想得起）；往前补只认了意思，算过了两轮（认得）。"""
    ex = {k: v for k, v in _extra(c).items() if k not in ("check",)}
    ex["known"] = 1
    engine.schedule_card(user_id, c, "good", due=db.today() + timedelta(days=KNOWN_DAYS), extra=ex, fmt=fmt)
    db.run("UPDATE cards SET box=? WHERE id=?", box, c["id"])


def _use_answer(user_id: int, c, q: str) -> dict | None:
    for u in (words.usage_of([c["front"]], words.band(_grade(user_id))).get(c["front"].lower()) or {}).get("use") or []:
        if u.get("q") == q:
            return u
    return None


def _answer_word(user_id: int, c, body: dict) -> dict:
    """一道单词题的对错和排期。几种特别的：
    known：学习卡上点了「我认识」，验的那一道——对了算认识（KNOWN_DAYS 天后再来），错了照常学（不排期）。
    卡片是 check（往前补、认一认）：认出来算认识；没认出来变成要学的新词（下一组里是学习卡）。
    push：抓词里往上推一级的题（卡还没到期）——对了只记下来，不动排期；错了明天再来。
    use：用法题，答案在用法卡里。"""
    given = str(body.get("answer") or "").strip()
    norm = lambda s: " ".join(s.lower().replace("’", "'").split()).strip(".!?。")
    mode = body.get("mode") or "choice"
    ex = _extra(c)
    if mode == "seen":    # 学习卡看过了，但这一轮没有别的词能隔开它、或者出不了题：今天不考，明天第一次考
        engine.schedule_card(user_id, c, "hard", due=db.today() + timedelta(days=YOUNG_STEPS[0]), fmt="choice")
        return {"seen": True}
    if mode == "learn":   # 学习卡隔开以后回来的那一下：先猜的那一下只记下来（不算对错），算数的是 check
        if body.get("guess") is not None:
            evidence.log(user_id, "word", f"{ex.get('list') or ''}:{c['front']}", "guess",
                         correct=str(body["guess"]).strip() == _meaning(c), mode="learn")
        mode = body.get("check") if body.get("check") in _TIER else "choice"
    use = _use_answer(user_id, c, str(body.get("q") or "")) if mode == "use" else None
    want = _meaning(c) if mode == "meaning" else use["answer"] if use else c["front"]
    correct = bool(given) and not body.get("dont_know") and norm(given) == norm(want)
    fmt = "recall" if mode in ("spell", "dictate", "cloze_spell") else "choice"
    tier = "tiles" if body.get("tiles") and fmt == "recall" else _TIER.get(mode, "reverse")   # 用字母块拼的：还不算自己拼得出
    probe = bool(ex.get("check")) and not c["reviews"] and mode == "meaning"
    out = {"correct": correct, "answer": c["front"], "meaning": c["back"], "example": ex.get("example", "")}
    if body.get("known") and not c["reviews"]:
        if correct:
            _known(user_id, c, fmt)
        out["known"] = correct
    elif probe:
        if correct:
            _known(user_id, c, fmt, box=2)
        else:   # 没认出来：变成今天要学的新词（下一组里先学一遍）
            db.run("UPDATE cards SET extra=?, due=? WHERE id=?", db.jdump({**{k: v for k, v in ex.items() if k != "check"}, "new": 1}),
                   db.today().isoformat(), c["id"])
            out["learn_next"] = True
        out["known"] = correct
    elif body.get("push"):
        if not correct:
            engine.schedule_card(user_id, c, "again", due=db.today() + timedelta(days=YOUNG_STEPS[0]), fmt=fmt)
    else:
        engine.schedule_card(user_id, c, "good" if correct else "again", due=_due(c, correct), fmt=fmt)
    if c["kind"] in ("word", "term"):
        words.log_answer(user_id, ex.get("list") or "", c["front"], correct, tier, mode="review",
                         dont_know=bool(body.get("dont_know")))
    if c["kp_id"]:
        engine.update_mastery(user_id, c["kp_id"], correct, weight=0.6, source="review", fmt=fmt, kind="review")
    if mode == "meaning":
        out["pick"] = want   # 认意思：选项里该亮的是这个意思
    if use:
        out.update(pick=use["answer"], explain=use.get("explain", ""))
    if not correct:   # 答错了：有记忆钩子（反复忘的词才有）就给一句
        hook = (words.usage_of([c["front"]], words.band(_grade(user_id))).get(c["front"].lower()) or {}).get("hook")
        if hook:
            out["hook"] = hook
    if mode in ("cloze", "cloze_spell"):   # 补好的整句给孩子看、听（出题时没给，免得先听到答案）
        got = words.examples([c["front"]], words.band(_grade(user_id))).get(c["front"].lower()) or []
        q = str(body.get("q") or "")
        for s in got:
            full = words._MARK.sub(lambda m: m.group(1), s["en"])
            if not q or words._MARK.sub("____", s["en"]) == q:
                out.update(full=full, zh=s.get("zh", ""))
                break
    return out
