"""游戏的题从哪来：几种「题源」，每种都能按孩子的水平出 20 秒内答得完的短题。

题不让孩子选，每个孩子出自己的题：pick 按这个孩子在学的科目、每个知识点的掌握度（个性化学习系统的记录）
挑题源和知识点，难度按他自己的 θ 定。一起玩的孩子各答各的题，高中生和小学生一起玩也各自答得上。
题只出自这个人在学的东西（在学的课、在用的单词包），知识点和每天的学习用同一把尺子筛（engine.Paused：
退掉的课、不是给这群人的课、还没上过的课都不出）。一种都出不了就直说（asks），不拿一年级口算凑：
成人没选数学课，游戏里就不出口算。

一种题源要回答四个问题：
- weight(kid, ctx)：这个孩子能不能用、出题时占多少（0 = 不能用）；
- pick(kid, ctx, target, rng)：出一道题，答对概率尽量接近目标；
- record(kid, item, correct, ...)：答完怎么记进学习记录（掌握度、生词本）；
- 每道题都带 dim（能力维度，存 θ）、family / level（难度 b 的校准单位）和 p（出题时预测的答对概率，公平看板用）。

现有题源：
- math：一到初中的口算、短计算，按题族现场生成（app/quickgen.py），答案由程序算出；只给学数学、物理这些课的人；
- words：英语核心词和教材术语（content/words_en.json 的词表 + 孩子自己的生词卡），四选一认词义；
- bank：题库里孩子学过的知识点上的短选择题（AI 出过、存下来的题），任何学科都能用。
加一种题源：写一个 Source 子类，登记到 SOURCES；游戏和经验值规则都不用改。

一次快练、一局游戏里的出题顺序（next_item / settle，冲刺、快练和所有游戏共用）：
- 出过的题这一次里不再出（单词按词算：看英文选意思、看中文选英文、听音、拼写都算同一个词）；
- 答错或点「不会」的题，隔 4 道再考一次，只考这一次：刚看完答案马上再答只是短时记忆，
  真记住要隔天、换个题再答对（掌握模型就是这么判的），所以以后交给隔天的复习，不在一次里反复刷；
- 这道「再来一次」的题作为证据只记一半，也不拿来估能力值和题目难度（刚看过答案）。
"""
import hashlib
import random
import re
from datetime import timedelta

from .. import ability, db, engine, evidence, explore, itemtypes, quickgen, wordpick
from .. import words as words_mod
from ..catalog import catalog, stage_rank
from ..content import content
from .errors import ArenaError

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
            "SELECT pack_id, stage, track FROM enrollments WHERE user_id=? AND active=1", self.kid["id"])
            if e["pack_id"] in catalog.packs])

    @property
    def paused(self) -> engine.Paused:
        """退掉的课留下的卡片不出（engine.Paused）。"""
        return self.get_or("paused", lambda: engine.Paused(self.kid["id"]))

    @property
    def subjects(self) -> set:
        return {catalog.packs[e["pack_id"]].subject for e in self.packs}

    @property
    def run(self) -> dict:
        """这一次快练 / 这一局里每个知识点（能力维度）已经出过几道（next_item 填进来）。"""
        return self.get("run") or {}

    @property
    def due(self) -> set:
        """学会过、现在快忘了的知识点（该复习了）。"""
        return self.get_or("due", lambda: {m["kp_id"] for m in evidence.due_checks(self.kid["id"], self.mastery, limit=30)})

    @property
    def rank(self) -> float:
        r = stage_rank(self.kid.get("grade") or "G3")
        return r if r < 99 else 6.0


def kp_ability(ctx: Ctx, kp_id: str, rank: float | None = None) -> tuple[float, int]:
    """知识点上的 θ（全站同一个，见 app/ability.py）；还没有就从掌握度起步，掌握度也没有按学段估。"""
    kp = catalog.kps.get(kp_id)
    gap = (rank if rank is not None else ctx.rank) - stage_rank(kp["stage"]) if kp else 0
    return ability.start(ctx.kid["id"], kp_id, 0.8 if gap >= 1 else 0.5)


DUE_BOOST = 2.5   # 该复习的知识点（记得的概率掉到目标以下）在快练和游戏里多出：答对了就是一次复习


def _status_weight(ctx: Ctx, kp_id: str, behind: bool) -> float:
    """学习中、薄弱的多出（按水平出题，所以薄弱也答得上），掌握了的偶尔复习，快忘了的（该复习了）再多出。"""
    status = (ctx.mastery.get(kp_id) or {}).get("status") or "unknown"
    w = {"learning": 3.0, "weak": 2.0, "mastered": 1.2}.get(status)
    if w is None:
        w = 1.0 if behind else (1.5 if kp_id in ctx.taught else 0.4)
    return w * (DUE_BOOST if kp_id in ctx.due else 1)


