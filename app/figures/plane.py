"""平面图：几何图形、方格纸上的图形、受力示意图、光路图。
点先命名（A、B、O…），图形引用点名，也可以直接写坐标 [x, y]。"""
import math

from .base import FigError, Kind, close, label, lst, num, register, text
from .svg import Svg

SHAPES = ("segment", "arrow", "line", "ray", "polygon", "circle", "arc", "angle", "text", "rect", "surface", "lens")


def _pt(ref, points, what="点"):
    if isinstance(ref, str):
        if ref not in points:
            raise FigError(f"没有定义{what} {ref}")
        return ref
    if isinstance(ref, list) and len(ref) == 2:
        return [num(ref[0], "坐标"), num(ref[1], "坐标")]
    raise FigError(f"{what}写法不对：{ref!r}")


def angle_at(a, b, c) -> float:
    """∠abc（度），平面、空间都能用。"""
    v1, v2 = [x - y for x, y in zip(a, b)], [x - y for x, y in zip(c, b)]
    n = math.hypot(*v1) * math.hypot(*v2)
    if n == 0:
        raise FigError("角的两边有一条长度为 0")
    return math.degrees(math.acos(max(-1, min(1, sum(x * y for x, y in zip(v1, v2)) / n))))


def check_facts(checks, at, dist=math.dist, angle=angle_at) -> list:
    """核对题目里说的数量和图对得上（at：点名 → 坐标；dist / angle：怎么算）。"""
    out = []
    for c in lst(checks, "checks", 30):
        if not isinstance(c, dict):
            raise FigError("checks 写法不对")
        if "dist" in c:
            p, q = c["dist"]
            want = num(c.get("is"), "checks 的长度")
            if not close(dist(at(p), at(q)), want):
                raise FigError(f"{p}{q} 在图上是 {dist(at(p), at(q)):.3g}，题目说是 {want:g}")
        elif "angle" in c:
            a, b, cc = c["angle"]
            want = num(c.get("is"), "checks 的角度")
            got = angle(at(a), at(b), at(cc))
            if abs(got - want) > 1.0:
                raise FigError(f"∠{a}{b}{cc} 在图上是 {got:.1f}°，题目说是 {want:g}°")
        elif "parallel" in c or "perp" in c:
            (a, b), (p, q) = c.get("parallel") or c.get("perp")
            u = [x - y for x, y in zip(at(b), at(a))]
            v = [x - y for x, y in zip(at(q), at(p))]
            nu, nv = math.sqrt(sum(x * x for x in u)), math.sqrt(sum(x * x for x in v))
            if not nu or not nv:
                raise FigError("checks 里有长度为 0 的线段")
            cos = abs(sum(x * y for x, y in zip(u, v))) / nu / nv
            if ("parallel" in c and cos < 0.999) or ("perp" in c and cos > 0.02):
                raise FigError(f"{a}{b} 和 {p}{q} {'不平行' if 'parallel' in c else '不垂直'}")
        else:
            raise FigError("checks 只支持 dist / angle / parallel / perp")
        out.append(c)
    return out


