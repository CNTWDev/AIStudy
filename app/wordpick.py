"""出词：哪儿要单词，都从这里挑（docs/DESIGN.md 5.58，方案 docs/独立背单词小产品-方案.md）。

每天的新词（words.daily_new）、抓词的「一组」（make_set）、热身里的旧词闪测（explore.pick_words）、
快练和游戏里的单词题（arena/sources.WordSource）以前各挑各的，现在都走这里，规则只有一套。

一个人的词从五路来：
- 跟课本（book）：主包里学校正在上的那一单元 → 往后的单元 → 下一个年级的课本包（往后学）。新词主要从这里来。
- 往前补（back）：学校已经教过、在这里还没见过的词（主包里学校进度之前的单元、低年级的课本包、核心词）。
  这些词多半会了，所以不当新词教，先闪测一下：认得就算认识，不认得才学（make_set 里的「认一认」、热身闪测）。
- 往外（out）：课本以外的高频词（课标词表、CEFR 词表；成人是 NGSL 高频词）。学校这一单元的词都见过了（跟上了），
  或者主包学完了，新词才分一半给它——学得深了，才向外扩。
- 联想（related）：已经抓牢的词的一家人（用法卡里的 family：学了 decide，过几天学 decision）。每天最多 1 个。
- 学过的（mine）：过去所有学过、查过、错过的词。复习（到期的卡）、游戏和快练里的单词题都从这里多拿，
  不只是今天学的那几个。

每天的新词有上限（cap，按学段，含每日任务里的），每周按「隔了几天以后还记得的比例」自动升降：记得的少，就少学新词。
"""
import hashlib
import math
import random
from datetime import date, timedelta

from . import db

SET_SIZE = 8                  # 一组几个词
TASK_URL = "/words/go?from=today"   # 每日任务里的「单词」：同一个抓词页，带着它来就是在今天的清单里——一组只出今天要做的词，做完进下一项
SET_NEW_MAX = 3               # 一组里最多几个新词
SET_BACK_MAX = 2              # 一组里最多几个「认一认」（往前补的词）
CAP = {"primary": 15, "secondary": 20, "adult": 25}   # 每天新词上限（每日任务的新词 + 抓词里多学的）
CAP_STEP, CAP_MIN, CAP_MAX = 5, 5, 40
KEEP_LOW, KEEP_HIGH = 0.80, 0.92   # 隔几天后还记得的比例：低于它少学新词，高于它多学
KEEP_GAP_DAYS = 4             # 离上一次见到这个词至少隔了几天，这一次才算「隔了几天还记不记得」
KEEP_MIN_SAMPLES = 10
RELATED_PER_DAY = 1
LINE_NAME = {"book": "跟课本", "back": "往前补", "out": "往外", "related": "联想", "mine": "学过的"}


def _w():
    from . import words
    return words


def _hash_order(user_id: int, w: str) -> str:
    """同一张大词表（课标、CEFR 按字母排）里打乱着挑：每个人一个固定的顺序，不总是从 a 开头。"""
    return hashlib.sha1(f"{user_id}|{w.lower()}".encode()).hexdigest()


class Pool:
    """一个人挑词要用的数据，按需读、只读一次。"""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self._c: dict = {}

    def _get(self, key, fn):
        if key not in self._c:
            self._c[key] = fn()
        return self._c[key]

    @property
    def user(self) -> dict:
        return self._get("user", lambda: _w()._user(self.user_id))

    @property
    def band(self) -> str:
        return _w().band(self.user["grade"])

    @property
    def attached(self) -> list[dict]:
        return self._get("attached", lambda: [a for a in _w().attached(self.user_id) if a["on"]])

    @property
    def ladder(self) -> dict:
        return self._get("ladder", lambda: _w().ladder(self.user_id))

    @property
    def seen(self) -> set[str]:
        """见过的词：有卡片的（学过、查过、错过），或者在哪儿答对过的。"""
        return self._get("seen", lambda: _w()._have(self.user_id) | _w()._known(self.user_id))

    def met(self, w: str) -> bool:
        return w.lower() in self.seen

    @property
    def main(self) -> dict | None:
        """主包（跟课本那一路）：不是术语包的第一个主包。"""
        return next((a["pack"] for a in self.attached if a["main"] and not _w().is_terms(a["pack"])), None)

    def school(self, wl: dict) -> int | None:
        return self._get(("school", wl["id"]), lambda: _w().school_unit(self.user_id, wl))

    def caught_up(self) -> bool:
        """跟上了：学校正在上的这一单元（和它前面的）都见过了；没有学校进度的（成人、放假）主包都见过了才算。"""
        wl = self.main
        if not wl:
            return True
        us = _w().units(wl)
        sch = self.school(wl)
        upto = us[:sch + 1] if sch is not None else us
        return all(self.met(w["w"]) for u in upto for w in u["words"] if w.get("zh"))


