"""每日短文今天写什么（docs/每日短文话题多样化-讨论稿.md，docs/DESIGN.md §5.55）。

孩子不挑话题：点一下「写一篇」，系统按「领域 × 切入点 × 体裁」替他定好今天写什么。
- 领域轮换：最近 3 天读过的领域不再用，一周内同一领域最多 2 次，读得越来越广。
- 切入点四路来源（按权重抽）：拓展（最久没读的领域）、学校（别的学科最近学的知识点）、
  兴趣（读完点过「喜欢」的领域）、新闻（这几天每日新闻背后的知识）。来源用不上就退回拓展。
- 体裁轮换兼顾考试：中考 / IGCSE 要考的文体占多数，连着两篇不是考试文体，下一篇一定是。
- 最近 14 篇的标题交给 AI「不要重复」；写完再查一次，太像就换个切入点重写一次。
记录写在这篇短文的 contents.gen_meta["pick"] 里，最近读过什么直接从 readings 查，不用新表。
"""
from __future__ import annotations

import random
from datetime import timedelta

from . import catalog, db

# 领域：(key, 名称, 举几个切入点给 AI 参考)
DOMAINS = {
    "primary": [
        ("animal", "动物", "蚂蚁怎么认路、企鹅爸爸孵蛋、狗为什么摇尾巴"),
        ("plant", "植物与四季", "种子怎么旅行、秋天叶子为什么变色、当季的节气"),
        ("body", "身体与健康", "我们为什么会打哈欠、牙齿的一生、睡觉时身体在做什么"),
        ("space", "太空与地球", "月亮为什么会变、宇航员怎么喝水、火山"),
        ("invent", "发明与机器", "铅笔是怎么做出来的、机器人、自行车的来历"),
        ("history", "历史故事", "造纸术、郑和下西洋、古人怎么过年"),
        ("world", "世界各地", "别的国家的孩子怎么上学、一座城市的一天"),
        ("art", "艺术与音乐", "一幅名画里的秘密、乐器的来历、剪纸"),
        ("sport", "运动", "足球的规则怎么来的、奥运会、一次比赛"),
        ("life", "家和学校", "第一次自己做饭、和朋友闹别扭、班里的新同学"),
        ("job", "各行各业", "消防员、医生、快递员的一天"),
        ("fairy", "童话与想象", "会说话的小物件、一次奇妙的旅行"),
    ],
    "school": [
        ("nature", "自然与生命", "候鸟怎么认路、一棵树的一年、深海里的生物"),
        ("science", "科学与技术", "机器人怎么走路、芯片、疫苗是怎么做出来的"),
        ("space", "太空与地球", "空间站的一天、火山和地震、潮汐"),
        ("history", "历史与人物", "郑和下西洋、造纸术的旅行、一位科学家的转折点"),
        ("world", "地理与世界", "丝绸之路上的城市、一个国家的节日、河流与文明"),
        ("culture", "中国传统文化", "当季的节气、一座古建筑、一首古诗背后的故事"),
        ("art", "艺术与音乐", "一幅名画背后、一件乐器的来历、电影是怎么拍的"),
        ("sport", "体育与身体", "马拉松、运动员的训练、睡眠和记忆"),
        ("society", "社会与职业", "城市的垃圾去了哪里、法官、程序员的一天"),
        ("econ", "钱与经济", "一件 T 恤从哪来、为什么东西会涨价、一家小店怎么开起来"),
        ("growth", "成长与情感", "第一次失败、和父母的分歧、坚持一件事"),
        ("story", "故事与想象", "小小说、科幻小故事、寓言"),
    ],
    "adult": [
        ("work", "职场与管理", "一次难开口的谈话、怎么带新人"),
        ("econ", "经济与商业", "一家公司的转折、价格背后的供需"),
        ("science", "科学与技术", "新药怎么做出来、AI 的边界"),
        ("mind", "心理与成长", "习惯怎么养成、拖延"),
        ("history", "历史与人物", "一个改变走向的决定"),
        ("society", "社会与城市", "城市怎么运转、人口变化"),
        ("art", "艺术与文化", "一件作品背后的时代"),
        ("health", "健康与生活", "睡眠、运动与大脑"),
        ("world", "世界与旅行", "一座城市的过去和现在"),
    ],
}

