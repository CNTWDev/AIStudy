"""单词模块（docs/DESIGN.md 5.41，讨论稿 docs/英语单词游戏与单词系统-讨论稿.md）：学什么、每天学几个新词、每个词会到哪一级。

四层，复习卡只是其中一层：
1. 单词包（学什么）：content/words_en.json 和 content/words/*.json 的每张词表就是一个包，一包分几组（词表写了 units 就按它，
   没写每 UNIT 个词一组）。包放在书架上（shelf）：课本包（一个年级一包，按册、按单元）、核心词、考试和课标、成人常用、成人场景、学科术语。
   按年级、教材自动挂（_auto：课本包给选了这套教材的孩子、英语核心词给学英语的孩子、IGCSE 物理术语给选了剑桥物理的孩子，
   学到当前年级为止；考试包、成人包不自动挂）；课本包的组对上课程目录的册和单元，新词先出学校正在上的那一单元（school_unit）；
   家长可以加一个包、停一个包、改每天几个新词（word_packs）。成人不自动加新词，自己挑包；还没挑的先挂上成人的高频词、场景词（只在游戏、闪测里用）。
2. 每天的新词（daily_new）：只从「主包」里来（覆盖孩子现在年级的包、术语包、家长加上的包），已经会的跳过；
   一组里隔着挑（第 1、4、7、10 个……），意思挨着的词（同一个话题）不在同一天学，免得互相串（Tinkham 1993）。
   要复习的词欠得多就少加新词，先把旧的找回来。高频词的缺口不在这里补：热身里的旧词闪测在找，没认出来的直接进复习。
   新词先「学」再进复习：复习页第一次见到它是学习卡（先猜、揭晓；隔开几个别的词再回来认一次，app/recall.py 的 learn，DESIGN 5.44）。
3. 每个词会到哪一级（ladder）：认得 → 听得出 → 想得起 → 拼得出 → 用得对，从学习记录（events 里 target='word' 的作答）推出来，
   在两个不同的日子答对过这一级（或更难的一级）才算到了。不另存一份，和掌握状态一样可以重放。
4. 用出来：例句（examples，AI 写一次、所有人复用）让单词能放进句子里考（句子补洞：复习、快练都用 cloze_item）。

在哪儿答的单词题都记成 target='word' 的事件（data.tier 是哪一级的题），所以游戏里错的词，第二天复习和别的游戏里会先出。
"""
import logging
import random
import re
import threading
from datetime import timedelta

from . import db, llm
from .catalog import is_adult, stage_rank

log = logging.getLogger("aistudy.words")

UNIT = 12                 # 词表没分组时，每组几个词
STRIDE = 3                # 一组里隔几个挑一个（同一天的新词不挨着）
TIERS = {"copy": 1, "read": 1, "listen": 2, "reverse": 3, "choice": 3, "spell": 4, "dictate": 4, "cloze": 5}
LEVEL_NAME = {0: "还没学", 1: "认得", 2: "听得出", 3: "想得起", 4: "拼得出", 5: "用得对"}
CAUGHT = 3                # 到「想得起」（隔天也想得起来）算抓牢了
BACKLOG_HALF, BACKLOG_STOP = 40, 80   # 欠着要复习的词这么多：新词减半 / 先不加新词
PER_DAY = {"primary": 4, "secondary": 5, "adult": 6}
TERMS_PER_DAY = 2
WORD_KINDS = ("word", "term")
ADULT_SHELVES = ("adult", "scene")   # 成人的包：日常高频、场景词（content/words_en.json 的 shelf）


def _content():
    from .content import content
    return content


# ------------------------------------------------------------------ 单词包

def packs() -> dict[str, dict]:
    return _content().word_lists


SHELVES = {   # 单词包书架（包的 shelf）：孩子的课本包按年级自动挂；考试包家长加；成人的包成人自己挑
    "grade": "课本单词包", "core": "核心词", "exam": "考试和课标词汇", "adult": "成人常用词", "scene": "成人场景词", "terms": "学科术语"}


def shelf(wl: dict) -> str:
    return wl.get("shelf") or ("terms" if is_terms(wl) else "core")


def is_terms(wl: dict) -> bool:
    """教材术语包（IGCSE 物理术语等）：给选了这套教材的孩子，卡片是 term。"""
    return wl.get("shelf") == "terms" or (bool(wl.get("packs")) and not wl.get("subject") and not wl.get("shelf"))