# ------------------------------------------------------------------ 新词：跟课本、往外、联想

def _usable(w: dict) -> bool:
    return bool(w.get("zh")) and bool(w.get("w"))


def _next_pack(p: Pool, wl: dict) -> dict | None:
    """同一套课本的下一个年级（往后学）。"""
    if not wl.get("series") or _w().shelf(wl) != "grade":
        return None
    lo = _w()._span(wl)[1]
    later = [x for x in _w().packs().values() if x.get("series") == wl["series"] and _w().shelf(x) == "grade" and _w()._span(x)[0] > lo]
    return min(later, key=lambda x: _w()._span(x)[0]) if later else None


def book_line(p: Pool, wl: dict, skip: set[str], n: int, ahead: bool = True) -> list[dict]:
    """跟课本：学校正在上的单元 → 往后的单元 → 下一个年级的课本包；一组里隔着挑（STRIDE），意思挨着的词不在同一天。
    学校进度之前的单元留给往前补（先认一认，会的不再教）；往后都学完了，才回头把前面没见过的当新词学。
    ahead=False：只学到学校正在上的那一单元（往后的不出）。"""
    words = _w()
    us = list(enumerate(words.units(wl)))
    sch = p.school(wl)
    if sch is not None and 0 <= sch < len(us):
        order = us[sch:] if ahead else us[sch:sch + 1]
        order += us[:sch] if ahead else []
    else:
        order = us
    out: list[dict] = []
    for ui, u in order:
        left = [(i, w) for i, w in enumerate(u["words"]) if _usable(w) and w["w"].lower() not in skip]
        left.sort(key=lambda x: (x[0] % words.STRIDE, x[0]))
        for _, w in left[:n - len(out)]:
            out.append({**w, "list": wl["id"], "unit": ui, "line": "book"})
        if len(out) >= n:
            return out
    nxt = _next_pack(p, wl) if ahead else None
    if nxt and len(out) < n:
        out += [{**w, "line": "book"} for w in book_line(p, nxt, skip | {x["w"].lower() for x in out}, n - len(out), ahead=False)] \
            if words.units(nxt) else []
    return out


def _out_lists(p: Pool) -> list[dict]:
    """往外那一路的词表：孩子是核心词和课标、CEFR 词表里到自己年级为止的；成人是 NGSL 高频词（一级一级往上）。"""
    from .catalog import stage_rank
    words = _w()
    if p.band == "adult":
        return [wl for wl in words.packs().values() if wl["id"].startswith("en-ngsl-") and wl["id"] != "en-ngsl-spoken"]
    g = stage_rank(p.user["grade"] or "G3")
    ls = [wl for wl in words.packs().values() if wl.get("subject") == "english" and words.shelf(wl) in ("core", "exam")
          and words._span(wl)[0] <= g]
    return sorted(ls, key=lambda wl: (words.shelf(wl) != "core", words._span(wl)[0]))


