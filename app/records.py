"""跟自己比：个人最好（PB）、「上周的我」、最长专注、每周进步卡。

原则：只跟自己比，不做排行榜；正确率不到 80% 的一组不算纪录（免得为了快乱答）；
速度只比「每道答对的题平均用时」，而且只在单词复习、错题重做这类已经学过的内容和热身上比。
"""
from datetime import timedelta

from . import db, sprint

KINDS = {"warmup": "热身", "words": "单词复习", "mistakes": "错题重做", "review": "复习卡片",
         "practice": "知识点练习", "read": "阅读"}
SPEED_KINDS = {"warmup", "words", "mistakes", "review"}
MIN_ACC = 0.8


def _valid(r) -> bool:
    return r["n_items"] > 0 and r["n_right"] / r["n_items"] >= MIN_ACC


def _per_item(r) -> float | None:
    return r["ms_active"] / r["n_right"] if r["n_right"] and r["ms_active"] else None


def personal_best(user_id: int, kind: str, before_id: int | None = None) -> dict:
    """这个孩子某类任务的 PB：答对最多、最长连对、每题最快（只算正确率≥80% 的组）；以及所有任务里最长的一次专注。"""
    cond = " AND id<?" if before_id else ""
    args = (before_id,) if before_id else ()
    rows = db.q(f"SELECT * FROM runs WHERE user_id=? AND kind=?{cond}", user_id, kind, *args)
    valid = [r for r in rows if _valid(r)]
    speeds = [s for s in (_per_item(r) for r in valid if r["n_right"] >= 3) if s]
    focus = db.one(f"SELECT MAX(ms_active) AS m FROM runs WHERE user_id=?{cond}", user_id, *args)["m"] or 0
    return {"right": max((r["n_right"] for r in valid), default=0),
            "combo": max((r["best_combo"] for r in rows), default=0),
            "speed": int(min(speeds)) if speeds and kind in SPEED_KINDS else None,
            "focus": int(focus), "runs": len(rows)}


def ghost(user_id: int, kind: str) -> int | None:
    """「上周的我」：过去 7 天（不含今天）这类任务每道答对的题平均用时（毫秒）。"""
    today = db.today()
    rows = db.q("SELECT * FROM runs WHERE user_id=? AND kind=? AND day>=? AND day<?", user_id, kind,
                (today - timedelta(days=7)).isoformat(), today.isoformat())
    right = sum(r["n_right"] for r in rows)
    ms = sum(r["ms_active"] for r in rows if r["n_right"])
    return int(ms / right) if right >= 3 and ms else None


def record(user_id: int, kind: str, n_items: int, n_right: int, ms_active: int, ms_total: int, best_combo: int) -> dict:
    """记一组，返回破了哪些 PB。"""
    kind = kind if kind in KINDS else "practice"
    n_items = max(0, min(int(n_items or 0), 500))
    n_right = max(0, min(int(n_right or 0), n_items))
    ms_active = max(0, min(int(ms_active or 0), 4 * 3600_000))
    ms_total = max(ms_active, min(int(ms_total or 0), 8 * 3600_000))
    best_combo = max(0, min(int(best_combo or 0), n_items))
    old = personal_best(user_id, kind)
    rid = db.insert("INSERT INTO runs(user_id,kind,day,n_items,n_right,ms_active,ms_total,best_combo,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", user_id, kind, db.today().isoformat(), n_items, n_right, ms_active,
                    ms_total, best_combo, db.now())
    row = {"n_items": n_items, "n_right": n_right, "ms_active": ms_active}
    pbs = []
    if old["runs"]:  # 第一次做不算破纪录，先立一个
        if _valid(row) and n_right > old["right"]:
            pbs.append({"key": "right", "label": f"{KINDS[kind]}答对最多：{n_right} 题"})
        if best_combo >= 3 and best_combo > old["combo"]:
            pbs.append({"key": "combo", "label": f"最长连对：{best_combo} 题"})
        sp = _per_item(row)
        if kind in SPEED_KINDS and _valid(row) and n_right >= 3 and sp and old["speed"] and sp < old["speed"]:
            pbs.append({"key": "speed", "label": f"{KINDS[kind]}每题最快：{sp / 1000:.1f} 秒"})
    if ms_active >= 5 * 60_000 and ms_active > old["focus"] and db.one(  # 专注跨所有任务比：以前做过任何一组就算
            "SELECT id FROM runs WHERE user_id=? AND id<?", user_id, rid):
        pbs.append({"key": "focus", "label": f"最长专注：{ms_active // 60000} 分 {ms_active // 1000 % 60} 秒"})
    db.run("UPDATE runs SET pbs=? WHERE id=?", db.jdump(pbs), rid)
    return {"pbs": pbs, "pb": personal_best(user_id, kind), "first": not old["runs"]}


