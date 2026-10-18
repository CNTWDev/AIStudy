"""每门课一组混合练习（docs/DESIGN.md 5.23）：新知识和旧知识混在一起反复练，一天的练习有面也有深度。

以前每个知识点一张任务卡、每张 3 道题，过去的内容测对一次就不再出，于是每天都围着「正在学的 + 做错的」打转。
现在今天页上每门课一张「XX 练习 N 题」，一组题里按比例混着出（比例在学习方式里调，profiles.toml 的 mix）：

- new     新内容：学校正在学的（按校历推演，见 app/schoolyear.py），同一个点分散着出 3～4 次，最后一次是融合题；
- recent  近期学的：最近 3 周练过、还不稳的，和按遗忘规律该复习的；
- past    往回：先挑今天的新内容要用到的旧知识（沿前置往回找，可以跨教材跨学科，见 app/ladder.py），
          再在最近两个学年里按覆盖面铺开；没测过的、只是推断会的按摸底出（答对顺带推断前置，答错沿前置一层层往回追），
          会了的按遗忘规律长间隔回来。更早的内容（地基、低两个年级以上）一组最多 3 道；往回的比例跟着摸底结果调
          （最近两周以前的题大多答对就少出，错得多就多出，见 past_factor）；
- cross   交叉校验：答对过但只用一种问法的、从别的教材推断来的，换一种题型 / 情境再问；
- preview 预习：学校下一步要学的，两道入门题。

一组里做错的题（摸底题除外）过几题换一道同类题再来，最多两次（连续重学）。
融合题：新知识点的最后一次是一道同时用到它和一两个旧知识点的题（AI 出、存进题库复用）。
"""
import random
from datetime import timedelta

from . import db, engine, evidence, explore, ladder, schoolyear
from .catalog import catalog, stage_label, stage_rank

DEFAULT_MIX = {"new": 0.3, "recent": 0.2, "past": 0.3, "cross": 0.1, "preview": 0.1}
ROLE_LABEL = {"new": "新学的", "recent": "最近学的", "past": "以前学过的", "cross": "换个问法", "preview": "预习",
              "again": "刚才错的，再来一道"}
RECENT_DAYS = 21
MAX_AGAIN = 2          # 一组里同一个知识点做错后最多再来几次
MIN_ITEMS, MAX_ITEMS = 6, 30
NEW_REPS = 5           # 一个新知识点一组里最多出几次（分散开，最后一次是融合题）
MAX_DEEP = 3           # 一组里「更早的」（地基、低两个年级以上）最多几道
CHUNK = 4
DEFER = 3          # 题库里没有现成题的那道往后挪几位（后台出题的时间）


def minutes_per_item(grade: str) -> float:
    """一道题大约几分钟（含看讲解）：低年级题短。"""
    r = stage_rank(grade or "")
    return 0.8 if r <= 4 else 1.0 if r < 99 else 1.2


def size_for(minutes: float, grade: str) -> int:
    return int(min(MAX_ITEMS, max(MIN_ITEMS, round(minutes / minutes_per_item(grade)))))


def _alloc(n: int, ratio: dict) -> dict:
    """按比例分 n 道题（最大余数法）。"""
    total = sum(ratio.values()) or 1
    raw = {k: n * v / total for k, v in ratio.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: n - sum(out.values())]:
        out[k] += 1
    return out


class _Ctx:
    def __init__(self, user_id: int, pack_id: str):
        self.user_id, self.pack_id = user_id, pack_id
        self.user = dict(db.one("SELECT * FROM users WHERE id=?", user_id))
        self.enrolls = [dict(e) for e in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", user_id)
                        if e["pack_id"] in catalog.packs]
        self.e = next((e for e in self.enrolls if e["pack_id"] == pack_id), None)
        self.mastery = engine.get_mastery(user_id)
        self.taught = engine.taught_set(user_id)
        self.profile = evidence.profile(user_id)
        own = set(catalog.ids_for(pack_id, self.e["track"])) if self.e else set()
        past = set(explore.past_range(user_id, self.e, self.taught)) if self.e else set()
        self.found = ladder.scope_for(pack_id, self.user, self.enrolls) if self.e else set()
        self.own = own
        self.calendar = bool(self.e and schoolyear.estimate(self.user, self.e))
        self.back = past | self.found      # 能往回练的
        self.scope = own | self.found      # 这一组能出的知识点

    def st(self, k):
        return self.mastery.get(k, {})