def out_line(p: Pool, skip: set[str], n: int) -> list[dict]:
    """往外：课本以外的高频词。一张一张词表往上，表里按这个人固定的乱序挑；这套课本里的词（哪个年级都算）不在这里出（跟课本那一路会教）。"""
    if n <= 0:
        return []
    words = _w()
    series = {a["pack"].get("series") for a in p.attached if words.shelf(a["pack"]) == "grade"}
    books = {w["w"].lower() for wl in words.packs().values() if words.shelf(wl) == "grade" and wl.get("series") in series
             for w in wl["words"]}   # 这套课本（所有年级）里的词：往后学会学到，不算课本外
    out: list[dict] = []
    for wl in _out_lists(p):
        cand = [w for w in wl["words"] if _usable(w) and " " not in w["w"] and w["w"].lower() not in skip
                and w["w"].lower() not in books and w["w"][:1].islower()]
        cand.sort(key=lambda w: _hash_order(p.user_id, w["w"]))
        for w in cand[:n - len(out)]:
            out.append({**w, "list": wl["id"], "line": "out"})
            skip = skip | {w["w"].lower()}
        if len(out) >= n:
            break
    return out


def related_line(p: Pool, skip: set[str], n: int) -> list[dict]:
    """联想：抓牢了的词的一家人（用法卡 family：decide → decision）。词根先抓牢了，再学它的一家人。"""
    if n <= 0:
        return []
    words = _w()
    roots = [w for w, v in p.ladder.items() if v["level"] >= words.CAUGHT]
    if not roots:
        return []
    out: list[dict] = []
    for root, data in words.usage_of(roots, p.band).items():
        for f in data.get("family") or []:
            w = str(f.get("w") or "").strip()
            if not w or not f.get("zh") or w.lower() in skip or not w.replace("-", "").isalpha():
                continue
            out.append({"w": w, "zh": str(f["zh"])[:40], "pos": str(f.get("pos") or "")[:12], "list": "", "line": "related", "root": root})
            skip = skip | {w.lower()}
            if len(out) >= n:
                return out
    return out


def _related_today(user_id: int) -> int:
    return sum(1 for r in db.q("SELECT extra FROM cards WHERE user_id=? AND kind='word' AND created_at>=?", user_id, db.today().isoformat())
               if (db.jload(r["extra"], {}) or {}).get("line") == "related")


def new_words(user_id: int, n: int, skip: set[str] | None = None, pack: dict | None = None, p: Pool | None = None) -> list[dict]:
    """接下来要学的 n 个新词：[{w, zh, pos, list, unit?, line, root?}]。
    还没跟上学校：都跟课本（联想的词每天最多 1 个）。跟上了：跟课本（往后学）一半，往外和联想一半。
    pack：只从这个包里挑（术语包、家长加上的包各自按自己的数来）。"""
    p = p or Pool(user_id)
    skip = set(skip or set()) | p.seen
    if n <= 0:
        return []
    wl = pack or p.main
    if pack is not None and pack is not p.main:
        return book_line(p, pack, skip, n)
    rel = min(RELATED_PER_DAY - _related_today(user_id), 1 if n >= 3 else 0)
    got = related_line(p, skip, max(0, rel))
    skip |= {x["w"].lower() for x in got}
    rest = n - len(got)
    if wl is None:
        return got + out_line(p, skip, rest)
    book_n = rest if not p.caught_up() else math.ceil(rest / 2)
    book = book_line(p, wl, skip, book_n)
    skip |= {x["w"].lower() for x in book}
    out = out_line(p, skip, rest - len(book))
    got += book + out
    if len(got) < n:   # 哪一路的词不够了，另一路补上
        skip |= {x["w"].lower() for x in got}
        got += book_line(p, wl, skip, n - len(got))
    return got[:n]


# ------------------------------------------------------------------ 往前补：先认一认

