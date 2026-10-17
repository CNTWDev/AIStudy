"""学校大概学到哪了：按校历推演，孩子不用天天报进度（docs/DESIGN.md 5.23）。

- 校历在 curricula/_meta/calendar.json：每个学期的开学、放假日（月-日，每年通用），不上新课的日子，
  每学期最后几周复习。学制（stages.json 的 systems）决定用哪份校历。
- 一门课这一学段的「章」（catalog.chapters）按上课的天数铺开：分上下册的教材（上海）第一学期铺上册、
  第二学期铺下册；不分册的教材（剑桥、IGCSE）整学年按大纲顺序铺；两年的课程（IGCSE，学段 year 是 x.5）
  按孩子的年级算第一年还是第二年。每章占的天数按它的知识点数分。
- 推演出来的「正在学的章」之前的内容，记为「学校大概教过了」（kp_taught.source = inferred，每天重算）。
  这只是估计：这些内容第一次出现时按摸底处理，不当成「学过却不会」。
- 家长纠正：「学校快一章 / 慢一章」或直接点学到哪一章，存成偏移量（只在这一学期有效），以后照样按日期往前走。
  孩子 / 家长报的、导入的试卷考到的（source = manual）是实打实的证据：考到了后面的章，进度至少到那一章。
- 放假（寒暑假、学期之间）不推新课：学过的照样往回练，预习下学期的开头。
"""
from datetime import date, timedelta
from functools import lru_cache

from . import db
from .catalog import catalog, stage_rank


def calendar_for(pack) -> dict | None:
    """这门课用哪份校历（职业资格考试、自定课程、大学没有校历）。"""
    cal = catalog.calendar or {}
    name = (cal.get("systems") or {}).get(pack.system or "cn")
    c = (cal.get("calendars") or {}).get(name) if name else None
    return {**c, "id": name} if c else None


def today() -> date:
    """推演用的「今天」（测试里可以换成固定的日子）。"""
    return db.today()


def _md(s: str, y: int) -> date:
    m, d = (int(x) for x in s.split("-"))
    return date(y if m >= 7 else y + 1, m, d)


def school_year_of(day: date) -> int:
    """学年按 7 月 1 日分：2026-10-17 属于 2026 学年，2027-03-01 也属于 2026 学年。"""
    return day.year if day.month >= 7 else day.year - 1


@lru_cache(maxsize=64)
def _terms(cal_id: str, year: int) -> tuple:
    """某个学年的各学期：(key, name, term, start, last_teach_day, end, teach_days)。"""
    cal = (catalog.calendar.get("calendars") or {}).get(cal_id) or {}
    off = set()
    for a, b in cal.get("no_new", []):
        for y in (year, year + 1):
            d0, d1 = _md(a, y), _md(b, y)
            if d0.year != (year if d0.month >= 7 else year + 1):
                continue
            while d0 <= d1:
                off.add(d0)
                d0 += timedelta(days=1)
    out = []
    for t in cal.get("terms", []):
        start, end = _md(t["start"], year), _md(t["end"], year)
        last = end - timedelta(days=7 * int(cal.get("review_weeks", 0)))
        days = []
        d = start
        while d <= last:
            if d.weekday() < 5 and d not in off:
                days.append(d)
            d += timedelta(days=1)
        out.append((t["key"], t.get("name", ""), t.get("term", ""), start, last, end, tuple(days)))
    return tuple(out)


def where(cal: dict, day: date) -> dict:
    """今天在学年里的位置：哪个学期、这学期上新课的进度（0–1）、整学年上新课的进度（0–1）、是不是放假 / 复习周。"""
    y = school_year_of(day)
    terms = _terms(cal["id"], y)
    if terms and day < terms[0][3]:   # 新学年开学前的暑假：还算上一学年学完了（孩子的年级多半还没改）
        y -= 1
        terms = _terms(cal["id"], y)
    total = sum(len(t[6]) for t in terms) or 1
    before, cur, phase = 0, None, "vacation"
    for i, t in enumerate(terms):
        key, name, term, start, last, end, days = t
        if day < start:
            break
        done = sum(1 for d in days if d < day)
        if day <= end:
            cur, phase = i, ("term" if day <= last else "review")
            before += done
            break
        before += len(days)
    t = terms[cur] if cur is not None else None
    # 放假中：上一个结束的学期算学完，下一个学期还没开始
    if t is None:
        last_i = max((i for i, x in enumerate(terms) if x[5] < day), default=None)
        nxt = next((x for x in terms if x[3] > day), None)
        return {"year": y, "phase": "vacation", "term": terms[last_i] if last_i is not None else None,
                "term_f": 1.0 if last_i is not None else 0.0, "year_f": before / total,
                "key": f"{y}-{terms[last_i][0]}" if last_i is not None else f"{y}-0",
                "next_start": nxt[3].isoformat() if nxt else None, "name": "放假"}
    done = sum(1 for d in t[6] if d < day)
    return {"year": y, "phase": phase, "term": t, "term_f": done / (len(t[6]) or 1), "year_f": before / total,
            "key": f"{y}-{t[0]}", "next_start": None, "name": t[1]}