def units(wl: dict) -> list[dict]:
    """一个包分成几组：[{title, words}]。"""
    if wl.get("units"):
        out = []
        for u in wl["units"]:
            ws = {w for w in u.get("words", [])}
            out.append({"title": u.get("title") or f"第 {len(out) + 1} 组", "words": [w for w in wl["words"] if w["w"] in ws]})
        return out
    ws = [w for w in wl["words"] if w.get("zh")]
    return [{"title": f"第 {i // UNIT + 1} 组", "words": ws[i:i + UNIT]} for i in range(0, len(ws), UNIT)]


def band(grade: str | None) -> str:
    """例句和新词数量按学段分三档。"""
    if is_adult(grade):
        return "adult"
    return "primary" if stage_rank(grade or "G3") < 7 else "secondary"


def _span(wl: dict) -> tuple[float, float]:
    parts = (wl.get("stage") or "").split("-")
    return stage_rank(parts[0]), stage_rank(parts[-1])


def _user(user_id: int) -> dict:
    return dict(db.one("SELECT id, grade FROM users WHERE id=?", user_id) or {"id": user_id, "grade": ""})


def _auto(user_id: int) -> list[dict]:
    """按科目、教材、年级自动挂的包：包声明给哪个学科（subject）或哪几套教材（packs）用，学到当前年级（含）为止。"""
    from . import explore
    from .catalog import catalog
    user = _user(user_id)
    enrolled = {e["pack_id"] for e in explore._enrolls(user_id)}
    subjects = {catalog.packs[p].subject for p in enrolled}
    g = stage_rank(user["grade"] or catalog.default_grade)
    def fits(wl):   # 写了教材的包只给选了这套教材的人（课本包、术语包）；只写了学科的给学这门课的人；考试包、成人包不自动挂
        if shelf(wl) in ("exam",) + ADULT_SHELVES:
            return False
        return bool(enrolled & set(wl["packs"])) if wl.get("packs") else wl.get("subject") in subjects
    return [wl for wl in packs().values() if fits(wl) and _span(wl)[0] <= g]


def _rows(user_id: int) -> dict[str, dict]:
    return {r["pack_id"]: dict(r) for r in db.q("SELECT * FROM word_packs WHERE user_id=?", user_id)}


def attached(user_id: int) -> list[dict]:
    """这个人挂着的包（含停掉的，on=False）：[{pack, on, auto, main, per_day}]。
    main：每天的新词从这些包里来——孩子是覆盖现在年级的包、术语包、家长加上的包；成人只有自己加上的包。"""
    user = _user(user_id)
    rows, auto = _rows(user_id), _auto(user_id)
    adult = is_adult(user["grade"])
    if adult and not any(r["state"] == "on" for r in rows.values()):   # 成人还没挑包：先挂上场景词和最常用的高频词（游戏、闪测有词可用，不每天加新词）
        auto = auto + [wl for wl in packs().values() if (wl.get("shelf") == "scene" or wl.get("starter")) and wl not in auto]
    g = stage_rank(user["grade"] or "G3")
    core = [wl for wl in auto if not is_terms(wl)]
    books = [wl for wl in core if shelf(wl) == "grade"] or core   # 有课本单词包就用课本包当主包，核心词留着闪测、游戏用
    now = [wl for wl in books if _span(wl)[0] <= g <= _span(wl)[1]] or books[-1:]   # 比所有词表都高的年级：最高那张
    out, seen = [], set()
    for wl in auto + [packs()[p] for p, r in rows.items() if r["state"] == "on" and p in packs()]:
        if wl["id"] in seen:
            continue
        seen.add(wl["id"])
        r = rows.get(wl["id"]) or {}
        mine = r.get("state") == "on"
        main = mine or (not adult and (wl in now or is_terms(wl)))
        default = TERMS_PER_DAY if is_terms(wl) else PER_DAY[band(user["grade"])]
        out.append({"pack": wl, "on": r.get("state") != "off", "auto": wl in auto, "main": main,
                    "per_day": default if r.get("per_day") is None else int(r["per_day"]), "last_day": r.get("last_day") or ""})
    return out