def _mcq(q: str, right: str, wrongs: list[str], rng: random.Random) -> dict:
    opts = [right] + wrongs[:3]
    rng.shuffle(opts)
    return {"type": "mcq", "q": q, "options": opts, "answer": opts.index(right)}


class Source:
    id = name = icon = desc = ""
    target_shift = 0.0   # 这种题本身更容易蒙对（四选一），目标答对率稍微调高一点
    redraw = False       # 这次没出成，再抽一次可能就成（现场生成的题：换一族、换一级）

    def weight(self, kid: dict, ctx: Ctx) -> float:
        return 0.0

    def pick(self, kid: dict, ctx: Ctx, target: float, rng: random.Random) -> dict:
        raise NotImplementedError

    def record(self, kid: dict, it: dict, correct: bool, ans, dont_know: bool, ms: int, mode="game", weight=GAME_WEIGHT,
               calibrate: bool | None = None):
        """记进学习记录；engine.record_attempt 同时更新 θ（刚看过答案再答的那一道不更新）。"""
        if it.get("kp_id"):
            engine.record_attempt(kid["id"], it, it["kp_id"], mode, correct, "" if dont_know else ans,
                                  dont_know=dont_know, touch=False, ms=ms, weight=weight,
                                  calibrate=not it.get("retry") if calibrate is None else calibrate)


# ------------------------------------------------------------------ 数学口算（现场生成）

FAR_BEHIND = 3      # 比孩子低 3 个年级以上的口算，除非这个知识点查出薄弱，不再出


class MathSource(Source):
    """只给在学数学（或学物理、化学这些用得上数学的课）的人出：没学数学的人（比如只选了成人课的家长）一道口算也不出。
    知识点也按每天的学习同一把尺子筛（ctx.paused：退掉的课、不是给这群人的课、还没上过的课都不出），
    比自己低 FAR_BEHIND 个年级以上的不出（查出薄弱的除外）。一个都剩不下就是这个人用不上口算（weight = 0），不拿一年级的题凑。"""
    id, name, icon, desc = "math", "口算和公式", "🔢", "口算、短计算、单位换算和代公式，数字现场生成"
    redraw = True
    USERS = {"math", "physics", "chemistry", "science", "economics"}   # 数学是这些课的地基（见 _meta/ladder.json）

    def _rank(self, ctx: Ctx) -> float:
        e = next((e for e in ctx.packs if catalog.packs[e["pack_id"]].subject == "math"), None)
        r = stage_rank(e["stage"]) if e else ctx.rank
        return r if r < 99 else 6.0

    def _kps(self, ctx: Ctx) -> list[tuple[str, float]]:
        """这个人能出的口算知识点和它的学段（和目标、这一局出过几道无关，一次出题只算一遍）。"""
        def load():
            if not self.USERS & ctx.subjects:
                return []
            rank, hold = self._rank(ctx), ctx.paused.kps
            enrolled = {e["pack_id"] for e in ctx.packs}
            out = []
            for kp_id in quickgen.BY_KP:
                kp = catalog.kps.get(kp_id)
                if not kp or kp_id in hold:
                    continue
                if catalog.packs[kp["pack"]].subject != "math":   # 别的学科的基本功（物理的单位、公式）：只给报了那门课的人，按正在学的算
                    if enrolled & set(catalog.kp_packs.get(kp_id, [])):
                        out.append((kp_id, rank))
                    continue
                r = stage_rank(kp["stage"])
                status = (ctx.mastery.get(kp_id) or {}).get("status")
                if r > rank or (rank - r >= FAR_BEHIND and status not in ("weak", "learning")):   # 初二做 10 以内加减没意义；查出薄弱了才出
                    continue
                out.append((kp_id, r))
            return out
        return ctx.get_or("math_kps", load)

    def pool(self, ctx: Ctx, target: float) -> list[tuple[str, float]]:
        rank = self._rank(ctx)
        out = []
        for kp_id, r in self._kps(ctx):
            w = _status_weight(ctx, kp_id, r < rank)
            theta, _ = kp_ability(ctx, kp_id, rank)
            # 这个知识点最合适的一级也离目标很远（太难或太简单）：少出，换别的知识点
            w *= max(0.05, 1 - 2.5 * ability.fit_gap(theta, ability.calib(quickgen.BY_KP[kp_id], quickgen.LEVELS), target))
            out.append((kp_id, w / (1 + 0.35 * (rank - r)) / (1 + ctx.run.get(kp_id, 0))))   # 这一次里出过的少出
        return out

    def weight(self, kid, ctx):
        if not self._kps(ctx):
            return 0.0
        if ctx.rank > 9 and "physics" not in ctx.subjects:      # 高中生做口算没意思，偶尔出
            return 0.15
        return 1.0

    def pick(self, kid, ctx, target, rng):
        pool = self.pool(ctx, target)
        if not pool:
            return None
        kp_id = rng.choices([k for k, _ in pool], weights=[w for _, w in pool])[0]
        fam = quickgen.BY_KP[kp_id]
        theta, _ = kp_ability(ctx, kp_id, self._rank(ctx))
        levels = ability.calib(fam, quickgen.LEVELS)
        lv = ability.pick_level(theta, levels, target, rng)
        it = quickgen.generate(fam, lv, rng)
        it.update(dim=kp_id, theta=theta, b=levels[lv][0], p=round(ability.sigmoid(theta - levels[lv][0]), 4),
                  topic=quickgen.FAMILIES[fam][2])
        return it


