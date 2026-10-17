"""题目配图：图是题的一部分，和文字一起存在题目里（items.data 的 fig 字段），存一次、反复用。

两种来源，存法和显示一样：
- 画出来的图：AI 不直接画图，而是写一份「画图说明」（点的坐标、连哪些线、电路里有哪些元件……），
  这里检查说明（题目里说的长度、角度和图对得上吗），再按说明画成 SVG。说不清、画不出、对不上的图，这道题就不要。
- 原卷上的图：试卷导入时 AI 报告图在照片上的位置，裁下来存好（见 photo.py），家长可以重新框。

加一种图：在这个包里写一个 Kind 子类（normalize / svg / prompt），用 @register 登记，并在下面 import 一下。
"""
import functools
import json
import logging

from .base import KINDS, FigError, Kind, register
from . import plane, solid, graph, chart, clock, scale, circuit, photo  # noqa: F401  各种图在 import 时登记

log = logging.getLogger(__name__)

__all__ = ["KINDS", "FigError", "Kind", "register", "normalize", "present", "describe", "prompt_for", "drawable_for", "wanted_for"]


def normalize(raw) -> dict | None:
    """检查、整理一张图的说明；不合格返回 None。"""
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        log.info("配图种类不认识：%r", raw.get("kind") if isinstance(raw, dict) else raw)
        return None
    k = KINDS[raw["kind"]]
    try:
        out = k.normalize(raw)
        if k.drawn:
            k.svg(out)  # 先画一遍，画不出来的现在就知道
            if raw.get("alt"):
                out["alt"] = str(raw["alt"]).strip()[:300]
    except (FigError, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError) as e:
        log.info("配图不合格（%s）：%s", raw.get("kind"), e)
        return None
    return out


@functools.lru_cache(maxsize=512)
def _svg(spec_json: str) -> str:
    spec = json.loads(spec_json)
    return KINDS[spec["kind"]].svg(spec)


def present(item: dict) -> dict | None:
    """给页面用的图：{"kind", "svg" 或 "img", "alt"}。没有图返回 None。"""
    fig = (item or {}).get("fig")
    if not isinstance(fig, dict) or fig.get("kind") not in KINDS:
        return None
    k = KINDS[fig["kind"]]
    out = {"kind": k.id, "alt": fig.get("alt") or k.label}
    if not k.drawn:
        if not item.get("id"):
            return None
        out["img"] = f"/fig/{item['id']}/{fig['file']}"
        return out
    try:
        out["svg"] = _svg(json.dumps(fig, ensure_ascii=False, sort_keys=True))
    except Exception:  # 旧的说明遇到新版画图程序出错：不显示图，也不让整页挂掉
        log.exception("画图失败：%s", item.get("id"))
        return None
    return out


def describe(fig) -> str:
    """给 AI 校对 / 改编看的文字版。"""
    if not isinstance(fig, dict) or fig.get("kind") not in KINDS:
        return ""
    k = KINDS[fig["kind"]]
    s = k.describe(fig)
    return s + (f"；图的说明：{fig['alt']}" if fig.get("alt") and k.drawn else "")


def drawable_for(subject: str) -> list[Kind]:
    return [k for k in KINDS.values() if k.drawn and subject in k.subjects]


def wanted_for(kp: dict, subject: str) -> list[Kind]:
    """这个知识点的题通常要看哪些图（按各种图的 cues 对知识点的名称、说明、关键词）。没有返回 []。"""
    text = " ".join([kp.get("name", ""), kp.get("name_en", ""), kp.get("desc", ""), *kp.get("terms", [])]).lower()
    return [k for k in drawable_for(subject) if any(c.lower() in text for c in k.cues)]


def prompt_for(subject: str) -> str:
    """出题提示词里的配图说明：只列这个学科用得上的图。"""
    kinds = drawable_for(subject)
    if not kinds:
        return ""
    return (
        '\n配图（可选）：题目确实需要看图才好懂（几何图形、坐标图像、电路、受力、刻度读数、钟面、统计图……）时，'
        '在这道题里加 "fig"：{"kind": 种类, …画图说明…, "alt": "一句话描述这张图"}。程序会按说明画出准确的图，'
        "所以坐标、数值必须和题目一致；题干里写「如图」，不要再用文字重复描述图；图上标的数量如果就是答案，不要标出来。"
        "几何图里题目给出的长度、角度、平行、垂直请写进 checks，程序会核对图和题对得上，对不上的题会被丢掉。不需要图的题不要加。\n"
        "各种图的写法：\n" + "\n".join("- " + k.prompt for k in kinds)
    )
