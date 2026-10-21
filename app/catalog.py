"""课程目录：教材体系的底层数据，分四层，互相独立、各自可扩展。

1. 学段（curricula/_meta/stages.json）：所有学制的学段放在同一根「学年」轴上（year：一年级 = 1，
   IGCSE ≈ 9.5，IB DP1 = 11 …），不同学制之间才能比先后。加一个学制 / 年级只改这个文件。
2. 教材包（curricula/*.json）：一个学科的一个版本（统编语文、上海英语、IGCSE 物理……），自带板块（strands）
   和知识点（kps）。包与包之间互不依赖，加一套教材就是放一个 JSON 文件。
   - 国内公办分两层：课标主干（kind = standard，知识点的家）+ 目录包（pack.base 指向主干，books 写
     「哪一册、哪一单元、按什么顺序教哪些点」）。同一个点在不同教材里学段不同，用 kp_in / stage_in 取。
   - tracks：同一套教材里的「方向」，比如剑桥英语的 0511 / 0510 / 0500、IGCSE 的 Core / Extended。
     知识点带 tracks 字段表示只属于这些方向；不带就是所有方向都学。
3. 关联（curricula/_links/*.json）：跨教材、跨学科的知识融合，和教材包分开存放，增删不动教材本身。
   - concepts：同一个概念在不同教材里的知识点（如勾股定理在沪教数学、剑桥数学、物理前置里各有一个），
     学会其中一个，其它的可以推断「大概率会」，换教材 / 转学不用从零开始。
   - links：有方向的关联。uses = 用到另一门课的知识（物理用到数学）；language = 同一内容的另一种语言
     （物理术语 ↔ 英语词汇）；context = 背景知识（英语阅读话题 ↔ 语文、历史、道法里学过的内容）。
4. 学校模板（curricula/_meta/presets.json）：「上海公办初中」「国际学校剑桥路线」这类常见组合，
   家长选了学校类型就把各科教材和方向预填好，仍然可以逐科改。

另有学科表（curricula/_meta/subjects.json）：学科的名字、图标、领域、语言类学科教哪种语言。

教材包不一定是「国家审定教材」：kind = textbook（审定教材）/ syllabus（考试局大纲，如剑桥）/
course（没有统一教材的课程：少儿编程、大学课程、某个技术栈）。course 没有统一学制时，
包里自己定义 levels（入门 / 进阶……，各对应大约几年级），会挂到学段轴上。
授课语言（teach_lang）和出题语言（item_lang）由包声明，没写就按学制和学科推断；代码里不再猜。

孩子自己的选择（年级、学校类型、每科选哪套教材、哪个方向、学到哪）存在数据库里，不进这些文件。
"""
import json
import hashlib
import re
from dataclasses import dataclass, field

from . import config
from .curriculum import Textbooks

# 学段：默认值（没有 _meta/stages.json 时用），加载时会被文件覆盖 / 扩充
STAGE_RANK = {f"G{i}": float(i) for i in range(1, 13)}
STAGE_RANK.update({"IGCSE": 9.5, "A-Level": 11.5})
STAGE_LABEL = {
    "G1": "一年级", "G2": "二年级", "G3": "三年级", "G4": "四年级", "G5": "五年级",
    "G6": "六年级", "G7": "初一", "G8": "初二", "G9": "初三",
    "G10": "高一", "G11": "高二", "G12": "高三", "IGCSE": "IGCSE", "A-Level": "A-Level",
}
GRADES = [f"G{i}" for i in range(1, 13)]
SYSTEMS: dict[str, dict] = {}   # 学制 id -> {name, international, lang}
SUBJECTS: dict[str, dict] = {"english": {"id": "english", "name": "英语", "lang": "en"},
                             "chinese": {"id": "chinese", "name": "语文", "lang": "zh"}}  # 加载 subjects.json 后覆盖
PACK_KINDS = {"textbook": "审定教材", "syllabus": "考试大纲", "course": "自定课程", "standard": "课程标准主干"}
TERM_RANK = {"上": 0, "下": 2}   # 章的先后：上册、不分册、下册
# 目录包从主干继承的字段（目录包只管「哪一册、哪一单元、按什么顺序教哪些点」）
EDITION_INHERITS = ("subject", "subject_name", "system", "teach_lang", "item_lang", "prompt_note", "progress",
                    "tracks", "default_track", "audience")
LINK_TYPES = {"uses": "要用到", "language": "另一种语言的说法", "context": "相关背景"}
# 证据牌：成人课里一条规律的证据有多硬（知识点的 evidence 字段，可选；evidence_note 一句话说重复情况和边界）。
# 前三档说的是研究结论，必须写出处（sources）；后三档不是研究结论，各自说明来路。所有课共用这一套。
EVIDENCE = {
    "稳": ("🟢", "多次独立重复、有元分析或大样本支持"),
    "要小心": ("🟡", "方向基本成立，但效应不大、条件多，或学界有争论"),
    "站不住": ("🔴", "大规模重复没有成功，或原研究有严重问题；讲它是为了知道它为什么会火、为什么会倒"),
    "基本原理": ("📐", "定义或推理得出的道理（会计、概率、研究方法），不需要实验来证明"),
    "经典案例": ("📖", "来自有名的真实案例，不是对照研究"),
    "经验之谈": ("💬", "有经验的人总结的做法，还没有系统研究"),
}
RESEARCH_EVIDENCE = ("稳", "要小心", "站不住")


def stage_rank(stage: str) -> float:
    return STAGE_RANK.get(stage, 99.0)