def _week(user_id: int, start, end) -> dict:
    rows = db.q("SELECT * FROM runs WHERE user_id=? AND day>=? AND day<?", user_id, start.isoformat(), end.isoformat())
    att = db.q("SELECT correct, ms FROM attempts WHERE user_id=? AND created_at>=? AND created_at<?",
               user_id, start.isoformat(), end.isoformat())
    timed = [a["ms"] for a in att if a["correct"] and a["ms"]]   # 用时只有知识点的题记着
    t = tally(answers(user_id, start.isoformat(), end.isoformat()))
    return {"runs": len(rows), "pbs": sum(len(db.jload(r["pbs"], [])) for r in rows),
            "focus": sum(r["ms_active"] for r in rows) // 60000,
            "attempts": t["n"], "acc": t["steady_acc"], "by": t["by"], "split": tally_words(t),
            "sec": round(sum(timed) / len(timed) / 1000, 1) if len(timed) >= 3 else None,
            "lit": db.one("SELECT COUNT(*) AS n FROM lights WHERE user_id=? AND day>=? AND day<?", user_id,
                          start.isoformat(), end.isoformat())["n"],
            "sprint": sprint.week(user_id, start.isoformat(), end.isoformat()),
            "days": db.one("SELECT COUNT(*) AS n FROM days WHERE user_id=? AND day>=? AND day<? AND (minutes>0 OR checked_in=1)",
                           user_id, start.isoformat(), end.isoformat())["n"]}


def weekly(user_id: int, last_mon=None) -> dict | None:
    """每周进步卡：上周（周一到周日）和再上一周比。上周没学就不出卡。last_mon：指定哪一周（每周报告邮件用）。"""
    today = db.today()
    last_mon = last_mon or today - timedelta(days=today.weekday() + 7)
    this_mon = last_mon + timedelta(days=7)
    w1, w0 = _week(user_id, last_mon, this_mon), _week(user_id, last_mon - timedelta(days=7), last_mon)
    if not (w1["days"] or w1["attempts"]):
        return None
    lines = []
    if w1["sec"] and w0["sec"] and w1["sec"] < w0["sec"]:
        lines.append(f"答对一题平均快了 {w0['sec'] - w1['sec']:.1f} 秒（{w0['sec']} → {w1['sec']} 秒）")
    if w1["acc"] is not None and w0["acc"] is not None and w1["acc"] > w0["acc"]:
        lines.append(f"正确率从 {w0['acc']}% 提高到 {w1['acc']}%（不算乐园和冲刺）")
    if w1["lit"]:
        lines.append(f"新点亮 {w1['lit']} 个知识点")
    if w1["pbs"]:
        lines.append(f"破了 {w1['pbs']} 次个人纪录")
    if w1["focus"]:
        lines.append(f"专注学习 {w1['focus']} 分钟" + (f"（比再上周多 {w1['focus'] - w0['focus']} 分钟）" if w1["focus"] > w0["focus"] else ""))
    if w1["sprint"]:
        lines.append(f"冲刺了 {w1['sprint']} 分" + (f"（比再上周多 {w1['sprint'] - w0['sprint']} 分）" if w1["sprint"] > w0["sprint"] else ""))
    lines.append(f"学习了 {w1['days']} 天，答题 {w1['attempts']} 道" + (f"：{w1['split']}" if w1["split"] else ""))
    return {"from": last_mon.isoformat(), "to": (this_mon - timedelta(days=1)).isoformat(), "lines": lines,
            "this": w1, "prev": w0, "monday": today.weekday() == 0}


