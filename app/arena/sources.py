"""游戏的题从哪来：几种「题源」，每种都能按孩子的水平出 20 秒内答得完的短题。

一种题源要回答四个问题：
- weight(kid, ctx)：这个孩子能不能用、混合出题时占多少（0 = 不能用）；
- pick(kid, ctx, target, rng)：出一道题，答对概率尽量接近目标；
- record(kid, item, correct, ...)：答完怎么记进学习记录（掌握度、生词本）；
- 每道题都带 dim（能力维度，存 θ）、family / level（难度 b 的校准单位）和 p（出题时预测的答对概率，公平看板用）。

现有题源：
- math：沪教版一到六年级的口算、短计算，按题族现场生成（app/quickgen.py），答案由程序算出；
- words：英语核心词和教材术语（content/words_en.json 的词表 + 孩子自己的生词卡），四选一认词义；
- bank：题库里孩子学过的知识点上的短选择题（AI 出过、存下来的题），任何学科都能用。
加一种题源：写一个 Source 子类，登记到 SOURCES；游戏和经验值规则都不用改。
"""
import hashlib
import random

from .. import db, engine, evidence, explore, itemtypes, quickgen
from ..catalog import catalog, stage_rank
from ..content import content
from . import fair

GAME_WEIGHT = 0.5   # 游戏里限时作答，作为掌握度证据打对折（不会因为手快手慢误判）


class Ctx(dict):
    """一次出题用到的孩子数据，按需读取、只读一次。"""

    def __init__(self, kid: dict):
        super().__init__()
        self.kid = kid

    def get_or(self, key, fn):
        if key not in self:
            self[key] = fn()
        return self[key]

    @property
    def mastery(self) -> dict:
        return self.get_or("mastery", lambda: engine.get_mastery(self.kid["id"]))

    @property
    def taught(self) -> set:
        return self.get_or("taught", lambda: engine.taught_set(self.kid["id"]))

    @property
    def packs(self) -> list[dict]:
        return self.get_or("packs", lambda: [dict(e) for e in db.q(
            "SELECT pack_id, stage FROM enrollments WHERE user_id=? AND active=1", self.kid["id"])
            if e["pack_id"] in catalog.packs])

    @property
    def subjects(self) -> set:
        return {catalog.packs[e["pack_id"]].subject for e in self.packs}

    @property
    def rank(self) -> float:
        r = stage_rank(self.kid.get("grade") or "G3")
        return r if r < 99 else 6.0


def kp_ability(ctx: Ctx, kp_id: str, rank: float | None = None) -> tuple[float, int]:
    """知识点上的 θ：玩过就用游戏里校准的；没玩过从掌握度（BKT 的「现在还记得的概率」）起步；都没有按学段估。"""
    got = fair.stored_ability(ctx.kid["id"], kp_id)
    if got:
        return got
    m = ctx.mastery.get(kp_id)
    if m and m.get("attempts"):
        p = evidence.now_p(ctx.kid["id"], dict(m))
    else:
        kp = catalog.kps.get(kp_id)
        gap = (rank if rank is not None else ctx.rank) - stage_rank(kp["stage"]) if kp else 0
        p = 0.8 if gap >= 1 else 0.5
    return fair.logit(p), 0


def _status_weight(ctx: Ctx, kp_id: str, behind: bool) -> float:
    """学习中、薄弱的多出（按水平出题，所以薄弱也答得上），掌握了的偶尔复习。"""
    status = (ctx.mastery.get(kp_id) or {}).get("status") or "unknown"
    w = {"learning": 3.0, "weak": 2.0, "mastered": 1.2}.get(status)
    if w is None:
        w = 1.0 if behind else (1.5 if kp_id in ctx.taught else 0.4)
    return w


def _mcq(q: str, right: str, wrongs: list[str], rng: random.Random) -> dict:
    opts = [right] + wrongs[:3]
    rng.shuffle(opts)
    return {"type": "mcq", "q": q, "options": opts, "answer": opts.index(right)}


class Source:
    id = name = icon = desc = ""
    target_shift = 0.0   # 这种题本身更容易蒙对（四选一），目标答对率稍微调高一点

    def weight(self, kid: dict, ctx: Ctx) -> float:
        return 0.0

    def pick(self, kid: dict, ctx: Ctx, target: float, rng: random.Random) -> dict:
        raise NotImplementedError

    def record(self, kid: dict, it: dict, correct: bool, ans, dont_know: bool, ms: int, mode="game", weight=GAME_WEIGHT):
        if it.get("kp_id"):
            engine.record_attempt(kid["id"], it, it["kp_id"], mode, correct, "" if dont_know else ans,
                                  dont_know=dont_know, touch=False, ms=ms, weight=weight)