# ------------------------------------------------------------------ 英语单词 / 教材术语（四选一）

def words_band(kid: dict) -> str:
    return words_mod.band(kid.get("grade"))


class WordSource(Source):
    """英语核心词表按年级分 5 级（同一个能力维度 vocab:en）；教材术语表（比如 IGCSE 物理术语）各自一个维度。
    今天该复习的生词卡、术语卡先出（四成左右，答了就算复习过，按记忆模型排下次），别的生词卡也常拿出来见见。"""
    id, name, icon, desc = "words", "单词", "🔤", "英语单词和课本术语，四选一认意思"
    target_shift = 0.05
    redraw = True

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
        got = ability.stored_ability(ctx.kid["id"], g["dim"])
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
        """自己的生词卡、术语卡。单词包里今天的新词还没学过（复习页的学习卡）先不出：先学一遍，再拿来练。"""
        return ctx.get_or("word_cards", lambda: [c for c in db.q(
            "SELECT id, front, back, kind, kp_id, due, extra, reviews FROM cards WHERE user_id=? AND kind IN ('word','term') AND LENGTH(back)<=24 "
            "ORDER BY due", ctx.kid["id"]) if not ctx.paused.card(c)
            and not (not c["reviews"] and (db.jload(c["extra"], {}) or {}).get("new"))][:60])

    KNOWN_DAYS = 3

    def _known(self, ctx: Ctx) -> set:
        """最近 KNOWN_DAYS 天在任何地方答对过的词（热身、复习、快练、游戏）。"""
        since = (db.today() - timedelta(days=self.KNOWN_DAYS)).isoformat()
        return ctx.get_or("known_words", lambda: {r["target_id"].rsplit(":", 1)[-1].lower() for r in db.q(
            "SELECT DISTINCT target_id FROM events WHERE user_id=? AND target='word' AND correct=1 AND created_at>=?",
            ctx.kid["id"], since)})

    # 同一个词换着方式考：认意思（看英文选中文）、反过来（看中文选英文）、听音选意思、拼写。
    # 认出来最容易，自己拼出来最难，也最能说明真记住了（填空蒙对的机会很小）。
    # 各方式难度不同，各自校准（题族 words / words:listen / words:spell……），能力值还是同一个。
    # 句子补洞（cloze）：把词放回例句里（app/words.py），有例句的词才出，没有就换成看中文选英文、后台去写例句。
    FORMATS = {"core": {"read": 0.42, "reverse": 0.18, "listen": 0.12, "spell": 0.16, "cloze": 0.12},
               "terms": {"read": 0.5, "reverse": 0.3, "spell": 0.1, "cloze": 0.1}}
    FORMAT_SHIFT = {"listen": 0.5, "spell": 1.5, "cloze": 0.6, "run": 1.2}   # 比认意思难多少（难度 b 的起点）
    # 单词跑酷（run）：撞字母把词拼出来，按这个词的等级给提示（words.run_hint），三档各自校准
    RUN_SHIFT = (-1.0, 1.2, 1.8)

    def _format(self, ctx: Ctx, g: dict, rng: random.Random) -> str:
        if ctx.get("ask_fmt"):   # 只出一种题的游戏（单词跑酷）
            return ctx["ask_fmt"]
        fm = dict(self.FORMATS["core" if g["family"] == "words" else "terms"])
        if ctx.get("shape") == "choices":   # 题要写成几个短选项的游戏（向下跳）：只出认意思、看中文选英文
            fm = {k: v for k, v in fm.items() if k in ("read", "reverse")}
        if ctx.get("mode") != "sprint":   # 听音只在快练和冲刺里出（有「不会」可点）；游戏里可能关着声音
            fm.pop("listen", None)
        return rng.choices(list(fm), weights=list(fm.values()))[0]

    @staticmethod
    def _spellable(w: str) -> bool:
        return w.isascii() and w.isalpha() and 3 <= len(w) <= 10

    def pick(self, kid, ctx, target, rng):
        groups = self._groups(ctx)
        g = rng.choices(groups, weights=[3 if x["family"] == "words" else 1 for x in groups])[0]
        fmt = self._format(ctx, g, rng)
        family = g["family"] if fmt in ("read", "reverse") else f"{g['family']}:{fmt}"
        shift = self.FORMAT_SHIFT.get(fmt, 0)
        theta, _ = self._theta(ctx, g)
        levels = ability.calib(family, list(g["levels"]), lambda lv: self._prior(lv) + shift)
        lv = ability.pick_level(theta, levels, target, rng)
        wl = rng.choice(g["levels"][lv])
        words = [w for w in wl["words"] if w.get("zh")]
        cards = [c for c in self._my_cards(ctx) if (c["kind"] == "word") == (g["family"] == "words")]
        ctx.get_or("known_recent", lambda: self._known(ctx))
        # 挑哪个词走统一的出词（app/wordpick.py）：到期的先，过去学过的词轮着来，再是这张词表里最近没答对过的；
        # 做完一组抓词送的那一局（word_set）只出那一组的词
        w = wordpick.practice(ctx.kid["id"], rng, words, cards, ctx)
        cloze = None
        if fmt == "cloze":
            w, cloze = self._cloze(ctx, g, wl, words, w, rng)
            if not cloze:
                fmt, family = "reverse", g["family"]
                levels = ability.calib(family, list(g["levels"]), self._prior)
        if fmt in ("spell", "run") and not self._spellable(w["w"]):
            w = next((x for x in rng.sample(words, len(words)) if self._spellable(x["w"]) and x["w"].lower() not in self._known(ctx)), None) \
                or next((x for x in words if self._spellable(x["w"])), w)
            if not self._spellable(w["w"]):
                fmt, family = "read", g["family"]
                levels = ability.calib(family, list(g["levels"]), self._prior)
        others = list({x["zh"] for x in words if x["zh"] != w["zh"] and x["w"] != w["w"]})
        if len(others) < 3:
            others += [x["zh"] for x in rng.sample(content.word_lists[next(iter(content.word_lists))]["words"], 6)]
        rng.shuffle(others)
        if cloze:
            it = cloze
        elif fmt == "run":
            it, fmt, family, levels = self._run(ctx, g, w, theta, lv, levels, rng)
        elif fmt == "reverse":
            eng = list({x["w"] for x in words if x["w"] != w["w"]})
            rng.shuffle(eng)
            it = _mcq(f"「{w['zh']}」用英语怎么说？", w["w"], eng, rng)
        elif fmt == "spell":
            it = {"type": "fill", "q": f"「{w['zh']}」怎么拼？", "answer": [w["w"]], "keypad": "letters",
                  "letters": words_mod.letter_pad(w["w"], rng), "say": w["w"]}
        elif fmt == "listen":
            it = _mcq("听一听，是什么意思？", w["zh"], others, rng)
            it["say"] = w["w"]
        else:
            it = _mcq(f"{w['w']}", w["zh"], others, rng)
            it["say"] = w["w"]   # 前端可以读出来
        h = hashlib.sha1(f"{wl['id']}|{w['w']}|{fmt}".encode()).hexdigest()[:10]
        it.update(id=f"word-{h}", family=family, level=lv, dim=g["dim"], word=w["w"], zh=w["zh"], list=w.get("list") or wl["id"], fmt=fmt,
                  kp_id=w.get("kp") or "", card=w.get("card"), topic=g["title"], theta=theta, b=levels[lv][0], p=round(ability.sigmoid(theta - levels[lv][0]), 4),
                  explain=f"{it['explain']}\n{w['w']}：{w['zh']}" if cloze else f"{w['w']}：{w['zh']}")
        return it

    def _run(self, ctx: Ctx, g: dict, w: dict, theta: float, lv: int, levels: dict, rng: random.Random) -> tuple:
        """单词跑酷的一个词：前端按 run（词、中文、提示几档）在跑道上摆字母；摔倒了答题起来时，这一题就是「把它拼出来」。
        返回 (题, 题型, 题族, 这个题族各级的难度)：题型是哪一档提示（copy / spell / dictate），学习记录按它算单词等级。"""
        lad = ctx.get_or("ladder", lambda: words_mod.ladder(ctx.kid["id"]))
        h = words_mod.run_hint(lad.get(w["w"].lower()), ability.sigmoid(theta - levels[lv][0]))
        family = f"{g['family']}:run{h}"
        levels = ability.calib(family, list(g["levels"]), lambda x: self._prior(x) + self.RUN_SHIFT[h])
        letters = words_mod.letter_pad(w["w"], rng)
        q = f"照着拼：{w['w']}（{w['zh']}）" if h == 0 else f"「{w['zh']}」怎么拼？" + (f"首字母 {w['w'][0]}" if h == 1 else "")
        it = {"type": "fill", "q": q, "answer": [w["w"]], "keypad": "letters", "letters": letters, "say": w["w"],
              "run": {"w": w["w"], "zh": w["zh"], "hint": h}}
        return it, words_mod.RUN_HINTS[h], family, levels

    def _cloze(self, ctx: Ctx, g: dict, wl: dict, words: list, w: dict, rng: random.Random) -> tuple[dict, dict | None]:
        """句子补洞：抽到的词有例句就用它，没有就在该复习的、自己的生词、这张表里找一个有例句的；都没有，后台去写。
        已经拼得出的词（单词等级到 4）自己把它拼进句子里，其余从几个同词性的词里选。"""
        b = ctx.get_or("band", lambda: words_band(ctx.kid))
        cards = [{"w": c["front"], "zh": c["back"], "kp": c["kp_id"], "card": c["id"] if str(c["due"]) <= db.today().isoformat() else None,
                  "list": (db.jload(c["extra"], {}) or {}).get("list")} for c in self._my_cards(ctx)
                 if (c["kind"] == "word") == (g["family"] == "words")]
        cand = [w] + [c for c in cards if c["card"]] + rng.sample(cards, min(len(cards), 8)) + rng.sample(words, min(len(words), 12))
        have = words_mod.examples([c["w"] for c in cand], b)
        served = ctx.get("served") or set()
        lad = ctx.get_or("ladder", lambda: words_mod.ladder(ctx.kid["id"]))
        for c in cand:
            if c["w"].lower() not in have or f"w:{c['w'].lower()}" in served:
                continue
            spell = lad.get(c["w"].lower(), {}).get("level", 0) >= 4 and self._spellable(c["w"])
            it = words_mod.cloze_item({**c, "pos": c.get("pos") or next((x.get("pos", "") for x in words if x["w"] == c["w"]), "")}, b, rng,
                                      words_mod.pool_for(ctx.kid["id"], c.get("list") or wl["id"]), spell=spell)
            if it:
                return {**c, "list": c.get("list") or wl["id"]}, it
        words_mod.prepare(ctx.kid["id"])
        return w, None

    def record(self, kid, it, correct, ans, dont_know, ms, mode="game", weight=GAME_WEIGHT, calibrate=None):
        fmt = "recall" if it.get("type") == "fill" else "choice"
        words_mod.log_answer(kid["id"], it["list"], it["word"], correct, it.get("fmt") or "read", mode=mode, weight=weight, dont_know=dont_know)
        if not it.get("retry"):
            ability.observe(kid["id"], it["dim"], it, correct)
            c = db.one("SELECT * FROM cards WHERE id=? AND user_id=? AND due<=?", it["card"], kid["id"], db.today().isoformat()) \
                if it.get("card") else None
            if c:   # 今天该复习的生词卡：在这里答了就算复习过了，按记忆模型排下次
                engine.schedule_card(kid["id"], c, "good" if correct else "again", fmt=fmt)
        if it.get("kp_id"):   # 术语卡挂了知识点：也算这个知识点的一条证据（θ 上面已经记在词汇维度上）；
            # 和单词复习一样记成 kind=review：这一题的作答已经在上面记过一次，学习报告不重复数（app/records.py）
            engine.update_mastery(kid["id"], it["kp_id"], correct, weight=weight, source=mode, fmt=fmt,
                                  dont_know=dont_know, kind="review")
        if not correct:       # 不认识的词放进复习
            engine.add_card(kid["id"], "word" if it["family"].startswith("words") else "term", it["word"], it["zh"],
                            {"list": it["list"], "from": mode}, it.get("kp_id") or None)