def _mmss(ms: int) -> str:
    return f"{ms // 60000}:{ms // 1000 % 60:02d}"


def summary(user_id: int) -> dict:
    """「我的纪录」：每类任务的 PB 一行，加上最长专注。没做过的类不显示。"""
    rows = []
    for kind, label in KINDS.items():
        pb = personal_best(user_id, kind)
        if not pb["runs"]:
            continue
        bits = []
        if pb["right"] and kind != "read":
            bits.append(f"答对最多 {pb['right']} 题")
        if pb["speed"]:
            bits.append(f"每题最快 {pb['speed'] / 1000:.1f} 秒")
        if pb["combo"] >= 3:
            bits.append(f"最长连对 {pb['combo']}")
        if bits:
            rows.append({"kind": kind, "label": label, "text": " · ".join(bits)})
    focus = db.one("SELECT MAX(ms_active) AS m FROM runs WHERE user_id=?", user_id)["m"] or 0
    return {"rows": rows, "focus": _mmss(int(focus)) if focus >= 60_000 else ""}


# ------------------------------------------------------------------ 所有作答：在哪儿答的都算，按来源分开看

# 每一次作答都记在事件表里（kind='answer'）：知识点的题（target='kp'，每日任务、练习、热身、复习、试卷重做、游戏、冲刺）、
# 单词（target='word'，热身、单词复习、游戏里的单词题）、阅读理解（target='reading'）。报告都从这里数，不再只数 attempts 表
# （单词和阅读理解从来不进 attempts）。不算作答的：原卷上老师批的对错（exam，不是那天做的）、诊断里的自评。
ANSWER_TARGETS = ("kp", "word", "reading")
NOT_ANSWERS = ("exam", "diagnose-self")
# 来源：乐园和冲刺的题跟着水平自动调难度，正确率本来就稳在七成多，和学习、复习的正确率分开看才有意义
SOURCES = {"study": ("📚", "学习和练习"), "words": ("🔤", "单词"), "reading": ("📖", "阅读理解"),
           "game": ("🎮", "乐园游戏"), "sprint": ("⚡", "冲刺")}
ADAPTIVE = ("game", "sprint")
ANSWER_SQL = ("kind='answer' AND target IN (%s) AND mode NOT IN (%s) AND correct IS NOT NULL"
              % (",".join(f"'{t}'" for t in ANSWER_TARGETS), ",".join(f"'{m}'" for m in NOT_ANSWERS)))


def source_of(target: str, mode: str) -> str:
    if mode in ADAPTIVE:
        return mode
    return {"word": "words", "reading": "reading"}.get(target, "study")


def answers(user_id: int, start: str, end: str | None = None, limit: int | None = None) -> list:
    """这段时间的每一次作答（start / end 是日期或时间字符串；limit：只要最近的几条，新的在前）。"""
    sql = f"SELECT target, target_id, mode, correct, dont_know, created_at FROM events WHERE user_id=? AND {ANSWER_SQL} AND created_at>=?"
    args = [user_id, start]
    if end:
        sql += " AND created_at<?"
        args.append(end)
    sql += " ORDER BY id DESC" + (f" LIMIT {int(limit)}" if limit else "")
    return db.q(sql, *args)


def tally(rows) -> dict:
    """一组作答的总数、答对、正确率，和每个来源各多少（只列有的来源，顺序固定）。"""
    by = {}
    for r in rows:
        c = by.setdefault(source_of(r["target"], r["mode"]), [0, 0, 0])
        c[0] += 1
        c[1] += 1 if r["correct"] else 0
        c[2] += 1 if r["dont_know"] else 0
    n, ok = sum(c[0] for c in by.values()), sum(c[1] for c in by.values())
    steady = [by[k] for k in by if k not in ADAPTIVE]
    sn, sok = sum(c[0] for c in steady), sum(c[1] for c in steady)
    return {"n": n, "ok": ok, "acc": _acc(n, ok), "dk": sum(c[2] for c in by.values()),
            "steady_acc": _acc(sn, sok) if sn >= TREND_MIN else None,   # 不算乐园和冲刺的正确率：比进步用
            "by": [{"key": k, "icon": SOURCES[k][0], "name": SOURCES[k][1], "n": by[k][0], "ok": by[k][1],
                    "acc": _acc(by[k][0], by[k][1])} for k in SOURCES if k in by]}