# ------------------------------------------------------------------ 数学口算（现场生成）

MATH_PACK = "math-shanghai"


class MathSource(Source):
    id, name, icon, desc = "math", "数学口算", "🔢", "口算和短计算，数字现场生成"

    def _rank(self, ctx: Ctx) -> float:
        e = next((e for e in ctx.packs if e["pack_id"] == MATH_PACK), None)
        r = stage_rank(e["stage"]) if e else ctx.rank
        return r if r < 99 else 6.0

    def pool(self, ctx: Ctx, target: float) -> list[tuple[str, float]]:
        rank = self._rank(ctx)
        out = []
        for fam, (kp_id, _, _) in quickgen.FAMILIES.items():
            kp = catalog.kps.get(kp_id)
            if not kp or stage_rank(kp["stage"]) > rank:
                continue
            r = stage_rank(kp["stage"])
            w = _status_weight(ctx, kp_id, r < rank)
            theta, _ = kp_ability(ctx, kp_id, rank)
            # 这个知识点最合适的一级也离目标很远（太难或太简单）：少出，换别的知识点
            w *= max(0.05, 1 - 2.5 * fair.fit_gap(theta, fair.calib(fam, quickgen.LEVELS), target))
            out.append((kp_id, w / (1 + 0.35 * (rank - r))))
        return out or [(quickgen.FAMILIES["add10"][0], 1.0)]

    def weight(self, kid, ctx):
        maths = {"math"} & ctx.subjects
        if ctx.rank > 9:      # 高中生做小学口算没意思
            return 0.15
        return 1.0 if maths else 0.4

    def pick(self, kid, ctx, target, rng):
        pool = self.pool(ctx, target)
        kp_id = rng.choices([k for k, _ in pool], weights=[w for _, w in pool])[0]
        fam = quickgen.BY_KP[kp_id]
        theta, _ = kp_ability(ctx, kp_id, self._rank(ctx))
        levels = fair.calib(fam, quickgen.LEVELS)
        lv = fair.pick_level(theta, levels, target, rng)
        it = quickgen.generate(fam, lv, rng)
        it.update(dim=kp_id, theta=theta, b=levels[lv][0], p=round(fair.sigmoid(theta - levels[lv][0]), 4),
                  topic=quickgen.FAMILIES[fam][2])
        return it


# ------------------------------------------------------------------ 英语单词 / 教材术语（四选一）