# 体裁：(名称, 是不是考试要的文体)
GENRES = {
    ("zh", "primary"): [("记叙文", True), ("童话", False), ("写景短文", True), ("日记", False), ("书信", True),
                        ("小科普", True), ("寓言", False), ("儿童诗", False)],
    ("zh", "school"): [("记叙文", True), ("说明文", True), ("议论文", True), ("散文", True), ("小说片段", True),
                       ("人物小传", True), ("科幻小故事", False), ("书信", False), ("演讲稿", False)],
    ("en", "primary"): [("story", True), ("diary", True), ("letter", True), ("fact file", True),
                        ("poem", False), ("fable", False)],
    ("en", "school"): [("article", True), ("email / letter", True), ("blog post", True), ("report", True),
                       ("review", True), ("story", True), ("interview", False), ("speech", False), ("poem", False)],
    ("zh", "adult"): [("随笔", False), ("评论", False), ("案例故事", False), ("人物小传", False)],
    ("en", "adult"): [("essay", False), ("feature article", False), ("case story", False), ("profile", False)],
}

SOURCES = (("explore", 40), ("school", 25), ("interest", 20), ("news", 15))  # 读得广为先
RECENT = 14          # 交给 AI「不要重复」的标题数
FRESH_DAYS = 3       # 这几天读过的领域不再用
WEEK_MAX = 2         # 一周内同一领域最多几次
SIMILAR = 0.5        # 标题字面相似度超过它就算重复


def stage(grade: str) -> str:
    if catalog.is_adult(grade):
        return "adult"
    return "primary" if catalog.stage_rank(grade) <= 6 else "school"


def recent(user_id: int, lang: str, n: int = 40) -> list[dict]:
    """这个孩子最近 AI 写给他的短文（新的在前）：标题、一句话说写了什么、哪天、当时定的话题、读完的感觉。"""
    out = []
    for r in db.q("SELECT r.title, r.created_at, r.feel, c.gen_meta, c.body FROM readings r LEFT JOIN contents c ON c.id=r.content_id "
                  "WHERE r.user_id=? AND r.lang=? AND r.source='ai' ORDER BY r.id DESC LIMIT ?", user_id, lang, n):
        out.append({"title": r["title"], "about": db.jload(r["body"], {}).get("topic") or "", "day": r["created_at"][:10],
                    "feel": r["feel"] or "", "pick": db.jload(r["gen_meta"], {}).get("pick") or {}})
    return out


_STOP = set("的了是在为什么会怎吗呢和与从哪里来一个这那它他她我你们也都就还")


def similar(a: str, b: str) -> float:
    """两段话字面上有多像：中文按相邻两个字（去掉「为什么」「从哪里」这类虚字），英文按词，看较短那段有多少落在另一段里。"""
    def grams(s: str) -> set:
        s = "".join(ch for ch in s.lower() if ch.isalnum() or ch == " ")
        if " " in s.strip():
            return {w for w in s.split() if len(w) > 3}
        s = s.replace(" ", "")
        return {s[i:i + 2] for i in range(len(s) - 1) if not (set(s[i:i + 2]) & _STOP)}
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / min(len(ga), len(gb)) if ga and gb else 0.0


def repeats(title: str, about: str, past: list[dict]) -> bool:
    """和最近读过的重样：标题像，或者「写的是什么」那句像。"""
    return any(similar(title, p["title"]) >= SIMILAR or (about and p["about"] and similar(about, p["about"]) >= SIMILAR)
               for p in past[:RECENT])