def tally_words(t: dict) -> str:
    """「学习和练习 120 道（对 85%）、乐园游戏 260 道（对 72%）」——只有一个来源时不用分。"""
    if len(t["by"]) < 2:
        return ""
    return "、".join(f"{s['name']} {s['n']} 道（对 {s['acc']}%）" for s in t["by"])


def recent(user_id: int, limit: int = 60) -> list[dict]:
    """「最近做题」：最近的作答，知识点的题写知识点名，单词写词，阅读理解写第几题。"""
    from .catalog import catalog
    out = []
    for r in answers(user_id, "", limit=limit):
        src = source_of(r["target"], r["mode"])
        if r["target"] == "kp":
            kp = catalog.kp(r["target_id"])
            what = kp["name"] if kp else r["target_id"]
        elif r["target"] == "word":
            what = r["target_id"].split(":", 1)[-1]
        else:
            what = "阅读理解"
        out.append({"at": r["created_at"], "what": what, "src": src, "icon": SOURCES[src][0],
                    "kind": RECENT_KIND.get(r["mode"]) or SOURCES[src][1], "correct": r["correct"], "dont_know": r["dont_know"]})
    return out


RECENT_KIND = {"practice": "练习", "preview": "预习", "helped": "跟着做", "diagnose": "小测", "probe": "热身",
               "review": "复习", "redo": "错题重做", "paper": "试卷重做", "game": "乐园", "sprint": "冲刺"}


# ------------------------------------------------------------------ 每天的正确率：画成曲线，看自己是在变好还是变差

TREND_MIN = 5          # 一天少于 5 题：点画成空心，提醒「题少，参考价值小」


def _acc(n: int, ok: int) -> int | None:
    return round(100 * ok / n) if n else None


