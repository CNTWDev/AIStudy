"""立体图形：正方体、长方体、棱柱、棱锥（斜二测画法，看不见的棱自动画虚线），圆柱、圆锥、球。
坐标：x 向右、y 向里（纵深）、z 向上。也可以直接给 vertices + faces 画任意凸多面体。"""
import math

from .base import FigError, Kind, label, lst, num, register
from .plane import check_facts
from .svg import Svg

SHAPES = ("cube", "cuboid", "prism", "pyramid", "cylinder", "cone", "sphere", "custom")
COS, SIN, K = math.cos(math.radians(45)), math.sin(math.radians(45)), 0.5
VIEW = (-K * COS, 1.0, -K * SIN)  # 斜二测的投影方向：面的外法线和它点积 < 0 就是朝向我们的面
SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _letters(n):
    return [chr(ord("A") + i) for i in range(n)]


def _poly_base(n, a, b=None):
    """底面多边形（z=0），A 在左前方，逆时针（从上往下看）。"""
    if n == 4 and b is not None:
        return [(0, 0), (a, 0), (a, b), (0, b)]
    R = a / (2 * math.sin(math.pi / n))
    out = []
    for i in range(n):
        f = math.radians(-90 - 180 / n + i * 360 / n)
        out.append((R * math.cos(f), R * math.sin(f)))
    mx, my = min(p[0] for p in out), min(p[1] for p in out)
    return [(x - mx, y - my) for x, y in out]


def build(spec) -> tuple[dict, list]:
    """说明 → (顶点名 → 坐标, 面列表)。"""
    shape = spec["shape"]
    if shape == "custom":
        return spec["vertices"], spec["faces"]
    n = spec.get("n", 4)
    a = spec.get("a", 2)
    if shape in ("cube", "cuboid", "prism"):
        if shape == "cube":
            base, h = _poly_base(4, a, a), a
        elif shape == "cuboid":
            base, h = _poly_base(4, a, spec.get("b", a)), spec.get("c", a)
        else:
            base, h = _poly_base(n, a, spec.get("b") if n == 4 else None), spec.get("h", a)
        names = _letters(len(base))
        verts = {nm: [x, y, 0.0] for nm, (x, y) in zip(names, base)}
        verts.update({nm + "1": [x, y, h] for nm, (x, y) in zip(names, base)})
        m = len(base)
        faces = [names[::-1], [nm + "1" for nm in names]]
        faces += [[names[i], names[(i + 1) % m], names[(i + 1) % m] + "1", names[i] + "1"] for i in range(m)]
    else:  # pyramid
        base = _poly_base(n, a, spec.get("b") if n == 4 else None)
        names = _letters(len(base))
        verts = {nm: [x, y, 0.0] for nm, (x, y) in zip(names, base)}
        apex = spec.get("apex")
        h = spec.get("h", a)
        if isinstance(apex, str) and apex in verts:
            top = [verts[apex][0], verts[apex][1], h]
        elif isinstance(apex, list):
            top = apex
        else:
            top = [sum(p[0] for p in base) / len(base), sum(p[1] for p in base) / len(base), h]
        verts["P"] = top
        m = len(base)
        faces = [names[::-1]] + [[names[i], names[(i + 1) % m], "P"] for i in range(m)]
    ren = spec.get("labels") or []
    if ren:
        old = list(verts)
        mp = {o: label(r) for o, r in zip(old, ren)}
        verts = {mp.get(k, k): v for k, v in verts.items()}
        faces = [[mp.get(k, k) for k in f] for f in faces]
    return verts, faces


def _normal(pts):
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = pts[0], pts[1], pts[2]
    u, v = (bx - ax, by - ay, bz - az), (cx - ax, cy - ay, cz - az)
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


