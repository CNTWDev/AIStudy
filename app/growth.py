"""积少成多：从第一天到现在一共攒了多少（docs/DESIGN.md 5.51，讨论稿 docs/积少成多-讨论稿.md）。

孩子平时只看得到「今天」和「这一周」，看不到每天一点点加起来有多少。这里只算「一共」，而且只增不减：
- 答过的题：`records` 的作答口径（PR #118，在哪儿答的都算）；
- 学会过的知识点：`gains` 表里第一次学会的记录（以后又不稳了也不减，那个点回到「再练练」）；
- 认识的单词：答对过一次就算（和 `words.stats` 同一个口径）；
- 学习的天数。
说法只说事实、夸过程（Mueller & Dweck 1998），不出百分比、不出「答对多少道」；里程碑只庆祝、不换东西（Deci 等 1999）。
所有的话按规则拼出来，不调 AI。
"""
from datetime import date, timedelta

from . import db

KINDS = ("answers", "kps", "words", "days")
# 里程碑：越往后越稀，避免天天放烟花；列表走完以后按最后一档的步长往上加
MILESTONES = {"answers": [100, 200, 500, 1000, 2000, 3000, 5000, 7500, 10000],
              "kps": [10, 25, 50, 75, 100, 150, 200, 300],
              "words": [50, 100, 200, 300, 500, 800, 1000, 1500, 2000, 3000]}
STEP_AFTER = {"answers": 5000, "kps": 100, "words": 1000}


def learned(user_id: int, kp_id: str, at: str | None = None) -> None:
    """知识点第一次变成「学会了」（`evidence._save` 调）：记一行，以后不删。"""
    db.run("INSERT INTO gains(user_id,kind,ref,at) VALUES(?,?,?,?) ON CONFLICT(user_id,kind,ref) DO NOTHING",
           user_id, "kp", kp_id, at or db.now())


def _answers(user_id: int, start: str = "", end: str | None = None, steady: bool = False) -> int:
    from .records import ADAPTIVE, ANSWER_SQL
    sql, args = f"SELECT COUNT(*) AS n FROM events WHERE user_id=? AND {ANSWER_SQL} AND created_at>=?", [user_id, start]
    if end:
        sql += " AND created_at<=?"
        args.append(end)
    if steady:   # 不算乐园和冲刺：「这一项」只数任务里答的
        sql += " AND mode NOT IN (%s)" % ",".join(f"'{m}'" for m in ADAPTIVE)
    return db.one(sql, *args)["n"] or 0


def _words(user_id: int) -> int:
    ws = {r["target_id"].rsplit(":", 1)[-1].lower() for r in db.q(
        "SELECT DISTINCT target_id FROM events WHERE user_id=? AND target='word' AND kind='answer' AND correct=1", user_id)}
    ws |= {r["f"] for r in db.q("SELECT LOWER(front) AS f FROM cards WHERE user_id=? AND kind IN ('word','term') AND box>0", user_id)}
    return len(ws)


def totals(user_id: int) -> dict:
    return {"answers": _answers(user_id),
            "kps": db.one("SELECT COUNT(*) AS n FROM gains WHERE user_id=? AND kind='kp'", user_id)["n"] or 0,
            "words": _words(user_id),
            "days": db.one("SELECT COUNT(*) AS n FROM days WHERE user_id=? AND (minutes>0 OR checked_in=1)", user_id)["n"] or 0}


def kps_between(user_id: int, start: str, end: str | None = None) -> list[dict]:
    """这段时间第一次学会的知识点：名字和「第几个」。"""
    from .catalog import catalog
    rows = db.q("SELECT ref, at FROM gains WHERE user_id=? AND kind='kp' AND at>=?" + (" AND at<=?" if end else "") + " ORDER BY at",
                *([user_id, start] + ([end] if end else [])))
    before = db.one("SELECT COUNT(*) AS n FROM gains WHERE user_id=? AND kind='kp' AND at<?", user_id, start)["n"] or 0
    out = []
    for i, r in enumerate(rows):
        kp = catalog.kp(r["ref"])
        if kp:
            out.append({"id": r["ref"], "name": kp["name"], "n": before + i + 1})
    return out