@register
class Plane(Kind):
    id, label = "plane", "平面图"
    subjects = ("math", "physics", "science", "chemistry")
    cues = ("三角", "四边形", "平行四边形", "梯形", "圆", "角度", "角的", "垂直", "平行线", "对称", "平移", "旋转", "周长", "面积", "勾股", "相似", "全等",
            "反射", "折射", "平面镜", "透镜", "受力", "力的示意", "杠杆", "滑轮", "斜面", "triangle", "angle", "circle", "polygon",
            "reflection", "refraction", "lens", "force diagram", "lever", "moment", "pulley")
    prompt = (
        'plane 平面图（几何、方格图形、受力图、光路图）：{"kind":"plane","grid":false,"axes":false,'
        '"points":{"A":[0,0],"B":[4,0],"C":[4,3]},"shapes":[{"type":"polygon","points":["A","B","C"]},'
        '{"type":"angle","at":"B","from":"A","to":"C"},{"type":"segment","from":"A","to":"C","label":"5 cm","dashed":false}],'
        '"checks":[{"dist":["A","C"],"is":5},{"angle":["A","B","C"],"is":90}]}。'
        "shapes 的 type：segment / arrow（带箭头，力、光线）/ line（直线）/ ray / polygon（fill:true 涂浅色）/ "
        "circle（center,r）/ arc（center,r,start,end 度）/ angle（at,from,to,label，直角自动画小方块）/ text（at,text）/ "
        "rect（at 左下角,w,h,label：木块、物体）/ surface（from,to：带斜线的地面、墙、镜面）/ lens（at,h,lens:convex|concave）。"
        "grid:true 画方格纸（每格 1）；axes:true 画 x、y 轴。点名以 _ 开头不显示名字。"
    )

    def normalize(self, spec):
        pts = {}
        for k, v in (spec.get("points") or {}).items():
            if not isinstance(v, list) or len(v) != 2:
                raise FigError(f"点 {k} 的坐标写法不对")
            pts[label(k)] = [num(v[0], f"点 {k} 的坐标"), num(v[1], f"点 {k} 的坐标")]
        if len(pts) > 40:
            raise FigError("点太多")
        shapes = []
        for s in lst(spec.get("shapes"), "shapes"):
            if not isinstance(s, dict) or s.get("type") not in SHAPES:
                raise FigError(f"不认识的图形：{s!r}")
            t = s["type"]
            o = {"type": t}
            for key in ("from", "to", "at", "center"):
                if key in s:
                    o[key] = _pt(s[key], pts)
            if t == "ray" and "through" in s:
                o["through"] = _pt(s["through"], pts)
            if t == "line":
                thr = s.get("through")
                if not (isinstance(thr, list) and len(thr) == 2):
                    raise FigError("line 的 through 要两个点")
                o["through"] = [_pt(x, pts) for x in thr]
            if t == "polygon":
                o["points"] = [_pt(p, pts) for p in lst(s.get("points"), "多边形的点", 30)]
                if len(o["points"]) < 3:
                    raise FigError("多边形至少 3 个点")
                o["fill"] = bool(s.get("fill"))
            if t in ("circle", "arc"):
                o["r"] = num(s.get("r"), "半径", 0.001)
            if t == "arc":
                o["start"], o["end"] = num(s.get("start", 0), "角度", -720, 720), num(s.get("end", 360), "角度", -720, 720)
            if t == "rect":
                o["w"], o["h"] = num(s.get("w"), "宽", 0.001), num(s.get("h"), "高", 0.001)
            if t == "lens":
                o["h"] = num(s.get("h", 3), "透镜高度", 0.001)
                o["lens"] = "concave" if s.get("lens") == "concave" else "convex"
            if t == "text":
                o["text"] = text(s.get("text"))
            need = {"segment": ("from", "to"), "arrow": ("from", "to"), "surface": ("from", "to"), "ray": ("from", "through"),
                    "line": ("through",), "circle": ("center",), "arc": ("center",), "angle": ("at", "from", "to"),
                    "text": ("at",), "rect": ("at",), "lens": ("at",)}.get(t, ())
            miss = [k for k in need if k not in o]
            if miss:
                raise FigError(f"{t} 少了 {', '.join(miss)}")
            if s.get("label"):
                o["label"] = label(s["label"])
            if s.get("dashed"):
                o["dashed"] = True
            shapes.append(o)
        if not pts and not shapes:
            raise FigError("平面图是空的")
        out = {"kind": self.id, "points": pts, "shapes": shapes}
        for flag in ("grid", "axes"):
            if spec.get(flag):
                out[flag] = True
        def at(r):
            r = _pt(r, pts)
            return pts[r] if isinstance(r, str) else r
        out["checks"] = check_facts(spec.get("checks"), at)
        return out

    # ------------------------------------------------------------ 画
    def svg(self, spec):
        pts = spec["points"]
        P = lambda r: pts[r] if isinstance(r, str) else r  # noqa: E731
        xs, ys = [], []
        for p in pts.values():
            xs.append(p[0]), ys.append(p[1])
        for s in spec["shapes"]:
            for key in ("from", "to", "at", "center"):
                if key in s:
                    p = P(s[key])
                    xs.append(p[0]), ys.append(p[1])
            if s["type"] in ("circle", "arc"):
                c = P(s["center"])
                xs += [c[0] - s["r"], c[0] + s["r"]]
                ys += [c[1] - s["r"], c[1] + s["r"]]
            if s["type"] == "rect":
                a = P(s["at"])
                xs.append(a[0] + s["w"]), ys.append(a[1] + s["h"])
            if s["type"] == "lens":
                a = P(s["at"])
                ys += [a[1] - s["h"] / 2, a[1] + s["h"] / 2]
            for p in s.get("points", []):
                p = P(p)
                xs.append(p[0]), ys.append(p[1])
        if spec.get("axes"):
            xs.append(0), ys.append(0)
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if spec.get("grid") or spec.get("axes"):
            x0, x1, y0, y1 = math.floor(x0) - (1 if spec.get("axes") else 0), math.ceil(x1) + 1, math.floor(y0) - 1, math.ceil(y1) + 1
        w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        k = min(340 / w, 260 / h, 60 if not spec.get("grid") else 34)
        X = lambda p: (P(p)[0] - x0) * k  # noqa: E731
        Y = lambda p: (y1 - P(p)[1]) * k  # noqa: E731
        S = lambda p: (X(p), Y(p))  # noqa: E731
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        g = Svg()
        if spec.get("grid"):
            for i in range(int(x0), int(x1) + 1):
                g.line((i - x0) * k, 0, (i - x0) * k, (y1 - y0) * k, width=0.6)
            for j in range(int(y0), int(y1) + 1):
                g.line(0, (y1 - j) * k, (x1 - x0) * k, (y1 - j) * k, width=0.6)
        if spec.get("axes"):
            g.line(0, Y([0, 0]), (x1 - x0) * k, Y([0, 0]), arrow=True, width=1.2)
            g.line(X([0, 0]), (y1 - y0) * k, X([0, 0]), 0, arrow=True, width=1.2)
            g.text((x1 - x0) * k - 4, Y([0, 0]) + 12, "x", italic=True)
            g.text(X([0, 0]) + 10, 6, "y", italic=True)
            if "O" not in pts:
                g.text(X([0, 0]) - 9, Y([0, 0]) + 10, "O", italic=True)

        def side_label(a, b, s):
            (ax, ay), (bx, by) = S(a), S(b)
            mx, my = (ax + bx) / 2, (ay + by) / 2
            nx, ny = -(by - ay), bx - ax
            n = math.hypot(nx, ny) or 1
            nx, ny = nx / n, ny / n
            ccx, ccy = S([cx, cy])
            if (mx - ccx) * nx + (my - ccy) * ny < 0:
                nx, ny = -nx, -ny
            g.text(mx + nx * 12, my + ny * 12, s)

        def extend(a, b):
            (ax, ay), (bx, by) = S(a), S(b)
            dx, dy = bx - ax, by - ay
            n = math.hypot(dx, dy) or 1
            L = (w + h) * k
            return ax + dx / n * L, ay + dy / n * L, dx / n, dy / n

        for s in spec["shapes"]:
            t, dashed = s["type"], s.get("dashed", False)
            if t in ("segment", "arrow"):
                (ax, ay), (bx, by) = S(s["from"]), S(s["to"])
                g.line(ax, ay, bx, by, dashed=dashed, arrow=t == "arrow", width=2 if t == "arrow" else 1.6)
                if s.get("label"):
                    side_label(s["from"], s["to"], s["label"])
            elif t in ("line", "ray"):
                a, b = (s["through"] if t == "line" else (s["from"], s["through"]))
                ex, ey, _, _ = extend(a, b)
                bx0, by0 = S(a)
                if t == "line":
                    ex2, ey2, _, _ = extend(b, a)
                    bx0, by0 = ex2, ey2
                g.line(bx0, by0, ex, ey, dashed=dashed)
            elif t == "polygon":
                g.poly([S(p) for p in s["points"]], dashed=dashed, fill=0.08 if s.get("fill") else None)
            elif t == "circle":
                c = S(s["center"])
                g.circle(c[0], c[1], s["r"] * k, dashed=dashed)
            elif t == "arc":
                c = S(s["center"])
                g.arc(c[0], c[1], s["r"] * k, s["r"] * k, s["start"], s["end"], dashed=dashed)
            elif t == "angle":
                b = P(s["at"])
                a1 = math.degrees(math.atan2(P(s["from"])[1] - b[1], P(s["from"])[0] - b[0]))
                a2 = math.degrees(math.atan2(P(s["to"])[1] - b[1], P(s["to"])[0] - b[0]))
                d = (a2 - a1) % 360
                if d > 180:
                    a1, a2, d = a2, a1, 360 - d
                bx, by = S(b)
                r = 16
                if abs(d - 90) < 0.5:
                    u = [math.cos(math.radians(a1)), -math.sin(math.radians(a1))]
                    v = [math.cos(math.radians(a1 + 90)), -math.sin(math.radians(a1 + 90))]
                    r = 10
                    g.poly([(bx + u[0] * r, by + u[1] * r), (bx + (u[0] + v[0]) * r, by + (u[1] + v[1]) * r),
                            (bx + v[0] * r, by + v[1] * r)], close=False, width=1.2)
                else:
                    g.arc(bx, by, r, r, a1, a1 + d, width=1.2)
                if s.get("label"):
                    m = math.radians(a1 + d / 2)
                    g.text(bx + math.cos(m) * (r + 14), by - math.sin(m) * (r + 14), s["label"], size=12)
            elif t == "text":
                x, y = S(s["at"])
                g.text(x, y, s["text"])
            elif t == "rect":
                a = P(s["at"])
                x, y = S([a[0], a[1] + s["h"]])
                g.rect(x, y, s["w"] * k, s["h"] * k, fill=0.06)
                if s.get("label"):
                    g.text(x + s["w"] * k / 2, y + s["h"] * k / 2, s["label"])
            elif t == "surface":
                (ax, ay), (bx, by) = S(s["from"]), S(s["to"])
                g.line(ax, ay, bx, by, width=2)
                L = math.hypot(bx - ax, by - ay) or 1
                ux, uy = (bx - ax) / L, (by - ay) / L
                nx, ny = uy, -ux  # 沿 from→to 方向的右手边画斜线
                n = int(L // 10)
                for i in range(n + 1):
                    px, py = ax + ux * i * 10, ay + uy * i * 10
                    g.line(px, py, px + nx * 7 - ux * 5, py + ny * 7 - uy * 5, width=1)
                if s.get("label"):
                    side_label(s["from"], s["to"], s["label"])
            elif t == "lens":
                x, y = S(s["at"])
                hh = s["h"] * k / 2
                g.line(x, y - hh, x, y + hh, width=2)
                sgn = 1 if s["lens"] == "convex" else -1
                for yy, d in ((y - hh, -1), (y + hh, 1)):
                    g.line(x - 7, yy - sgn * d * 8, x, yy, width=2)
                    g.line(x + 7, yy - sgn * d * 8, x, yy, width=2)
                if s.get("label"):
                    g.text(x, y - hh - 12, s["label"])
        for name, p in pts.items():
            if name.startswith("_"):
                continue
            x, y = S(p)
            g.circle(x, y, 2.2, fill=1, stroke=False)
            ccx, ccy = S([cx, cy])
            dx, dy = x - ccx, y - ccy
            n = math.hypot(dx, dy)
            dx, dy = (dx / n, dy / n) if n > 1 else (0.7, -0.7)
            g.text(x + dx * 13, y + dy * 13, name, italic=len(name) <= 2)
        return g.out()
