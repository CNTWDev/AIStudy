"""图的种类注册表和公用的检查工具。

一种图 = 一个 Kind 子类，登记到 KINDS：
- normalize(spec)：检查、整理 AI 给的画图说明，不合格就 raise FigError（这道题就不要了）。
- svg(spec)：按说明画成 SVG（矢量图，线条用 currentColor，深色模式自动变色）。
- describe(spec)：给 AI 校对 / 改编用的文字版（默认就是画图说明本身，坐标都在里面）。
- prompt：告诉 AI 这种图怎么写（只给 subjects 里的学科看，免得提示词太长）。
- cues：知识点的名称 / 说明 / 关键词里出现这些词，这个知识点的题通常要看这种图（出题时要求多配图）。
"""
import json
import math

KINDS: dict[str, "Kind"] = {}

MAX_COORD = 1000
MAX_LABEL = 24
MAX_TEXT = 60


class FigError(ValueError):
    pass


class Kind:
    id = ""
    label = ""
    subjects: tuple = ()   # 哪些学科的出题提示词里带上它（subjects.json 的 id）
    cues: tuple = ()       # 知识点里出现这些词：这个点的题通常要看这种图
    drawn = True           # 程序按说明画出来的图；原卷裁图是 False
    prompt = ""

    def normalize(self, spec: dict) -> dict:
        raise NotImplementedError

    def svg(self, spec: dict) -> str:
        raise NotImplementedError

    def describe(self, spec: dict) -> str:
        body = {k: v for k, v in spec.items() if k not in ("kind", "alt")}
        return f"{self.label}（画图说明）：{json.dumps(body, ensure_ascii=False)}"


def register(cls):
    KINDS[cls.id] = cls()
    return cls


# ---------------------------------------------------------------- 检查工具

def num(v, what="数值", lo=-MAX_COORD, hi=MAX_COORD) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise FigError(f"{what}不是数字：{v!r}")
    if not math.isfinite(f) or not lo <= f <= hi:
        raise FigError(f"{what}超出范围：{v!r}")
    return f


def text(v, n=MAX_TEXT) -> str:
    return str(v if v is not None else "").strip()[:n]


def label(v) -> str:
    return text(v, MAX_LABEL)


def lst(v, what, most=60) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        raise FigError(f"{what}应该是列表")
    if len(v) > most:
        raise FigError(f"{what}太多（最多 {most} 个）")
    return v


def close(a: float, b: float, rel=0.02) -> bool:
    return abs(a - b) <= max(abs(b) * rel, 1e-6)