def _new_kps(c: _Ctx) -> list[str]:
    e = c.e
    out = []
    ex = engine.exam_view(c.user_id, e, c.mastery, c.taught)
    if ex and ex["phase"] == "learn":
        out += ex["todo"][:2]
    cur = e["progress_kp"]
    if cur and catalog.kp(cur) and c.st(cur).get("status") != "mastered":
        out.append(cur)
    if cur and catalog.kp(cur):   # 同一章里正在学的下一个（推演到这里了、还没掌握）
        ch = next((ch for ch in catalog.chapters(e["pack_id"], e["stage"], e["track"]) if cur in ch["kps"]), None)
        if ch:
            i = ch["kps"].index(cur)
            out += [k for k in ch["kps"][:i] if k in c.taught and c.st(k).get("status") in ("weak", "learning")][-1:]
    if len(out) < 2 and not cur and not c.calendar:   # 没有学校进度的课（按能力分的）：前置都会了、还没学的；按校历走的课放假不学新的
        out += [k["id"] for k in engine.frontier(c.user_id, e["pack_id"], e["stage"], c.mastery)[:2 - len(out)]]
    return list(dict.fromkeys(out))[:2]


def _recent(c: _Ctx, used: set) -> list[str]:
    rows = [m for k, m in c.mastery.items() if k in c.scope and k not in used and m.get("last_ev") and m.get("attempts")
            and 0.5 <= evidence.days_since(m["last_ev"]) <= RECENT_DAYS and m.get("status") in ("weak", "learning")]
    rows.sort(key=lambda m: (m["status"] != "weak", m.get("score") or 0))
    due = [m for m in evidence.due_checks(c.user_id, c.mastery, limit=200) if m["kp_id"] in c.scope and m["kp_id"] not in used
           and evidence.days_since(m["last_ev"]) <= 60]
    return list(dict.fromkeys([m["kp_id"] for m in rows] + [m["kp_id"] for m in due]))


def _cross(c: _Ctx, used: set) -> list[str]:
    out = []
    for k, m in c.mastery.items():
        if k not in c.scope or k in used or not catalog.kp(k):
            continue
        fmts = (db.jload(m.get("evidence"), {}) or {}).get("fmts", [])
        if m.get("source") == "concept-transfer" or (m.get("status") in ("learning", "mastered") and m.get("attempts")
                                                     and len(fmts) <= 1 and (m.get("score") or 0) >= 0.6):
            out.append(k)
    random.Random(f"{c.user_id}-{db.today()}").shuffle(out)
    return out


def past_factor(user_id: int) -> float:
    """往回练多少跟着摸底的结果调：最近两周以前的题大多答对（基础扎实），往回的少出一些，时间留给新的和近期的；
    错得多（基础有洞），多出一些，但不超过一半，免得新课没时间学（认知负荷：旧的洞太多时先补关键的）。"""
    rows = db.q("SELECT result FROM probes WHERE user_id=? AND kind='kp' AND status='done' AND done_at>=?",
                user_id, (db.today() - timedelta(days=14)).isoformat())
    if len(rows) < 8:
        return 1.0
    acc = sum(1 for r in rows if r["result"]) / len(rows)
    return 0.6 if acc >= 0.85 else 1.4 if acc < 0.6 else 1.0


def _deep(c: _Ctx, k: str) -> bool:
    """「更早的」：别的教材里的地基，或者比孩子年级低两年以上。"""
    g = stage_rank(c.user.get("grade") or "")
    return k in c.found or (g < 99 and stage_rank(catalog.kps[k]["stage"]) < g - explore.BREADTH_YEARS)