def lists(user_id: int) -> list[dict]:
    """在用的包（停掉的不算）：热身闪测、快练和游戏出词、复习卡要不要暂停都按它。"""
    return [a["pack"] for a in attached(user_id) if a["on"]]


def set_pack(user_id: int, pack_id: str, on: bool, per_day: int | None = None) -> bool:
    """家长（成人自己）加上 / 停掉一个包、改每天几个新词。"""
    if pack_id not in packs():
        return False
    # 本来就自动当主包的（本年级的课本包等）记 auto，升年级跟着换；自动挂着却不是主包的（低年级包、核心词），家长加上就当主包
    auto = {a["pack"]["id"] for a in attached(user_id) if a["auto"] and a["main"]}
    state = ("auto" if pack_id in auto else "on") if on else "off"
    per = None if per_day is None else max(0, min(30, int(per_day)))
    if db.one("SELECT 1 FROM word_packs WHERE user_id=? AND pack_id=?", user_id, pack_id):
        db.run("UPDATE word_packs SET state=?, per_day=COALESCE(?, per_day) WHERE user_id=? AND pack_id=?", state, per, user_id, pack_id)
    else:
        db.run("INSERT INTO word_packs(user_id, pack_id, state, per_day, created_at) VALUES(?,?,?,?,?)",
               user_id, pack_id, state, per, db.now())
    return True


# ------------------------------------------------------------------ 每天的新词

def _have(user_id: int) -> set[str]:
    """已经有卡片的词（学过、查过、错过的），小写。"""
    return {r["f"] for r in db.q("SELECT LOWER(front) AS f FROM cards WHERE user_id=? AND kind IN ('word','term','phrase')", user_id)}


def _known(user_id: int) -> set[str]:
    """在任何地方答对过的词（热身闪测、快练、游戏、复习），小写。"""
    return {r["target_id"].rsplit(":", 1)[-1].lower() for r in db.q(
        "SELECT DISTINCT target_id FROM events WHERE user_id=? AND target='word' AND kind='answer' AND correct=1", user_id)}


def school_unit(user_id: int, wl: dict) -> int | None:
    """课本单词包跟着学校进度走：学校大概正在上哪一册哪一单元（app/schoolyear.py 按校历推演、家长可纠正），
    就是包里的第几组。单元名对得上（新版词表）按名字对；对不上（旧版词表）按「这一册第几单元」对。放假、没选这套教材时 None。"""
    if not wl.get("units") or not wl.get("packs"):
        return None
    from . import explore, schoolyear
    user = db.one("SELECT * FROM users WHERE id=?", user_id)
    e = next((e for e in explore._enrolls(user_id) if e["pack_id"] in wl["packs"]), None)
    est = schoolyear.estimate(dict(user), e, manual=schoolyear._manual(user_id)) if user and e else None
    ch = est and est["current"]
    if not ch:
        return None
    pos = [c["name"] for c in est["chapters"] if c.get("book") == ch.get("book")].index(ch["name"])
    for exact in (True, False):
        for i, u in enumerate(wl["units"]):
            if u.get("book") == ch.get("book") and (u.get("unit") == ch["name"] if exact else u.get("pos") == pos):
                return i
    return None


def _pick(wl: dict, skip: set[str], n: int, start: int | None = None) -> list[tuple[int, dict]]:
    """从还有没学的词的那一组起，隔着挑 n 个：[(第几组, 词)]。
    start：学校正在上的那一组——先学它，再补前面落下的，最后才往后学。"""
    out = []
    us = list(enumerate(units(wl)))
    if start is not None and 0 <= start < len(us):
        us = [us[start]] + us[:start] + us[start + 1:]
    for ui, u in us:
        left = [(i, w) for i, w in enumerate(u["words"]) if w.get("zh") and w["w"].lower() not in skip]
        left.sort(key=lambda x: (x[0] % STRIDE, x[0]))
        for _, w in left[:n - len(out)]:
            out.append((ui, w))
        if len(out) >= n:
            break
    return out


def due_backlog(user_id: int) -> int:
    return (db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND kind IN ('word','term') AND due<=? AND reviews>0",
                   user_id, db.today().isoformat()) or {}).get("n") or 0