def _uses_terms(chs: list[dict]) -> bool:
    return any(ch.get("rank") in (0, 2) for ch in chs) and any(ch.get("rank") == 2 for ch in chs)


def _span(stage: str) -> tuple[int, int]:
    """学段占几个学年：year 是 x.5 的是两年的课程（IGCSE、A-Level），从 x 开始。"""
    yr = stage_rank(stage)
    if yr >= 99:
        return 0, 1
    return (int(yr), 2) if yr != int(yr) else (int(yr), 1)


def _at(chs: list[dict], f: float) -> tuple[int, int]:
    """进度 f（0–1）落在这几章的哪里：（第几章，这一章里第几个知识点）。每个知识点占的天数一样；
    f = 1 表示都学完了，返回（章数, 0）。"""
    if f >= 1 or not chs:
        return len(chs), 0
    total = sum(len(ch["kps"]) for ch in chs) or 1
    pos, cum = int(f * total), 0
    for i, ch in enumerate(chs):
        if pos < cum + len(ch["kps"]):
            return i, pos - cum
        cum += len(ch["kps"])
    return len(chs), 0


def estimate(user: dict, e, day: date | None = None, manual: set | None = None) -> dict | None:
    """一门课现在大概学到哪：{chapters, idx（正在学的章，= 章数表示都学完了 / 放假）, inferred（没纠正时的估计）,
    shift, phase, term_name, key}。没有章节或没有校历的课返回 None。"""
    pack = catalog.packs.get(e["pack_id"])
    if not pack or not pack.chapters:
        return None
    cal = calendar_for(pack)
    if not cal:
        return None
    chs = catalog.chapters(pack.id, e["stage"], e["track"])
    if not chs:
        return None
    day = day or today()
    w = where(cal, day)
    if _uses_terms(chs) and cal["id"] == "cn":
        first = [i for i, ch in enumerate(chs) if ch.get("rank") != 2]
        second = [i for i, ch in enumerate(chs) if ch.get("rank") == 2]
        tkey = w["term"][0] if w["term"] else None
        if tkey is None:                       # 学年第一个学期开学前：这一学段还没开始
            idx, within = 0, 0
        elif tkey == "1":                      # 第一学期铺上册；寒假里上册学完，下册还没开始
            j, within = _at([chs[i] for i in first], w["term_f"])
            idx = first[j] if j < len(first) else (second[0] if second else len(chs))
        else:
            j, within = _at([chs[i] for i in second], w["term_f"])
            idx = second[j] if j < len(second) else len(chs)
    else:
        start, span = _span(e["stage"])
        grade = stage_rank(user.get("grade") or "")
        yi = min(max(int(grade) - start, 0), span - 1) if grade < 99 and start else 0
        idx, within = _at(chs, (yi + w["year_f"]) / span)
    inferred = idx
    shift = int(e.get("progress_shift") or 0) if (e.get("progress_shift_term") or "") == w["key"] else 0
    if shift:
        idx, within = min(max(idx + shift, 0), len(chs)), 0
    # 实打实的证据（报过的、试卷考到的）：考到了后面的章，进度至少到那一章
    if manual:
        hit = max((i for i, ch in enumerate(chs) if manual & set(ch["kps"])), default=-1)
        if hit > idx:
            idx, within = hit, 0
        if idx < len(chs):   # 这一章里报到了后面的知识点：学校至少讲到那里了
            inch = [i for i, k in enumerate(chs[idx]["kps"]) if k in manual]
            within = max([within] + inch)
    if idx >= len(chs) or w["phase"] != "term":
        within = 0
    return {"chapters": chs, "idx": idx, "within": within, "inferred": inferred, "shift": shift, "phase": w["phase"],
            "term_name": w["name"], "key": w["key"], "calendar": cal.get("name", ""),
            "current": chs[idx] if idx < len(chs) and w["phase"] == "term" else None}


