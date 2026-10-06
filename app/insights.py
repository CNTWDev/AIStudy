"""自动发现问题：不只看「做错了」，而是从孩子的整个学习过程里主动找信号。

每天生成计划时跑一遍（refresh），结果存在 insights 表：
- 计划会用它：根源前置 → 补前置；反复「还不会」/ 常问 / 做对但很慢 → 针对练；很久没练 → 回顾小检查。
- 孩子在「今天」页看到对自己有用的几条（语气是鼓励），家长在家长页看到全部，包括习惯类的提醒。

每条发现：kind（类型）、key（去重用）、kp_id、title、detail、severity（1 提示 / 2 注意 / 3 重要）、
for_kid（孩子能不能看到）、action（backfill / weak / check / words / ''）。
"""
from collections import Counter, defaultdict
from datetime import timedelta

from . import db, engine
from .catalog import catalog

WINDOW_DAYS = 14


def _since(days: int) -> str:
    return (db.today() - timedelta(days=days)).isoformat()


def _name(kp_id: str) -> str:
    kp = catalog.kp(kp_id)
    return kp["name"] if kp else kp_id


def detect(user_id: int) -> list[dict]:
    m = engine.get_mastery(user_id)
    since = _since(WINDOW_DAYS)
    out: list[dict] = []
    att = db.q("SELECT kp_id, correct, dont_know, ms, mode, created_at FROM attempts WHERE user_id=? AND created_at>=?",
               user_id, since)

    # 1. 共同的薄弱前置：好几个没掌握的知识点都要用到同一个前置，它多半是「根」
    shaky = {k for k, v in m.items() if v["status"] == "weak" or (v["status"] == "learning" and v["score"] < 0.6)}
    shaky |= {a["kp_id"] for a in att if not a["correct"] and a["mode"] != "exam"}
    shaky = {k for k in shaky if catalog.kp(k)}
    roots: dict[str, list[str]] = defaultdict(list)
    for k in shaky:
        for p, _ in catalog.ancestors(k, depth=3):
            if m.get(p["id"], {}).get("status") != "mastered":
                roots[p["id"]].append(k)
    for p, kids in sorted(roots.items(), key=lambda kv: -len(kv[1])):
        if len(kids) >= 2:
            names = "、".join(_name(k) for k in kids[:3])
            out.append({"kind": "root", "key": p, "kp_id": p, "severity": 3, "action": "backfill", "for_kid": 1,
                        "title": f"根源可能在「{_name(p)}」",
                        "detail": f"{names}{'等' if len(kids) > 3 else ''} {len(kids)} 个知识点都要用到它，先把它补牢，后面会顺很多"})
        if sum(1 for x in out if x["kind"] == "root") >= 3:
            break

    # 2. 反复点「这道题还不会」
    dk = Counter(a["kp_id"] for a in att if a["dont_know"])
    for k, n in dk.most_common(3):
        if n >= 2 and catalog.kp(k):
            out.append({"kind": "dont_know", "key": k, "kp_id": k, "severity": 2, "action": "weak", "for_kid": 1,
                        "title": f"「{_name(k)}」还没想通", "detail": f"最近点了 {n} 次「还不会」，换个讲法再学一遍"})

    # 3. 同一个知识点问了好几次
    for r in db.q("SELECT kp_id, COUNT(*) AS n FROM ask_threads WHERE user_id=? AND created_at>=? AND kp_id IS NOT NULL "
                  "GROUP BY kp_id ORDER BY n DESC LIMIT 3", user_id, since):
        if r["n"] >= 2 and catalog.kp(r["kp_id"]):
            out.append({"kind": "asked", "key": r["kp_id"], "kp_id": r["kp_id"], "severity": 2, "action": "weak", "for_kid": 1,
                        "title": f"「{_name(r['kp_id'])}」问了 {r['n']} 次", "detail": "说明这里还有点糊涂，安排一次专门的练习"})

    # 4. 做对了但很慢：会，但还不熟练
    oks = [a for a in att if a["correct"] and a["ms"]]
    if len(oks) >= 5:
        med = sorted(a["ms"] for a in oks)[len(oks) // 2]
        by_kp = defaultdict(list)
        for a in oks:
            by_kp[a["kp_id"]].append(a["ms"])
        slow = [(k, sum(v) / len(v)) for k, v in by_kp.items() if len(v) >= 2 and sum(v) / len(v) > max(60000, 2 * med)]
        for k, avg in sorted(slow, key=lambda x: -x[1])[:2]:
            if catalog.kp(k):
                out.append({"kind": "slow", "key": k, "kp_id": k, "severity": 1, "action": "weak", "for_kid": 1,
                            "title": f"「{_name(k)}」会做，但还不熟", "detail": f"平均 {int(avg / 1000)} 秒一题，多练两次就快了"})

    # 5. 总记不住的词 / 卡片
    hard = db.q("SELECT front, kind, kp_id, lapses FROM cards WHERE user_id=? AND lapses>=2 ORDER BY lapses DESC LIMIT 8", user_id)
    words = [c["front"] for c in hard if c["kind"] in ("word", "phrase", "term")]
    if words:
        out.append({"kind": "forget_words", "key": "words", "kp_id": None, "severity": 2, "action": "words", "for_kid": 1,
                    "title": f"{len(words)} 个词总是忘", "detail": "、".join(words[:6]) + "：试试用它们各造一个句子"})
    for c in hard:
        if c["kind"] in ("mistake", "kp") and c["kp_id"] and catalog.kp(c["kp_id"]):
            out.append({"kind": "forget_kp", "key": c["kp_id"], "kp_id": c["kp_id"], "severity": 2, "action": "weak", "for_kid": 1,
                        "title": f"「{_name(c['kp_id'])}」复习时总忘", "detail": "错题回顾忘了好几次，重新学一遍再复习"})
            break

    # 6. 同一个词查了好几次还没收藏：自动加进单词本
    saved = {r["front"].lower() for r in db.q("SELECT front FROM cards WHERE user_id=? AND kind IN ('word','phrase')", user_id)}
    looked = Counter(r["query"].strip().lower() for r in
                     db.q("SELECT query FROM lookups WHERE user_id=? AND created_at>=?", user_id, since))
    added = []
    for q, n in looked.most_common(20):
        if n >= 2 and q and q not in saved and len(q) <= 40:
            row = db.one("SELECT query, result FROM lookups WHERE user_id=? AND LOWER(query)=? ORDER BY id DESC", user_id, q)
            res = db.jload(row["result"], {}) if row else {}
            meaning = res.get("meaning") or ""
            if meaning:
                engine.add_card(user_id, "phrase" if " " in q else "word", row["query"].strip(), meaning,
                                {"phonetic": res.get("phonetic", ""), "example": res.get("example", ""), "source": "自动"},
                                due=db.today())
                added.append(row["query"].strip())
    if added:
        out.append({"kind": "lookup_repeat", "key": ",".join(sorted(added))[:200], "kp_id": None, "severity": 1, "action": "words",
                    "for_kid": 1, "title": f"查了好几次的词已经放进单词本", "detail": "、".join(added[:6])})

    # 7. 学过、掌握过，但很久没练：可能在悄悄遗忘
    old = _since(30)
    learned = set()
    for e in db.q("SELECT * FROM enrollments WHERE user_id=? AND active=1", user_id):
        if e["pack_id"] in catalog.packs:
            learned |= set(engine.progress_view(user_id, e)["learned"])
    stale = [v for k, v in m.items() if v["status"] == "mastered" and (v["updated_at"] or "") < old
             and (k in learned or not learned) and catalog.kp(k)]
    stale.sort(key=lambda v: (-(catalog.kp(v["kp_id"]) or {}).get("hot", False), v["updated_at"] or ""))
    for v in stale[:2]:
        out.append({"kind": "decay", "key": v["kp_id"], "kp_id": v["kp_id"], "severity": 1, "action": "check", "for_kid": 1,
                    "title": f"「{_name(v['kp_id'])}」很久没练了", "detail": "一个多月前掌握的，做一道小题看看还记得吗"})

    # 8. 考试会做却丢分（原卷错、重做对）
    careless = db.q("SELECT i.kp_id, COUNT(*) AS n FROM paper_items i JOIN papers p ON p.id=i.paper_id "
                    "WHERE p.user_id=? AND i.orig IN ('wrong','partial') AND i.correct=1 AND i.kp_id IS NOT NULL "
                    "GROUP BY i.kp_id ORDER BY n DESC LIMIT 3", user_id)
    if careless:
        names = "、".join(_name(r["kp_id"]) for r in careless)
        out.append({"kind": "careless", "key": ",".join(r["kp_id"] for r in careless), "kp_id": None, "severity": 2,
                    "action": "", "for_kid": 1, "title": "考试时会做的题丢了分",
                    "detail": f"{names}：重做都对了，考试时留 5 分钟专门检查这几类题"})

    # 9. 习惯（只给家长看）
    week = db.q("SELECT day, minutes, checked_in FROM days WHERE user_id=? AND day>=? AND day<?", user_id, _since(7),
                db.today().isoformat())
    active = {d["day"] for d in week if d["minutes"] > 0 or d["checked_in"]}
    created = (db.one("SELECT created_at FROM users WHERE id=?", user_id) or {}).get("created_at") or ""
    if created[:10] <= _since(7) and len(active) <= 4:
        out.append({"kind": "habit", "key": "week", "kp_id": None, "severity": 2, "action": "", "for_kid": 0,
                    "title": f"最近 7 天只学了 {len(active)} 天", "detail": "坚持比做对更重要：可以把每天的任务时间调短一点，先保证天天打卡"})
    for t in engine.tracks(user_id):
        if t["kind"] == "words":
            continue
        last = db.one("SELECT MAX(day) AS d FROM reading_logs WHERE track_id=?", t["id"])["d"] or (t["created_at"] or "")[:10]
        if last and last <= _since(4):
            out.append({"kind": "reading_gap", "key": str(t["id"]), "kp_id": None, "severity": 1, "action": "", "for_kid": 0,
                        "title": f"《{t['title']}》已经几天没读了", "detail": f"上次是 {last}，可以把每天的量减一点，保持节奏"})
    return out


def refresh(user_id: int) -> list[dict]:
    """重新检测并写入 insights：新的插入、还在的更新、消失的标记为已解决。"""
    found = detect(user_id)
    now = db.now()
    open_rows = {(r["kind"], r["key"]): r for r in db.q("SELECT * FROM insights WHERE user_id=? AND resolved_at IS NULL", user_id)}
    seen = set()
    with db.tx() as t:
        for f in found:
            k = (f["kind"], f["key"])
            seen.add(k)
            if k in open_rows:
                t.run("UPDATE insights SET title=?, detail=?, severity=?, updated_at=? WHERE id=?",
                      f["title"], f["detail"], f["severity"], now, open_rows[k]["id"])
            else:
                t.run("INSERT INTO insights(user_id,kind,key,kp_id,title,detail,severity,for_kid,action,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?)", user_id, f["kind"], f["key"], f["kp_id"], f["title"], f["detail"],
                      f["severity"], f["for_kid"], f["action"], now, now)
        for k, r in open_rows.items():
            if k not in seen:
                t.run("UPDATE insights SET resolved_at=? WHERE id=?", now, r["id"])
    return found


def open_insights(user_id: int, for_kid: bool = False, limit: int = 20) -> list:
    sql = "SELECT * FROM insights WHERE user_id=? AND resolved_at IS NULL" + (" AND for_kid=1" if for_kid else "")
    return db.q(sql + " ORDER BY severity DESC, id DESC LIMIT ?", user_id, limit)


def resolved_recent(user_id: int, days: int = 14) -> list:
    return db.q("SELECT * FROM insights WHERE user_id=? AND resolved_at>=? ORDER BY resolved_at DESC LIMIT 10",
                user_id, _since(days))


def plan_tasks(found: list[dict]) -> dict[str, list[dict]]:
    """把发现变成计划里的任务（由 build_plan 放到对应位置）。"""
    out = {"backfill": [], "weak": [], "check": []}
    seen = set()
    for f in sorted(found, key=lambda f: -f["severity"]):
        kp = catalog.kp(f["kp_id"]) if f.get("kp_id") else None
        if not kp or f["action"] not in out or kp["id"] in seen:
            continue
        seen.add(kp["id"])
        title = {"backfill": "补根源：", "weak": "再练练：", "check": "回顾小检查："}[f["action"]] + kp["name"]
        out[f["action"]].append({"type": f["action"], "kp": kp["id"], "title": title, "why": "🔍 " + f["title"],
                                 "minutes": {"backfill": 10, "weak": 12, "check": 6}[f["action"]], "pack": kp["pack"],
                                 "auto": True})
    return out