def steps(kind: str):
    yield from MILESTONES[kind]
    n = MILESTONES[kind][-1]
    while True:
        n += STEP_AFTER[kind]
        yield n


def next_mark(kind: str, n: int) -> int:
    return next(m for m in steps(kind) if m > n)


def _crossed(kind: str, n: int) -> list[int]:
    out = []
    for m in steps(kind):
        if m > n:
            return out
        out.append(m)


def _first_day(user_id: int) -> dict | None:
    from .records import ANSWER_SQL
    r = db.one(f"SELECT MIN(created_at) AS t FROM events WHERE user_id=? AND {ANSWER_SQL}", user_id)
    if not r or not r["t"]:
        return None
    d = str(r["t"])[:10]
    nxt = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    return {"day": d, "answers": _answers(user_id, d, nxt)}


def milestones(user_id: int, tot: dict, teen: bool = False, latest: str = "") -> list[dict]:
    """刚跨过、还没庆祝过的里程碑（每类只报最大的那个），报了就记下。只在孩子自己做了事以后调。
    第一次调（上线那天）：已经跨过的都悄悄记成庆祝过，不一口气放一堆。"""
    seen = {r["ref"] for r in db.q("SELECT ref FROM gains WHERE user_id=? AND kind='milestone'", user_id)}
    fresh = {k: [m for m in _crossed(k, tot[k]) if f"{k}:{m}" not in seen] for k in MILESTONES}
    for k, ms in fresh.items():
        for m in ms:
            db.run("INSERT INTO gains(user_id,kind,ref,at) VALUES(?,?,?,?) ON CONFLICT(user_id,kind,ref) DO NOTHING",
                   user_id, "milestone", f"{k}:{m}", db.now())
    if "start" not in seen:
        db.run("INSERT INTO gains(user_id,kind,ref,at) VALUES(?,?,?,?) ON CONFLICT(user_id,kind,ref) DO NOTHING",
               user_id, "milestone", "start", db.now())
        return []
    out = []
    for k, ms in fresh.items():
        if ms:
            out.append({"kind": k, "n": ms[-1], "text": _milestone_text(user_id, k, ms[-1], teen, latest)})
    return out


def _milestone_text(user_id: int, kind: str, n: int, teen: bool, latest: str = "") -> str:
    """把第一天拿出来对比：一天天攒起来的（积少成多）。"""
    if kind == "answers":
        fd = _first_day(user_id)
        back = f"第 1 天是 {fd['answers']} 道，" if fd and fd["answers"] else ""
        return f"第 {n:,} 道题！{back}一天天攒到这么多。" if teen else f"你答到第 {n:,} 道题了！{back}是你一天天攒起来的。"
    if kind == "kps":
        if not latest:
            from .catalog import catalog
            last = db.one("SELECT ref FROM gains WHERE user_id=? AND kind='kp' ORDER BY at DESC LIMIT 1", user_id)
            latest = (catalog.kp(last["ref"]) or {}).get("name", "") if last else ""
        tail = f"最新的一个是「{latest}」。" if latest else ""
        return f"学会的知识点到 {n} 个了。{tail}" if teen else f"你学会 {n} 个知识点了！{tail}"
    return f"认识的单词到 {n:,} 个了。" if teen else f"你认识 {n:,} 个单词了！每个都是一次次见过、答过记住的。"


def teen_of(learner) -> bool:
    """初中以上说事实就好，不用哄（13、14 岁的孩子讨厌哄小孩的话）。"""
    from .catalog import stage_rank
    return stage_rank(learner["grade"] or "") >= 7


