"""每日任务清单：任务从哪来（SOURCES）和怎么排（学习方式的 plan 策略）分开。

- 每个任务来源是一个函数：看孩子的情况，给出一类候选任务（单词、错题、补弱、预习……）。
  加一类任务 = 写一个来源函数，登记到 SOURCES。
- 每天做哪些、按什么顺序、每类最多几个，由孩子的学习方式决定（app/methods/profiles.toml 的 plan）：
  fixed 每天都有（坚持比做对更重要，不受时间预算限制），flex 按顺序排、超出每天可用时间就截掉，
  "a+b" 表示两类交替穿插（交错练习），limits 是每类的上限。
"""
from . import db, engine, evidence, explore, insights
from .catalog import catalog, stage_rank


class Ctx:
    """一次排计划要用的孩子数据（只查一次，各来源共用）。"""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.user = db.one("SELECT * FROM users WHERE id=?", user_id)
        self.profile = evidence.profile(user_id)
        self.policy = self.profile.plan
        self.limits = self.policy.get("limits", {})
        self.prefer_hot = bool(self.policy.get("prefer_hot"))
        self.mastery = engine.get_mastery(user_id)
        self.enrolls = [e for e in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", user_id)
                        if e["pack_id"] in catalog.packs]
        self.taught = engine.taught_set(user_id)
        self.day_seed = db.today().toordinal()
        self._auto = None
        self._per_pack = None

    def limit(self, kind: str, default: int) -> int:
        return int(self.limits.get(kind, default))

    @property
    def auto(self) -> dict:
        """系统自动发现的问题（根源前置、反复还不会、常问、做对但慢、久未复习……），排在同类任务最前面。"""
        if self._auto is None:
            self._auto = insights.plan_tasks(insights.refresh(self.user_id))
        return self._auto

    @property
    def per_pack(self) -> dict:
        """按学科逐个看：跟上学校、补弱 / 补前置、回顾小检查、预习、要不要诊断。"""
        if self._per_pack is None:
            self._per_pack = _scan_packs(self)
        return self._per_pack


def _hot_first(kps: list, key=lambda k: k):
    return sorted(kps, key=lambda k: not catalog.kps[key(k)].get("hot")) if kps else kps


def _scan_packs(c: Ctx) -> dict:
    out = {"sync": [], "weak": [], "backfill": [], "check": [], "preview": [], "diagnose": [], "stale": []}
    model = c.profile.mastery
    for e in c.enrolls:
        pack_id, stage = e["pack_id"], e["stage"]
        pv = engine.progress_view(c.user_id, e)
        if pv["stale"]:
            out["stale"].append(catalog.packs[pack_id].subject_name)
        # 跟上学校：正在学的知识点没掌握，就练它；进度之后的下一个可以预习
        if e["progress_kp"] and catalog.kp(e["progress_kp"]):
            cur = catalog.kp(e["progress_kp"])
            if c.mastery.get(cur["id"], {}).get("status") != "mastered":
                out["sync"].append({"type": "sync", "kp": cur["id"], "title": f"跟上学校：{cur['name']}",
                                    "why": "学校正在学这个，趁热练一练", "minutes": 12, "pack": pack_id})
            nxt = engine.next_after(pack_id, cur["id"], c.mastery, c.taught, e["track"])
            if nxt:
                out["preview"].append({"type": "preview", "kp": nxt["id"], "title": f"预习：{nxt['name']}",
                                       "why": "学校马上要学，先看一眼", "minutes": 8, "pack": pack_id})
        # 往回巩固：学校学过、但系统里还没检测过的知识点，每天轮一个做个小检查
        unchecked = [k for k in pv["learned"] if c.mastery.get(k, {}).get("status") in (None, "unknown")
                     and stage_rank(catalog.kps[k]["stage"]) >= stage_rank(stage) - 2]
        if c.prefer_hot:
            unchecked = [k for k in unchecked if catalog.kps[k].get("hot")] or unchecked
        if unchecked:
            k = catalog.kps[unchecked[c.day_seed % len(unchecked)]]
            out["check"].append({"type": "check", "kp": k["id"], "title": f"回顾小检查：{k['name']}",
                                 "why": f"{'学校学过' if k['id'] in c.taught else '以前学过'}，看看还记得吗（会就很快过）",
                                 "minutes": 6, "pack": pack_id})
        diag = db.one("SELECT id FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='done'", c.user_id, pack_id)
        if not diag:
            out["diagnose"].append(pack_id)
        kp_ids = set(catalog.ids_for(pack_id, e["track"]))
        weak = sorted([c.mastery[k] for k in kp_ids if k in c.mastery and model.needs_work(c.mastery[k])],
                      key=lambda m: m["score"])
        if c.prefer_hot:
            weak = _hot_first(weak, key=lambda m: m["kp_id"])
        for w in weak[:c.limit("weak", 3)]:
            kp = catalog.kp(w["kp_id"])
            gap = [p for p, _ in catalog.ancestors(w["kp_id"], depth=3)
                   if c.mastery.get(p["id"], {}).get("status") in (None, "weak", "unknown")]
            if gap:
                g = gap[0]
                out["backfill"].append({"type": "backfill", "kp": g["id"], "for": w["kp_id"], "title": f"补前置：{g['name']}",
                                        "why": f"「{kp['name']}」要用到它", "minutes": 10, "pack": pack_id})
            else:
                out["weak"].append({"type": "weak", "kp": w["kp_id"], "title": f"攻克：{kp['name']}",
                                    "why": f"掌握度 {int(w['score'] * 100)}%，{'刚学' if w['status'] == 'learning' else '薄弱'}",
                                    "minutes": 12, "pack": pack_id})
        if diag and not e["progress_kp"]:  # 诊断前不安排预习
            for k in engine.frontier(c.user_id, pack_id, stage, c.mastery)[:2]:
                out["preview"].append({"type": "preview", "kp": k["id"], "title": f"预习：{k['name']}",
                                       "why": "前置已具备" + ("，高频考点" if k.get("hot") else ""), "minutes": 8, "pack": pack_id})
    return out