# ------------------------------------------------------------------ 题库里的快答题（任何学科）

QUICK_SKIP = re.compile("写作|听|口语|阅读|考试|探究|实践|思辨|实用性")   # 这些方向的题快答不了（写作、听说、长阅读、实验探究）
WIDE_BACK = 2       # 以前学过的往回铺多远：最近两个学年（再早的靠追根、热身沿着前置知识找）
SHARES = {"focus": 0.3, "due": 0.25, "recent": 0.25, "wide": 0.2}
KP_TRIES = 4        # 选中的知识点一时没有新题，换几个知识点试


class BankSource(Source):
    """在学的所有科目、所有知识点都能出（语文、英语语法、物理、历史……），题和每日练习走同一个入口（engine.items_for）：
    这个孩子没做过的先出，按水平挑难度，一次里不出重复的。选知识点分四类，按比例混着出：
    正在学 / 薄弱的、快忘了该复习的、这学期学的、以前学过的（铺开知识面，越久没碰的越先出）。
    选中的知识点没有没做过的快答题：后台请 AI 出一批存进题库（prefetch.quick，每个孩子每天有上限），这次先换个知识点。"""
    id, name, icon, desc = "bank", "我的科目", "📚", "在学的各科知识点上的快答题（语文、英语、物理……）"
    target_shift = 0.05

    def _kps(self, ctx: Ctx) -> dict[str, list[str]]:
        def load():
            mastery, taught, due = ctx.mastery, ctx.taught, ctx.due
            needs = evidence.needs_work(ctx.kid["id"], mastery)
            out: dict[str, list[str]] = {c: [] for c in SHARES}
            hold = ctx.paused.unlearned
            for e in ctx.packs:
                pack = catalog.packs[e["pack_id"]]
                skip = {x["id"] for x in pack.strands if x.get("audio") or QUICK_SKIP.search(x.get("name", ""))}
                top = stage_rank(e["stage"])
                top = top if top < 99 else ctx.rank
                for k in catalog.ids_for(pack.id, e.get("track")):
                    kp = catalog.kp_in(pack.id, k)
                    if kp.get("strand") in skip or k in hold:   # 只能在这里学的课还没上过：先上课
                        continue
                    r = stage_rank(kp["stage"])
                    if k in needs:
                        out["focus"].append(k)
                    elif k in due:
                        out["due"].append(k)
                    elif k in taught and r >= top:          # 这学期学的
                        out["recent"].append(k)
                    elif top - WIDE_BACK <= r < top or k in taught or (mastery.get(k) or {}).get("attempts"):
                        out["wide"].append(k)
            return out
        return ctx.get_or("bank_kps", load)

    def _kp_weight(self, ctx: Ctx, kp_id: str, cat: str) -> float:
        w = 1 / (1 + 2 * ctx.run.get(kp_id, 0))   # 这一次里出过的知识点少出，铺开
        if cat == "wide":                          # 以前学过的：越久没碰越先出
            last = (ctx.mastery.get(kp_id) or {}).get("last_ev")
            w *= 1 + min(3.0, evidence.days_since(last) / 14) if last else 4.0
        return w

    def weight(self, kid, ctx):
        return 1.5 if any(self._kps(ctx).values()) else 0.0

    def pick(self, kid, ctx, target, rng):
        from .. import prefetch
        cats = {c: ks for c, ks in self._kps(ctx).items() if ks}
        tried: set = set()
        for _ in range(KP_TRIES):
            cs = [c for c in cats if any(k not in tried for k in cats[c])]
            if not cs:
                break
            cat = rng.choices(cs, weights=[SHARES[c] for c in cs])[0]
            ks = [k for k in cats[cat] if k not in tried]
            kp_id = rng.choices(ks, weights=[self._kp_weight(ctx, k, cat) for k in ks])[0]
            tried.add(kp_id)
            theta, _ = kp_ability(ctx, kp_id)
            lv = ability.pick_level(theta, ability.calib("bank", quickgen.LEVELS), target, rng)
            again = bool(ctx.get("again"))   # 别的题源也出不了：拿做错过的老题、这一局出过的题凑，不让这一局没题
            got = engine.items_for(kid["id"], kp_id, n=1, purpose="quick", grade=kid.get("grade") or "",
                                   exclude=() if again else ctx.get("served") or (), ai=False, near_level=lv,
                                   fallback=True if again else None)
            if not got:
                prefetch.quick(kid["id"], kp_id, kid.get("grade") or "")
                continue
            it = got[0]
            family, level = ability.scale_of(it) or (ability.bank_family(it["type"]), 3)
            b = ability.calib(family, [level])[level][0]
            it.update(family=family, level=level, dim=kp_id, kp_id=kp_id, theta=theta, b=b,
                      p=round(ability.sigmoid(theta - b), 4), topic=catalog.kps[kp_id]["name"], why=cat)
            return _reshuffle(it, rng)
        return None