def _past(c: _Ctx, used: set, near: list[str]) -> list[dict]:
    """往回：先挑新内容要用到的旧知识，再按覆盖面铺开（explore.candidates 的打分），再加上会了、按遗忘规律该回来的。"""
    out, seen = [], set(used)
    pools = [explore.candidates(c.user_id, c.mastery, near_kp=k) for k in near]
    pools.append(explore.candidates(c.user_id, c.mastery))
    deep = 0
    for pool in pools:
        for x in pool:
            k = x["kp"]
            if k in c.back and k not in seen:
                if _deep(c, k):   # 更早的内容一组最多几道：沿着新知识往回找，一天补几个关键的，不把一组题变成小学复习
                    if deep >= MAX_DEEP:
                        continue
                    deep += 1
                seen.add(k)
                out.append({"kp": k, "probe": True, "reason": x["reason"], "depth": x["depth"], "from": x["from"]})
    for m in evidence.due_checks(c.user_id, c.mastery, limit=200):
        k = m["kp_id"]
        if k in c.back and k not in seen and not (_deep(c, k) and deep >= MAX_DEEP):
            deep += _deep(c, k)
            seen.add(k)
            out.append({"kp": k, "probe": False, "reason": f"{int(evidence.days_since(m['last_ev']))} 天没碰了"})
    return out


def _preview(c: _Ctx, used: set, new: list[str]) -> list[str]:
    e = c.e
    out = []
    base = e["progress_kp"] or (new[0] if new else None)
    if base:
        nxt = engine.next_after(e["pack_id"], base, c.mastery, c.taught, e["track"])
        if nxt and nxt["id"] not in used:
            out.append(nxt["id"])
    if not out:   # 放假中 / 没有进度：前置都会了、还没学的
        out += [k["id"] for k in engine.frontier(c.user_id, e["pack_id"], e["stage"], c.mastery) if k["id"] not in used][:1]
    return out


def _interleave(groups: list[list[dict]]) -> list[dict]:
    """几类题按剩下的比例轮着出，同一个知识点不挨着。"""
    groups = [list(g) for g in groups if g]
    total = {id(g): len(g) for g in groups}
    out = []
    while any(groups):
        live = [g for g in groups if g]
        live.sort(key=lambda g: -len(g) / total[id(g)])
        pick = next((g for g in live if not out or g[0]["kp"] != out[-1]["kp"]), live[0])
        out.append(pick.pop(0))
    return out


def build(user_id: int, pack_id: str, n: int) -> list[dict]:
    """排一组题：[{kp, role, mode, purpose, probe?, uses?}]。"""
    c = _Ctx(user_id, pack_id)
    if not c.e:
        return []
    ratio = {**DEFAULT_MIX, **(c.profile.plan.get("mix") or {})}
    if int((c.profile.plan.get("limits") or {}).get("preview", 1)) == 0:
        ratio["preview"] = 0
    ratio["past"] *= past_factor(user_id)
    want = _alloc(n, ratio)
    used: set = set()
    new = _new_kps(c)
    used |= set(new)
    cand = {"recent": _recent(c, used)}
    used |= set(cand["recent"][: want["recent"]])
    cand["cross"] = _cross(c, used)
    used |= set(cand["cross"][: want["cross"]])
    cand["preview"] = _preview(c, used, new)
    used |= set(cand["preview"])
    past = _past(c, used, new)
    # 某一类凑不够：先给往回，再给近期、新内容
    got = {"new": min(want["new"], 4 * len(new)), "recent": min(want["recent"], len(cand["recent"])),
           "cross": min(want["cross"], len(cand["cross"])), "preview": min(want["preview"], 2 * len(cand["preview"]))}
    # 往回的最多占一半（刚开始用、近期和换问法都还没有的时候，空出来的先给新内容多练几遍），实在没别的才再多给往回
    half = (n + 1) // 2 if new else n
    got["past"] = min(len(past), n - sum(got.values()), half)
    left = n - sum(got.values())
    for role, cap in (("recent", len(cand["recent"])), ("new", NEW_REPS * len(new)), ("cross", len(cand["cross"])),
                      ("past", len(past))):
        add = max(0, min(left, cap - got[role]))
        got[role] += add
        left -= add

    slots = {r: [] for r in DEFAULT_MIX}
    for i in range(got["new"]):
        k = new[i % len(new)]
        slots["new"].append({"kp": k, "role": "new", "mode": "practice", "purpose": "practice"})
    # 每个新知识点的最后一次换成融合题：带上一两个它要用到的旧知识点
    for k in new:
        mine = [s for s in slots["new"] if s["kp"] == k]
        if len(mine) >= 3:
            uses = [p["id"] for p, _ in catalog.ancestors(k, depth=3) if p["id"] in c.back][:2]
            uses += [r["kp"]["id"] for r in catalog.related(k, types=["uses"]) if r["dir"] == "out" and r["kp"]["id"] in c.back][:2 - len(uses)]
            if uses:
                mine[-1].update(purpose="combine", uses=uses)
    for k in cand["recent"][: got["recent"]]:
        slots["recent"].append({"kp": k, "role": "recent", "mode": "practice", "purpose": "practice"})
    for k in cand["cross"][: got["cross"]]:
        slots["cross"].append({"kp": k, "role": "cross", "mode": "practice", "purpose": "practice"})
    for i in range(got["preview"]):
        k = cand["preview"][i % len(cand["preview"])]
        slots["preview"].append({"kp": k, "role": "preview", "mode": "preview", "purpose": "preview"})
    for x in past[: got["past"]]:
        s = {"kp": x["kp"], "role": "past", "mode": "probe" if x["probe"] else "practice",
             "purpose": "diagnose" if x["probe"] else "practice", "reason": x["reason"]}
        if x["probe"]:
            s["probe"] = {"depth": x.get("depth", 0), "from": x.get("from", "")}
        slots["past"].append(s)
    # 新内容打头（先见到今天要学的），预习放在后半段
    seq = _interleave([slots["new"], slots["past"], slots["recent"], slots["cross"]])
    if seq and slots["new"] and seq[0]["role"] != "new":
        first = next(i for i, s in enumerate(seq) if s["role"] == "new")
        seq.insert(0, seq.pop(first))
    half = len(seq) // 2
    for j, s in enumerate(slots["preview"]):
        seq.insert(min(len(seq), half + 2 * j + 1), s)
    return seq


