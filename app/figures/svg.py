"""极简 SVG 画布：各种图在屏幕坐标（y 向下）里画，最后按实际用到的范围算 viewBox。
线和字都用 currentColor，跟着页面文字颜色走（深色模式不用另画）。"""
import html
import math

STROKE = 1.6


def _w(s: str, size: float) -> float:
    return sum(size * (1.0 if ord(c) > 0x2e80 else 0.58) for c in s)


class Svg:
    def __init__(self):
        self.parts: list[str] = []
        self.box = [math.inf, math.inf, -math.inf, -math.inf]

    def _grow(self, x, y):
        b = self.box
        b[0], b[1], b[2], b[3] = min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y)

    @staticmethod
    def _f(v: float) -> str:
        return f"{v:.1f}".rstrip("0").rstrip(".")

    def _style(self, dashed=False, width=STROKE, fill=None, stroke=True):
        s = f'stroke="currentColor" stroke-width="{width}"' if stroke else 'stroke="none"'
        if dashed:
            s += ' stroke-dasharray="5 4"'
        if fill is None:
            s += ' fill="none"'
        else:
            s += f' fill="currentColor" fill-opacity="{fill}"'
        return s + ' stroke-linecap="round" stroke-linejoin="round"'

    def line(self, x1, y1, x2, y2, dashed=False, width=STROKE, arrow=False):
        f = self._f
        self._grow(x1, y1), self._grow(x2, y2)
        self.parts.append(f'<line x1="{f(x1)}" y1="{f(y1)}" x2="{f(x2)}" y2="{f(y2)}" {self._style(dashed, width)}/>')
        if arrow:
            self.arrowhead(x1, y1, x2, y2)

    def arrowhead(self, x1, y1, x2, y2, size=9):
        a = math.atan2(y2 - y1, x2 - x1)
        pts = [(x2, y2), (x2 - size * math.cos(a - 0.4), y2 - size * math.sin(a - 0.4)),
               (x2 - size * math.cos(a + 0.4), y2 - size * math.sin(a + 0.4))]
        self.poly(pts, fill=1, stroke=False)

    def poly(self, pts, close=True, dashed=False, fill=None, width=STROKE, stroke=True):
        for x, y in pts:
            self._grow(x, y)
        d = " ".join(f"{self._f(x)},{self._f(y)}" for x, y in pts)
        tag = "polygon" if close else "polyline"
        self.parts.append(f'<{tag} points="{d}" {self._style(dashed, width, fill, stroke)}/>')

    def circle(self, cx, cy, r, dashed=False, fill=None, width=STROKE, stroke=True):
        self._grow(cx - r, cy - r), self._grow(cx + r, cy + r)
        f = self._f
        self.parts.append(f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}" {self._style(dashed, width, fill, stroke)}/>')

    def arc(self, cx, cy, rx, ry, a0, a1, dashed=False, width=STROKE):
        """椭圆弧，角度用度、屏幕坐标下逆时针为正（和数学习惯一致）。"""
        n = max(4, int(abs(a1 - a0) / 6))
        pts = [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy - ry * math.sin(math.radians(a0 + (a1 - a0) * i / n)))
               for i in range(n + 1)]
        self.poly(pts, close=False, dashed=dashed, width=width)

    def rect(self, x, y, w, h, fill=None, dashed=False, width=STROKE, stroke=True):
        self._grow(x, y), self._grow(x + w, y + h)
        f = self._f
        self.parts.append(f'<rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{f(h)}" {self._style(dashed, width, fill, stroke)}/>')

    def text(self, x, y, s, size=13, anchor="middle", italic=False, bold=False):
        s = str(s)
        if not s:
            return
        w = _w(s, size)
        x0 = {"start": x, "middle": x - w / 2, "end": x - w}[anchor]
        self._grow(x0, y - size * 0.6), self._grow(x0 + w, y + size * 0.6)
        f = self._f
        st = (' font-style="italic"' if italic else "") + (' font-weight="600"' if bold else "")
        self.parts.append(f'<text x="{f(x)}" y="{f(y)}" font-size="{size}" text-anchor="{anchor}" dominant-baseline="central" '
                          f'fill="currentColor" stroke="none"{st}>{html.escape(s)}</text>')

    def out(self, pad=8, max_w=420) -> str:
        x0, y0, x1, y1 = self.box
        if not math.isfinite(x0):
            x0 = y0 = 0
            x1 = y1 = 10
        x0, y0, w, h = x0 - pad, y0 - pad, (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad
        f = self._f
        k = min(1.0, max_w / w) if w else 1
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{f(x0)} {f(y0)} {f(w)} {f(h)}" width="{f(w * k)}" '
                f'height="{f(h * k)}" font-family="system-ui, sans-serif" role="img">' + "".join(self.parts) + "</svg>")