# ------------------------------------------------------------------ 任务来源

def src_progress(c: Ctx) -> list[dict]:
    stale = c.per_pack["stale"]
    return [{"type": "progress", "title": "更新一下学校进度（1 分钟）",
             "why": f"{'、'.join(stale[:3])}：告诉系统学校学到哪了，计划才跟得上", "minutes": 2, "url": "/progress"}] if stale else []


def src_warmup(c: Ctx) -> list[dict]:
    """热身：几道小题，混着以前学过的知识点和旧单词——不知不觉中把过去摸清（见 explore.py）。"""
    has_probe = bool(explore.candidates(c.user_id, c.mastery)[:1])
    has_words = bool(explore.word_lists(c.user_id))
    if not (has_probe or has_words):
        return []
    n = (3 if has_probe else 0) + (2 if has_words else 0)
    return [{"type": "warmup", "title": f"热身 {n} 题", "why": "先动动脑：混着以前学过的内容，答完系统更懂你",
             "minutes": 4, "url": "/warmup"}]


def src_words(c: Ctx) -> list[dict]:
    new_words = engine.add_daily_words(c.user_id)
    n = len(engine.due_cards(c.user_id, 300, "words"))
    if not n:
        return []
    return [{"type": "words", "title": f"单词：复习 {n} 个" + (f"（含新词 {new_words} 个）" if new_words else ""),
             "why": "记得点「记得」，忘了就点「忘了」，明天再来", "minutes": min(15, 3 + n // 4), "url": "/review?group=words"}]


def src_mistakes(c: Ctx) -> list[dict]:
    n = len(engine.due_cards(c.user_id, 300, "mistakes"))
    return [{"type": "mistakes", "title": f"错题回顾 {min(n, 8)} 道", "why": "先想再翻答案，想不起来也没关系",
             "minutes": min(10, 2 + min(n, 8)), "url": "/review?group=mistakes"}] if n else []


def src_reading(c: Ctx) -> list[dict]:
    out = []
    active = engine.tracks(c.user_id)
    langs = {catalog.packs[e["pack_id"]].lang for e in c.enrolls} - {""}
    for kind, lang, label in (("read_en", "en", "英语阅读"), ("read_zh", "zh", "名著接着读")):
        tr = [t for t in active if t["kind"] == kind]
        if tr:
            t, seg = tr[0], engine.track_today(tr[0])
            out.append({"type": kind, "track": t["id"], "title": f"{label}：《{t['title']}》{seg['label']}",
                        "why": f"读 {t['daily_minutes']} 分钟，读完用一句话说说讲了什么",
                        "minutes": t["daily_minutes"], "url": f"/track/{t['id']}"})
        elif lang in langs:
            out.append({"type": kind, "lang": lang, "title": f"{'英文' if lang == 'en' else '中文'}阅读 15 分钟",
                        "why": "读一篇短文，不懂的词点一下就查，收藏后自动进单词复习", "minutes": 15, "url": f"/reading?lang={lang}"})
    return out


def src_sync(c: Ctx) -> list[dict]:
    return c.per_pack["sync"]


def src_top(c: Ctx) -> list[dict]:
    """系统自动发现的最重要的一条：紧跟在「跟上学校」后面，不会因为时间不够被截掉。"""
    return [{**t, "keep": True} for t in (c.auto["backfill"] + c.auto["weak"])[:1]]


def src_paper(c: Ctx) -> list[dict]:
    return [{"type": "paper", "title": f"试卷订正：{p['title']}", "why": "把卷子上的题在线再做一遍，做完看诊断",
             "minutes": 20, "url": f"/papers/{p['id']}"}
            for p in db.q("SELECT id, title FROM papers WHERE user_id=? AND status='ready' ORDER BY id", c.user_id)]


def src_diagnose(c: Ctx) -> list[dict]:
    return [{"type": "diagnose", "pack": p, "title": f"{catalog.packs[p].subject_name}摸底诊断（约 10 分钟）",
             "why": "先找到真正的薄弱点，计划才准", "minutes": 12, "url": f"/diagnose/{p}"} for p in c.per_pack["diagnose"]]


def _merge_auto(c: Ctx, kind: str) -> list[dict]:
    auto = c.auto[kind]
    kps = {t["kp"] for lst in c.auto.values() for t in lst}
    top = {t["kp"] for t in src_top(c)} if kind != "check" else set()
    return [t for t in auto if t["kp"] not in top] + [t for t in c.per_pack[kind] if t["kp"] not in kps]


def src_weak(c: Ctx) -> list[dict]:
    return _merge_auto(c, "weak")


def src_backfill(c: Ctx) -> list[dict]:
    return _merge_auto(c, "backfill")


def src_check(c: Ctx) -> list[dict]:
    """交叉验证：昨天以前答对过、还差「隔天再对 / 换一种题型」的，今天换一道题确认（排在最前），然后是回顾小检查。"""
    mine = {k for e in c.enrolls for k in catalog.ids_for(e["pack_id"], e["track"])}
    confirm = [m for k, m in c.mastery.items() if k in mine and m["status"] == "learning" and (m["score"] or 0) >= 0.6
               and m.get("last_ev") and evidence.days_since(m["last_ev"]) >= 0.5 and m["attempts"] and catalog.kp(k)]
    confirm.sort(key=lambda m: -(m["score"] or 0))
    out = []
    for m in confirm[:2]:
        need = "、".join(evidence.missing(c.user_id, m)[:2])
        out.append({"type": "check", "kp": m["kp_id"], "title": f"确认一下：{catalog.kps[m['kp_id']]['name']}",
                    "why": f"上次答对了，{need}才算真的掌握", "minutes": 5, "pack": catalog.kp_pack[m["kp_id"]]})
    planned = {t["kp"] for t in out}
    return out + [t for t in _merge_auto(c, "check") if t["kp"] not in planned]


def src_preview(c: Ctx) -> list[dict]:
    return c.per_pack["preview"]


def src_review(c: Ctx) -> list[dict]:
    n = len(engine.due_cards(c.user_id, 100, "other"))
    return [{"type": "review", "title": f"知识点回顾 {n} 张", "why": "间隔复习学过的知识点",
             "minutes": min(10, 2 + n), "url": "/review?group=other"}] if n else []


SOURCES = {"progress": src_progress, "warmup": src_warmup, "words": src_words, "mistakes": src_mistakes,
           "reading": src_reading, "sync": src_sync, "top": src_top, "paper": src_paper, "diagnose": src_diagnose,
           "weak": src_weak, "backfill": src_backfill, "check": src_check, "preview": src_preview, "review": src_review}
DEFAULT_LIMITS = {"sync": 2, "top": 1, "paper": 1, "diagnose": 1, "check": 2, "preview": 1}


# ------------------------------------------------------------------ 按策略排

def interleave(*lists):
    out, i = [], 0
    while any(i < len(lst) for lst in lists):
        for lst in lists:
            if i < len(lst):
                out.append(lst[i])
        i += 1
    return out


def _take(c: Ctx, kind: str) -> list[dict]:
    tasks = SOURCES[kind](c)
    cap = c.limits.get(kind, DEFAULT_LIMITS.get(kind))
    tasks = tasks if cap is None or kind == "weak" else tasks[:int(cap)]  # weak 的上限是每个学科几个，已在来源里截过
    for t in tasks:
        if t.get("kp") and "url" not in t:
            t["url"] = f"/learn/{t['kp']}?task={t['type']}"
    return tasks


def build_plan(user_id: int) -> list[dict]:
    c = Ctx(user_id)
    fixed = [t for kind in c.policy.get("fixed", []) for t in _take(c, kind)]
    flex, seen = [], set()
    for group in c.policy.get("flex", []):
        for t in interleave(*[_take(c, k) for k in group.split("+")]):
            key = (t["type"], t.get("kp"), t.get("pack"), t.get("url"))
            if key not in seen:  # 同一个任务不排两次（比如自动发现的那条已经排在前面）
                seen.add(key)
                flex.append(t)
    budget = c.user["daily_minutes"] or 60
    out, used, n_flex = [], 0, 0
    for is_flex, t in [(False, t) for t in fixed] + [(True, t) for t in flex]:
        if is_flex and n_flex and not t.get("keep") and used + t["minutes"] > budget:
            continue
        n_flex += is_flex
        t["id"] = f"t{len(out)}"
        t["done"] = False
        out.append(t)
        used += t["minutes"]
    return out