def after_task(user_id: int, current: str | None, teen: bool = False) -> dict:
    """做完一项：这一项 → 今天 → 一共，这一项里第一次学会的知识点，刚跨过的里程碑。"""
    from . import engine
    plan = engine.plan_of_today(user_id)
    day = db.today().isoformat()
    cur = next((t for t in plan if t["id"] == current), None)
    end = (cur or {}).get("done_at") or db.now()
    start = max([t["done_at"] for t in plan if t.get("done_at") and t["done_at"] < end] + [day])
    tot = totals(user_id)
    got = kps_between(user_id, start, end) if cur else []
    return {"item": _answers(user_id, start, end, steady=True) if cur else 0, "today": _answers(user_id, day),
            "total": tot["answers"], "learned": got, "milestones": milestones(user_id, tot, teen, got[-1]["name"] if got else "")}


def hello(user_id: int, tot: dict, teen: bool = False, settled: dict | None = None, weekly: dict | None = None) -> str:
    """每天第一次打开（今天还一项没做）时小伙伴在主卡里说的话：上次学了什么、一共攒了多少。
    昨天没来不说「你没学」，只说一共和今天从哪开始；没有可说的返回空，用平常那句。"""
    today = db.today()
    near = _near(tot)
    if weekly and weekly.get("monday"):
        w1, w0 = weekly["this"], weekly["prev"]
        more = f"，比再上周多 {w1['days'] - w0['days']} 天" if w1["days"] > w0["days"] else f"，答了 {w1['attempts']:,} 道题" if w1["attempts"] else ""
        return f"上周学了 {w1['days']} 天{more}。新的一周开始了。" if teen else f"上周你学了 {w1['days']} 天{more}！新的一周，接着来！"
    last = db.one("SELECT day, minutes FROM days WHERE user_id=? AND day<? AND (minutes>0 OR checked_in=1) ORDER BY day DESC LIMIT 1",
                  user_id, today.isoformat())
    if not last:
        return ""
    yday = last["day"] == (today - timedelta(days=1)).isoformat()
    if not yday:
        if settled and settled.get("used"):
            lead = "补签卡帮你保住了连续天数。"
        else:
            lead = "欢迎回来。" if teen else "欢迎回来！"
        if tot["kps"]:
            return lead + (f"一共学会了 {tot['kps']} 个知识点，今天接着来。" if teen else f"你一共学会了 {tot['kps']} 个知识点，今天接着来！")
        return lead + (f"一共答了 {tot['answers']:,} 道题，今天接着来。" if teen else f"你一共答了 {tot['answers']:,} 道题，今天接着来！") if tot["answers"] else ""
    d0 = last["day"]
    n = _answers(user_id, d0, today.isoformat())
    got = kps_between(user_id, d0, today.isoformat())
    if not n:
        if not last["minutes"]:
            return ""
        return (f"昨天学了 {last['minutes']} 分钟。" if teen else f"昨天你学了 {last['minutes']} 分钟！") + _total_line(tot, teen)
    what = f"，学会了「{got[-1]['name']}」" + (f"等 {len(got)} 个知识点" if len(got) > 1 else "") if got else ""
    head = f"昨天答了 {n} 道题{what}。" if teen else f"昨天你答了 {n} 道题{what}！"
    reach = {"answers": max(n, 10), "kps": max(len(got), 2), "words": 20}   # 照昨天的量，今天够得着
    if near and near["to_go"] <= reach[near["kind"]]:
        return head + (f"再{near['verb']} {near['to_go']} {near['unit']}就到 {near['mark']:,} {near['unit']}，今天就能到。" if teen
                       else f"再{near['verb']} {near['to_go']} {near['unit']}就到 {near['mark']:,} {near['unit']}了，今天就能到！")
    return head + _total_line(tot, teen)


def _total_line(tot: dict, teen: bool) -> str:
    if tot["answers"]:
        return f"到今天一共 {tot['answers']:,} 道。" if teen else f"到今天一共 {tot['answers']:,} 道啦！"
    return f"一共学会了 {tot['kps']} 个知识点。" if tot["kps"] else ""