def summary(slots: list[dict]) -> dict:
    out = {r: 0 for r in DEFAULT_MIX}
    for s in slots:
        if s["role"] in out:
            out[s["role"]] += 1
    return out


# ------------------------------------------------------------------ 一天一组：存起来，分批出题

def get_or_create(user_id: int, pack_id: str, n: int) -> dict:
    day = db.today().isoformat()
    row = db.one("SELECT * FROM mix_sets WHERE user_id=? AND day=? AND pack_id=?", user_id, day, pack_id)
    if row and not row["pos"] and len(db.jload(row["slots"], [])) != n:
        # 早上备题时建的、还没开始做，后来今天的计划改了题数：按新的题数重建（已经备好的题都在题库里，照样用得上）
        db.run("UPDATE mix_sets SET slots=?, served='[]' WHERE id=? AND pos=0", db.jdump(build(user_id, pack_id, n)), row["id"])
        row = db.one("SELECT * FROM mix_sets WHERE id=?", row["id"])
    if row:
        return dict(row)
    slots = build(user_id, pack_id, n)
    try:
        db.insert("INSERT INTO mix_sets(user_id,day,pack_id,slots,created_at) VALUES(?,?,?,?,?)",
                  user_id, day, pack_id, db.jdump(slots), db.now())
    except Exception:  # noqa: BLE001  两个页面同时打开：用先建好的那组
        pass
    return dict(db.one("SELECT * FROM mix_sets WHERE user_id=? AND day=? AND pack_id=?", user_id, day, pack_id))


def _set(user_id: int, set_id: int) -> dict | None:
    r = db.one("SELECT * FROM mix_sets WHERE id=? AND user_id=?", set_id, user_id)
    return dict(r) if r else None


def _pick(user_id: int, sl: dict, grade: str, served: list, ai: bool) -> list[dict]:
    items = engine.items_for(user_id, sl["kp"], n=1, purpose=sl.get("purpose", "practice"), grade=grade,
                             exclude=served, uses=sl.get("uses"), ai=ai)
    if not items and sl.get("purpose") == "combine" and ai:   # 融合题出不来：照常出一道
        items = engine.items_for(user_id, sl["kp"], n=1, grade=grade, exclude=served)
    return items


def _locked(t, set_id: int) -> list:
    """在事务里读题单（PostgreSQL 锁住这一行）：挪题和答错加题都是「读出来改了再写回」，不能互相覆盖。"""
    row = t.one("SELECT slots FROM mix_sets WHERE id=?" + (" FOR UPDATE" if db.IS_PG else ""), set_id)
    return db.jload(row["slots"], []) if row else []


def _defer(set_id: int, pos: int, sl: dict) -> list:
    """把第 pos 道往后挪 DEFER 位、标上挪过。返回挪完的题单。"""
    with db.tx(write=True) as t:
        slots = _locked(t, set_id)
        if pos < len(slots) and slots[pos] == sl:
            slots.pop(pos)
            slots.insert(min(len(slots), pos + DEFER), {**sl, "deferred": 1})
            t.run("UPDATE mix_sets SET slots=? WHERE id=?", db.jdump(slots), set_id)
        return slots


