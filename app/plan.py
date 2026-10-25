"""每日任务清单：任务从哪来（SOURCES）和怎么排（学习方式的 plan 策略）分开。

- 每个任务来源是一个函数：看孩子的情况，给出一类候选任务（单词、错题、补弱、预习……）。
  加一类任务 = 写一个来源函数，登记到 SOURCES。
- 每天做哪些、按什么顺序、每类最多几个，由孩子的学习方式决定（app/methods/profiles.toml 的 plan）：
  fixed 每天都有、排在最前（坚持比做对更重要，不受时间预算限制），flex 按顺序排、超出每天可用时间就截掉，
  tail 每天都有、排在最后（默认是阅读），
  "a+b" 表示两类交替穿插（交错练习），limits 是每类的上限。
"""
from datetime import timedelta

from . import bankpapers, db, engine, evidence, explore, insights, lessons, mix, schoolyear, trace, words
from .catalog import catalog, is_adult, stage_rank


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
        self.hold = engine.unlearned(user_id, [dict(e) for e in self.enrolls])   # 只能在这里学的课里没上过的：先上课
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
    out = {"sync": [], "weak": [], "backfill": [], "check": [], "preview": [], "lesson": [], "diagnose": [], "stale": []}
    model = c.profile.mastery
    for e in c.enrolls:
        pack_id, stage = e["pack_id"], e["stage"]
        pv = engine.progress_view(c.user_id, e)
        cert = catalog.packs[pack_id].system == "cert"  # 职业资格考试：按考试日期走，不跟学校进度
        own = engine.self_taught(pack_id)   # 只能在这里学的课：先上课再做题，不摸底（docs/没学就先考-讨论稿.md）
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
        unchecked = [k for k in pv["learned"] if k not in c.hold and c.mastery.get(k, {}).get("status") in (None, "unknown")
                     and stage_rank(catalog.stage_in(pack_id, k)) >= stage_rank(stage) - 2]
        if c.prefer_hot:
            unchecked = [k for k in unchecked if catalog.kps[k].get("hot")] or unchecked
        if unchecked:
            k = catalog.kps[unchecked[c.day_seed % len(unchecked)]]
            out["check"].append({"type": "check", "kp": k["id"], "title": f"还记得吗：{k['name']}",
                                 "why": f"{'学校学过' if k['id'] in c.taught else '以前学过'}，做道小题看看（会的话很快就过）",
                                 "minutes": 6, "pack": pack_id})
        diag = db.one("SELECT id FROM diag_sessions WHERE user_id=? AND pack_id=? AND status='done'", c.user_id, pack_id)
        if not diag and not own:  # 考证、自编课从零学起，不先摸底（想测可以在「学科」里自己点诊断；每课也能「考我一下」跳过）
            out["diagnose"].append(pack_id)
        kp_ids = set(catalog.ids_for(pack_id, e["track"])) - c.hold
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
                out["backfill"].append({"type": "backfill", "kp": g["id"], "for": w["kp_id"], "title": f"先打基础：{g['name']}",
                                        "why": f"「{kp['name']}」要用到它", "minutes": 10, "pack": pack_id})
            else:
                out["weak"].append({"type": "weak", "kp": w["kp_id"], "title": f"攻克：{kp['name']}",
                                    "why": "刚学不久，多练几次就熟了" if w["status"] == "learning" else "上次有点卡，再练一次会更稳",
                                    "minutes": 12, "pack": pack_id})
        if own and not e["exam_date"]:   # 自编课：按课本顺序上下一课；有考试日期的由 src_exam 安排新内容
            for k in engine.next_lessons(c.user_id, pack_id, c.hold)[:1]:
                out["lesson"].append({"type": "lesson", "kp": k["id"], "title": f"新的一课：{k['name']}", "pack": pack_id,
                                      "why": "先看这一课讲什么、为什么学，学完再做题", "minutes": 10})
        elif diag and not progress_kp and not e["exam_date"]:  # 诊断前不安排预习；有考试日期的由 src_exam 安排新内容
            for k in engine.frontier(c.user_id, pack_id, stage, c.mastery)[:2]:
                out["preview"].append({"type": "preview", "kp": k["id"], "title": f"预习：{k['name']}",
                                       "why": "要先会的你都会了，可以先看一眼" + ("，考试常考" if k.get("hot") else ""), "minutes": 8, "pack": pack_id})
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
             "why": f"{'、'.join(names[:3])}：填上哪天考试，每天学多少就按剩下的天数排", "minutes": 2,
             "url": "/progress"}] if names else []