def stage_label(stage: str) -> str:
    return STAGE_LABEL.get(stage, stage)


ADULT_YEAR = 13  # 大学一年级及以上（含「成人」）算成人学习者：AI 用成人的口吻，不显示乐园


def is_adult(grade: str | None) -> bool:
    return bool(grade) and stage_rank(grade) < 99 and stage_rank(grade) >= ADULT_YEAR


# 课面向谁：中小学的课只给中小学生，成人的课只给成人（docs/课程按人群分开-方案.md）。
# 分界线和 is_adult 是同一条：看学习者现在的年级，不看账号类型（家长、孩子、自学者只决定谁管谁）。
AUDIENCES = {"school": "中小学", "adult": "成人", "all": "不分"}


def audience(grade: str | None) -> str:
    """学习者属于哪一群：年级是大学及以上、「成人」→ adult，其余 → school。"""
    return "adult" if is_adult(grade) else "school"


def _audience_of(years: list) -> str:
    """课的学段都在大学及以上 → adult；都在高三及以下 → school；两边都有 → all。"""
    sides = {y >= ADULT_YEAR for y in years if y < 99}
    return "all" if len(sides) == 2 else "adult" if sides == {True} else "school"


@dataclass
class Pack:
    id: str
    subject: str
    subject_name: str
    edition: str
    region: str
    stages: list
    notes: str
    strands: list
    kp_ids: list = field(default_factory=list)
    tracks: list = field(default_factory=list)       # [{id, name, desc?}]
    default_track: str = ""
    school_types: list = field(default_factory=list)  # public / private / international；空 = 不限
    system: str = ""                                  # 学制 id，见 _meta/stages.json 的 systems
    kind: str = ""                                    # textbook / syllabus / course，见 PACK_KINDS
    teach_lang: str = ""                              # 授课 / 讲解语言（zh / en …）
    item_lang: str = ""                               # 题目语言
    prompt_note: str = ""                             # 给 AI 出题 / 讲解时的额外说明（比如「代码用 Python 3」）
    levels: list = field(default_factory=list)        # course 自己的级别 [{id, label, year}]
    chapters: bool = True                             # 能不能按章报进度；按能力分的（剑桥英语等）写 "progress": "none"
    selection: dict = field(default_factory=dict)    # 家长选课用的短名称、核对提示
    copyright: str = ""                               # 自编教材的版权声明，显示在知识点页底部
    audience: str = "school"                          # 面向谁（AUDIENCES）：默认按学段推，包里写 "audience" 可覆盖
    open_review: str = ""                             # "ai"：简答题由 AI 点评和追问，不让学员自评（app/coach.py）
    thinking_moves: list = field(default_factory=list)  # 这门课看重的思考方式 [{id, name, desc}]；列了才出思辨题（app/discuss.py）
    journal: dict = field(default_factory=dict)       # 决策日志：写了才有（字段、回看天数、可标的陷阱），见 app/journal.py
    discuss_action: str = "接下来我会做的一件事"        # 思辨题收尾时，「落到自己身上」那一栏问什么（CEO 课：回到公司做的事）
    base: str = ""                                    # 目录包：建在哪个主干上（知识点、题、讲解都是主干的）
    books: list = field(default_factory=list)        # 目录包的册次 [{stage, term, name, status, sources, units: [{name, kps}]}]

    @property
    def standard(self) -> bool:
        """课程标准主干：知识点的家，孩子不直接选它，选的是建在它上面的某套教材（目录包）。"""
        return self.kind == "standard"

    @property
    def lang(self) -> str:
        """语言类学科教的是哪种语言（英语课 → en，语文 → zh）；其他学科为空。"""
        return SUBJECTS.get(self.subject, {}).get("lang", "")

    @property
    def international(self) -> bool:
        return bool(SYSTEMS.get(self.system, {}).get("international"))

    @property
    def domain(self) -> str:
        return SUBJECTS.get(self.subject, {}).get("domain", "")

    def track(self, track: str | None) -> str:
        """有效方向：选了合法的就用它，否则用默认方向；没有方向的教材返回空。"""
        ids = [t["id"] for t in self.tracks]
        if not ids:
            return ""
        return track if track in ids else (self.default_track if self.default_track in ids else ids[0])

    def track_name(self, track: str | None) -> str:
        t = self.track(track)
        return next((x["name"] for x in self.tracks if x["id"] == t), "")


_MAJOR = re.compile(r"^(\d+)\.\d+")
_CN_NUM = {c: i for i, c in enumerate("零一二三四五六七八九十", 0)}


def _chapter_num(name: str) -> float:
    """章名里的序号（第3章、第三单元、Module 2、专题1、1. …），用来排先后；没有序号的排在后面。"""
    m = re.search(r"第\s*([0-9]+|[一二三四五六七八九十]+)\s*[章单元节课]", name) or \
        re.search(r"(?:Module|Unit|Chapter|专题|主题)\s*([0-9]+)", name) or re.match(r"([0-9]+)[.\s]", name)
    if not m:
        return 999
    v = m[1]
    if v.isdigit():
        return int(v)
    if v.startswith("十"):  # 十、十一 …
        return 10 + _CN_NUM.get(v[1:], 0) if len(v) > 1 else 10
    if "十" in v:           # 二十、二十一 …
        a, _, b = v.partition("十")
        return _CN_NUM.get(a, 0) * 10 + _CN_NUM.get(b, 0)
    return _CN_NUM.get(v, 999)