# ------------------------------------------------------------------ 写成几个短选项（向下跳：选项写在洞口的小牌子上）

OPT_WIDTH = 12     # 一个选项最宽多少（汉字算 2）：洞口的牌子只放得下这么多
Q_WIDTH = 64       # 题目最长多少：挂在柱子上的题牌


def _width(s: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(s))


def _fmt_num(x) -> str:
    x = round(float(x), 4)
    return str(int(x)) if x == int(x) else f"{x:.4f}".rstrip("0").rstrip(".")


def _near_numbers(ans: float, rng: random.Random) -> list[str]:
    """数字答案旁边、容易算错成的数：差 1、差 2、差 10（大数差 100）、小数点挪一位。"""
    step = 100 if abs(ans) >= 1000 else 10 if abs(ans) >= 20 else 3   # 10 以内的题，错的选项也在 10 左右，不一眼排除
    cands = [ans + 1, ans - 1, ans + 2, ans - 2, ans + step, ans - step]
    if ans != int(ans):
        cands += [ans * 10, ans / 10]
    right = _fmt_num(ans)
    out = list(dict.fromkeys(_fmt_num(c) for c in cands if (c >= 0 or ans < 0) and _fmt_num(c) != right))
    rng.shuffle(out)
    return out


def _near_fractions(right: str, rng: random.Random) -> list[str]:
    from fractions import Fraction
    try:
        f = Fraction(right)
    except (ValueError, ZeroDivisionError):
        return []
    d = f.denominator
    cands = [f + Fraction(1, d), f - Fraction(1, d), Fraction(f.numerator + 1, d + 1) if d > 1 else f + 1, 1 / f if f else f + 2]
    out = list(dict.fromkeys(quickgen._frac_str(c) for c in cands if c > 0 and c != f))
    rng.shuffle(out)
    return out


