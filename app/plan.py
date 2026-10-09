"""每日任务清单：任务从哪来（SOURCES）和怎么排（学习方式的 plan 策略）分开。

- 每个任务来源是一个函数：看孩子的情况，给出一类候选任务（单词、错题、补弱、预习……）。
  加一类任务 = 写一个来源函数，登记到 SOURCES。
- 每天做哪些、按什么顺序、每类最多几个，由孩子的学习方式决定（app/methods/profiles.toml 的 plan）：
  fixed 每天都有（坚持比做对更重要，不受时间预算限制），flex 按顺序排、超出每天可用时间就截掉，
  "a+b" 表示两类交替穿插（交错练习），limits 是每类的上限。
"""
from datetime import timedelta

from . import bankpapers, db, engine, evidence, explore, insights
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
        cert = catalog.packs[pack_id].system == "cert"  # 职业资格考试：按考试日期走，不跟学校进度
        if pv["stale"] and not cert:
            out["stale"].append(catalog.packs[pack_id].subject_name)
        # 跟上学校：正在学的知识点没掌握，就练它；进度之后的下一个可以预习。掌握了就自动换下一个（见 advance_progress）
        progress_kp = engine.advance_progress(c.user_id, e, c.mastery, c.taught) if not cert else e["progress_kp"]
        if progress_kp and catalog.kp(progress_kp):
            cur = catalog.kp(progress_kp)
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
        if not diag and not cert:  # 考证一般从零学起，不先摸底（想测可以在「学科」里自己点诊断）
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
        if diag and not progress_kp and not e["exam_date"]:  # 诊断前不安排预习；有考试日期的由 src_exam 安排新内容
            for k in engine.frontier(c.user_id, pack_id, stage, c.mastery)[:2]:
                out["preview"].append({"type": "preview", "kp": k["id"], "title": f"预习：{k['name']}",
                                       "why": "前置已具备" + ("，高频考点" if k.get("hot") else ""), "minutes": 8, "pack": pack_id})
    return out


# ------------------------------------------------------------------ 任务来源

def src_progress(c: Ctx) -> list[dict]:
    """学校进度不再是每天的任务：今天页上方一周最多提示一次「这周学校学了啥？」，可以跳过（见 engine.progress_prompt）。
    保留这个来源名，是为了自定义学习方式（config/methods.toml）里写了 progress 的也不出错。"""
    return []


