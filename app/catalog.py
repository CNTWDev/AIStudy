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

孩子自己的选择（年级、学校类型、每科选哪套教材、哪个方向、学到哪）存在数据库里，不进这些文件。
"""
import json
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
LANG_SUBJECTS = {"english": "en", "chinese": "zh"}
LINK_TYPES = {"uses": "要用到", "language": "另一种语言的说法", "context": "相关背景"}


def stage_rank(stage: str) -> float:
    return STAGE_RANK.get(stage, 99.0)


def stage_label(stage: str) -> str:
    return STAGE_LABEL.get(stage, stage)


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
    system: str = ""                                  # cn / cambridge / ib / alevel / us

    @property
    def lang(self) -> str:
        return LANG_SUBJECTS.get(self.subject, "")

    @property
    def international(self) -> bool:
        return self.system in ("cambridge", "ib", "alevel", "us") or "cambridge" in self.id or "igcse" in self.id

    def track(self, track: str | None) -> str:
        """有效方向：选了合法的就用它，否则用默认方向；没有方向的教材返回空。"""
        ids = [t["id"] for t in self.tracks]
        if not ids:
            return ""
        return track if track in ids else (self.default_track if self.default_track in ids else ids[0])

    def track_name(self, track: str | None) -> str:
        t = self.track(track)
        return next((x["name"] for x in self.tracks if x["id"] == t), "")


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

    # ---- 加载 ----
    def load(self, directory=None):
        directory = directory or config.CURRICULA_DIR
        self.packs, self.kps, self.kp_pack, self.errors = {}, {}, {}, []
        self._load_meta(directory / "_meta")
        for path in sorted(directory.glob("*.json")):
            d = json.loads(path.read_text(encoding="utf-8"))
            meta = d["pack"]
            pack = Pack(
                id=meta["id"], subject=meta["subject"], subject_name=meta.get("subject_name", meta["subject"]),
                edition=meta.get("edition", ""), region=meta.get("region", ""),
                stages=sorted(meta.get("stages") or [], key=stage_rank), notes=meta.get("notes", ""),
                strands=d.get("strands", []), tracks=meta.get("tracks", []), default_track=meta.get("default_track", ""),
                school_types=meta.get("school_types", []), system=meta.get("system", ""),
            )
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
                self.children.setdefault(p["id"], []).append(kid)
        self._load_links(directory / "_links")
        for pr in self.presets:
            for subj, choice in pr.get("packs", {}).items():
                pid = choice["pack"] if isinstance(choice, dict) else choice
                if pid not in self.packs:
                    self.errors.append(f"学校模板 {pr['id']}：{subj} 用的教材 {pid} 不存在")
        return self

    def _load_meta(self, meta_dir):
        f = meta_dir / "stages.json"
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            self.systems, self.stages = d.get("systems", []), d.get("stages", [])
            for s in self.stages:
                STAGE_RANK[s["id"]] = float(s["year"])
                STAGE_LABEL[s["id"]] = s.get("label", s["id"])
            if d.get("grades"):
                GRADES[:] = d["grades"]
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

    def preset(self, preset_id: str) -> dict | None:
        return next((p for p in self.presets if p["id"] == preset_id), None)


catalog = Catalog()
