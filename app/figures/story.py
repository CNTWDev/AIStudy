"""故事条：知识点是怎么来的。学习页第一步「为什么学」的配图，按时间排开几格（什么时候 · 一个图标 · 一句话）。"""
from .base import FigError, Kind, lst, register, text
from .svg import Svg, _w


def _wrap(s: str, size: float, width: float) -> list[str]:
    lines, cur = [], ""
    for ch in s:
        if cur and _w(cur + ch, size) > width:
            lines.append(cur)
            cur = ""
        cur += ch
    return [*lines, cur] if cur else lines


@register
class Story(Kind):
    id, label = "story", "故事条"
    subjects = ()  # 不进出题提示词：只给「为什么学」的导入用（llm.kp_context）
    prompt = ('story 故事条：{"kind":"story","beats":[{"when":"1790 年","icon":"👑","text":"法国各地的尺子长短不一"},…]}'
              "（2-5 格，按时间先后；when 不超过 8 个字，icon 一个 emoji，text 一句话不超过 24 个字；最后一格可以落到今天的生活）。")

    def normalize(self, spec):
        beats = []
        for b in lst(spec.get("beats"), "beats", most=5):
            if not isinstance(b, dict) or not text(b.get("text")):
                raise FigError("每一格要有 text")
            beats.append({"when": text(b.get("when"), 10), "icon": text(b.get("icon"), 4), "text": text(b.get("text"), 30)})
        if len(beats) < 2:
            raise FigError("故事条至少 2 格")
        return {"kind": self.id, "beats": beats}

    def describe(self, spec):
        return "故事条：" + "；".join(f"{b['when']} {b['text']}" for b in spec["beats"])

    def svg(self, spec):
        g, y, W = Svg(), 0, 300
        beats = spec["beats"]
        for i, b in enumerate(beats):
            lines = _wrap(b["text"], 15, W)
            h = max(64, 30 + 22 * len(lines))
            if i < len(beats) - 1:
                g.line(28, y + 56, 28, y + h + 4, width=2, dashed=True)
            g.circle(28, y + 28, 26, fill=0.08, width=2)
            g.text(28, y + 29, b["icon"] or "•", size=26)
            g.text(70, y + 12, b["when"], size=14, anchor="start", bold=True)
            for j, ln in enumerate(lines):
                g.text(70, y + 36 + 22 * j, ln, size=15, anchor="start")
            y += h + 8
        return g.out(max_w=420)