def _manual(user_id: int) -> set[str]:
    return {r["kp_id"] for r in db.q("SELECT kp_id FROM kp_taught WHERE user_id=? AND COALESCE(source,'manual')<>'inferred'", user_id)}


def sync(user_id: int, mastery: dict | None = None, day: date | None = None) -> dict:
    """按今天的日期重算每门课的进度：写回 enrollments.progress_kp（正在学的章里第一个还没掌握的知识点），
    重写「学校大概教过了」（kp_taught.source = inferred）。返回 {pack_id: estimate}。"""
    from . import engine
    user = db.one("SELECT * FROM users WHERE id=?", user_id)
    if not user:
        return {}
    mastery = engine.get_mastery(user_id) if mastery is None else mastery
    manual = _manual(user_id)
    have = {r["kp_id"] for r in db.q("SELECT kp_id FROM kp_taught WHERE user_id=? AND source='inferred'", user_id)}
    out, want, scope = {}, set(), set()
    for e in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", user_id):
        est = estimate(user, e, day, manual)
        if not est:
            continue
        out[e["pack_id"]] = est
        chs = est["chapters"]
        scope |= {k for ch in chs for k in ch["kps"]}
        want |= {k for ch in chs[:est["idx"]] for k in ch["kps"]} - manual
        cur = None
        if est["current"]:   # 大章里也按日期往前走：这一章前面的知识点算教过了，从推演到的位置起找第一个还没掌握的
            kps = est["current"]["kps"]
            want |= set(kps[:est["within"]]) - manual
            cur = next((k for k in kps[est["within"]:] if mastery.get(k, {}).get("status") != "mastered"), None)
            if cur and cur not in manual:   # 正在学的这个知识点也算教过了（学校正在讲）
                want.add(cur)
        if cur != e["progress_kp"]:
            db.run("UPDATE enrollments SET progress_kp=? WHERE user_id=? AND pack_id=?", cur, user_id, e["pack_id"])
    now = db.now()
    gone = (have & scope) - want
    for k in gone:
        db.run("DELETE FROM kp_taught WHERE user_id=? AND kp_id=? AND source='inferred'", user_id, k)
    for k in want - have:
        db.run("INSERT INTO kp_taught(user_id,kp_id,marked_at,source) VALUES(?,?,?,'inferred') "
               "ON CONFLICT(user_id,kp_id) DO NOTHING", user_id, k, now)
    return out


def correct(user_id: int, pack_id: str, *, delta: int | None = None, chapter: str | None = None, confirm=False) -> None:
    """家长纠正：学校比估计的快（delta=+1）/ 慢（-1）几章，或者直接点学到哪一章（chapter = 章的 key，空 = 放假 / 不确定）。
    存成这学期的偏移量；确认（confirm）只记一下核对过的日期。"""
    user = db.one("SELECT * FROM users WHERE id=?", user_id)
    e = db.one("SELECT * FROM enrollments WHERE user_id=? AND pack_id=?", user_id, pack_id)
    if not user or not e:
        return
    est = estimate(user, {**dict(e), "progress_shift": 0}, manual=None)
    now = db.now()
    if not est:
        return
    if not confirm:
        chs = est["chapters"]
        cur = est["idx"] + int(e["progress_shift"] or 0) * (e["progress_shift_term"] == est["key"])
        if chapter == "":     # 「不确定」：回到按校历估计的
            target = est["inferred"]
        elif chapter is not None:
            target = next((i for i, ch in enumerate(chs) if ch["key"] == chapter), len(chs))
        else:
            target = min(max(cur + int(delta or 0), 0), len(chs))
        # 以前报的、和新进度对不上的（在新的正在学的章及以后）不再算数，以家长这次说的为准
        later = [k for ch in chs[target:] for k in ch["kps"]]
        for i in range(0, len(later), 300):
            part = later[i:i + 300]
            db.run(f"DELETE FROM kp_taught WHERE user_id=? AND kp_id IN ({','.join('?' * len(part))})", user_id, *part)
        db.run("UPDATE enrollments SET progress_shift=?, progress_shift_term=? WHERE user_id=? AND pack_id=?",
               target - est["inferred"], est["key"], user_id, pack_id)
    db.run("UPDATE enrollments SET progress_at=? WHERE user_id=? AND pack_id=?", now, user_id, pack_id)
    sync(user_id)
