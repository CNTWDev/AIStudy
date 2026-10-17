"""刻度读数：刻度尺量物体长度、量筒读液面、温度计读温度。"""
from .base import FigError, Kind, label, num, register
from .svg import Svg

TOOLS = ("ruler", "cylinder", "thermometer")


@register
class Scale(Kind):
    id, label = "scale", "刻度读数"
    subjects = ("math", "physics", "science", "chemistry")
    cues = ("刻度", "读数", "量筒", "温度计", "刻度尺", "测力计", "电流表", "电压表", "measuring cylinder", "thermometer", "ruler", "ammeter", "voltmeter")
    prompt = (
        'scale 刻度读数：{"kind":"scale","tool":"ruler","min":0,"max":6,"major":1,"minor":10,"unit":"cm",'
        '"from":1.0,"to":4.35,"object":"铅笔"}；量筒 {"tool":"cylinder","min":0,"max":50,"major":10,"minor":5,"unit":"mL","value":32}；'
        '温度计 {"tool":"thermometer","min":-10,"max":50,"major":10,"minor":10,"unit":"°C","value":23}。'
        "major 是标数字的大格，minor 是每个大格分几小格。"
    )

    def normalize(self, spec):
        tool = spec.get("tool")
        if tool not in TOOLS:
            raise FigError(f"不认识的量具：{tool!r}")
        lo, hi = num(spec.get("min", 0), "最小刻度"), num(spec.get("max"), "最大刻度")
        major = num(spec.get("major", 1), "大格", 1e-6)
        minor = int(num(spec.get("minor", 10), "小格数", 1, 20))
        if hi <= lo or (hi - lo) / major * minor > 300:
            raise FigError("刻度范围不对或太密")
        out = {"kind": self.id, "tool": tool, "min": lo, "max": hi, "major": major, "minor": minor,
               "unit": label(spec.get("unit"))}
        if tool == "ruler":
            a, b = num(spec.get("from"), "物体起点"), num(spec.get("to"), "物体终点")
            if not lo <= a < b <= hi:
                raise FigError("物体要在尺子量程里")
            out.update({"from": a, "to": b, "object": label(spec.get("object"))})
        else:
            v = num(spec.get("value"), "读数")
            if not lo <= v <= hi:
                raise FigError("读数超出量程")
            out["value"] = v
        return out

    def describe(self, spec):
        if spec["tool"] == "ruler":
            return (f"刻度尺 {spec['min']:g}–{spec['max']:g} {spec['unit']}（每大格 {spec['major']:g}，分 {spec['minor']} 小格），"
                    f"物体从 {spec['from']:g} 量到 {spec['to']:g}")
        name = "量筒液面（凹液面底部）" if spec["tool"] == "cylinder" else "温度计液柱"
        return (f"{name}：刻度 {spec['min']:g}–{spec['max']:g} {spec['unit']}（每大格 {spec['major']:g}，分 {spec['minor']} 小格），"
                f"指在 {spec['value']:g}")

    def svg(self, spec):
        lo, hi, major, minor = spec["min"], spec["max"], spec["major"], spec["minor"]
        n = round((hi - lo) / major * minor)
        g = Svg()
        if spec["tool"] == "ruler":
            L = min(360, max(220, n * 6))
            px = lambda v: (v - lo) / (hi - lo) * L  # noqa: E731
            g.rect(-10, 0, L + 20, 46, fill=0.05)
            for i in range(n + 1):
                v = lo + i * major / minor
                big = i % minor == 0
                half = minor % 2 == 0 and i % (minor // 2) == 0
                g.line(px(v), 0, px(v), 16 if big else (11 if half else 7), width=1.2 if big else 0.8)
                if big:
                    g.text(px(v), 26, f"{v:g}", size=11)
            g.text(L + 2, 38, spec["unit"], size=11, anchor="end")
            a, b = px(spec["from"]), px(spec["to"])
            g.rect(a, -20, b - a, 18, fill=0.25)
            g.line(a, -20, a, 0, dashed=True, width=0.8)
            g.line(b, -20, b, 0, dashed=True, width=0.8)
            if spec.get("object"):
                g.text((a + b) / 2, -30, spec["object"], size=12)
            return g.out()
        L = min(300, max(200, n * 5))
        py = lambda v: L - (v - lo) / (hi - lo) * L  # noqa: E731
        y = py(spec["value"])
        if spec["tool"] == "cylinder":
            w = 56
            g.line(0, -14, 0, L + 8), g.line(w, -14, w, L + 8)
            g.line(-14, L + 8, w + 14, L + 8, width=2)
            g.poly([(0, y - 4), (w / 2, y + 3), (w, y - 4), (w, L + 8), (0, L + 8)], fill=0.18, stroke=False)
            g.arc(w / 2, y - 4, w / 2, 7, 180, 360, width=1.4)  # 凹液面
            for i in range(n + 1):
                v = lo + i * major / minor
                big = i % minor == 0
                g.line(0, py(v), 14 if big else 7, py(v), width=1.2 if big else 0.8)
                if big:
                    g.text(18, py(v), f"{v:g}", size=10, anchor="start")
            g.text(w / 2, -24, spec["unit"], size=11)
        else:
            w = 14
            g.rect(0, -10, w, L + 12, width=1.4)
            g.circle(w / 2, L + 14, 11, width=1.4)
            g.circle(w / 2, L + 14, 8, fill=0.7, stroke=False)
            g.rect(w / 2 - 3, y, 6, L + 8 - y, fill=0.7, stroke=False)
            for i in range(n + 1):
                v = lo + i * major / minor
                big = i % minor == 0
                half = minor % 2 == 0 and i % (minor // 2) == 0
                g.line(w, py(v), w + (12 if big else (8 if half else 5)), py(v), width=1.2 if big else 0.8)
                if big:
                    g.text(w + 16, py(v), f"{v:g}", size=10, anchor="start")
            g.text(w / 2, -20, spec["unit"], size=11)
        return g.out()