class WordSource(Source):
    """英语核心词表按年级分 5 级（同一个能力维度 vocab:en）；教材术语表（比如 IGCSE 物理术语）各自一个维度。
    孩子自己的生词卡、术语卡优先出（三成左右），这样游戏也在帮着复习。"""
    id, name, icon, desc = "words", "单词", "🔤", "英语单词和课本术语，四选一认意思"
    target_shift = 0.05

    @staticmethod
    def _lists(ctx: Ctx) -> list[dict]:
        return ctx.get_or("word_lists", lambda: [wl for wl in explore.word_lists(ctx.kid["id"])
                                                if [w for w in wl["words"] if w.get("zh")]])

    @staticmethod
    def _core_level(wl: dict) -> int:
        lo = stage_rank(wl["stage"].split("-")[0])
        return 1 if lo <= 2 else 2 if lo <= 3 else 3 if lo <= 5 else 4 if lo <= 7 else 5

    @staticmethod
    def _prior(level: int) -> float:
        return 0.9 * (level - 3)

    def _groups(self, ctx: Ctx) -> list[dict]:
        """能力维度分组：英语核心词一组（按年级分级），每份教材术语表一组（单级）。"""
        core = [wl for wl in self._lists(ctx) if wl.get("subject") == "english"]
        out = []
        if core:
            by_lv: dict[int, list] = {}
            for wl in core:
                by_lv.setdefault(self._core_level(wl), []).append(wl)
            out.append({"dim": "vocab:en", "family": "words", "levels": by_lv, "title": "英语单词"})
        for wl in self._lists(ctx):
            if wl.get("packs") and not wl.get("subject"):
                out.append({"dim": f"vocab:{wl['id']}", "family": f"terms:{wl['id']}", "levels": {3: [wl]},
                            "title": wl["title"]})
        return out

    def _theta(self, ctx: Ctx, g: dict) -> tuple[float, int]:
        got = fair.stored_ability(ctx.kid["id"], g["dim"])
        if got:
            return got
        if g["family"] == "words":   # 和孩子同年级的词表答对率约 75%
            lv_of_grade = 1 if ctx.rank <= 2 else 2 if ctx.rank <= 3 else 3 if ctx.rank <= 5 else 4 if ctx.rank <= 7 else 5
            return self._prior(lv_of_grade) + 1.1, 0
        return 1.1, 0

    def weight(self, kid, ctx):
        if not self._groups(ctx):
            return 0.0
        return 1.2 if "english" in ctx.subjects else 0.6

    def _my_cards(self, ctx: Ctx) -> list:
        return ctx.get_or("word_cards", lambda: db.q(
            "SELECT front, back, kind, kp_id FROM cards WHERE user_id=? AND kind IN ('word','term') AND LENGTH(back)<=24 "
            "ORDER BY due LIMIT 60", ctx.kid["id"]))

    def pick(self, kid, ctx, target, rng):
        groups = self._groups(ctx)
        g = rng.choices(groups, weights=[3 if x["family"] == "words" else 1 for x in groups])[0]
        theta, _ = self._theta(ctx, g)
        levels = fair.calib(g["family"], list(g["levels"]), self._prior)
        lv = fair.pick_level(theta, levels, target, rng)
        wl = rng.choice(g["levels"][lv])
        words = [w for w in wl["words"] if w.get("zh")]
        cards = [c for c in self._my_cards(ctx) if (c["kind"] == "word") == (g["family"] == "words")]
        if cards and rng.random() < 0.3:   # 自己的生词 / 术语卡
            c = rng.choice(cards)
            w = {"w": c["front"], "zh": c["back"], "kp": c["kp_id"]}
        else:
            w = rng.choice(words)
        others = list({x["zh"] for x in words if x["zh"] != w["zh"] and x["w"] != w["w"]})
        if len(others) < 3:
            others += [x["zh"] for x in rng.sample(content.word_lists[next(iter(content.word_lists))]["words"], 6)]
        rng.shuffle(others)
        zh_first = g["family"] == "words" and lv >= 3 and rng.random() < 0.3   # 高级别有时反过来：看中文选英文
        if zh_first:
            eng = list({x["w"] for x in words if x["w"] != w["w"]})
            rng.shuffle(eng)
            it = _mcq(f"「{w['zh']}」用英语怎么说？", w["w"], eng, rng)
        else:
            it = _mcq(f"{w['w']}", w["zh"], others, rng)
            it["say"] = w["w"]   # 前端可以读出来
        h = hashlib.sha1(f"{wl['id']}|{w['w']}|{zh_first}".encode()).hexdigest()[:10]
        it.update(id=f"word-{h}", family=g["family"], level=lv, dim=g["dim"], word=w["w"], zh=w["zh"], list=wl["id"],
                  kp_id=w.get("kp") or "", topic=g["title"], theta=theta, b=levels[lv][0], p=round(fair.sigmoid(theta - levels[lv][0]), 4),
                  explain=f"{w['w']}：{w['zh']}")
        return it

    def record(self, kid, it, correct, ans, dont_know, ms, mode="game", weight=GAME_WEIGHT):
        evidence.log(kid["id"], "word", f"{it['list']}:{it['word']}", "answer", correct=correct, mode=mode,
                     fmt="choice", weight=weight)
        if it.get("kp_id"):   # 术语卡挂了知识点：也算这个知识点的一条证据
            super().record(kid, it, correct, ans, dont_know, ms, mode, weight)
        if not correct:       # 不认识的词放进复习
            engine.add_card(kid["id"], "word" if it["family"] == "words" else "term", it["word"], it["zh"],
                            {"list": it["list"], "from": mode}, it.get("kp_id") or None)


# ------------------------------------------------------------------ 题库里的短选择题（任何学科）

def _short(d: dict) -> bool:
    opts = d.get("options") or []
    return (len(str(d.get("q", ""))) <= 90 and len(opts) == 4 and all(len(str(o)) <= 36 for o in opts)
            and not d.get("code") and not d.get("passage") and not d.get("image"))


def bank_level(r) -> int:
    """题目难度分 5 级：做过的人多就看大家的正确率，少就看出题时标的难度。"""
    n, c = r["n_attempts"] or 0, r["n_correct"] or 0
    if n >= 5:
        acc = c / n
        return 1 if acc >= 0.85 else 2 if acc >= 0.7 else 3 if acc >= 0.55 else 4 if acc >= 0.4 else 5
    return {1: 2, 2: 3, 3: 4}.get(r["difficulty"] or 2, 3)