def _added_today(user_id: int) -> dict[str, int]:
    """今天每个包已经加了几个新词。"""
    out: dict[str, int] = {}
    for r in db.q("SELECT extra FROM cards WHERE user_id=? AND kind IN ('word','term') AND created_at>=?", user_id, db.today().isoformat()):
        ex = db.jload(r["extra"], {}) or {}
        if ex.get("new") and ex.get("list"):
            out[ex["list"]] = out.get(ex["list"], 0) + 1
    return out


def daily_new(user_id: int) -> int:
    """排今天的计划时，从主包里把今天的新词放进单词卡（今天学），每个包补到今天该有的个数（家长白天调多了，当天就补上）。
    返回新加了几个。"""
    from . import engine
    today = db.today().isoformat()
    todo = [a for a in attached(user_id) if a["on"] and a["main"] and a["per_day"] > 0]
    if not todo:
        return 0
    backlog = due_backlog(user_id)
    scale = 0 if backlog >= BACKLOG_STOP else 0.5 if backlog >= BACKLOG_HALF else 1
    done = _added_today(user_id)
    skip = _have(user_id) | _known(user_id)
    added = 0
    for a in todo:
        wl = a["pack"]
        n = max(0, int(a["per_day"] * scale + 0.5) - done.get(wl["id"], 0))
        if not n:
            continue
        for ui, w in _pick(wl, skip, n, school_unit(user_id, wl)):
            skip.add(w["w"].lower())
            if engine.add_card(user_id, "term" if is_terms(wl) else "word", w["w"], w["zh"],
                               {"pos": w.get("pos", ""), "list": wl["id"], "unit": ui, "new": 1}, w.get("kp") or None, due=db.today()):
                added += 1
        if db.one("SELECT 1 FROM word_packs WHERE user_id=? AND pack_id=?", user_id, wl["id"]):
            db.run("UPDATE word_packs SET last_day=? WHERE user_id=? AND pack_id=?", today, user_id, wl["id"])
        else:
            db.run("INSERT INTO word_packs(user_id, pack_id, state, last_day, created_at) VALUES(?,?,'auto',?,?)",
                   user_id, wl["id"], today, db.now())
    return added


# ------------------------------------------------------------------ 每个词会到哪一级

def log_answer(user_id: int, list_id: str, word: str, correct: bool, tier: str, *, mode="", weight=1.0, dont_know=False) -> None:
    """一次单词作答记进学习记录（所有地方都走这里）：tier 是题型（read / listen / reverse / spell / dictate / cloze）。"""
    from . import evidence
    evidence.log(user_id, "word", f"{list_id or ''}:{word}", "answer", correct=correct, mode=mode, weight=weight,
                 fmt="recall" if TIERS.get(tier, 1) >= 4 and tier != "cloze" else "choice", dont_know=dont_know, data={"tier": tier})


# 单词跑酷（app/arena/games/runner.py）里一个词给多少提示，跟着这个词的等级走：新词整个词淡淡地写在上面照着拼（copy），
# 认得了只给中文和首字母（spell），拼得出了只给中文和读音（dictate）；最近一次答错了退一档。
RUN_HINTS = ("copy", "spell", "dictate")


def run_hint(v: dict | None, p: float) -> int:
    """v：这个词在 ladder 里的那一条（没答过是 None）；p：按水平预计这一级的词答对的概率（没见过的词，水平高的也不用照着拼）。"""
    v = v or {}
    lv = v.get("level", 0)
    h = 2 if lv >= 4 else 1 if lv >= 1 or v.get("ok") or p >= 0.85 else 0
    return max(0, h - 1) if v.get("wobbly") else h


def _tier(r) -> int:
    t = (db.jload(r["data"], {}) or {}).get("tier")
    return TIERS.get(t) or (TIERS["spell"] if r["fmt"] == "recall" else TIERS["read"])