def old_words(user_id: int, n: int, p: Pool | None = None, skip: set[str] | None = None) -> list[dict]:
    """学校已经教过、在这里还没见过的词，先闪测一下（认得就算认识，不认得才学）：
    主包里学校进度之前的单元 → 低年级的课本包 → 核心词，离现在越近越先。[{w, zh, pos, list, line='back'}]"""
    p = p or Pool(user_id)
    words = _w()
    skip = set(skip or set()) | p.seen
    out: list[dict] = []
    wl = p.main
    tiers: list[list[tuple[str, dict]]] = []
    if wl and wl.get("units"):
        sch = p.school(wl)
        if sch:
            tiers.append([(wl["id"], w) for u in reversed(words.units(wl)[:sch]) for w in u["words"]])
    lower = [a["pack"] for a in p.attached if not a["main"] and not words.is_terms(a["pack"])]
    lower.sort(key=lambda x: (words.shelf(x) != "grade", -words._span(x)[1]))
    for x in lower:
        tiers.append([(x["id"], w) for w in x["words"]])
    for tier in tiers:
        cand = [(lid, w) for lid, w in tier if _usable(w) and w["w"].lower() not in skip]
        cand.sort(key=lambda t: _hash_order(user_id, t[1]["w"]))
        for lid, w in cand:
            if len(out) >= n:
                return out
            if w["w"].lower() in skip:
                continue
            skip.add(w["w"].lower())
            out.append({**w, "list": lid, "line": "back"})
    return out


# ------------------------------------------------------------------ 学过的：复习、游戏、快练

def last_seen(user_id: int) -> dict[str, str]:
    """每个答过的词最近一次答的日子（小写词为键）。"""
    out: dict[str, str] = {}
    for r in db.q("SELECT target_id, MAX(created_at) AS at FROM events WHERE user_id=? AND target='word' AND kind='answer' GROUP BY target_id",
                  user_id):
        w = r["target_id"].rsplit(":", 1)[-1].lower()
        at = str(r["at"])[:10]
        if at > out.get(w, ""):
            out[w] = at
    return out


def practice(user_id: int, rng: random.Random, words: list[dict], cards: list, cache: dict) -> dict:
    """游戏、快练里的一个单词（WordSource.pick 用）：{w, zh, kp?, card?, list?, line}。
    cards：自己的卡（学过、查过、错过的词）；words：按水平挑好的那张词表。
    到期的卡先出（四成，答了就算复习过）→ 学过的词里最久没见的（三成，过去所有学过的词都会轮到）→ 词表里最近几天没答对过的。
    cache 里有 word_set（做完一组送的那一局）：只出这一组的词，轮着来。"""
    ws = cache.get("word_set")
    if ws:
        served = cache.setdefault("set_served", {})
        w = min(ws, key=lambda x: (served.get(x["w"], 0), rng.random()))
        served[w["w"]] = served.get(w["w"], 0) + 1
        return {**w, "line": "set"}
    today = db.today().isoformat()
    due = [c for c in cards if str(c["due"]) <= today]
    if due and rng.random() < 0.4:
        c = rng.choice(due)
        return {"w": c["front"], "zh": c["back"], "kp": c["kp_id"], "card": c["id"], "line": "mine"}
    if cards and rng.random() < 0.5:
        seen = cache.get("last_seen")
        if seen is None:
            seen = cache["last_seen"] = last_seen(user_id)
        stale = sorted(cards, key=lambda c: (seen.get(c["front"].lower(), ""), rng.random()))[:8]
        c = rng.choice(stale)
        return {"w": c["front"], "zh": c["back"], "kp": c["kp_id"], "line": "mine"}
    known = cache.get("known_recent") or set()
    w = next((x for x in rng.sample(words, min(len(words), 12)) if x["w"].lower() not in known), None) or rng.choice(words)
    return {**w, "line": "list"}


# ------------------------------------------------------------------ 每天学多少

def keep_rate(user_id: int) -> tuple[float | None, int]:
    """最近 7 天里，隔了 KEEP_GAP_DAYS 天以上又见到的词，第一次就答对的比例（隔几天还记得多少）。返回 (比例, 样本数)。"""
    since = (db.today() - timedelta(days=35)).isoformat()
    week = (db.today() - timedelta(days=7)).isoformat()
    last: dict[str, str] = {}
    hit = n = 0
    for r in db.q("SELECT target_id, correct, created_at FROM events WHERE user_id=? AND target='word' AND kind='answer' AND created_at>=? "
                  "ORDER BY id", user_id, since):
        w, day = r["target_id"].rsplit(":", 1)[-1].lower(), str(r["created_at"])[:10]
        prev = last.get(w)
        if prev and prev != day and day >= week and (date.fromisoformat(day) - date.fromisoformat(prev)).days >= KEEP_GAP_DAYS:
            n += 1
            hit += 1 if r["correct"] else 0
        last[w] = day
    return (hit / n if n else None), n


