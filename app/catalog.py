"""课程目录：从 curricula/*.json 加载「教材包」，提供知识图谱查询。

一个教材包 = 一个学科的一个版本（如 统编语文、上海牛津英语、IGCSE 物理），
包含 strands（板块）和 kps（知识点）。知识点 id 全局唯一，前置关系可以跨包引用
（例如物理知识点依赖数学知识点）。新增教材只需往 curricula/ 放一个 JSON 文件。
"""
import json
from dataclasses import dataclass, field

from . import config

STAGE_RANK = {f"G{i}": float(i) for i in range(1, 13)}
STAGE_RANK.update({"IGCSE": 9.5, "A-Level": 11.5})
STAGE_LABEL = {
    "G1": "一年级", "G2": "二年级", "G3": "三年级", "G4": "四年级", "G5": "五年级",
    "G6": "六年级", "G7": "初一", "G8": "初二", "G9": "初三",
    "G10": "高一", "G11": "高二", "G12": "高三", "IGCSE": "IGCSE", "A-Level": "A-Level",
}
GRADES = [f"G{i}" for i in range(1, 13)]
LANG_SUBJECTS = {"english": "en", "chinese": "zh"}


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

    @property
    def lang(self) -> str:
        return LANG_SUBJECTS.get(self.subject, "")

    @property
    def international(self) -> bool:
        return "cambridge" in self.id or "igcse" in self.id


class Catalog:
    def __init__(self):
        self.packs: dict[str, Pack] = {}
        self.kps: dict[str, dict] = {}
        self.kp_pack: dict[str, str] = {}
        self.children: dict[str, list] = {}  # kp -> 以它为前置的后续知识点

    def load(self, directory=None):
        directory = directory or config.CURRICULA_DIR
        self.packs, self.kps, self.kp_pack = {}, {}, {}  # 允许重复调用（重新加载）
        for path in sorted(directory.glob("*.json")):
            d = json.loads(path.read_text(encoding="utf-8"))
            meta = d["pack"]
            pack = Pack(
                id=meta["id"], subject=meta["subject"], subject_name=meta.get("subject_name", meta["subject"]),
                edition=meta.get("edition", ""), region=meta.get("region", ""),
                stages=sorted(meta.get("stages") or [], key=stage_rank), notes=meta.get("notes", ""),
                strands=d.get("strands", []),
            )
            for kp in d["kps"]:
                kp = dict(kp)
                kp.setdefault("prereqs", [])
                kp["prereqs"] = [p if isinstance(p, dict) else {"id": p, "strength": "必须"} for p in kp["prereqs"]]
                kp["pack"] = pack.id
                if kp["id"] in self.kps:  # 同一知识点出现在多个包里时保留第一个
                    continue
                self.kps[kp["id"]] = kp
                self.kp_pack[kp["id"]] = pack.id
                pack.kp_ids.append(kp["id"])
            if not pack.stages:
                pack.stages = sorted({self.kps[k]["stage"] for k in pack.kp_ids}, key=stage_rank)
            self.packs[pack.id] = pack
        self.children = {}
        for kid, kp in self.kps.items():
            for p in kp["prereqs"]:
                self.children.setdefault(p["id"], []).append(kid)
        return self

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

    def pack_kps(self, pack_id: str, max_stage: str | None = None, min_stage: str | None = None) -> list[dict]:
        pack = self.packs.get(pack_id)
        if not pack:
            return []
        hi = stage_rank(max_stage) if max_stage else 999
        lo = stage_rank(min_stage) if min_stage else -1
        return [self.kps[k] for k in pack.kp_ids if lo <= stage_rank(self.kps[k]["stage"]) <= hi]

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

    def by_subject(self) -> dict[str, list[Pack]]:
        out: dict[str, list[Pack]] = {}
        for p in self.packs.values():
            out.setdefault(p.subject_name, []).append(p)
        return out


catalog = Catalog()