def as_choices(it: dict, rng: random.Random) -> dict | None:
    """把一道题写成 2～4 个短选项（选择题；答案是数、分数的计算题按容易算错的数配错误选项）。写不成的返回 None。
    计算题改成选一个以后容易了（能排除、能蒙），所以换个题族单独校准（<题族>:pick），能力值还是同一个。"""
    if _width(it.get("q", "")) > Q_WIDTH:
        return None
    t = it.get("type")
    if t == "mcq":
        opts = list(it.get("options") or [])
        try:
            right = opts[int(it["answer"])]
        except (TypeError, ValueError, IndexError, KeyError):
            return None
        if _width(right) > OPT_WIDTH:
            return None
        wrongs = [o for o in opts if o != right and _width(o) <= OPT_WIDTH][:3]
        if not wrongs:
            return None
        return {**_mcq(it["q"], right, wrongs, rng), **{k: v for k, v in it.items() if k not in ("type", "q", "options", "answer")}}
    if t == "num" and _width(_fmt_num(it["answer"])) <= OPT_WIDTH:
        right, wrongs = _fmt_num(it["answer"]), _near_numbers(float(it["answer"]), rng)[:2]
    elif t == "fill" and it.get("keypad") == "frac":
        it = {**it, "q": re.sub(r"（写成[^）]*）", "", it["q"]).strip()}   # 选一个，不用自己写
        right = it["answer"][0]
        wrongs = [w for w in _near_fractions(right, rng) if w not in it["answer"]][:2]
    else:
        return None
    if not wrongs:
        return None
    family = f"{it['family']}:pick" if it.get("family") else ""
    out = {**{k: v for k, v in it.items() if k not in ("tol", "keypad")}, **_mcq(it["q"], right, wrongs, rng)}
    if family and it.get("level"):
        b = ability.calib(family, [it["level"]])[it["level"]][0]
        out.update(family=family, b=b, p=round(ability.sigmoid(it.get("theta", 0) - b), 4))
    return out