def ladder(user_id: int) -> dict[str, dict]:
    """每个答过的词：{level 0-5, ok（答对过）, wobbly（最近一次答错了）}，小写词为键。
    level：两个不同的日子都在这一级或更难的题上答对过。老的单词卡（以前的复习没记事件）按复习轮数补一个下限。"""
    days: dict[str, dict[int, set]] = {}
    last: dict[str, bool] = {}
    for r in db.q("SELECT target_id, correct, fmt, data, created_at FROM events WHERE user_id=? AND target='word' AND kind='answer' "
                  "ORDER BY id", user_id):
        w = r["target_id"].rsplit(":", 1)[-1].lower()
        last[w] = bool(r["correct"])
        if r["correct"]:
            days.setdefault(w, {}).setdefault(_tier(r), set()).add(str(r["created_at"])[:10])
    out = {}
    for w in last:
        by = days.get(w, {})
        level = 0
        for t in range(5, 0, -1):
            if len(set().union(*[d for k, d in by.items() if k >= t] or [set()])) >= 2:
                level = t
                break
        out[w] = {"level": level, "ok": bool(by), "wobbly": not last[w]}
    for c in db.q("SELECT LOWER(front) AS f, box FROM cards WHERE user_id=? AND kind IN ('word','term') AND box>0", user_id):
        floor = CAUGHT if c["box"] >= 3 else 1
        cur = out.setdefault(c["f"], {"level": 0, "ok": True, "wobbly": False})
        cur["level"], cur["ok"] = max(cur["level"], floor), True
    return out


def level_of(user_id: int, word: str) -> int:
    return ladder(user_id).get(word.lower(), {}).get("level", 0)


def stats(user_id: int) -> dict:
    """「我的词」：认识的词一共多少（只增不减：答对过一次就算认识），每个包每一组抓牢了几个。"""
    lad = ladder(user_id)
    known = sum(1 for v in lad.values() if v["ok"])
    have = _have(user_id)
    out = []
    for a in attached(user_id):
        if not a["on"]:
            continue
        us = []
        for u in units(a["pack"]):
            ws = [w["w"].lower() for w in u["words"]]
            caught = sum(1 for w in ws if lad.get(w, {}).get("level", 0) >= CAUGHT)
            met = sum(1 for w in ws if w in have or lad.get(w, {}).get("ok"))
            us.append({"title": u["title"], "total": len(ws), "caught": caught, "met": met})
        sch = school_unit(user_id, a["pack"]) if a["main"] else None
        cur = sch if sch is not None and us[sch]["met"] < us[sch]["total"] else next((i for i, u in enumerate(us) if u["met"] < u["total"]), None)
        out.append({"id": a["pack"]["id"], "title": a["pack"]["title"], "main": a["main"], "per_day": a["per_day"],
                    "units": us, "current": cur, "school": sch is not None and cur == sch,
                    "total": sum(u["total"] for u in us), "caught": sum(u["caught"] for u in us)})
    out.sort(key=lambda p: (not p["main"], -_span(packs()[p["id"]])[0]))
    wobbly = [w for w, v in lad.items() if v["wobbly"]]
    return {"known": known, "packs": out, "wobbly": wobbly[-12:]}


def shelves(user_id: int) -> list[dict]:
    """单词包书架：[{id, name, packs: [{pack, on}]}]。孩子看自己这套教材的课本包；成人看成人、场景、考试和全部课本包。"""
    user = _user(user_id)
    adult = is_adult(user["grade"])
    on = {a["pack"]["id"] for a in attached(user_id) if a["on"]}
    mine = {wl["id"] for wl in _auto(user_id)}
    series = {wl.get("series") for wl in packs().values() if wl["id"] in mine and wl.get("series")}
    order = ("adult", "scene", "exam", "grade") if adult else ("grade",)   # 孩子只看自己这套课本的包（孩子不挑包，家长在学习安排里加）
    out = []
    for sid in order:
        ps = [wl for wl in packs().values() if shelf(wl) == sid and (adult or sid != "grade" or wl.get("series") in series)]
        if ps:
            ps.sort(key=lambda wl: (wl.get("series") or "", _span(wl)[0]))
            out.append({"id": sid, "name": SHELVES[sid], "packs": [{"pack": wl, "on": wl["id"] in on} for wl in ps]})
    return out


def grouped() -> list[tuple[str, list[dict]]]:
    """全部单词包按书架分组（家长「加一个单词包」的下拉框用）。"""
    out = []
    for sid, name in SHELVES.items():
        ps = sorted((wl for wl in packs().values() if shelf(wl) == sid), key=lambda wl: (wl.get("series") or "", _span(wl)[0]))
        if ps:
            out.append((name, ps))
    return out