UNITS = {"answers": ("答", "道"), "kps": ("学会", "个"), "words": ("认识", "个")}


def _near(tot: dict) -> dict | None:
    """离下一个里程碑最近的一类（按剩下的比例），用来说「再答 37 道就到 1,000 道」（目标越近越有劲，Kivetz 等 2006）。"""
    best = None
    for k in MILESTONES:
        if not tot[k]:
            continue
        m = next_mark(k, tot[k])
        frac = (m - tot[k]) / m
        if frac <= 0.1 and (best is None or frac < best[0]):
            best = (frac, {"kind": k, "mark": m, "to_go": m - tot[k], "verb": UNITS[k][0], "unit": UNITS[k][1]})
    return best[1] if best else None


def _weeks(user_id: int, n_weeks: int = 12) -> list[dict]:
    """每周结束时一共答了多少（越叠越高）：最近 n_weeks 周，从有记录的那周开始。"""
    from .records import ANSWER_SQL
    rows = db.q(f"SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM events WHERE user_id=? AND {ANSWER_SQL} "
                "GROUP BY substr(created_at,1,10) ORDER BY d", user_id)
    if not rows:
        return []
    today = db.today()
    this_mon = today - timedelta(days=today.weekday())
    first = date.fromisoformat(rows[0]["d"])
    start = max(first - timedelta(days=first.weekday()), this_mon - timedelta(weeks=n_weeks - 1))
    out, i, run = [], 0, 0
    while i < len(rows) and rows[i]["d"] < start.isoformat():
        run += rows[i]["n"]
        i += 1
    mon = start
    while mon <= this_mon:
        end = (mon + timedelta(days=7)).isoformat()
        add = 0
        while i < len(rows) and rows[i]["d"] < end:
            add += rows[i]["n"]
            i += 1
        run += add
        out.append({"mon": mon.isoformat(), "add": add, "n": run, "now": mon == this_mon})
        mon += timedelta(days=7)
    return out


JAR_MAX = 40


def board(user_id: int, look: dict | None = None) -> dict:
    """记录页「我的积累」：四个「一共」、一格一格装满的罐子、每周越叠越高、最近学会的、下一个里程碑。
    罐子跟着主题换（theme.toml 的 [grow]：icon、name），颜色用主题的 --brand。"""
    tot = totals(user_id)
    unit = 100 if tot["answers"] < 3000 else 1000
    full, part = divmod(tot["answers"], unit)
    g = (look or {}).get("grow") or {}
    nxt = [{"kind": k, "verb": UNITS[k][0], "unit": UNITS[k][1], "n": tot[k], "mark": next_mark(k, tot[k]),
            "prev": max([0] + _crossed(k, tot[k]))} for k in MILESTONES if tot[k]]
    for x in nxt:
        x["pct"] = round(100 * (x["n"] - x["prev"]) / (x["mark"] - x["prev"]))
    since = (db.today() - timedelta(days=13)).isoformat()
    return {"tot": tot, "jars": {"full": min(full, JAR_MAX), "more": max(0, full - JAR_MAX), "part": round(100 * part / unit), "part_n": part,
                                 "unit": unit, "icon": g.get("icon", "🍯"), "name": g.get("name", "罐")},
            "weeks": _weeks(user_id), "recent": kps_between(user_id, since)[::-1][:12], "next": nxt}


def line(user_id: int) -> str:
    """给家长的一句「一共」（家长页、每周报告）：家长照着夸，比系统说十句管用。"""
    t = totals(user_id)
    bits = [f"学会 {t['kps']} 个知识点" if t["kps"] else "", f"答过 {t['answers']:,} 道题" if t["answers"] else "",
            f"认识 {t['words']:,} 个单词" if t["words"] else "", f"学了 {t['days']} 天" if t["days"] else ""]
    return "从第一天到现在：" + "、".join(b for b in bits if b) if any(bits) else ""