def next_items(user_id: int, set_id: int, grade: str, k: int = CHUNK) -> dict:
    """再出 k 道。只给题库里现成的：某一道没有现成题，不停下来等 AI，把它往后挪 DEFER 位、交给后台去出
    （app/prefetch.py），先给后面有题的；挪过一次又轮到它还没好：手上已经有题就先给这些，下次再来；
    一道都没有才现场出。返回 {items, pos, total, done}。"""
    from . import llm, prefetch
    s = _set(user_id, set_id)
    if not s:
        return {"items": [], "pos": 0, "total": 0, "done": True}
    slots, served = db.jload(s["slots"], []), db.jload(s["served"], [])
    pos, out = s["pos"], []
    while pos < len(slots) and len(out) < k:
        sl = slots[pos]
        kp = catalog.kp(sl["kp"])
        if not kp:
            pos += 1
            continue
        items = _pick(user_id, sl, grade, served, ai=not llm.enabled())
        if not items and llm.enabled():
            if not sl.get("deferred") and pos + 1 < len(slots):
                left = sum(1 for x in slots[pos:] if x["kp"] == sl["kp"] and x.get("purpose", "practice") == sl.get("purpose", "practice"))
                prefetch.kick(user_id, sl["kp"], n=min(5, left), purpose=sl.get("purpose", "practice"), grade=grade,
                              uses=sl.get("uses"))
                slots = _defer(set_id, pos, sl)
                continue
            if out:   # 已经有题可做：这道等后台出好，下次取题时再给
                break
            prefetch.wait(user_id, sl["kp"], sl.get("purpose", "practice"))
            items = _pick(user_id, sl, grade, served, ai=True)
        pos += 1
        if not items:
            continue
        it = items[0]
        served.append(it["id"])
        pack = catalog.packs[catalog.kp_pack[sl["kp"]]]
        tag = {"role": sl["role"], "label": ROLE_LABEL.get(sl["role"], ""), "kp": sl["kp"], "name": kp["name"],
               "subject": pack.subject_name, "stage": kp["stage"], "stage_label": stage_label(kp["stage"]), "mode": sl["mode"],
               "combine": it.get("id") and sl.get("purpose") == "combine" and len(it.get("kp_ids") or []) > 1,
               "reason": sl.get("reason", "")}
        if sl.get("probe"):
            tag["probe"] = sl["probe"]
        out.append({**it, "kp_id": sl["kp"], "mix": tag})
    db.run("UPDATE mix_sets SET pos=?, served=? WHERE id=?", pos, db.jdump(served), set_id)
    return {"items": out, "pos": pos, "total": len(slots), "done": pos >= len(slots)}


def tick(user_id: int, set_id: int, kp_id: str, role: str, correct: bool) -> dict:
    """答完一道：记数；做错了（摸底题除外）过几题换一道同类题再来，同一个点一组里最多两次。"""
    s = _set(user_id, set_id)
    if not s:
        return {}
    with db.tx(write=True) as t:
        slots = _locked(t, set_id)
        pos = t.one("SELECT pos FROM mix_sets WHERE id=?", set_id)["pos"]
        again = False
        if not correct and role != "past" and catalog.kp(kp_id):
            n_again = sum(1 for x in slots if x["kp"] == kp_id and x["role"] == "again")
            if n_again < MAX_AGAIN:
                at = min(len(slots), pos + 1)
                slots.insert(at, {"kp": kp_id, "role": "again", "mode": "practice", "purpose": "practice"})
                again = True
        t.run("UPDATE mix_sets SET slots=?, answered=answered+1, right_n=right_n+? WHERE id=?",
              db.jdump(slots), 1 if correct else 0, set_id)
    return {"again": again, "total": len(slots)}


def finish(user_id: int, set_id: int) -> dict:
    s = _set(user_id, set_id)
    if not s:
        return {}
    db.run("UPDATE mix_sets SET done_at=? WHERE id=?", db.now(), set_id)
    s = _set(user_id, set_id)
    return {"answered": s["answered"], "right": s["right_n"], "pack": s["pack_id"]}