class Catalog:
    def __init__(self):
        self.packs: dict[str, Pack] = {}
        self.kps: dict[str, dict] = {}
        self.kp_pack: dict[str, str] = {}
        self.children: dict[str, list] = {}   # kp -> 以它为前置的后续知识点
        self.concepts: dict[str, dict] = {}   # concept id -> {id, name, kps}
        self.concept_of: dict[str, str] = {}  # kp -> concept id
        self.links: list[dict] = []           # {from, to, type, note}
        self.link_index: dict[str, list] = {}  # kp -> [link]（两个方向都记）
        self.foundations: dict[str, set] = {}  # 人群 -> 别的人群的课里、这群人的课要用到的基础知识点
        self.systems: list[dict] = []
        self.stages: list[dict] = []
        self.presets: list[dict] = []
        self.calendar: dict = {}
        self.ladder: dict = {}
        self.errors: list[str] = []           # 加载时发现的问题（check 命令会列出来）
        self.default_grade = "G3"             # 新建孩子时的默认年级（_meta/stages.json 的 default_grade）

    # ---- 加载 ----
    def load(self, directory=None, *, register_meta=True):
        self._register_meta = register_meta
        directory = directory or config.CURRICULA_DIR
        self.packs, self.kps, self.kp_pack, self.errors, self._meta = {}, {}, {}, [], {}
        self._load_meta(directory / "_meta")
        self.views = {}
        files = []
        for path in sorted(directory.glob("*.json")):
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(d, dict) or not isinstance(d.get("pack"), dict) or not isinstance(d.get("kps", [] if d["pack"].get("base") else None), list):
                    raise ValueError("教材包必须包含 pack 对象和 kps 列表")
                files.append((path, d))
            except (ValueError, TypeError) as exc:
                self.errors.append(f"{path.name}: {exc}")
        # 主干和独立教材先加载，目录包（pack.base）后加载：它要用主干的知识点
        for path, d in sorted(files, key=lambda f: bool(f[1]["pack"].get("base"))):
            meta = d["pack"]
            base = self.packs.get(meta.get("base", ""))
            if meta.get("base"):
                if not base or base.base:
                    self.errors.append(f"{path.name}: 目录包的主干 {meta['base']} 不存在")
                    continue
                meta = {**{k: self._meta[base.id][k] for k in EDITION_INHERITS if k in self._meta[base.id]}, **meta}
                d = {**d, "strands": base.strands + d.get("strands", [])}
            try:
                if not all(isinstance(meta.get(k), str) and meta[k] for k in ("id", "subject", "edition", "system")):
                    raise ValueError("教材包身份字段缺失")
                for kp in d.get("kps", []):
                    if not isinstance(kp, dict) or not all(isinstance(kp.get(k), str) and kp[k] for k in ("id", "name", "stage", "strand")):
                        raise ValueError("知识点身份字段缺失")
                    if not isinstance(kp.get("prereqs", []), list) or not all(isinstance(x, str) or (isinstance(x, dict) and isinstance(x.get("id"), str)) for x in kp.get("prereqs", [])):
                        raise ValueError("前置知识点结构不正确")
            except (ValueError, TypeError) as exc:
                self.errors.append(f"{path.name}: {exc}")
                continue
            if meta["id"] in self.packs:
                self.errors.append(f"{path.name}: 重复教材包 ID {meta['id']}")
                continue
            pack = self._pack(meta, d)
            track_ids = {t["id"] for t in pack.tracks}
            for kp in d.get("kps", []):
                kp = dict(kp)
                kp.setdefault("prereqs", [])
                kp["prereqs"] = [p if isinstance(p, dict) else {"id": p, "strength": "必须"} for p in kp["prereqs"]]
                kp["pack"] = pack.id
                if kp["id"] in self.kps:  # 同一知识点出现在多个包里时保留第一个
                    self.errors.append(f"{pack.id}: 知识点 {kp['id']} 和 {self.kp_pack[kp['id']]} 重复，已忽略")
                    continue
                if kp["stage"] not in STAGE_RANK:
                    self.errors.append(f"{pack.id}: {kp['id']} 的学段 {kp['stage']} 没有在 _meta/stages.json 里定义")
                ev = kp.get("evidence")
                if ev and ev not in EVIDENCE:
                    self.errors.append(f"{pack.id}: {kp['id']} 的证据牌 {ev} 不认识（可选 {list(EVIDENCE)}）")
                elif ev in RESEARCH_EVIDENCE and not kp.get("sources"):
                    self.errors.append(f"{pack.id}: {kp['id']} 标了「{ev}」，要写出处（sources）")
                bad = set(kp.get("tracks") or []) - track_ids
                if bad:
                    self.errors.append(f"{pack.id}: {kp['id']} 引用了不存在的方向 {sorted(bad)}")
                self.kps[kp["id"]] = kp
                self.kp_pack[kp["id"]] = pack.id
                pack.kp_ids.append(kp["id"])
            if base:
                self._edition(pack, base, d.get("books") or [])
            if not pack.stages:
                pack.stages = sorted({self.kp_in(pack.id, k)["stage"] for k in pack.kp_ids}, key=stage_rank)
            years = {lv["id"]: float(lv.get("year", 99)) for lv in pack.levels}
            pack.audience = meta.get("audience") or _audience_of([years.get(st, stage_rank(st)) for st in pack.stages])
            if pack.audience not in AUDIENCES:
                self.errors.append(f"{pack.id}: audience {pack.audience} 不认识（可选 {list(AUDIENCES)}）")
            self._meta[pack.id] = meta
            self.packs[pack.id] = pack
        self.kp_packs = {}   # 知识点 -> 用到它的教材（主干 + 各套目录）
        for pack in self.packs.values():
            for k in pack.kp_ids:
                self.kp_packs.setdefault(k, []).append(pack.id)
        self.children = {}
        for kid, kp in self.kps.items():
            for p in kp["prereqs"]:
                if p["id"] not in self.kps:
                    self.errors.append(f"{kp['pack']}: {kid} 的前置 {p['id']} 不存在")
                elif p.get("strength") == "必须" and stage_rank(self.kps[p["id"]]["stage"]) > stage_rank(kp["stage"]):
                    self.errors.append(f"{kp['pack']}: {kid} 的必须前置 {p['id']} 学段更晚（改成「有帮助」或调整学段）")
                self.children.setdefault(p["id"], []).append(kid)
        self._load_links(directory / "_links")
        self._audiences()
        for pr in self.presets:
            for subj, choice in pr.get("packs", {}).items():
                pid = choice["pack"] if isinstance(choice, dict) else choice
                if pid not in self.packs:
                    self.errors.append(f"学校模板 {pr['id']}：{subj} 用的教材 {pid} 不存在")
        self.textbooks = Textbooks().load(directory, self)
        self.errors.extend(self.textbooks.errors)
        self._validate_graph()
        return self

    def _validate_graph(self):
        # Iterative DFS avoids recursion limits on future larger curricula.
        colors = {}
        for root in self.kps:
            if colors.get(root):
                continue
            stack = [(root, False)]
            while stack:
                kid, leaving = stack.pop()
                if leaving:
                    colors[kid] = 2
                    continue
                if colors.get(kid) == 2:
                    continue
                if colors.get(kid) == 1:
                    self.errors.append(f"必须前置关系成环：{kid}")
                    continue
                colors[kid] = 1
                stack.append((kid, True))
                stack.extend((p["id"], False) for p in self.kps[kid]["prereqs"]
                             if p.get("strength", "必须") == "必须" and p["id"] in self.kps)
        for pr in self.presets:
            for subj, choice in pr.get("packs", {}).items():
                pid = choice["pack"] if isinstance(choice, dict) else choice
                if pid in self.packs and self.packs[pid].subject != subj:
                    self.errors.append(f"学校模板 {pr['id']}：{pid} 不属于学科 {subj}")

    def _pack(self, meta: dict, d: dict) -> Pack:
        global_subj = SUBJECTS.get(meta["subject"], {})
        if meta["subject"] not in SUBJECTS:
            self.errors.append(f"{meta['id']}: 学科 {meta['subject']} 没有在 _meta/subjects.json 里定义")
        system = meta.get("system", "")
        if system and system not in SYSTEMS:
            self.errors.append(f"{meta['id']}: 学制 {system} 没有在 _meta/stages.json 里定义")
        sysd = SYSTEMS.get(system, {})
        kind = meta.get("kind") or ("syllabus" if sysd.get("international") else "textbook" if system == "cn" else "course")
        if kind not in PACK_KINDS:
            self.errors.append(f"{meta['id']}: kind {kind} 不认识（可选 {list(PACK_KINDS)}）")
        # 语言：包里写了就用。没写：语言课的题目用它教的语言（语文课讲解也用中文）；其他学科按学制（国际学制英文）
        subj_lang = global_subj.get("lang", "")
        teach = meta.get("teach_lang") or ("zh" if subj_lang == "zh" else sysd.get("lang") or "zh")
        item = meta.get("item_lang") or subj_lang or teach
        levels = meta.get("levels") or []
        for lv in levels:  # 自定课程的级别挂到学段轴上，和年级比先后
            if self._register_meta:
                STAGE_RANK[lv["id"]] = float(lv.get("year", 99))
                STAGE_LABEL[lv["id"]] = lv.get("label", lv["id"])
        return Pack(
            id=meta["id"], subject=meta["subject"], subject_name=meta.get("subject_name") or global_subj.get("name", meta["subject"]),
            edition=meta.get("edition", ""), region=meta.get("region", ""),
            stages=sorted(meta.get("stages") or [lv["id"] for lv in levels], key=stage_rank), notes=meta.get("notes", ""),
            strands=d.get("strands", []), tracks=meta.get("tracks", []), default_track=meta.get("default_track", ""),
            school_types=meta.get("school_types", []), system=system, kind=kind, teach_lang=teach, item_lang=item,
            prompt_note=meta.get("prompt_note", ""), levels=levels, chapters=meta.get("progress") != "none",
            selection=meta.get("selection") or {}, copyright=meta.get("copyright", ""),
            open_review=meta.get("open_review", ""), thinking_moves=self._moves(meta, d), base=meta.get("base", ""),
            journal=self._journal(meta, d),
            **({"discuss_action": meta["discuss_action"]} if meta.get("discuss_action") else {}),
        )

    def _journal(self, meta, d) -> dict:
        """决策日志（app/journal.py）：{name, intro, subject_label, fields: [{id, label, hint}], review_days, traps: [本包知识点 id]}。"""
        j = meta.get("journal") or {}
        if not j:
            return {}
        kps = {k["id"] for k in d.get("kps", [])}
        ids = [f.get("id") for f in j.get("fields") or []]
        if not ids or not all(ids) or len(set(ids)) != len(ids) or not all(f.get("label") for f in j["fields"]):
            self.errors.append(f"{meta['id']}: journal.fields 每项要有不重复的 id 和 label")
        if not j.get("review_days") or not all(isinstance(n, int) and n > 0 for n in j["review_days"]):
            self.errors.append(f"{meta['id']}: journal.review_days 要写正整数天数")
        for t in j.get("traps") or []:
            if t not in kps:
                self.errors.append(f"{meta['id']}: journal.traps 里的 {t} 不是本包的知识点")
        return {"name": "决策日志", "intro": "", "subject_label": "决定什么", "subject_hint": "", "traps": [], **j}

    def _moves(self, meta, d) -> list:
        """thinking_moves 可以直接写 {id, name, desc}，也可以写本包里知识点的 id（用它的名称和说明，学员能点开那一课）。"""
        kps = {k["id"]: k for k in d.get("kps", [])}
        out = []
        for m in meta.get("thinking_moves") or []:
            if isinstance(m, str) and m in kps:
                out.append({"id": m, "name": kps[m]["name"], "desc": kps[m].get("desc", ""), "kp": m})
            elif isinstance(m, dict) and m.get("id") and m.get("name"):
                out.append({"desc": "", **m})
            else:
                self.errors.append(f"{meta['id']}: thinking_moves 里的 {m} 不是本包的知识点，也不是 {{id, name}}")
        return out

    def _edition(self, pack: Pack, base: Pack, books: list) -> None:
        """目录包：一套教材 = 册次 → 单元 → 按顺序教哪些知识点（主干里的，或这套书自己的）。
        知识点在这套书里的学段、学期、单元取它第一次出现的位置；同一个点可以在后面的单元里再出现（螺旋上升）。
        目录还没核实全时，目录里没出现的主干知识点放进后面第一本没列目录的册（「目录待核实」，按板块分章）；
        上下册都列全了还没出现的点、比这套书开始的年级还早的点，不算这套书的内容。"""
        own, view, out = list(pack.kp_ids), {}, []
        allowed = set(base.kp_ids) | set(own)
        for b in books:
            stage, term = b.get("stage", ""), b.get("term", "")
            if stage not in STAGE_RANK or not isinstance(b.get("units"), list):
                self.errors.append(f"{pack.id}: 册次 {b.get('name') or stage} 的学段或单元不正确")
                continue
            book = {"stage": stage, "term": term, "name": b.get("name") or f"{STAGE_LABEL.get(stage, stage)}{term}册",
                    "status": b.get("status", "partial"), "sources": b.get("sources", []), "units": []}
            for u in b["units"]:
                kps = [k for k in u.get("kps", []) if k in allowed]
                for k in set(u.get("kps", [])) - allowed:
                    self.errors.append(f"{pack.id}: {book['name']}「{u.get('name')}」里的 {k} 不在主干 {base.id} 或本包里")
                for k in kps:
                    view.setdefault(k, {**self.kps[k], "stage": stage, "term": term, "unit": u.get("name", "")})
                book["units"].append({"name": u.get("name", ""), "key": u.get("key") or u.get("name", ""), "kps": kps})
            if book["status"] in ("verified", "partial") and not book["sources"]:
                self.errors.append(f"{pack.id}: {book['name']} 缺少目录来源")
            out.append(book)
        # 语音、阅读这类板块不归哪一个单元、每个单元都在练（strand.throughout）：目录列了这个年级的册，就记在这一学年
        # 第一册的 throughout 里，算这一年的内容，但不占章（不参与按章推演学校进度）
        throughout = {s["id"] for s in base.strands if s.get("throughout")}
        for k in base.kp_ids + own:
            kp = self.kps[k]
            book = next((b for b in sorted(out, key=lambda b: TERM_RANK.get(b["term"], 1)) if b["stage"] == kp["stage"]), None)
            if k in view or kp["strand"] not in throughout or not book:
                continue
            book.setdefault("throughout", []).append(k)
            view[k] = {**kp, "stage": book["stage"], "term": book["term"], "unit": ""}
        listed = {(b["stage"], t) for b in out for t in ((b["term"],) if b["term"] in TERM_RANK else TERM_RANK)}
        open_slots = [(s, t) for s in sorted(set(pack.stages) or {b["stage"] for b in out}, key=stage_rank)
                      for t in TERM_RANK if (s, t) not in listed]
        first = min((stage_rank(s) for s in pack.stages or [b["stage"] for b in out]), default=0)
        auto: dict[tuple, dict] = {}
        for k in base.kp_ids + own:
            rank = stage_rank(self.kps[k]["stage"])
            slot = next((x for x in open_slots if stage_rank(x[0]) >= rank), None)
            if k in view or not slot or rank < first:   # 比这套书开始的年级还早的：是地基（app/ladder.py），不是这套书的内容
                continue
            stage, term = slot
            book = auto.setdefault(slot, {"stage": stage, "term": term, "name": f"{STAGE_LABEL.get(stage, stage)}{term}册（目录待核实）",
                                          "status": "pending", "sources": [], "units": []})
            strand = self.strand_name(base.id, self.kps[k]["strand"])
            unit = next((u for u in book["units"] if u["name"] == strand), None)
            if not unit:
                unit = {"name": strand, "key": f"{stage}{term}:{self.kps[k]['strand']}", "kps": []}
                book["units"].append(unit)
            unit["kps"].append(k)
            view[k] = {**self.kps[k], "stage": stage, "term": term, "unit": strand}
        out = sorted(out + list(auto.values()), key=lambda b: (stage_rank(b["stage"]), TERM_RANK.get(b["term"], 1)))
        order = list(dict.fromkeys(k for b in out for k in [x for u in b["units"] for x in u["kps"]] + b.get("throughout", [])))
        order += [k for k in own if k not in view]   # 本包自己的点没进目录：按它自己写的学段
        pack.kp_ids, pack.books, self.views[pack.id] = order, out, {**{k: self.kps[k] for k in own}, **view}

    def _load_meta(self, meta_dir):
        f = meta_dir / "stages.json"
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            self.systems, self.stages = d.get("systems", []), d.get("stages", [])
            if self._register_meta:
                SYSTEMS.clear()
                SYSTEMS.update({x["id"]: x for x in self.systems})
                for s in self.stages:
                    STAGE_RANK[s["id"]] = float(s["year"])
                    STAGE_LABEL[s["id"]] = s.get("label", s["id"])
                if d.get("grades"):
                    GRADES[:] = d["grades"]
            self.default_grade = d.get("default_grade") or GRADES[0]
        f = meta_dir / "subjects.json"
        if f.exists() and self._register_meta:
            SUBJECTS.clear()
            SUBJECTS.update({x["id"]: x for x in json.loads(f.read_text(encoding="utf-8")).get("subjects", [])})
        f = meta_dir / "presets.json"
        self.presets = json.loads(f.read_text(encoding="utf-8")).get("presets", []) if f.exists() else []
        f = meta_dir / "selection.json"
        self.selection_guides = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        f = meta_dir / "calendar.json"   # 校历：按日期推演学校进度（app/schoolyear.py）
        self.calendar = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        f = meta_dir / "ladder.json"     # 地基：往回一直到一年级（app/ladder.py）
        self.ladder = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}

    def selection_guide(self, pack):
        """Presentation copy stays separate from the full curriculum and AI instructions."""
        guide = {**getattr(self, 'selection_guides', {}).get(pack.id, {}), **pack.selection}
        name = guide.get('name') or pack.edition
        return {**guide, 'name': name if len(name) <= 44 else name[:43] + '…',
                'hint': guide.get('hint') or '核对课本封面、出版社、学制和年级；不确定时可先暂不安排。'}

    def _load_links(self, links_dir):
        self.concepts, self.concept_of, self.links, self.link_index = {}, {}, [], {}
        if not links_dir.exists():
            return
        for path in sorted(links_dir.glob("*.json")):
            d = json.loads(path.read_text(encoding="utf-8"))
            for c in d.get("concepts", []):
                kps = [k for k in c.get("kps", []) if k in self.kps]
                for k in set(c.get("kps", [])) - set(kps):
                    self.errors.append(f"{path.name}: 概念 {c['id']} 里的 {k} 不存在")
                c = {**self.concepts.get(c["id"], {"id": c["id"], "name": c.get("name", c["id"]), "kps": []})}
                c["kps"] = list(dict.fromkeys(c["kps"] + kps))
                self.concepts[c["id"]] = c
                for k in kps:
                    if self.concept_of.get(k, c["id"]) != c["id"]:
                        self.errors.append(f"{path.name}: {k} 同时属于概念 {self.concept_of[k]} 和 {c['id']}")
                    self.concept_of[k] = c["id"]
            for ln in d.get("links", []):
                if ln.get("type") not in LINK_TYPES:
                    self.errors.append(f"{path.name}: 关联类型 {ln.get('type')} 不认识（{ln.get('from')} → {ln.get('to')}）")
                    continue
                if ln["from"] not in self.kps or ln["to"] not in self.kps:
                    self.errors.append(f"{path.name}: 关联 {ln['from']} → {ln['to']} 里有不存在的知识点")
                    continue
                self.links.append(ln)
                self.link_index.setdefault(ln["from"], []).append(ln)
                self.link_index.setdefault(ln["to"], []).append(ln)

    def _audiences(self):
        """人群之间的知识点。成人的课可以把中小学的点当前置（补基础）：这些点成人也能打开（foundations）。
        反过来不行：中小学的课用到成人课的点（前置、关联、同一概念），孩子追根、看关联就会走进成人课。"""
        def side(k):
            return self.packs[self.kp_pack[k]].audience
        self.foundations = {}
        for aud in ("school", "adult"):
            seen, stack = set(), [k for k in self.kps if side(k) in (aud, "all")]
            while stack:
                for p in self.kps[stack.pop()]["prereqs"]:
                    if p["id"] in self.kps and p["id"] not in seen:
                        seen.add(p["id"])
                        stack.append(p["id"])
            self.foundations[aud] = {k for k in seen if side(k) not in (aud, "all")}
        for k in sorted(self.foundations["school"]):
            self.errors.append(f"{self.kp_pack[k]}: {k} 是成人课的知识点，中小学的课不能把它当前置")
        pairs = [(f"关联 {ln['from']} → {ln['to']}", (ln["from"], ln["to"])) for ln in self.links]
        pairs += [(f"概念 {c['id']}", c["kps"]) for c in self.concepts.values()]
        for name, kps in pairs:
            if {"school", "adult"} <= {side(k) for k in kps}:
                self.errors.append(f"{name} 把中小学的课和成人的课连在一起了")

    # ---- 图谱查询 ----
    def kp(self, kp_id: str) -> dict | None:
        return self.kps.get(kp_id)

    def kp_in(self, pack_id: str, kp_id: str) -> dict | None:
        """知识点在某套教材里的样子：学段、学期、单元按这套书的目录（同一个点，上海三年级教，苏教四年级教）。"""
        return self.views.get(pack_id, {}).get(kp_id) or self.kps.get(kp_id)

    def stage_in(self, pack_id: str, kp_id: str) -> str:
        return self.kp_in(pack_id, kp_id)["stage"]

    def visible(self, pack, grade: str | None) -> bool:
        """这门课这个学习者能不能看到、选、学（按年级，见 AUDIENCES）。"""
        p = pack if isinstance(pack, Pack) else self.packs.get(pack or "")
        return bool(p) and p.audience in ("all", audience(grade))

    def kp_visible(self, kp_id: str, grade: str | None) -> bool:
        """知识点能不能打开：它所在的课能看到，或者它是这群人的课要用到的基础。"""
        return self.visible(self.kp_pack.get(kp_id), grade) or kp_id in self.foundations.get(audience(grade), ())

    def courses(self) -> list[Pack]:
        """能选的课：主干不直接选，选建在它上面的教材。"""
        return [p for p in self.packs.values() if not p.standard]

    def editions_of(self, pack_id: str) -> list[Pack]:
        return [p for p in self.packs.values() if p.base == pack_id]

    def prereqs(self, kp_id: str, required_only=False) -> list[dict]:
        kp = self.kps.get(kp_id)
        if not kp:
            return []
        out = []
        for p in kp["prereqs"]:
            if required_only and p.get("strength") != "必须":
                continue
            if p["id"] in self.kps:
                out.append({**self.kps[p["id"]], "strength": p.get("strength", "必须")})
        return out

    def successors(self, kp_id: str) -> list[dict]:
        return [self.kps[c] for c in self.children.get(kp_id, []) if c in self.kps]

    def ancestors(self, kp_id: str, depth=3, required_only=True) -> list[tuple[dict, int]]:
        seen, out, frontier = {kp_id}, [], [kp_id]
        for d in range(1, depth + 1):
            nxt = []
            for k in frontier:
                for p in self.prereqs(k, required_only):
                    if p["id"] not in seen:
                        seen.add(p["id"])
                        out.append((p, d))
                        nxt.append(p["id"])
            frontier = nxt
        return out

    def in_track(self, kp_id: str, track: str | None) -> bool:
        kp = self.kps.get(kp_id)
        if not kp:
            return False
        only = kp.get("tracks")
        return not only or self.packs[kp["pack"]].track(track) in only

    def ids_for(self, pack_id: str, track: str | None = None) -> list[str]:
        """某个方向下这套教材要学的知识点（不传方向就按默认方向）。"""
        pack = self.packs.get(pack_id)
        if not pack:
            return []
        if not pack.tracks:
            return pack.kp_ids
        t = pack.track(track)
        return [k for k in pack.kp_ids if not self.kps[k].get("tracks") or t in self.kps[k]["tracks"]]

    def pack_kps(self, pack_id: str, max_stage: str | None = None, min_stage: str | None = None,
                 track: str | None = None) -> list[dict]:
        hi = stage_rank(max_stage) if max_stage else 999
        lo = stage_rank(min_stage) if min_stage else -1
        kps = [self.kp_in(pack_id, k) for k in self.ids_for(pack_id, track)]
        return [k for k in kps if lo <= stage_rank(k["stage"]) <= hi]

    # ---- 跨教材融合 ----
    def equivalents(self, kp_id: str) -> list[dict]:
        """同一个概念在其它教材里的知识点。"""
        c = self.concepts.get(self.concept_of.get(kp_id, ""))
        return [self.kps[k] for k in c["kps"] if k != kp_id] if c else []

    def related(self, kp_id: str, types=None) -> list[dict]:
        """跨学科关联：[{kp, type, note, dir}]，dir = out（本点指向对方）或 in（对方指向本点）。"""
        out = []
        for ln in self.link_index.get(kp_id, []):
            if types and ln["type"] not in types:
                continue
            other = ln["to"] if ln["from"] == kp_id else ln["from"]
            out.append({"kp": self.kps[other], "type": ln["type"], "note": ln.get("note", ""),
                        "dir": "out" if ln["from"] == kp_id else "in"})
        return out

    def chapters(self, pack_id: str, stage: str, track: str | None = None) -> list[dict]:
        """某学段的「章」：孩子报进度用（孩子记得的是学到第几章，不是知识点名字）。
        知识点标了 unit 的按 unit 分；整个学段都没标的按板块（strand）分。上册在前、下册在后，其余按教材里的顺序。"""
        pack = self.packs.get(pack_id)
        if not pack or not pack.chapters:
            return []
        if any(b["stage"] == stage for b in pack.books):   # 目录包：按册次、单元
            allowed = set(self.ids_for(pack_id, track))
            return [{"key": u["key"], "name": u["name"], "kps": kps, "book": b["name"], "status": b["status"],
                     "rank": TERM_RANK.get(b["term"], 1)}
                    for b in pack.books if b["stage"] == stage for u in b["units"]
                    if (kps := [k for k in u["kps"] if k in allowed])]
        explicit = []
        if hasattr(self, "textbooks"):
            explicit = self.textbooks.chapters(pack_id, stage, set(self.ids_for(pack_id, track)))
        mapped = {kid for ch in explicit for kid in ch["kps"]}
        kps = [kp for k in self.ids_for(pack_id, track) if (kp := self.kp_in(pack_id, k))["stage"] == stage and k not in mapped]
        by_unit = len({k.get("unit") for k in kps if k.get("unit")}) > 1
        coarse = by_unit and len({k.get("unit") for k in kps}) > 15  # 大纲分得很细（1.1、1.2…）：归到大题
        out: dict[str, dict] = {}
        for i, kp in enumerate(kps):
            unit, strand = kp.get("unit") or "", self.strand_name(pack_id, kp.get("strand", ""))
            major = _MAJOR.match(unit)
            if by_unit and unit and coarse and " / " in unit:
                key = name = unit.split(" / ")[0]
            elif by_unit and unit and coarse and major:
                key, name = "n:" + major[1], f"{major[1]}. {strand}"
            elif by_unit and unit:
                key = name = unit
            else:
                key, name = "s:" + kp.get("strand", ""), ("其他 · " if by_unit else "") + strand
            ch = out.setdefault(key, {"key": key, "name": name, "kps": [], "first": i, "terms": []})
            ch["kps"].append(kp["id"])
            ch["terms"].append(kp.get("term"))
        for ch in out.values():
            terms = [t for t in ch.pop("terms") if t in TERM_RANK]
            ch["rank"] = TERM_RANK[max(sorted(set(terms)), key=terms.count)] if terms else 1  # 一样多时算上册
            ch["num"] = _chapter_num(ch["name"])
        chs = sorted(out.values(), key=lambda c: (c["rank"], c["first"]))
        # 章名里的序号不重复时按序号排（教材数据里的先后不一定是上课顺序）；有重复（第1节、第1节…）就保持原顺序
        nums = [(c["rank"], c["num"]) for c in chs if c["num"] < 999]
        if len(nums) == len(set(nums)):
            chs.sort(key=lambda c: (c["rank"], c["num"], c["first"]))
        return explicit + chs

    def strand_name(self, pack_id: str, strand_id: str) -> str:
        for s in self.packs[pack_id].strands:
            if s["id"] == strand_id:
                return s["name"]
        return strand_id

    def default_stage(self, pack_id: str, grade: str) -> str:
        """孩子年级在该包里对应的学段：有同名学段就用它，否则取不超过年级的最高学段。"""
        pack = self.packs[pack_id]
        if grade in pack.stages:
            return grade
        g = stage_rank(grade)
        below = [s for s in pack.stages if stage_rank(s) <= g]
        return below[-1] if below else pack.stages[0]

    def by_subject(self, school_type: str = "", grade: str | None = None) -> dict[str, list[Pack]]:
        """能选的课按学科分组；给了年级就只列这个学习者能学的课。"""
        out: dict[str, list[Pack]] = {}
        for p in self.courses():
            if grade is None or self.visible(p, grade):
                out.setdefault(p.subject_name, []).append(p)
        if school_type:  # 适合这类学校的教材排前面（其它的仍可选：转学、提前学）
            for lst in out.values():
                lst.sort(key=lambda p: bool(p.school_types) and school_type not in p.school_types)
        return out

    def concept_id(self, kp_id: str) -> str:
        """Unmapped knowledge points retain an independent identity."""
        return self.concept_of.get(kp_id, "kp:" + kp_id)

    @property
    def concept_key(self) -> str:
        return hashlib.sha256(json.dumps(self.concept_of, sort_keys=True).encode()).hexdigest()[:12]

    def learning_key(self, kp_id: str) -> str:
        return self.concept_id(kp_id)

    @staticmethod
    def subject(subject_id: str) -> dict:
        return SUBJECTS.get(subject_id) or {"id": subject_id, "name": subject_id, "icon": "📘", "tone": "brand"}

    def preset(self, preset_id: str) -> dict | None:
        return next((p for p in self.presets if p["id"] == preset_id), None)

    # 添加孩子时「在哪上学」：学校类型（和教材包的 school_types 同一套词）
    SCHOOL_TYPES = {"public": "公办", "private": "民办双语", "international": "国际学校"}

    @property
    def regions(self) -> list[str]:
        """有学校模板的地区（模板里写了 region 的），按模板顺序；其它地区家长逐科选。"""
        return list(dict.fromkeys(p["region"] for p in self.presets if p.get("region")))

    def match_preset(self, region: str, school_type: str, grade: str) -> dict | None:
        """按 地区 + 学校类型 + 年级 找学校模板：先找这个地区的，再找各地通用的（国际学校）；
        这个地区没有这类学校的模板（比如江苏的民办），就按同地区公办的教材起步。"""
        g = stage_rank(grade)
        def fits(p, st):
            lo, hi = (p.get("grades") or [None, None])[0], (p.get("grades") or [None, None])[-1]
            return (p.get("school_type") == st and (not lo or stage_rank(lo) <= g <= stage_rank(hi))
                    and p.get("region", "") in (region, ""))
        for st in dict.fromkeys((school_type, "public")):
            hits = sorted((p for p in self.presets if fits(p, st)), key=lambda p: not p.get("region"))
            if hits:
                return hits[0]
        return None

    def default_courses(self, region: str, school_type: str, grade: str) -> tuple[dict | None, list[dict]]:
        """添加孩子时按 地区 + 学校类型 + 年级 默认安排的课：模板里这个年级用得上的教材；
        没有模板的地区先只排统编语文（全国通用），其余由家长逐科选。"""
        preset = self.match_preset(region, school_type, grade)
        choices = preset["packs"] if preset else {"chinese": "chn-tongbian"}
        g, out = stage_rank(grade), []
        for subj, choice in choices.items():
            pid = choice["pack"] if isinstance(choice, dict) else choice
            p = self.packs.get(pid)
            if not p or p.standard or not p.stages or not (stage_rank(p.stages[0]) <= g <= stage_rank(p.stages[-1]) + 0.5):
                continue
            out.append({"subject": subj, "pack": pid, "track": choice.get("track", "") if isinstance(choice, dict) else "",
                        "subject_name": p.subject_name, "name": self.selection_guide(p)["name"]})
        return preset, out


catalog = Catalog()