class BankSource(Source):
    id, name, icon, desc = "bank", "我的科目", "📚", "学过的知识点上的短选择题（语文、英语、物理……）"
    target_shift = 0.05

    def _items(self, ctx: Ctx) -> list[dict]:
        def load():
            kps = [k for k, m in ctx.mastery.items() if m.get("attempts")] + list(ctx.taught)
            enrolled = {e["pack_id"] for e in ctx.packs}
            kps = [k for k in dict.fromkeys(kps) if (kp := catalog.kps.get(k)) and kp.get("pack") in enrolled
                   and stage_rank(kp["stage"]) <= ctx.rank + 0.5]
            if not kps:
                return []
            out = []
            for i in range(0, len(kps), 400):
                part = kps[i:i + 400]
                rows = db.q("SELECT i.id, i.kp_id AS own_kp, i.type, i.difficulty, i.data, i.n_attempts, i.n_correct, x.kp_id "
                            "FROM items i JOIN item_kps x ON x.item_id=i.id AND x.role='main' "
                            f"WHERE i.status='active' AND i.type='mcq' AND i.source<>'paper' AND x.kp_id IN ({','.join('?' * len(part))}) "
                            "AND i.id NOT IN (SELECT target_id FROM flags WHERE user_id=? AND target='item')",
                            *part, ctx.kid["id"])
                for r in rows:
                    d = db.jload(r["data"], {})
                    if _short(d):
                        out.append({"row": r, "data": d, "kp_id": r["kp_id"], "level": bank_level(r)})
            return out
        return ctx.get_or("bank_items", load)

    def weight(self, kid, ctx):
        n = len({x["kp_id"] for x in self._items(ctx)})
        return 0.0 if n == 0 else min(1.5, 0.3 + n / 8)

    def pick(self, kid, ctx, target, rng):
        items = self._items(ctx)
        by_kp: dict[str, list] = {}
        for x in items:
            by_kp.setdefault(x["kp_id"], []).append(x)
        kps = list(by_kp)
        kp_id = rng.choices(kps, weights=[_status_weight(ctx, k, True) for k in kps])[0]
        theta, _ = kp_ability(ctx, kp_id)
        cand = by_kp[kp_id]
        levels_all = fair.calib("bank", quickgen.LEVELS)
        levels = {lv: levels_all[lv] for lv in {x["level"] for x in cand}}
        lv = fair.pick_level(theta, levels, target, rng)
        x = rng.choice([c for c in cand if c["level"] == lv])
        d, r = x["data"], x["row"]
        opts = list(d["options"])
        order = list(range(len(opts)))
        rng.shuffle(order)   # 选项打乱，答案跟着换
        it = {**d, "id": r["id"], "type": "mcq", "options": [opts[i] for i in order], "answer": order.index(int(d["answer"])),
              "family": "bank", "level": lv, "dim": kp_id, "kp_id": kp_id, "difficulty": r["difficulty"],
              "topic": (catalog.kps.get(kp_id) or {}).get("name", ""), "theta": theta, "b": levels[lv][0], "p": round(fair.sigmoid(theta - levels[lv][0]), 4)}
        return it


SOURCES: dict[str, Source] = {s.id: s for s in (MathSource(), WordSource(), BankSource())}


def available(kid: dict, ctx: Ctx | None = None) -> list[dict]:
    """孩子能选的题源（游戏开始前选「出什么题」），第一项是「混合」。"""
    ctx = ctx or Ctx(kid)
    out = []
    for s in SOURCES.values():
        w = s.weight(kid, ctx)
        if w > 0:
            out.append({"id": s.id, "name": s.name, "icon": s.icon, "desc": s.desc, "weight": round(w, 2)})
    if len(out) > 1:
        out.insert(0, {"id": "mix", "name": "混合", "icon": "🎲", "desc": "按你在学的科目混着出", "weight": 0})
    return out


def pick(kid: dict, src: str, target: float, rng: random.Random) -> dict:
    ctx = Ctx(kid)
    if src in SOURCES and SOURCES[src].weight(kid, ctx) > 0:
        s = SOURCES[src]
    else:
        ws = [(s, s.weight(kid, ctx)) for s in SOURCES.values()]
        ws = [(s, w) for s, w in ws if w > 0] or [(SOURCES["math"], 1.0)]
        s = rng.choices([s for s, _ in ws], weights=[w for _, w in ws])[0]
    it = s.pick(kid, ctx, min(0.92, target + s.target_shift), rng)
    it["src"] = s.id
    return it


def record(kid: dict, it: dict, correct: bool, ans, dont_know: bool, ms: int, mode="game", weight=GAME_WEIGHT):
    SOURCES.get(it.get("src") or "math", SOURCES["math"]).record(kid, it, correct, ans, dont_know, ms, mode, weight)


def public(it: dict) -> dict:
    """发给前端的题（不含答案）。"""
    t = itemtypes.of(it)
    keypad = it.get("keypad") or ("dec" if t.id == "num" else "")
    return {"id": it["id"], "q": it["q"], "type": t.id, "widget": t.widget, "unit": it.get("unit", ""),
            "options": it.get("options"), "keypad": keypad, "topic": it.get("topic", ""), "src": it.get("src", ""),
            "say": it.get("say", "")}
