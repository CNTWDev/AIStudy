"""地基：孩子是半路开始用系统的，以前学过的内容没有记录。初中的课建在小学的课上，所以「以前学过的」
不只是这门课里更低的学段，最远能追到一年级，并且跨教材、跨学科（物理要用到小学的数学和科学）。

规则在 curricula/_meta/ladder.json（对所有孩子一样，不针对某个孩子）：
- 孩子报名的每门课，比现在低的学段本来就算「以前学过」（explore.past_range）；
- 教材是从中途开始的（剑桥数学从六年级开始），更低的学段用 base_packs 里这门学科的教材补齐；
- builds_on：这门学科要用到的学科（物理 → 科学、数学），孩子没报的，用 base_packs 的教材，到孩子的年级为止；
- 同一个概念在孩子报名的教材里已经有了，不重复算。

地基只是「能往回找到哪里」的边界，不是要全部摸一遍：只有和正在学的、今天要学的有关（是它们的前置或要用到的），
或者答错以后沿前置一层层往回追到的，才会出题（见 explore.candidates）。一组混合练习里这类更早的题最多 3 道。
不跟学校进度，也不学新的。
"""
from .catalog import catalog, stage_rank


def _closure(subject: str, builds_on: dict, depth: int = 3) -> list[str]:
    out, frontier = [], [subject]
    for _ in range(depth):
        nxt = []
        for s in frontier:
            for t in builds_on.get(s, []):
                if t not in out and t != subject:
                    out.append(t)
                    nxt.append(t)
        frontier = nxt
    return out


def foundations(user: dict, enrolls: list) -> list[dict]:
    """孩子的地基：[{pack_id, upto（学段 year 小于它的才算）, for_pack（为哪门报名的课打地基）, foundation: True,
    stage, track, progress_kp}]，样子和报名记录一样，explore 的往回逻辑可以直接用。"""
    cfg = catalog.ladder or {}
    systems = set(cfg.get("systems") or [])
    base, builds_on = cfg.get("base_packs") or {}, cfg.get("builds_on") or {}
    grade = stage_rank(user.get("grade") or "") if user else 99.0
    enrolled = {e["pack_id"] for e in enrolls}
    by_subject: dict[str, list] = {}
    for e in enrolls:
        by_subject.setdefault(catalog.packs[e["pack_id"]].subject, []).append(e)
    out: dict[str, dict] = {}

    def add(pack_id, upto, for_pack):
        if pack_id not in catalog.packs or pack_id in enrolled or upto <= 1:
            return
        cur = out.get(pack_id)
        if not cur or upto > cur["upto"]:
            out[pack_id] = {"pack_id": pack_id, "upto": upto, "for_pack": for_pack, "foundation": True,
                            "stage": "", "track": None, "progress_kp": None}

    for e in enrolls:
        pack = catalog.packs[e["pack_id"]]
        if pack.system not in systems:
            continue
        top = min(grade, stage_rank(e["stage"])) if grade < 99 else stage_rank(e["stage"])
        own_low = min((stage_rank(catalog.stage_in(pack.id, k)) for k in catalog.ids_for(pack.id, e["track"])), default=1)
        b = base.get(pack.subject)
        if b and b != pack.id:
            add(b, min(own_low, top), pack.id)        # 自己的教材从中途开始：更低的学段用基础教材补齐
        for s in _closure(pack.subject, builds_on):
            if s in by_subject:                        # 那门学科孩子报了名：它自己的往回范围已经算了
                continue
            if base.get(s):
                add(base[s], top, pack.id)
    return list(out.values())


def range_of(f: dict, known_concepts: set) -> list[str]:
    """一份地基里算数的知识点：学段比 upto 低，同一概念在报名的教材里没有。"""
    return [k for k in catalog.ids_for(f["pack_id"]) if stage_rank(catalog.stage_in(f["pack_id"], k)) < f["upto"]
            and catalog.concept_id(k) not in known_concepts]


def enrolled_concepts(enrolls: list) -> set:
    return {catalog.concept_id(k) for e in enrolls for k in catalog.ids_for(e["pack_id"], e["track"])}


def scope_for(pack_id: str, user: dict, enrolls: list) -> set[str]:
    """一门课的混合练习能往回练到的知识点：这门课自己比现在低的、已经教过的，加上为它打地基的教材。"""
    known = enrolled_concepts(enrolls)
    out = set()
    for f in foundations(user, enrolls):
        if f["for_pack"] == pack_id or catalog.packs[f["pack_id"]].subject in _closure(catalog.packs[pack_id].subject,
                                                                                       (catalog.ladder or {}).get("builds_on") or {}):
            out |= set(range_of(f, known))
    return out
