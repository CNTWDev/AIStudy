"""统计图：条形统计图、折线统计图（可以几组数据放一起比）。"""
import math

from .base import FigError, Kind, label, lst, num, register, text
from .svg import Svg


@register
class Chart(Kind):
    id, label = "chart", "统计图"
    subjects = ("math", "science", "geography", "biology", "economics")
    prompt = (
        'chart 统计图：{"kind":"chart","style":"bar|line","title":"三年级各班借书数量","y_label":"本",'
        '"categories":["一班","二班","三班"],"series":[{"name":"上学期","values":[30,45,25]}],"step":10}。'
        "style 是 bar 条形图或 line 折线图；series 可以有 1-3 组。"
    )

    def normalize(self, spec):
        cats = [label(c) for c in lst(spec.get("categories"), "categories", 16)]
        if len(cats) < 2:
            raise FigError("统计图至少两类")
        series = []
        for s in lst(spec.get("series"), "series", 3):
            vals = [num(v, "数据", 0, 1e7) for v in lst(s.get("values") if isinstance(s, dict) else None, "values", 16)]
            if len(vals) != len(cats):
                raise FigError("数据个数和类别个数对不上")
            series.append({"name": label(s.get("name")), "values": vals})
        if not series:
            raise FigError("统计图没有数据")
        out = {"kind": self.id, "style": "line" if spec.get("style") == "line" else "bar", "categories": cats,
               "series": series, "title": text(spec.get("title")), "y_label": label(spec.get("y_label"))}
        if spec.get("step"):
            out["step"] = num(spec["step"], "刻度", 1e-6)
        return out

    def svg(self, spec):
        cats, series = spec["categories"], spec["series"]
        top = max(max(s["values"]) for s in series) or 1
        step = spec.get("step") or 10 ** math.floor(math.log10(top)) / (2 if top / 10 ** math.floor(math.log10(top)) < 3 else 1)
        while top / step > 12:
            step *= 2
        ymax = math.ceil(top / step) * step
        W, H = max(220, 56 * len(cats)), 190
        py = lambda v: H - v / ymax * H  # noqa: E731
        g = Svg()
        if spec["title"]:
            g.text(W / 2, -30, spec["title"], size=13, bold=True)
        v = 0
        while v <= ymax + 1e-9:
            g.line(0, py(v), W, py(v), width=0.5, dashed=v > 0)
            g.text(-6, py(v), f"{v:g}", size=11, anchor="end")
            v += step
        g.line(0, H, 0, -8, width=1.4)
        if spec["y_label"]:
            g.text(0, -18, spec["y_label"], size=11)
        bw = W / len(cats)
        n = len(series)
        for i, c in enumerate(cats):
            g.text(bw * (i + 0.5), H + 13, c, size=11)
        for j, s in enumerate(series):
            fill = (0.75, 0.35, 0.12)[j]
            if spec["style"] == "bar":
                w = bw * 0.6 / n
                for i, val in enumerate(s["values"]):
                    x = bw * i + bw * 0.2 + w * j
                    g.rect(x, py(val), w, H - py(val), fill=fill, width=1)
                    g.text(x + w / 2, py(val) - 8, f"{val:g}", size=10)
            else:
                pts = [(bw * (i + 0.5), py(val)) for i, val in enumerate(s["values"])]
                g.poly(pts, close=False, width=2, dashed=j == 1)
                for (x, y), val in zip(pts, s["values"]):
                    g.circle(x, y, 3, fill=1 if j != 1 else None)
                    g.text(x, y - 11, f"{val:g}", size=10)
        if n > 1:  # 图例放在类别名下面
            x = 0
            for j, s in enumerate(series):
                y = H + 34
                if spec["style"] == "bar":
                    g.rect(x, y - 5, 10, 10, fill=(0.75, 0.35, 0.12)[j], width=1)
                else:
                    g.line(x - 2, y, x + 12, y, dashed=j == 1, width=2)
                g.text(x + 16, y, s["name"], size=11, anchor="start")
                x += 36 + 12 * len(s["name"])
        g.line(0, H, W, H, width=1.4)
        return g.out()
