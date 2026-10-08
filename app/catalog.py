"""课程目录：教材体系的底层数据，分四层，互相独立、各自可扩展。

1. 学段（curricula/_meta/stages.json）：所有学制的学段放在同一根「学年」轴上（year：一年级 = 1，
   IGCSE ≈ 9.5，IB DP1 = 11 …），不同学制之间才能比先后。加一个学制 / 年级只改这个文件。
2. 教材包（curricula/*.json）：一个学科的一个版本（统编语文、上海英语、IGCSE 物理……），自带板块（strands）
   和知识点（kps）。包与包之间互不依赖，加一套教材就是放一个 JSON 文件。
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
import re
from dataclasses import dataclass, field

from . import config

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
PACK_KINDS = {"textbook": "审定教材", "syllabus": "考试大纲", "course": "自定课程"}
LINK_TYPES = {"uses": "要用到", "language": "另一种语言的说法", "context": "相关背景"}


def stage_rank(stage: str) -> float:
    return STAGE_RANK.get(stage, 99.0)


def stage_label(stage: str) -> str:
    return STAGE_LABEL.get(stage, stage)


ADULT_YEAR = 13  # 大学一年级及以上（含「成人」）算成人学习者：AI 用成人的口吻，不显示乐园


def is_adult(grade: str | None) -> bool:
    return bool(grade) and stage_rank(grade) < 99 and stage_rank(grade) >= ADULT_YEAR


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
        self.systems: list[dict] = []
        self.stages: list[dict] = []
        self.presets: list[dict] = []
        self.errors: list[str] = []           # 加载时发现的问题（check 命令会列出来）
        self.default_grade = "G3"             # 新建孩子时的默认年级（_meta/stages.json 的 default_grade）

    # ---- 加载 ----
    def load(self, directory=None):
        directory = directory or config.CURRICULA_DIR
        self.packs, self.kps, self.kp_pack, self.errors = {}, {}, {}, []
        self._load_meta(directory / "_meta")
        for path in sorted(directory.glob("*.json")):
            d = json.loads(path.read_text(encoding="utf-8"))
            meta = d["pack"]
            pack = self._pack(meta, d)
            track_ids = {t["id"] for t in pack.tracks}
            for kp in d["kps"]:
                kp = dict(kp)
                kp.setdefault("prereqs", [])
                kp["prereqs"] = [p if isinstance(p, dict) else {"id": p, "strength": "必须"} for p in kp["prereqs"]]
                kp["pack"] = pack.id
                if kp["id"] in self.kps:  # 同一知识点出现在多个包里时保留第一个
                    self.errors.append(f"{pack.id}: 知识点 {kp['id']} 和 {self.kp_pack[kp['id']]} 重复，已忽略")
                    continue
                if kp["stage"] not in STAGE_RANK:
                    self.errors.append(f"{pack.id}: {kp['id']} 的学段 {kp['stage']} 没有在 _meta/stages.json 里定义")
                bad = set(kp.get("tracks") or []) - track_ids
                if bad:
                    self.errors.append(f"{pack.id}: {kp['id']} 引用了不存在的方向 {sorted(bad)}")
                self.kps[kp["id"]] = kp
                self.kp_pack[kp["id"]] = pack.id
                pack.kp_ids.append(kp["id"])
            if not pack.stages:
                pack.stages = sorted({self.kps[k]["stage"] for k in pack.kp_ids}, key=stage_rank)
            self.packs[pack.id] = pack
        self.children = {}
        for kid, kp in self.kps.items():
            for p in kp["prereqs"]:
                if p["id"] not in self.kps:
                    self.errors.append(f"{kp['pack']}: {kid} 的前置 {p['id']} 不存在")
                elif p.get("strength") == "必须" and stage_rank(self.kps[p["id"]]["stage"]) > stage_rank(kp["stage"]):
                    self.errors.append(f"{kp['pack']}: {kid} 的必须前置 {p['id']} 学段更晚（改成「有帮助」或调整学段）")
                self.children.setdefault(p["id"], []).append(kid)
        self._load_links(directory / "_links")
        for pr in self.presets:
            for subj, choice in pr.get("packs", {}).items():
                pid = choice["pack"] if isinstance(choice, dict) else choice
                if pid not in self.packs:
                    self.errors.append(f"学校模板 {pr['id']}：{subj} 用的教材 {pid} 不存在")
        return self

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
            STAGE_RANK[lv["id"]] = float(lv.get("year", 99))
            STAGE_LABEL[lv["id"]] = lv.get("label", lv["id"])
        return Pack(
            id=meta["id"], subject=meta["subject"], subject_name=meta.get("subject_name") or global_subj.get("name", meta["subject"]),
            edition=meta.get("edition", ""), region=meta.get("region", ""),
            stages=sorted(meta.get("stages") or [lv["id"] for lv in levels], key=stage_rank), notes=meta.get("notes", ""),
            strands=d.get("strands", []), tracks=meta.get("tracks", []), default_track=meta.get("default_track", ""),
            school_types=meta.get("school_types", []), system=system, kind=kind, teach_lang=teach, item_lang=item,
            prompt_note=meta.get("prompt_note", ""), levels=levels, chapters=meta.get("progress") != "none",
        )

    def _load_meta(self, meta_dir):
        f = meta_dir / "stages.json"
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            self.systems, self.stages = d.get("systems", []), d.get("stages", [])
            SYSTEMS.clear()
            SYSTEMS.update({x["id"]: x for x in self.systems})
            for s in self.stages:
                STAGE_RANK[s["id"]] = float(s["year"])
                STAGE_LABEL[s["id"]] = s.get("label", s["id"])
            if d.get("grades"):
                GRADES[:] = d["grades"]
            self.default_grade = d.get("default_grade") or GRADES[0]
        f = meta_dir / "subjects.json"
        if f.exists():
            SUBJECTS.clear()
            SUBJECTS.update({x["id"]: x for x in json.loads(f.read_text(encoding="utf-8")).get("subjects", [])})
        f = meta_dir / "presets.json"
        self.presets = json.loads(f.read_text(encoding="utf-8")).get("presets", []) if f.exists() else []

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

    # ---- 图谱查询 ----
    def kp(self, kp_id: str) -> dict | None:
        return self.kps.get(kp_id)

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
        return [self.kps[k] for k in self.ids_for(pack_id, track) if lo <= stage_rank(self.kps[k]["stage"]) <= hi]

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
        kps = [self.kps[k] for k in self.ids_for(pack_id, track) if self.kps[k]["stage"] == stage]
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
        term_rank = {"上": 0, "下": 2}
        for ch in out.values():
            terms = [t for t in ch.pop("terms") if t in term_rank]
            ch["rank"] = term_rank[max(sorted(set(terms)), key=terms.count)] if terms else 1  # 一样多时算上册
            ch["num"] = _chapter_num(ch["name"])
        chs = sorted(out.values(), key=lambda c: (c["rank"], c["first"]))
        # 章名里的序号不重复时按序号排（教材数据里的先后不一定是上课顺序）；有重复（第1节、第1节…）就保持原顺序
        nums = [(c["rank"], c["num"]) for c in chs if c["num"] < 999]
        if len(nums) == len(set(nums)):
            chs.sort(key=lambda c: (c["rank"], c["num"], c["first"]))
        return chs

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

    def by_subject(self, school_type: str = "") -> dict[str, list[Pack]]:
        out: dict[str, list[Pack]] = {}
        for p in self.packs.values():
            out.setdefault(p.subject_name, []).append(p)
        if school_type:  # 适合这类学校的教材排前面（其它的仍可选：转学、提前学）
            for lst in out.values():
                lst.sort(key=lambda p: bool(p.school_types) and school_type not in p.school_types)
        return out

    @staticmethod
    def subject(subject_id: str) -> dict:
        return SUBJECTS.get(subject_id) or {"id": subject_id, "name": subject_id, "icon": "📘", "tone": "brand"}

    def preset(self, preset_id: str) -> dict | None:
        return next((p for p in self.presets if p["id"] == preset_id), None)


catalog = Catalog()