def src_exam_date(c: Ctx) -> list[dict]:
    """职业资格考试的课还没设考试日期：先设一个，计划才能按剩余天数排。"""
    names = [catalog.packs[e["pack_id"]].subject_name for e in c.enrolls
             if catalog.packs[e["pack_id"]].system == "cert" and not e["exam_date"]]
    return [{"type": "progress", "title": "设定考试日期（1 分钟）",
             "why": f"{'、'.join(names[:3])}：告诉系统哪天考试，每天学多少就按剩下的天数排", "minutes": 2,
             "url": "/progress"}] if names else []


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
             "why": "看意思选单词、写单词，系统来判；答错的过一会儿再来", "minutes": min(15, 3 + n // 4), "url": "/review?group=words"}]


def src_mistakes(c: Ctx) -> list[dict]:
    n = len(engine.due_cards(c.user_id, 300, "mistakes"))
    return [{"type": "mistakes", "title": f"错题重做 {min(n, 8)} 道", "why": "原题再做一遍，隔天做对两次换一道新题，做对就过关",
             "minutes": min(10, 2 + min(n, 8)), "url": "/review?group=mistakes"}] if n else []


def src_reading(c: Ctx) -> list[dict]:
    out = []
    active = engine.tracks(c.user_id)
    langs = {catalog.packs[e["pack_id"]].lang for e in c.enrolls} - {""}
    for kind, lang, label in (("read_en", "en", "英语阅读"), ("read_zh", "zh", "名著接着读")):
        tr = [t for t in active if t["kind"] == kind]
        if tr and tr[0]["ref"].startswith("lib:"):  # 书库里的书：在网站上按页读，读够自动打勾
            out.append(_library_task(c, tr[0], kind, label))
        elif tr:
            t, seg = tr[0], engine.track_today(tr[0])
            out.append({"type": kind, "track": t["id"], "title": f"{label}：《{t['title']}》{seg['label']}",
                        "why": f"读 {t['daily_minutes']} 分钟，读完用一句话说说讲了什么",
                        "minutes": t["daily_minutes"], "url": f"/track/{t['id']}"})
        elif lang in langs:
            out.append({"type": kind, "lang": lang, "title": f"{'英文' if lang == 'en' else '中文'}阅读 15 分钟",
                        "why": "读一篇短文，不懂的词点一下就查，收藏后自动进单词复习", "minutes": 15, "url": f"/reading?lang={lang}"})
    for t in active:
        if t["kind"] == "listen" and t["ref"].startswith("lib:"):
            bid = t["ref"][4:]
            from .library_web import progress
            p = progress(c.user_id, bid)
            start = max(1, p["listen_page"] or (p["page"] + 1))
            out.append({"type": "listen", "track": t["id"], "title": f"🎧 听书 {t['daily_minutes']} 分钟：《{t['title']}》",
                        "why": "跟着声音看文字，正在读的段落会亮起来；听够时间自动完成", "minutes": t["daily_minutes"],
                        "url": f"/books/{bid}/p/{start}?listen=1"})
    return out


def _library_task(c: Ctx, t, kind: str, label: str) -> dict:
    from .library import library
    bid = t["ref"][4:]
    n = max(1, t["daily_amount"])
    start = t["position"] + 1
    total = t["total_units"] or (library.text(bid) or {}).get("n_pages", 0)
    if total and start > total:
        return {"type": kind, "track": t["id"], "title": f"{label}：《{t['title']}》读完啦 🎉", "why": "请家长换一本新书",
                "minutes": 1, "url": f"/books/{bid}"}
    end = min(start + n - 1, total) if total else start + n - 1
    return {"type": kind, "track": t["id"], "title": f"📖 {label}：《{t['title']}》第 {start}–{end} 页" if end > start else
            f"📖 {label}：《{t['title']}》第 {start} 页", "why": f"今天读 {n} 页，不认识的词点一下就查；读够自动打勾",
            "minutes": t["daily_minutes"], "url": f"/books/{bid}/p/{start}"}


def src_exam(c: Ctx) -> list[dict]:
    """有考试日期的课：离考试两周以上，按剩余天数算出每天要学几个新考点，按大纲顺序排；
    最后两周不学新内容，改成把还没掌握的高频考点过一遍。"""
    out = []
    for e in c.enrolls:
        ex = engine.exam_view(c.user_id, e, c.mastery, c.taught)
        if not ex or ex["phase"] == "past":
            continue
        pack_id = e["pack_id"]
        if ex["phase"] == "learn":
            n = min(ex["per_day"], c.limit("exam", 4))
            pace = (f"每天学 {ex['per_day']} 个能学完" if n == ex["per_day"]
                    else f"每天要学 {ex['per_day']} 个才学得完，今天先排 {n} 个，有余力可以去「学科」多学几个")
            for i, k in enumerate(ex["todo"][:n]):
                kp = catalog.kps[k]
                out.append({"type": "exam", "kp": k, "title": f"按考期学：{kp['name']}", "pack": pack_id,
                            "why": f"离考试还有 {ex['days_left']} 天，还剩 {len(ex['todo'])} 个考点，{pace}",
                            "minutes": 10, "url": f"/learn/{k}?task=preview", "keep": i == 0})
        else:
            left = [k for k in catalog.ids_for(pack_id, e["track"])
                    if catalog.kps[k].get("hot") and c.mastery.get(k, {}).get("status") != "mastered"]
            left.sort(key=lambda k: c.mastery.get(k, {}).get("score") or 0)
            # 冲刺期每周一套整卷模拟考（卷库里有发布的卷子、手上没有没做完的模拟考时）
            last = bankpapers.last_mock_day(c.user_id, pack_id)
            if (not last or last <= (db.today() - timedelta(days=7)).isoformat()) and bankpapers.for_learner(c.user_id, [e]) \
                    and not db.one("SELECT 1 AS ok FROM papers WHERE user_id=? AND pack_id=? AND bank_paper_id IS NOT NULL "
                                   "AND status='ready'", c.user_id, pack_id):
                out.append({"type": "paper", "pack": pack_id, "title": f"{catalog.packs[pack_id].subject_name}整卷模拟考",
                            "why": f"离考试还有 {ex['days_left']} 天，找一段完整的时间按考试的节奏做一套", "minutes": 40,
                            "url": f"/mocks?pack={pack_id}"})
            for i, k in enumerate(left[:c.limit("exam", 3)]):
                out.append({"type": "exam", "kp": k, "title": f"考前过一遍：{catalog.kps[k]['name']}", "pack": pack_id,
                            "why": f"离考试还有 {ex['days_left']} 天，高频考点还没掌握", "minutes": 8,
                            "url": f"/learn/{k}?task=exam", "keep": i == 0})
    return out


def src_sync(c: Ctx) -> list[dict]:
    return c.per_pack["sync"]


def src_top(c: Ctx) -> list[dict]:
    """系统自动发现的最重要的一条：紧跟在「跟上学校」后面，不会因为时间不够被截掉。"""
    return [{**t, "keep": True} for t in (c.auto["backfill"] + c.auto["weak"])[:1]]


def src_paper(c: Ctx) -> list[dict]:
    return [{"type": "paper", "url": f"/papers/{p['id']}",
             **({"title": f"模拟考：{p['title']}", "why": "接着把这套卷子做完，做完看诊断", "minutes": 30} if p["bank_paper_id"] else
                {"title": f"试卷订正：{p['title']}", "why": "把卷子上的题在线再做一遍，做完看诊断", "minutes": 20})}
            for p in db.q("SELECT id, title, bank_paper_id FROM papers WHERE user_id=? AND status='ready' ORDER BY id", c.user_id)]


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


SOURCES = {"progress": src_progress, "exam_date": src_exam_date, "warmup": src_warmup, "words": src_words, "mistakes": src_mistakes,
           "reading": src_reading, "exam": src_exam, "sync": src_sync, "top": src_top, "paper": src_paper, "diagnose": src_diagnose,
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