def pack_view(user_id: int, pack_id: str) -> dict | None:
    """一个单词包的全部词，按组排，每个词带上我会到哪一级（0 = 还没学）。"""
    wl = packs().get(pack_id)
    if not wl:
        return None
    lad = ladder(user_id)
    sch = school_unit(user_id, wl)
    us = [{"title": u["title"], "school": i == sch,
           "words": [{**w, "level": lad.get(w["w"].lower(), {}).get("level", 0), "met": w["w"].lower() in lad} for w in u["words"]]}
          for i, u in enumerate(units(wl))]
    return {"pack": wl, "shelf": SHELVES[shelf(wl)], "units": us, "total": sum(len(u["words"]) for u in us),
            "caught": sum(1 for u in us for w in u["words"] if w["level"] >= CAUGHT),
            "on": any(a["on"] and a["pack"]["id"] == pack_id for a in attached(user_id))}


# ------------------------------------------------------------------ 例句（AI 写一次，按学段存，所有人复用）

_MARK = re.compile(r"\[\[([^\]]+)\]\]")
BATCH = 15               # 一次请 AI 写几个词的例句
PREP_PER_DAY = 6         # 每个人每天最多请几批（兜底；按进程计）
_prep_used: dict[tuple, int] = {}
_running: set = set()
_lock = threading.Lock()


def examples(words: list[str], b: str) -> dict[str, list[dict]]:
    """{小写词: [{en, zh}]}，只有写好了的词。"""
    ws = sorted({w.lower() for w in words if w})
    out = {}
    for i in range(0, len(ws), 200):
        part = ws[i:i + 200]
        for r in db.q(f"SELECT word, sentences FROM word_examples WHERE band=? AND word IN ({','.join('?' * len(part))})", b, *part):
            s = db.jload(r["sentences"], []) or []
            if s:
                out[r["word"]] = s
    return out


def _clean(word: str, sentences) -> list[dict]:
    """只留把这个词原样标出来、只标了一次的句子。"""
    out = []
    for s in sentences or []:
        en = str((s or {}).get("en") or "").strip()
        marks = _MARK.findall(en)
        if len(marks) == 1 and marks[0].strip().lower() == word.lower() and 3 <= len(en) <= 220:
            out.append({"en": en, "zh": str(s.get("zh") or "").strip()[:200]})
    return out[:3]


def write_examples(words: list[dict], b: str, user_id: int | None = None) -> int:
    """请 AI 给这些词（{w, zh, pos}）写例句，存起来。返回存了几个词。"""
    if not words or not llm.enabled():
        return 0
    from .llm import tasks
    data = tasks.word_examples(words, b, user_id=user_id)
    n = 0
    for x in data.get("words") or []:
        w = str((x or {}).get("w") or "").strip()
        got = _clean(w, x.get("sentences"))
        if not w or not got or w.lower() not in {y["w"].lower() for y in words}:
            continue
        if db.one("SELECT 1 FROM word_examples WHERE word=? AND band=?", w.lower(), b):
            continue
        db.run("INSERT INTO word_examples(word, band, sentences, created_at) VALUES(?,?,?,?)", w.lower(), b, db.jdump(got), db.now())
        n += 1
    return n


def wanted(user_id: int, limit: int = 40) -> list[dict]:
    """这个人接下来要用例句的词（今天的新词、到期的复习词、正在学的那一组），还没有例句的。"""
    rows = db.q("SELECT front, back, extra FROM cards WHERE user_id=? AND kind IN ('word','term') AND due<=? ORDER BY due LIMIT 80",
                user_id, (db.today() + timedelta(days=1)).isoformat())
    cand = [{"w": r["front"], "zh": (r["back"] or "").split("\n")[0][:40], "pos": (db.jload(r["extra"], {}) or {}).get("pos", "")}
            for r in rows]
    have = _have(user_id)
    for a in attached(user_id):
        if a["on"] and a["main"]:
            for w in _pick(a["pack"], set(), UNIT):   # 正在学的那一组（游戏里补句子也常用到）
                cand.append(w[1])
            cand += [w for u in units(a["pack"]) for w in u["words"] if w["w"].lower() in have][:30]
    b = band(_user(user_id)["grade"])
    done = examples([c["w"] for c in cand], b)
    out, seen = [], set()
    for c in cand:
        k = c["w"].lower()
        if k in done or k in seen or not c.get("zh") or not re.fullmatch(r"[A-Za-z][A-Za-z '\-]*", c["w"]):
            continue
        seen.add(k)
        out.append({"w": c["w"], "zh": c["zh"], "pos": c.get("pos", "")})
    return out[:limit]