def _reshuffle(it: dict, rng: random.Random) -> dict:
    """选项打乱，答案跟着换（再考一次时也换个位置，不让孩子记位置）。"""
    if it.get("type") != "mcq":
        return it
    opts = list(it["options"])
    order = list(range(len(opts)))
    rng.shuffle(order)
    return {**it, "options": [opts[i] for i in order], "answer": order.index(int(it["answer"]))}


SOURCES: dict[str, Source] = {s.id: s for s in (MathSource(), WordSource(), BankSource())}


def weights(kid: dict, ctx: Ctx | None = None) -> dict[str, float]:
    """这个孩子每种题源占多少（0 的不出）。"""
    ctx = ctx or Ctx(kid)
    return {k: w for k, s in SOURCES.items() if (w := s.weight(kid, ctx)) > 0}


NO_SOURCE = {"words": "还没有单词包：孩子请家长在「学习安排 → 单词包」里加一个，自己学的在「我的词」里挑一个。"}
NOTHING = ("游戏里的题都出自你正在学的课和单词包，现在还没有能在这里出的题。"
           "孩子请家长在「学习安排」里加一门课或一个单词包；自己学的在「我的课程」「我的词」里加。")
BUSY = "你在学的课题还在准备，过一会儿再来。"


def asks(kid: dict, ask: tuple | None = None, ctx: Ctx | None = None) -> dict[str, float]:
    """这个人在这款游戏里能出哪几种题、各占多少；一种都没有就说为什么（开局前先查，不让人进了游戏才发现没题）。"""
    ws = weights(kid, ctx)
    if ask:
        if ask[0] not in ws:
            raise ArenaError(NO_SOURCE.get(ask[0], NOTHING), 409)
        return {ask[0]: 1.0}
    if not ws:
        raise ArenaError(NOTHING, 409)
    return ws