@register
class Solid(Kind):
    id, label = "solid", "立体图形"
    subjects = ("math",)
    cues = ("立体", "长方体", "正方体", "圆柱", "圆锥", "棱柱", "棱锥", "球", "体积", "表面积", "三视图", "展开图", "cuboid", "cube", "prism", "cylinder", "cone", "pyramid", "volume", "surface area")
    prompt = (
        'solid 立体图形：{"kind":"solid","shape":"cube|cuboid|prism|pyramid|cylinder|cone|sphere","a":2,'
        '"points":{"M":{"mid":["A","B"]},"N":{"on":["A","C1"],"t":0.5}},"segments":[["A","C1"],["M","D1"]],'
        '"checks":[{"dist":["A","C1"],"is":3.464}]}。'
        "cube 棱长 a；cuboid 长 a 宽 b 高 c；prism 底面正 n 边形边长 a（n=4 时可给 b 成矩形）高 h；"
        "pyramid 底面同上、高 h、顶点 P 默认在底面中心正上方，\"apex\":\"A\" 表示 P 在 A 正上方（PA⊥底面）；"
        "顶点名默认：底面 A B C D…（A 在左前，逆时针），顶面 A1 B1 C1 D1…；labels 可以按这个顺序改名。"
        "cylinder / cone / sphere 用 r、h，中心点 O、顶面中心 O1、锥顶 P。看不见的棱自动画虚线；"
        "segments 里的线默认按是否被挡住自动虚实，也可写 {\"from\":\"A\",\"to\":\"C1\",\"dashed\":true}。"
    )

    def normalize(self, spec):
        shape = spec.get("shape")
        if shape not in SHAPES:
            raise FigError(f"不认识的立体：{shape!r}")
        out = {"kind": self.id, "shape": shape}
        for k in ("a", "b", "c", "h", "r"):
            if k in spec:
                out[k] = num(spec[k], k, 0.001, 100)
        if shape in ("prism", "pyramid"):
            out["n"] = int(num(spec.get("n", 4), "n", 3, 12))
        if shape == "pyramid" and spec.get("apex") is not None:
            ap = spec["apex"]
            out["apex"] = [num(x, "顶点坐标") for x in ap] if isinstance(ap, list) and len(ap) == 3 else label(ap)
        if spec.get("labels"):
            out["labels"] = [label(x) for x in lst(spec["labels"], "labels", 30)]
        if shape == "custom":
            vs = {}
            for k, v in (spec.get("vertices") or {}).items():
                if not (isinstance(v, list) and len(v) == 3):
                    raise FigError(f"顶点 {k} 要三个坐标")
                vs[label(k)] = [num(x, f"顶点 {k}") for x in v]
            faces = [[label(x) for x in lst(f, "面", 12)] for f in lst(spec.get("faces"), "faces", 30)]
            if len(vs) < 4 or len(faces) < 4 or any(len(f) < 3 or any(x not in vs for x in f) for f in faces):
                raise FigError("custom 立体要至少 4 个顶点、4 个面，面里的点都要定义")
            out["vertices"], out["faces"] = vs, faces
        verts = self._verts(out)
        pts = {}
        for k, v in (spec.get("points") or {}).items():
            k = label(k)
            if isinstance(v, list) and len(v) == 3:
                pts[k] = {"at": [num(x, f"点 {k}") for x in v]}
            elif isinstance(v, dict) and isinstance(v.get("mid"), list) and len(v["mid"]) == 2:
                pts[k] = {"mid": [label(x) for x in v["mid"]]}
            elif isinstance(v, dict) and isinstance(v.get("on"), list) and len(v["on"]) == 2:
                pts[k] = {"on": [label(x) for x in v["on"]], "t": num(v.get("t", 0.5), "t", 0, 1)}
            else:
                raise FigError(f"点 {k} 的写法不对")
            for ref in pts[k].get("mid", []) + pts[k].get("on", []):
                if ref not in verts and ref not in pts:
                    raise FigError(f"点 {k} 用到的 {ref} 没有定义")
            verts[k] = self._resolve(pts[k], verts)
        if pts:
            out["points"] = pts
        segs = []
        for s in lst(spec.get("segments"), "segments", 30):
            if isinstance(s, list) and len(s) == 2:
                s = {"from": s[0], "to": s[1]}
            if not isinstance(s, dict) or s.get("from") not in verts or s.get("to") not in verts:
                raise FigError(f"线段 {s!r} 的端点没有定义")
            o = {"from": s["from"], "to": s["to"]}
            if "dashed" in s:
                o["dashed"] = bool(s["dashed"])
            segs.append(o)
        if segs:
            out["segments"] = segs

        def at(r):
            if r not in verts:
                raise FigError(f"checks 里的点 {r} 没有定义")
            return verts[r]
        out["checks"] = check_facts(spec.get("checks"), at)
        return out

    @staticmethod
    def _resolve(p, verts):
        if "at" in p:
            return p["at"]
        if "mid" in p:
            a, b = (verts[x] for x in p["mid"])
            return [(x + y) / 2 for x, y in zip(a, b)]
        a, b = (verts[x] for x in p["on"])
        return [x + (y - x) * p["t"] for x, y in zip(a, b)]

    def _verts(self, spec) -> dict:
        if spec["shape"] in ("cylinder", "cone", "sphere"):
            r, h = spec.get("r", 1), spec.get("h", 2)
            v = {"O": [0.0, 0.0, 0.0]}
            if spec["shape"] == "cylinder":
                v["O1"] = [0.0, 0.0, h]
            elif spec["shape"] == "cone":
                v["P"] = [0.0, 0.0, h]
            return v
        return {k: list(p) for k, p in build(spec)[0].items()}

    def svg(self, spec):
        curved = spec["shape"] in ("cylinder", "cone", "sphere")
        verts = self._verts(spec)
        for k, p in (spec.get("points") or {}).items():
            verts[k] = self._resolve(p, verts)
        if curved:
            proj = lambda p: (p[0], p[2] + 0.35 * p[1])  # noqa: E731  圆画成扁椭圆
        else:
            proj = lambda p: (p[0] + K * p[1] * COS, p[2] + K * p[1] * SIN)  # noqa: E731
        scr = {}
        allp = list(verts.values())
        if curved:
            r = spec.get("r", 1)
            allp += [[-r, 0, 0], [r, 0, 0], [0, -r, 0], [0, r, 0]] + ([[0, 0, r], [0, 0, -r]] if spec["shape"] == "sphere" else [])
        xs = [proj(p)[0] for p in allp]
        ys = [proj(p)[1] for p in allp]
        k = min(260 / max(max(xs) - min(xs), 1e-6), 230 / max(max(ys) - min(ys), 1e-6))
        x0, y1 = min(xs), max(ys)
        S = lambda p: ((proj(p)[0] - x0) * k, (y1 - proj(p)[1]) * k)  # noqa: E731
        for nm, p in verts.items():
            scr[nm] = S(p)
        g = Svg()
        faces_vis = []
        if curved:
            self._curved(g, spec, S, k)
        else:
            _, faces = build(spec)
            vis = {}
            for f in faces:
                pts = [verts[x] for x in f]
                n = _normal(pts)
                c = [sum(p[i] for p in pts) / len(pts) for i in range(3)]
                mid = [sum(p[i] for p in verts.values()) / len(verts) for i in range(3)]
                if _dot(n, [c[i] - mid[i] for i in range(3)]) < 0:  # 统一成朝外的法线
                    n = tuple(-x for x in n)
                vis[tuple(f)] = _dot(n, VIEW) < -1e-9
                if vis[tuple(f)]:
                    faces_vis.append((f, n, pts[0]))
            edges = {}
            for f, v in vis.items():
                for i in range(len(f)):
                    e = tuple(sorted((f[i], f[(i + 1) % len(f)])))
                    edges[e] = edges.get(e, False) or v
            for (a, b), v in edges.items():
                g.line(*scr[a], *scr[b], dashed=not v)
        for s in spec.get("segments") or []:
            a, b = s["from"], s["to"]
            dashed = s["dashed"] if "dashed" in s else (not curved and not self._on_visible(verts[a], verts[b], faces_vis))
            g.line(*scr[a], *scr[b], dashed=dashed)
        cx = sum(p[0] for p in scr.values()) / len(scr)
        cy = sum(p[1] for p in scr.values()) / len(scr)
        for nm, (x, y) in scr.items():
            if nm.startswith("_"):
                continue
            g.circle(x, y, 2, fill=1, stroke=False)
            dx, dy = x - cx, y - cy
            n = math.hypot(dx, dy)
            dx, dy = (dx / n, dy / n) if n > 1 else (0.7, -0.7)
            g.text(x + dx * 13, y + dy * 13, nm.translate(SUB), italic=True)
        return g.out()

    @staticmethod
    def _on_visible(a, b, faces_vis) -> bool:
        for f, n, p0 in faces_vis:
            nn = math.sqrt(_dot(n, n)) or 1
            if all(abs(_dot(n, [q[i] - p0[i] for i in range(3)])) / nn < 1e-6 for q in (a, b)):
                return True
        return False

    @staticmethod
    def _curved(g, spec, S, k):
        r, h = spec.get("r", 1), spec.get("h", 2)
        shape = spec["shape"]
        o = S([0, 0, 0])
        if shape == "sphere":
            g.circle(o[0], o[1], r * k)
            g.arc(o[0], o[1], r * k, 0.35 * r * k, 180, 360)
            g.arc(o[0], o[1], r * k, 0.35 * r * k, 0, 180, dashed=True)
            return
        g.arc(o[0], o[1], r * k, 0.35 * r * k, 180, 360)            # 底面前半圈
        g.arc(o[0], o[1], r * k, 0.35 * r * k, 0, 180, dashed=True)  # 底面后半圈看不见
        if shape == "cylinder":
            t = S([0, 0, h])
            g.arc(t[0], t[1], r * k, 0.35 * r * k, 0, 360)
            g.line(o[0] - r * k, o[1], t[0] - r * k, t[1])
            g.line(o[0] + r * k, o[1], t[0] + r * k, t[1])
        else:
            t = S([0, 0, h])
            g.line(o[0] - r * k, o[1], *t)
            g.line(o[0] + r * k, o[1], *t)