def pick(user_id: int, lang: str, grade: str, rng: random.Random | None = None) -> dict:
    """今天这篇写什么。返回 {source, domain, name, angle, genre, exam, key}，key 用来在库里找能复用的。"""
    st = stage(grade)
    past = recent(user_id, lang)
    rng = rng or random.Random(f"{user_id}:{lang}:{db.today()}:{len(past)}")
    today = db.today()
    fresh = {p["pick"].get("domain") for p in past if p["day"] >= (today - timedelta(days=FRESH_DAYS - 1)).isoformat()}
    week = [p["pick"].get("domain") for p in past if p["day"] >= (today - timedelta(days=6)).isoformat()]
    blocked = fresh | {d for d in set(week) if week.count(d) >= WEEK_MAX}
    domains = DOMAINS[st]
    used_angles = {p["pick"].get("angle") for p in past}

    def explore() -> dict:
        last = {}  # 领域 → 最近一次读的序号（越小越近）
        for i, p in enumerate(past):
            last.setdefault(p["pick"].get("domain"), i)
        open_ = [d for d in domains if d[0] not in blocked] or domains
        open_.sort(key=lambda d: -last.get(d[0], 999))
        key, name, ideas = rng.choice(open_[:3])
        return {"source": "explore", "domain": key, "name": name, "angle": "", "ideas": ideas}

    def school() -> dict | None:
        from .engine import cross_topics
        for c in cross_topics(user_id, lang, limit=6):
            dom = f"school:{c['subject']}"
            if dom not in blocked and c["topic"] not in used_angles:
                return {"source": "school", "domain": dom, "name": f"{c['subject']}课学过的", "angle": c["topic"], "ideas": ""}
        return None

    def interest() -> dict | None:
        liked = [p["pick"].get("domain") for p in past if p["feel"] == "like" and p["pick"].get("source") in ("explore", "interest")]
        names = {d[0]: d for d in domains}
        pool = [d for d in liked if d in names and d not in blocked]
        if not pool:
            return None
        key, name, ideas = names[rng.choice(pool)]  # 喜欢过几次就有几份机会
        return {"source": "interest", "domain": key, "name": name, "angle": "", "ideas": ideas}

    def from_news() -> dict | None:
        if st == "adult" or "news" in blocked:
            return None
        band = "primary" if st == "primary" else "secondary"
        since = (today - timedelta(days=5)).isoformat()
        for n in db.q("SELECT title, why FROM news_picks WHERE band=? AND day>=? AND day<=? ORDER BY day DESC, id DESC LIMIT 6",
                      band, since, today.isoformat()):
            angle = f"新闻「{n['title']}」背后的知识或道理（不复述新闻本身）"
            if angle not in used_angles:
                return {"source": "news", "domain": "news", "name": "新闻背后", "angle": angle, "ideas": n["why"] or ""}
        return None

    ways = {"explore": explore, "school": school, "interest": interest, "news": from_news}
    src = rng.choices([s for s, _ in SOURCES], weights=[w for _, w in SOURCES])[0]
    p = ways[src]() or explore()

    genres = GENRES[(lang if lang in ("en", "zh") else "en", st)]
    gpast = [x["pick"].get("genre") for x in past[:10]]
    if st != "adult" and len(gpast) >= 2 and not any(g == x[0] and x[1] for x in genres for g in gpast[:2]):
        genres = [g for g in genres if g[1]]  # 连着两篇不是考试文体：这篇一定是
    least = min(gpast.count(g[0]) for g in genres)
    genre, exam = rng.choice([g for g in genres if gpast.count(g[0]) == least])
    p.update(genre=genre, exam=exam, key=f"auto:{p['domain']}/{genre}" + (f"/{p['angle'][:80]}" if p["angle"] else ""))
    return p


def avoid_titles(user_id: int, lang: str) -> list[str]:
    return [p["title"] for p in recent(user_id, lang, RECENT)]