def pick(kid: dict, target: float, rng: random.Random, ctx: Ctx | None = None, mode: str = "game", ask: tuple | None = None,
         shape: str = "") -> dict:
    """给这个人出一道题（游戏、冲刺都用它）：target 是目标答对率；mode = sprint 时也出听音题。
    题只出自他在学的东西（asks）：没有就不出，不拿哪一级的口算凑。
    ask：只出一种题的游戏（单词跑酷：("words", "run")），只从这个题源出。
    shape = "choices"：题都写成 2～4 个短选项（as_choices），写不成的换一道、换别的题源。
    抽中的题源这次出不了题（题库里一时没有新题、后台还在出），换别的题源；都出不了，再来一轮，这一轮题库的题可以拿老题凑。"""
    ctx = ctx or Ctx(kid)
    ctx["mode"], ctx["shape"] = mode, shape
    if ask:
        ctx["ask_fmt"] = ask[1] if len(ask) > 1 else None
    shaped = (lambda x: x and as_choices(x, rng)) if shape == "choices" else (lambda x: x)
    ws = asks(kid, ask, ctx)
    for again in (False, True):
        ctx["again"] = again
        left = dict(ws)
        for _ in range(PICK_TRIES):   # 口算写成选项偶尔写不成（这一族不是数），同一个题源多抽几次
            if not left:
                break
            sid = rng.choices(list(left), weights=list(left.values()))[0]
            s = SOURCES[sid]
            it = shaped(s.pick(kid, ctx, min(0.92, target + s.target_shift), rng))
            if it:
                it["src"] = sid
                return it
            if not s.redraw:
                left.pop(sid)
    raise ArenaError(BUSY, 503)


# ------------------------------------------------------------------ 一次快练 / 一局游戏里的出题顺序（见文件开头）

SEEN_KEEP = 60    # 一次里出过的题都记着（快练 5 分钟大约 20–40 题）
RETRY_GAP = 4     # 答错的题隔几道再考一次
PICK_TRIES = 8    # 抽到出过的题就重抽；抽 8 次都是出过的（能出的题实在太少）才让它重复


def key(it: dict) -> str:
    """同一道题：单词按词算（换个考法还是这个词），其余按题目 id（现场生成的题 id 由题面算出）。"""
    return f"w:{it['word'].lower()}" if it.get("word") else it["id"]


def next_item(kid: dict, st: dict, target: float, rng: random.Random, mode: str = "game", ask: tuple | None = None,
              shape: str = "") -> dict:
    """这一次（st 是这次快练 / 这一局的状态，调用方负责存）的下一题。"""
    n = st.get("n", 0)
    st["n"] = n + 1
    due = next((r for r in st.get("retry", []) if r["at"] <= n), None)
    if due:
        st["retry"].remove(due)
        return _reshuffle({**due["item"], "retry": True}, rng)
    ctx = Ctx(kid)
    seen = st.setdefault("seen", [])
    run = st.setdefault("run", {})
    ctx.update(served=set(seen), run=run, word_set=st.get("word_set"))
    for _ in range(PICK_TRIES):
        it = pick(kid, target, rng, ctx, mode, ask, shape)
        if key(it) not in seen:
            break
    seen.append(key(it))
    del seen[:-SEEN_KEEP]
    run[it["dim"]] = run.get(it["dim"], 0) + 1
    return it


def settle(kid: dict, st: dict, it: dict, ans, dont_know: bool, ms: int, mode="game", weight=GAME_WEIGHT) -> bool:
    """判对错、记进学习记录（同时更新水平和题目难度）；答错（或不会）的排进「隔几道再考一次」。返回答对没有。"""
    correct = (not dont_know) and bool(itemtypes.check(it, ans))
    if it.get("retry"):   # 刚看过答案：证据记一半，不估水平和难度（record 里看 retry），也不再排
        record(kid, it, correct, ans, dont_know, ms, mode, weight * 0.5)
        return correct
    record(kid, it, correct, ans, dont_know, ms, mode, weight)
    if not correct:
        st.setdefault("retry", []).append({"at": st.get("n", 0) + RETRY_GAP, "item": it})
    return correct


def record(kid: dict, it: dict, correct: bool, ans, dont_know: bool, ms: int, mode="game", weight=GAME_WEIGHT):
    SOURCES.get(it.get("src") or "math", SOURCES["math"]).record(kid, it, correct, ans, dont_know, ms, mode, weight)


def public(it: dict) -> dict:
    """发给前端的题（不含答案）。"""
    t = itemtypes.of(it)
    keypad = it.get("keypad") or ("dec" if t.id == "num" else "")
    return {"id": it["id"], "q": it["q"], "type": t.id, "widget": t.widget, "unit": it.get("unit", ""),
            "options": it.get("options"), "keypad": keypad, "topic": it.get("topic", ""), "src": it.get("src", ""),
            "say": it.get("say", ""), "letters": it.get("letters"), "cloze": bool(it.get("cloze")), "hint": it.get("hint", ""),
            "run": it.get("run"), "retry": bool(it.get("retry"))}