def cap(user_id: int, p: Pool | None = None) -> int:
    """今天最多学几个新词（每日任务里的 + 抓词里多学的）。按学段一个基数，隔几天还记得的少就降一档、多就升一档。"""
    p = p or Pool(user_id)
    base = CAP[p.band]
    rate, n = keep_rate(user_id)
    if rate is not None and n >= KEEP_MIN_SAMPLES:
        base += -CAP_STEP if rate < KEEP_LOW else CAP_STEP if rate > KEEP_HIGH else 0
    return max(CAP_MIN, min(CAP_MAX, base))


def new_today(user_id: int) -> int:
    """今天已经加了几个新词（所有包、所有路）。"""
    return sum(1 for r in db.q("SELECT extra FROM cards WHERE user_id=? AND kind IN ('word','term') AND created_at>=?",
                               user_id, db.today().isoformat()) if (db.jload(r["extra"], {}) or {}).get("new"))


def room_today(user_id: int, p: Pool | None = None) -> int:
    """今天还能学几个新词：上限减去已经学的；欠的复习太多也先不学新的（和每日任务同一条规矩）。"""
    words = _w()
    backlog = words.due_backlog(user_id)
    if backlog >= words.BACKLOG_STOP:
        return 0
    left = cap(user_id, p) - new_today(user_id)
    return max(0, left // 2 if backlog >= words.BACKLOG_HALF else left)


# ------------------------------------------------------------------ 抓词的一组

def add_new(user_id: int, w: dict) -> int | None:
    """把一个新词放进单词卡（今天学：复习页第一次见它是学习卡）。"""
    from . import engine
    kind = "term" if w.get("list") and w["list"] in _w().packs() and _w().is_terms(_w().packs()[w["list"]]) else "word"
    extra = {"pos": w.get("pos", ""), "list": w.get("list") or "", "new": 1, "line": w.get("line", "book")}
    if w.get("unit") is not None:
        extra["unit"] = w["unit"]
    if w.get("root"):
        extra["root"] = w["root"]
    if w.get("count_as"):
        extra["for"] = w["count_as"]
    return engine.add_card(user_id, kind, w["w"], w["zh"], extra, w.get("kp") or None, due=db.today())


def add_check(user_id: int, w: dict) -> int | None:
    """往前补的词：放进单词卡，先认一认（check）。认得就算认识（7 天后再见），不认得变成要学的新词。"""
    from . import engine
    return engine.add_card(user_id, "word", w["w"], w["zh"], {"pos": w.get("pos", ""), "list": w.get("list") or "", "check": 1, "line": "back"},
                           due=db.today())


def _push_cards(user_id: int, p: Pool, skip_ids: set[int], n: int) -> list:
    """往上推一级的词：有卡片、还没到期、还没到「用得对」、今天还没答过的，最久没见的先。"""
    if n <= 0:
        return []
    from . import engine
    today = db.today().isoformat()
    seen = last_seen(user_id)
    paused = engine.Paused(user_id)
    rows = [c for c in db.q("SELECT * FROM cards WHERE user_id=? AND kind IN ('word','term') AND due>? AND reviews>0", user_id, today)
            if c["id"] not in skip_ids and not paused.card(c) and seen.get(c["front"].lower(), "") < today
            and p.ladder.get(c["front"].lower(), {}).get("level", 0) < 5]
    rows.sort(key=lambda c: (not p.ladder.get(c["front"].lower(), {}).get("wobbly"), seen.get(c["front"].lower(), "")))
    return rows[:n]


def make_set(user_id: int, size: int = SET_SIZE, due_only: bool = False) -> dict:
    """抓词的一组：到期要复习的（含今天的新词）先 → 还能学新词就再加 1～3 个新词 → 往前补的词认一认 → 都没有了，把学过的词往上推一级。
    due_only：每日任务里（TASK_URL）只出到期的——今天清单上写的就是这些词，多学的留给抓词自己。
    返回 {id, cards: [(卡片, 角色)], room}；角色：due / new / check / push。一个都没有返回 {id: None, cards: []}。"""
    from . import engine
    p = Pool(user_id)
    _w().daily_new(user_id)   # 每日任务的新词先放进来（今天还没排过计划也一样）
    due = list(engine.due_cards(user_id, 300, "words"))
    chosen = [(c, "due") for c in due[:size]]
    room = room_today(user_id, p)
    if due_only:
        size = len(chosen)
    if len(chosen) < size:
        k = min(SET_NEW_MAX, room, size - len(chosen))
        have = {c["front"].lower() for c, _ in chosen}
        for w in new_words(user_id, k, have, p=p):
            cid = add_new(user_id, w)
            if cid:
                chosen.append((db.one("SELECT * FROM cards WHERE id=?", cid), "new"))
    if len(chosen) < size:
        have = {c["front"].lower() for c, _ in chosen}
        for w in old_words(user_id, min(SET_BACK_MAX, size - len(chosen)), p, have):
            cid = add_check(user_id, w)
            if cid:
                chosen.append((db.one("SELECT * FROM cards WHERE id=?", cid), "check"))
    if len(chosen) < size:
        for c in _push_cards(user_id, p, {c["id"] for c, _ in chosen}, size - len(chosen)):
            chosen.append((c, "push"))
    if not chosen:
        return {"id": None, "cards": [], "room": room}
    sid = db.insert("INSERT INTO word_sets(user_id, day, cards, created_at) VALUES(?,?,?,?)", user_id, db.today().isoformat(),
                    db.jdump([{"id": c["id"], "role": r} for c, r in chosen]), db.now())
    return {"id": sid, "cards": chosen, "room": max(0, room - sum(1 for _, r in chosen if r == "new"))}


def _set(user_id: int, sid) -> dict | None:
    r = db.one("SELECT * FROM word_sets WHERE id=? AND user_id=?", sid, user_id)
    return {**dict(r), "cards": db.jload(r["cards"], []) or []} if r else None


def _rows(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    return {r["id"]: dict(r) for r in db.q(f"SELECT * FROM cards WHERE id IN ({','.join('?' * len(ids))})", *ids)}


def summary(user_id: int, sid) -> dict | None:
    """一组做完的小结：新抓住的、找回来的、认识的（「我认识」和认一认）、更熟了的、还调皮的（最后一次答错的），
    认识的词这一组前后各多少；还能不能再来一组；送不送一局跑酷。第一次调的时候记下这一组做完了。"""
    s = _set(user_id, sid)
    if not s:
        return None
    if not s["done_at"]:
        s["done_at"] = db.now()
        db.run("UPDATE word_sets SET done_at=? WHERE id=?", s["done_at"], s["id"])
    cards = _rows([x["id"] for x in s["cards"]])
    role = {x["id"]: x["role"] for x in s["cards"]}
    by_word: dict[str, list] = {}
    for r in db.q("SELECT target_id, correct, created_at FROM events WHERE user_id=? AND target='word' AND kind='answer' ORDER BY id", user_id):
        by_word.setdefault(r["target_id"].rsplit(":", 1)[-1].lower(), []).append(r)
    out = {"new": [], "back": [], "known": [], "up": [], "wobbly": []}
    first = 0
    for cid, c in cards.items():
        w = c["front"]
        evs = by_word.get(w.lower(), [])
        mine = [e for e in evs if str(e["created_at"]) >= str(s["created_at"])]
        if not mine:
            continue
        before_ok = any(e["correct"] for e in evs if str(e["created_at"]) < str(s["created_at"]))
        ok_now = any(e["correct"] for e in mine)
        first += 1 if ok_now and not before_ok else 0
        ex = db.jload(c["extra"], {}) or {}
        if not mine[-1]["correct"]:
            out["wobbly"].append(w)
        elif ex.get("known"):
            out["known"].append(w)
        elif role.get(cid) == "push":
            out["up"].append(w)
        elif ex.get("new") and not before_ok:
            out["new"].append(w)
        else:
            out["back"].append(w)
    total = sum(1 for v in _w().ladder(user_id).values() if v["ok"])
    from . import engine
    due_left = len(engine.due_cards(user_id, 300, "words"))
    return {**out, "known_total": total, "known_before": total - first, "due_left": due_left, "room": room_today(user_id),
            "reward": reward_ok(user_id, s), "more": bool(due_left or room_today(user_id) or old_words(user_id, 1)
                                                            or _push_cards(user_id, Pool(user_id), set(), 1))}


REWARD_PER_DAY = 3


def _spellable(w: str) -> bool:
    return w.isascii() and w.isalpha() and 3 <= len(w) <= 10


def reward_words(user_id: int, s: dict) -> list[dict]:
    """送的那一局跑酷出哪些词：这一组里能拼的词（{w, zh, list, card?}）。"""
    cards = _rows([x["id"] for x in s["cards"]])
    return [{"w": c["front"], "zh": (c["back"] or "").split("\n")[0][:24], "list": (db.jload(c["extra"], {}) or {}).get("list") or ""}
            for c in cards.values() if _spellable(c["front"]) and c["back"]]


def reward_ok(user_id: int, s: dict | int | None) -> bool:
    """这一组做完了、还没用过奖励、今天送的还不到 REWARD_PER_DAY 局、有至少 2 个能拼的词：可以送一局跑酷（不算游戏时间）。"""
    s = _set(user_id, s) if isinstance(s, int) or (isinstance(s, str) and s.isdigit()) else s
    if not s or not s.get("done_at") or s.get("reward_match"):
        return False
    used = (db.one("SELECT COUNT(*) AS n FROM word_sets WHERE user_id=? AND day=? AND reward_match IS NOT NULL",
                   user_id, db.today().isoformat()) or {}).get("n") or 0
    return used < REWARD_PER_DAY and len(reward_words(user_id, s)) >= 2


def take_reward(user_id: int, sid) -> list[dict] | None:
    """开奖励局：可以就返回这一组的词（调用方开局后用 bind_reward 记下是哪一局），不可以返回 None。"""
    s = _set(user_id, sid)
    if not s or not s["done_at"] or not reward_ok(user_id, s):
        return None
    return reward_words(user_id, s)


def bind_reward(user_id: int, sid, match_id: int) -> None:
    db.run("UPDATE word_sets SET reward_match=? WHERE id=? AND user_id=? AND reward_match IS NULL", match_id, sid, user_id)


def parent_summary(user_id: int) -> dict | None:
    """家长页的一行：这一周新学了几个词、其中抓牢了几个，做了几组抓词，隔几天还记得的比例，每天新词上限。没学过单词返回 None。"""
    words = _w()
    since = (db.today() - timedelta(days=6)).isoformat()
    lad = words.ladder(user_id)
    if not lad and not db.one("SELECT 1 FROM cards WHERE user_id=? AND kind IN ('word','term') LIMIT 1", user_id):
        return None
    fresh = [r["front"] for r in db.q("SELECT front, extra FROM cards WHERE user_id=? AND kind IN ('word','term') AND created_at>=?", user_id, since)
             if (db.jload(r["extra"], {}) or {}).get("new")]
    caught = [w for w in fresh if lad.get(w.lower(), {}).get("level", 0) >= words.CAUGHT]
    sets = (db.one("SELECT COUNT(*) AS n FROM word_sets WHERE user_id=? AND day>=? AND done_at IS NOT NULL", user_id, since) or {}).get("n") or 0
    rate, n = keep_rate(user_id)
    return {"known": sum(1 for v in lad.values() if v["ok"]), "week_new": len(fresh), "week_caught": caught[:12], "sets": sets,
            "keep": round(rate * 100) if rate is not None and n >= KEEP_MIN_SAMPLES else None, "cap": cap(user_id)}