def src_warmup(c: Ctx) -> list[dict]:
    """热身：几道小题，混着以前学过的知识点和旧单词——不知不觉中把过去摸清（见 explore.py）。"""
    has_probe = bool(explore.candidates(c.user_id, c.mastery)[:1])
    has_words = bool(explore.word_lists(c.user_id))
    if not (has_probe or has_words):
        return []
    n = (3 if has_probe else 0) + (2 if has_words else 0)
    return [{"type": "warmup", "title": f"热身 {n} 题", "why": "先动动脑：几道小题有新有旧，答错也没关系",
             "minutes": 4, "url": "/warmup"}]


def src_words(c: Ctx) -> list[dict]:
    """单词：单词包里今天的新词（先学一遍）+ 到期要复习的词（app/words.py）。"""
    words.daily_new(c.user_id)
    due = engine.due_cards(c.user_id, 300, "words")
    if not due:
        return []
    new = sum(1 for d in due if not d["reviews"] and (db.jload(d["extra"], {}) or {}).get("new"))
    old = len(due) - new
    title = "单词：" + "，".join(x for x in (f"{new} 个新词" if new else "", f"复习 {old} 个" if old else "") if x)
    return [{"type": "words", "title": title, "why": "新词先猜意思，再放进句子里用一次；答错的一会儿还会再来",
             "minutes": min(15, 3 + new + len(due) // 4), "url": "/words/go"}]


def src_mistakes(c: Ctx) -> list[dict]:
    n = len(engine.due_cards(c.user_id, 300, "mistakes"))
    return [{"type": "mistakes", "title": f"错题重做 {min(n, 8)} 道", "why": "以前做错的题回来了，这次做对，离过关又近一步",
             "minutes": min(10, 2 + min(n, 8)), "url": "/review?group=mistakes"}] if n else []


def _news_title(c: Ctx) -> str | None:
    from . import news
    u = db.one("SELECT grade, settings FROM users WHERE id=?", c.user_id)
    return news.today_title(u) if u else None


def src_reading(c: Ctx) -> list[dict]:
    out = []
    active = engine.tracks(c.user_id)
    langs = {catalog.packs[e["pack_id"]].lang for e in c.enrolls} - {""}
    for kind, lang, label in (("read_en", "en", "英语阅读"), ("read_zh", "zh", "名著接着读")):
        tr = [t for t in active if t["kind"] == kind]
        if tr and tr[0]["ref"].startswith("lib:") and not _lib_ok(tr[0]):
            tr = []   # 这本书现在读不了（原文坏了、被下架）：先当没选书
        if tr and tr[0]["ref"].startswith("lib:"):  # 书库里的书：在网站上按页读，读够自动打勾
            out.append(_library_task(c, tr[0], kind, label))
        elif tr:
            t, seg = tr[0], engine.track_today(tr[0])
            out.append({"type": kind, "track": t["id"], "title": f"{label}：《{t['title']}》{seg['label']}",
                        "why": f"读 {t['daily_minutes']} 分钟，读完用一句话说说讲了什么",
                        "minutes": t["daily_minutes"], "url": f"/track/{t['id']}"})
        elif lang == "en" and lang in langs and (nt := _news_title(c)):  # 没指定书：英文阅读读今天的新闻
            out.append({"type": kind, "lang": lang, "title": f"英文阅读：今日新闻《{nt}》",
                        "why": "世界上今天发生的一件大事，按你的水平写成英文；不懂的词点一下就查，读完做小题", "minutes": 12, "url": "/news"})
        elif lang in langs:
            out.append({"type": kind, "lang": lang, "title": f"{'英文' if lang == 'en' else '中文'}阅读 15 分钟",
                        "why": "读一篇短文，不懂的词点一下就查，收藏后自动进单词复习", "minutes": 15, "url": f"/reading/today?lang={lang}"})
    for t in active:
        if t["kind"] == "listen" and t["ref"].startswith("lib:") and _lib_ok(t):
            bid = t["ref"][4:]
            from .library_web import progress
            p = progress(c.user_id, bid)
            start = max(1, p["listen_page"] or (p["page"] + 1))
            out.append({"type": "listen", "track": t["id"], "title": f"🎧 听书 {t['daily_minutes']} 分钟：《{t['title']}》",
                        "why": "跟着声音看文字，正在读的段落会亮起来；听够时间自动完成", "minutes": t["daily_minutes"],
                        "url": f"/books/{bid}/p/{start}?listen=1"})
    return out


def _lib_ok(t) -> bool:
    from .library import library
    return library.available(t["ref"][4:])


def _library_task(c: Ctx, t, kind: str, label: str) -> dict:
    from .library import library
    bid = t["ref"][4:]
    n = max(1, t["daily_amount"])
    start = t["position"] + 1
    total = (library.text(bid) or {}).get("n_pages", 0) or t["total_units"]  # 书重新整理过页数会变，以现在的为准
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
                out.append({"type": "exam", "kp": k, "title": f"新考点：{kp['name']}", "pack": pack_id,
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
                            "why": f"离考试还有 {ex['days_left']} 天，这个考试常考，还不太熟", "minutes": 8,
                            "url": f"/learn/{k}?task=exam", "keep": i == 0})
    return out


def src_sync(c: Ctx) -> list[dict]:
    return c.per_pack["sync"]


def src_top(c: Ctx) -> list[dict]:
    """系统自动发现的最重要的一条：紧跟在「跟上学校」后面，不会因为时间不够被截掉。"""
    return [{**t, "keep": True} for t in (c.auto["backfill"] + c.auto["weak"])[:1]]


def src_paper(c: Ctx) -> list[dict]:
    return [{"type": "paper", "url": f"/papers/{p['id']}",
             **({"title": f"模拟考：{p['title']}", "why": "接着把这套卷子做完，做完看看哪里丢了分", "minutes": 30} if p["bank_paper_id"] else
                {"title": f"试卷订正：{p['title']}", "why": "把卷子上的题在线再做一遍，做完看看哪些是真不会", "minutes": 20})}
            for p in db.q("SELECT id, title, bank_paper_id FROM papers WHERE user_id=? AND status='ready' ORDER BY id", c.user_id)]


def src_diagnose(c: Ctx) -> list[dict]:
    return [{"type": "diagnose", "pack": p, "title": f"{catalog.packs[p].subject_name}小测：看看你会哪些（约 10 分钟）",
             "why": "测完就知道从哪开始最合适，不用做太难或太简单的题", "minutes": 12, "url": f"/diagnose/{p}"} for p in c.per_pack["diagnose"]]


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
    mine = {k for e in c.enrolls for k in catalog.ids_for(e["pack_id"], e["track"])} - c.hold
    confirm = [m for k, m in c.mastery.items() if k in mine and m["status"] == "learning" and (m["score"] or 0) >= 0.6
               and m.get("last_ev") and evidence.days_since(m["last_ev"]) >= 0.5 and m["attempts"] and catalog.kp(k)]
    confirm.sort(key=lambda m: -(m["score"] or 0))
    due = [m for m in evidence.due_checks(c.user_id, c.mastery, limit=len(c.mastery)) if m["kp_id"] in mine]
    transferred = [m for k, m in c.mastery.items() if k in mine and m.get("source") == "concept-transfer"]
    out = []
    selected, seen_concepts = [], set()
    for m in due + confirm + transferred:
        cid = catalog.concept_id(m["kp_id"])
        if cid not in seen_concepts:
            selected.append(m)
            seen_concepts.add(cid)
    for m in selected[:c.limit("check", 2)]:
        need = "、".join(evidence.missing(c.user_id, m)[:2])
        label = "换个情境试试" if m.get("source") == "concept-transfer" else "隔一阵再试试" if m in due else "再练一次"
        out.append({"type": "check", "kp": m["kp_id"], "title": f"{label}：{catalog.kps[m['kp_id']]['name']}",
                    "why": "先不看答案，试着想起来；做完再看讲解", "minutes": 5, "keep": not out, "pack": catalog.kp_pack[m["kp_id"]]})
    planned = {t["kp"] for t in out}
    return out + [t for t in _merge_auto(c, "check") if t["kp"] not in planned]


def src_preview(c: Ctx) -> list[dict]:
    return c.per_pack["preview"]


def src_lesson(c: Ctx) -> list[dict]:
    """只能在这里学的课（自编课、考证）：每门课按课本顺序上下一课。学完了，这一课的题才进练习（engine.unlearned）。
    第一门课的那一课不因为时间不够被截掉：不上课，后面就没有能练的。"""
    return [{**t, "keep": i == 0} for i, t in enumerate(c.per_pack["lesson"])]


MIX_MIN_MINUTES = 10


def src_mix(c: Ctx) -> list[dict]:
    """每门课一组混合练习：新的、近期的、以前的（跟着今天学的往回找）、换问法、预习混着出（见 app/mix.py）。
    先按最少 10 分钟排；排完整天的计划后，把剩下的时间分给它们（build_plan 里的 fill）。"""
    out = []
    # 早上备题时建好、还没开始做的不算（app/prefetch.py）：题数照常按今天的时间分
    done_today = {r["pack_id"]: r for r in db.q("SELECT pack_id, slots, done_at FROM mix_sets WHERE user_id=? AND day=? AND pos>0",
                                                c.user_id, db.today().isoformat())}
    # 时间不够时排在后面的会被截掉：每天换一门打头，几门课轮着来
    k = c.day_seed % len(c.enrolls) if c.enrolls else 0
    for i, e in enumerate(c.enrolls[k:] + c.enrolls[:k]):
        pack = catalog.packs[e["pack_id"]]
        if pack.system == "cert" and not e["exam_date"]:   # 考证的课先设考试日期（src_exam_date）
            continue
        if engine.self_taught(pack.id) and not set(catalog.ids_for(pack.id, e["track"])) - c.hold:
            continue   # 只能在这里学的课，一课都还没上过：今天先上课（「新的一课」），没有能练的
        n = mix.size_for(MIX_MIN_MINUTES, c.user["grade"])
        made = done_today.get(pack.id)
        if made:   # 今天已经开始做了：题数以那一组为准
            n = len(db.jload(made["slots"], [])) or n
        out.append({"type": "mix", "pack": pack.id, "title": f"{pack.subject_name}练习 {n} 题", "n": n,
                    "why": "今天学的、最近学的、以前学的混着练；做错的过几题还会再来一道",
                    "minutes": round(n * mix.minutes_per_item(c.user["grade"])), "url": f"/practice/{pack.id}",
                    "keep": i == 0, "fill": not made})
    return out


def src_drill(c: Ctx) -> list[dict]:
    """快练 5 分钟：口算、单词、术语、公式和单位这类短题，限时、连对加倍（冲刺的玩法，见 app/sprint.py）。
    求又快又准：基本功练到不用想。成人学习者不排。"""
    if is_adult(c.user["grade"]) or not c.enrolls:
        return []
    return [{"type": "drill", "title": "⚡ 快练 5 分钟", "why": "口算、单词、术语这类短题，越快越准越好；连对倍数会越来越高",
             "minutes": 5, "url": "/sprint?drill=1"}]


def src_review(c: Ctx) -> list[dict]:
    n = len(engine.due_cards(c.user_id, 100, "other"))
    return [{"type": "review", "title": f"复习卡片 {n} 张", "why": "学过的隔几天看一眼，就不容易忘",
             "minutes": min(10, 2 + n), "url": "/review?group=other"}] if n else []


SOURCES = {"progress": src_progress, "exam_date": src_exam_date, "warmup": src_warmup, "words": src_words, "mistakes": src_mistakes,
           "reading": src_reading, "exam": src_exam, "sync": src_sync, "top": src_top, "paper": src_paper, "diagnose": src_diagnose,
           "weak": src_weak, "backfill": src_backfill, "check": src_check, "preview": src_preview, "lesson": src_lesson, "review": src_review,
           "mix": src_mix, "drill": src_drill}
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
    schoolyear.sync(user_id)   # 先按今天的日期推演学校进度（孩子不用报）
    c = Ctx(user_id)
    fixed = [t for kind in c.policy.get("fixed", []) for t in _take(c, kind)]
    # 今天追根找到的结论（先补根、再回来打）：不管哪种学习方式都排在学知识点的任务最前面
    flex, seen = trace.today_tasks(user_id, c.mastery), set()

    seen |= {("concept", catalog.learning_key(t["kp"])) if t.get("kp") else (t["type"], t.get("kp"), t.get("pack"), t.get("url")) for t in flex}
    for group in c.policy.get("flex", []):
        for t in interleave(*[_take(c, k) for k in group.split("+")]):
            key = ("concept", catalog.learning_key(t["kp"])) if t.get("kp") else (t["type"], t.get("kp"), t.get("pack"), t.get("url"))
            if key not in seen:  # 同一个任务不排两次（比如自动发现的那条已经排在前面）
                seen.add(key)
                flex.append(t)
    # tail：每天都有、排在最后的任务（默认是阅读：学完新的，用大量输入放松收尾），和 fixed 一样不受时间预算限制
    tail = [t for kind in c.policy.get("tail", []) for t in _take(c, kind)]
    # 最后一道关：任务指向的知识点学习者必须打得开（和学习页同一条规则 catalog.kp_visible），哪个来源漏了都排不进来
    fixed, flex, tail = ([t for t in ts if not t.get("kp") or catalog.kp_visible(t["kp"], c.user["grade"])]
                         for ts in (fixed, flex, tail))
    # 成人课：刚学完（或刚回访完）的课隔几天才再排（3 / 7 / 14…天，app/lessons.py），不连着几天同一课
    if is_adult(c.user["grade"]):
        rest = lessons.resting(user_id)
        fixed, flex, tail = ([t for t in ts if t.get("kp") not in rest] for ts in (fixed, flex, tail))
    budget = c.user["daily_minutes"] or 60
    out, used, n_flex = [], sum(t["minutes"] for t in tail), 0
    for part, t in [("fixed", t) for t in fixed] + [("flex", t) for t in flex] + [("tail", t) for t in tail]:
        is_flex = part == "flex"
        if is_flex and n_flex and not t.get("keep") and used + t["minutes"] > budget:
            continue
        n_flex += is_flex
        t["id"] = f"t{len(out)}"
        t["done"] = False
        out.append(t)
        if part != "tail":  # tail 的时间开头已经算进去了
            used += t["minutes"]
    _fill(c, out, budget - used)
    return out


def _fill(c: Ctx, plan: list[dict], left: float) -> None:
    """排完以后还剩的时间，平分给还没开始的混合练习：每天练多少由家长设的每天时长决定。"""
    fills = [t for t in plan if t.get("fill")]
    if not fills or left <= 0:
        for t in plan:
            t.pop("fill", None)
        return
    per = mix.minutes_per_item(c.user["grade"])
    share = left / len(fills)
    for t in fills:
        n = mix.size_for(t["minutes"] + share, c.user["grade"])
        name = t["title"].rsplit("练习", 1)[0]
        t.update(n=n, title=f"{name}练习 {n} 题", minutes=round(n * per))
    for t in plan:
        t.pop("fill", None)