def prepare(user_id: int) -> None:
    """后台把接下来要用的词的例句写好（打开今天页、做到单词前一项、开一局句子补洞时调）。只往前备一步，不一次写整本词表。"""
    from . import prefetch
    if prefetch.MODE == "off" or not llm.enabled():
        return
    k = (user_id, db.today().isoformat())
    with _lock:
        if user_id in _running or _prep_used.get(k, 0) >= PREP_PER_DAY:
            return
        _running.add(user_id)
    if prefetch.MODE == "inline":
        _prepare_run(user_id, k)
    else:
        threading.Thread(target=_prepare_run, args=(user_id, k), name=f"words-{user_id}", daemon=True).start()


def _prepare_run(user_id: int, k: tuple) -> None:
    try:
        b = band(_user(user_id)["grade"])
        for _ in range(2):
            ws = wanted(user_id, BATCH)
            if not ws:
                break
            with _lock:
                if _prep_used.get(k, 0) >= PREP_PER_DAY:
                    break
                _prep_used[k] = _prep_used.get(k, 0) + 1
            if not write_examples(ws, b, user_id):
                break
    except Exception:  # noqa: BLE001  写不成就先用不带句子的题型
        log.exception("word examples failed: user %s", user_id)
    finally:
        with _lock:
            _running.discard(user_id)
        from . import prefetch
        if not db.IS_PG and prefetch.MODE != "inline":
            db.close()


# ------------------------------------------------------------------ 句子补洞

def _options(word: dict, pool: list[dict], n: int, rng: random.Random) -> list[str]:
    """干扰项：同一个包里词性一样的词（句子的语法不泄露答案，要靠意思选），不够再用别的词。"""
    w = word["w"].lower()
    same = [x["w"] for x in pool if x["w"].lower() != w and x.get("pos") == word.get("pos") and " " not in x["w"]]
    other = [x["w"] for x in pool if x["w"].lower() != w and " " not in x["w"]]
    rng.shuffle(same)
    rng.shuffle(other)
    out = []
    for x in same + other:
        if x.lower() not in {o.lower() for o in out}:
            out.append(x)
        if len(out) >= n - 1:
            break
    return out


def cloze_item(word: dict, b: str, rng: random.Random, pool: list[dict], spell: bool = False) -> dict | None:
    """把这个词（{w, zh, pos}）挖掉的一句话：选词补洞（mcq，选项是英文词），spell 时自己拼进去（fill）。还没有例句返回 None。
    不带读音（say）：先读出整句就把答案说出来了；full 是补好的整句，答完再看、再听。"""
    got = examples([word["w"]], b).get(word["w"].lower())
    if not got:
        return None
    s = rng.choice(got)
    full = _MARK.sub(lambda m: m.group(1), s["en"])
    gap = _MARK.sub("____", s["en"])
    explain = f"{full}\n{s['zh']}" if s.get("zh") else full
    if spell:
        letters = sorted(set(word["w"].lower()))
        rest = [ch for ch in "abcdefghijklmnopqrstuvwxyz" if ch not in letters]
        letters += rng.sample(rest, max(0, min(3, 11 - len(letters))))
        rng.shuffle(letters)
        return {"type": "fill", "q": gap, "answer": [word["w"]], "keypad": "letters", "letters": letters, "full": full,
                "cloze": True, "zh": s.get("zh", ""), "hint": f"{word.get('zh', '')} · 首字母 {word['w'][0]}", "explain": explain}
    others = _options(word, pool, 3 if b == "primary" else 4, rng)
    if len(others) < 2:
        return None
    opts = others + [word["w"]]
    rng.shuffle(opts)
    return {"type": "mcq", "q": gap, "options": opts, "answer": opts.index(word["w"]), "full": full, "cloze": True,
            "zh": s.get("zh", ""), "explain": explain}


def pool_for(user_id: int, list_id: str | None) -> list[dict]:
    """补洞的干扰项从哪来：这个词所在的包；不在哪个包里（查词收藏的）就用主包。"""
    if list_id and list_id in packs():
        return packs()[list_id]["words"]
    for a in attached(user_id):
        if a["main"]:
            return a["pack"]["words"]
    return next(iter(packs().values()))["words"] if packs() else []
