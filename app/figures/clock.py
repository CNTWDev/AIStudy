"""钟面：认读时间。"""
import math
import re

from .base import FigError, Kind, register
from .svg import Svg


@register
class Clock(Kind):
    id, label = "clock", "钟面"
    subjects = ("math",)
    prompt = 'clock 钟面：{"kind":"clock","time":"3:45","numbers":true}（time 用 12 小时制 时:分，可带 :秒；numbers:false 不写数字）。'

    def normalize(self, spec):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", str(spec.get("time") or ""))
        if not m:
            raise FigError("time 要写成 时:分")
        h, mi = int(m[1]), int(m[2])
        if not (0 <= h <= 23 and 0 <= mi < 60 and (m[3] is None or int(m[3]) < 60)):
            raise FigError("时间不对")
        out = {"kind": self.id, "time": f"{h}:{mi:02d}" + (f":{int(m[3]):02d}" if m[3] else ""),
               "numbers": spec.get("numbers") is not False}
        return out

    def describe(self, spec):
        return f"钟面：时针、分针指向 {spec['time']}"

    def svg(self, spec):
        parts = [int(x) for x in spec["time"].split(":")]
        h, m, s = parts[0] % 12, parts[1], parts[2] if len(parts) > 2 else None
        R, g = 90, Svg()
        g.circle(0, 0, R, width=2.4)
        for i in range(60):
            a = math.radians(i * 6)
            r0 = R - (9 if i % 5 == 0 else 4)
            g.line(r0 * math.sin(a), -r0 * math.cos(a), R * math.sin(a), -R * math.cos(a), width=2 if i % 5 == 0 else 1)
        if spec["numbers"]:
            for i in range(1, 13):
                a = math.radians(i * 30)
                g.text((R - 21) * math.sin(a), -(R - 21) * math.cos(a), str(i), size=15, bold=True)

        def hand(deg, length, width):
            a = math.radians(deg)
            g.line(0, 0, length * math.sin(a), -length * math.cos(a), width=width)
        hand((h + m / 60) * 30, R * 0.5, 4.5)
        hand(m * 6 + (s or 0) / 10, R * 0.75, 3)
        if s is not None:
            hand(s * 6, R * 0.82, 1)
        g.circle(0, 0, 4, fill=1)
        return g.out()