def _series(days: list[str], by_day: dict[str, list[int]]) -> dict:
    """一条序列：每天的点 + 近 7 天合起来的平均线（按题数加权，7 天不到 5 题不画）。
    坐标是图里的百分比（左→右、上→下），手机和电脑上都按宽度铺开；纵轴从「最低那天往下一点」起，不从 0 起，免得线挤在上半截。"""
    daily, rolling = [], []
    for i, d in enumerate(days):
        n, ok = by_day.get(d, (0, 0))
        daily.append((i, d, n, ok))
        win = [by_day.get(dd, (0, 0)) for dd in days[max(0, i - 6):i + 1]]
        wn, wok = sum(a for a, _ in win), sum(b for _, b in win)
        rolling.append(100 * wok / wn if wn >= TREND_MIN else None)
    seen = [_acc(n, ok) for _, _, n, ok in daily if n] + [r for r in rolling if r is not None]
    lo = max(0, min(50, (int(min(seen, default=50)) - 5) // 10 * 10))
    span = max(1, len(days) - 1)
    x = lambda i: round(100 * i / span, 2)
    y = lambda a: round(100 * (100 - a) / (100 - lo), 2)
    pts = [{"day": d, "n": n, "ok": ok, "acc": _acc(n, ok), "x": x(i), "y": y(_acc(n, ok)), "few": n < TREND_MIN}
           for i, d, n, ok in daily if n]
    avg, seg = [], []
    for i, r in enumerate(rolling):
        if r is not None:
            seg.append(f"{x(i)},{y(r)}")
        elif seg:
            avg.append(" ".join(seg))
            seg = []
    if seg:
        avg.append(" ".join(seg))
    grid = [{"v": v, "y": y(v)} for v in sorted({lo, 80, 100} | ({50} if lo < 50 else set()))]
    n = sum(p["n"] for p in pts)
    # 每一天（包括没做题的日子）的数，给鼠标悬停 / 手指点按时显示那一天的具体情况
    days_out = [{"d": d, "n": n_, "ok": ok, "acc": _acc(n_, ok) if n_ else None, "x": x(i),
                 "y": y(_acc(n_, ok)) if n_ else None,
                 "avg": None if rolling[i] is None else round(rolling[i]),
                 "ya": None if rolling[i] is None else y(rolling[i])} for i, d, n_, ok in daily]
    return {"pts": pts, "avg": avg, "grid": grid, "n": n, "acc": _acc(n, sum(p["ok"] for p in pts)), "days": days_out}


def accuracy_trend(user_id: int, days: int = 30) -> dict | None:
    """最近 days 天每天的正确率（全部、分学科、分来源），和「最近 7 天 vs 再前 7 天」的对比。一题都没答过返回 None。
    全部 = 在哪儿答的都算（每日任务、单词、阅读理解、乐园、冲刺）；来源分开的曲线另外能切换着看。"""
    from .catalog import catalog
    today = db.today()
    span = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    start = (today - timedelta(days=max(days, 14) - 1)).isoformat()
    rows = answers(user_id, start)
    if not rows:
        return None
    by: dict[str, dict[str, list[int]]] = {"all": {}}
    names = {}
    for r in rows:
        d = r["created_at"][:10]
        if r["target"] == "kp":
            kp = catalog.kp(r["target_id"])
            pack = catalog.packs.get(kp["pack"]) if kp else None
            subj = (pack.subject, pack.subject_name) if pack else None
        elif r["target"] == "word":
            subj = ("english", "英语")
        else:   # 阅读理解：target_id 是「语言:第几篇:第几题」
            subj = ("english", "英语") if r["target_id"].startswith("en:") else ("chinese", "语文")
        keys = ["all", "src:" + source_of(r["target"], r["mode"])] + ([subj[0]] if subj else [])
        if subj:
            names[subj[0]] = subj[1]
        for key in keys:
            c = by.setdefault(key, {}).setdefault(d, [0, 0])
            c[0] += 1
            c[1] += 1 if r["correct"] else 0

    def week(key, back):
        cells = [by[key].get((today - timedelta(days=back + i)).isoformat(), (0, 0)) for i in range(7)]
        n = sum(a for a, _ in cells)
        return {"n": n, "acc": _acc(n, sum(b for _, b in cells))}

    def make(key, name, group):
        s = _series(span, by[key])
        return {"key": key, "name": name, "group": group, **s, "now": week(key, 0), "prev": week(key, 7)}

    subjects = [make(k, names[k], "subject") for k in sorted(names, key=lambda s: names[s]) if _series(span, by[k])["n"] >= 10]
    sources = [make("src:" + k, SOURCES[k][0] + " " + SOURCES[k][1], "source") for k in SOURCES
               if "src:" + k in by and _series(span, by["src:" + k])["n"] >= 10]   # 题太少的学科、来源不单独画
    series = [make("all", "全部", "all")]
    if len(subjects) >= 2:   # 只有一门课：「全部」就是它，不用切换
        series += subjects
    if len(sources) >= 2:
        series += sources
    labels = [{"x": round(100 * i / max(1, days - 1), 2), "text": d[5:].replace("-", "/")}
              for i, d in enumerate(span) if (days - 1 - i) % 7 == 0]
    week_rows = [r for r in rows if r["created_at"][:10] > (today - timedelta(days=7)).isoformat()]
    return {"days": days, "series": series, "labels": labels, "min": TREND_MIN, "week": tally(week_rows)}


def trend_words(s: dict) -> str:
    """一句话：最近 7 天和再前 7 天比。只说事实，不批评。"""
    now, prev = s["now"], s["prev"]
    if not now["n"]:
        return f"最近 7 天还没做题；前一周正确率 {prev['acc']}%（{prev['n']} 题）" if prev["n"] else "最近 7 天还没做题"
    head = f"最近 7 天正确率 {now['acc']}%（{now['n']} 题）"
    if not prev["n"]:
        return head
    diff = now["acc"] - prev["acc"]
    if abs(diff) <= 2:
        return f"{head}，和前一周（{prev['acc']}%）差不多，很稳"
    if diff > 0:
        return f"{head}，比前一周的 {prev['acc']}% 高 👍"
    return f"{head}，比前一周的 {prev['acc']}% 低一点"
