"""坐标图像：物理里的 s-t、v-t、温度-时间图，数学里的函数图像、坐标里的点；只给 x 轴就是数轴。"""
import ast
import math

from .base import FigError, Kind, label, lst, num, register, text
from .svg import Svg

_FUNCS = {"sin": math.sin, "cos": math.cos, "tan": math.tan, "sqrt": math.sqrt, "abs": abs, "log": math.log,
          "ln": math.log, "exp": math.exp}
_CONST = {"pi": math.pi, "e": math.e}
_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)


def compile_fn(src: str):
    """把 "2*x^2 - 3" 这样的式子编成函数；只允许 x、数字、+ - * / ^ 和常见函数，别的一律不认。"""
    src = str(src).replace("^", "**").strip()
    if not src or len(src) > 80:
        raise FigError("函数式为空或太长")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise FigError(f"函数式看不懂：{src}")
    for n in ast.walk(tree):
        ok = (isinstance(n, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Load) + _OPS)
              or (isinstance(n, ast.Constant) and isinstance(n.value, (int, float)))
              or (isinstance(n, ast.Name) and (n.id == "x" or n.id in _CONST or n.id in _FUNCS))
              or (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords))
        if not ok:
            raise FigError(f"函数式里有不支持的写法：{src}")
        if isinstance(n, ast.Constant):
            n.value = float(n.value)  # 全用浮点算：9^9^9 这种只会溢出报错，不会算半天
    code = compile(tree, "<fn>", "eval")
    env = {"__builtins__": {}, **_FUNCS, **_CONST}
    return lambda x: eval(code, env, {"x": x})  # noqa: S307  只剩算术，见上面的检查


def _axis(a, name) -> dict:
    if not isinstance(a, dict):
        raise FigError(f"{name} 轴要写成对象")
    lo, hi = num(a.get("min", 0), f"{name} 轴最小值"), num(a.get("max"), f"{name} 轴最大值")
    if hi <= lo:
        raise FigError(f"{name} 轴 max 要大于 min")
    step = num(a.get("step") or (hi - lo) / 5, f"{name} 轴刻度", 1e-6)
    if (hi - lo) / step > 40:
        raise FigError(f"{name} 轴刻度太密")
    return {"min": lo, "max": hi, "step": step, "label": text(a.get("label"), 30)}


def _fmt(v: float) -> str:
    return f"{v:.4g}"


@register
class Graph(Kind):
    id, label = "graph", "坐标图像"
    subjects = ("math", "physics", "science", "chemistry", "biology", "geography", "economics")
    prompt = (
        'graph 坐标图像 / 数轴：{"kind":"graph","x":{"label":"t / s","min":0,"max":10,"step":2},'
        '"y":{"label":"s / m","min":0,"max":50,"step":10},"grid":true,'
        '"series":[{"points":[[0,0],[4,20],[8,20],[10,40]],"label":"A"},{"fn":"0.5*x^2","from":0,"to":10,"dashed":true}],'
        '"marks":[{"at":[4,20],"label":"P","guides":true}]}。'
        "series 是折线（points）或函数（fn，只能用 x、+ - * / ^、sin cos sqrt 等）；marks 是标出的点，guides:true 画到两轴的虚线。"
        "不写 y 就是数轴（marks 标数轴上的点）。"
    )

    def normalize(self, spec):
        out = {"kind": self.id, "x": _axis(spec.get("x"), "x")}
        if spec.get("y") is not None:
            out["y"] = _axis(spec["y"], "y")
        if spec.get("grid"):
            out["grid"] = True
        series = []
        for s in lst(spec.get("series"), "series", 8):
            if not isinstance(s, dict):
                raise FigError("series 写法不对")
            o = {}
            if s.get("fn"):
                if "y" not in out:
                    raise FigError("数轴上不能画函数")
                compile_fn(s["fn"])
                o["fn"] = str(s["fn"])[:80]
                o["from"] = num(s.get("from", out["x"]["min"]), "函数起点")
                o["to"] = num(s.get("to", out["x"]["max"]), "函数终点")
            else:
                pts = [[num(p[0], "点的坐标"), num(p[1], "点的坐标")] for p in lst(s.get("points"), "折线的点", 200)
                       if isinstance(p, list) and len(p) == 2]
                if len(pts) < 2 or "y" not in out:
                    raise FigError("折线至少要两个点（并且要有 y 轴）")
                o["points"] = pts
            if s.get("label"):
                o["label"] = label(s["label"])
            if s.get("dashed"):
                o["dashed"] = True
            series.append(o)
        out["series"] = series
        marks = []
        for m in lst(spec.get("marks"), "marks", 20):
            at = m.get("at") if isinstance(m, dict) else None
            if isinstance(at, (int, float)):
                at = [at, 0]
            if not (isinstance(at, list) and len(at) in (1, 2)):
                raise FigError("marks 的 at 写法不对")
            at = [num(at[0], "点的坐标"), num(at[1] if len(at) > 1 and "y" in out else 0, "点的坐标")]
            o = {"at": at}
            if m.get("label"):
                o["label"] = label(m["label"])
            if m.get("guides"):
                o["guides"] = True
            if m.get("open"):
                o["open"] = True  # 数轴上的空心点
            marks.append(o)
        out["marks"] = marks
        if not series and not marks and "y" not in out:
            raise FigError("数轴上什么都没有")
        return out

    def svg(self, spec):
        X, Y = spec["x"], spec.get("y")
        W = 300
        H = 210 if Y else 0
        sx = W / (X["max"] - X["min"])
        px = lambda v: (v - X["min"]) * sx  # noqa: E731
        g = Svg()

        def ticks(a):
            v, out = a["min"], []
            while v <= a["max"] + a["step"] * 1e-6:
                out.append(round(v, 10))
                v += a["step"]
            return out
        if not Y:  # 数轴
            g.line(-14, 0, W + 18, 0, arrow=True)
            for v in ticks(X):
                g.line(px(v), -5, px(v), 0, width=1.2)
                g.text(px(v), 14, _fmt(v), size=11)
            if X["label"]:
                g.text(W + 26, 0, X["label"], size=12, anchor="start")
            for m in spec["marks"]:
                x = px(m["at"][0])
                g.circle(x, 0, 4, fill=None if m.get("open") else 1, width=1.6)
                if m.get("label"):
                    g.text(x, -14, m["label"], italic=True)
            return g.out()
        sy = H / (Y["max"] - Y["min"])
        py = lambda v: H - (v - Y["min"]) * sy  # noqa: E731
        ox, oy = px(min(max(0, X["min"]), X["max"])), py(min(max(0, Y["min"]), Y["max"]))
        if spec.get("grid"):
            for v in ticks(X):
                g.line(px(v), 0, px(v), H, width=0.5)
            for v in ticks(Y):
                g.line(0, py(v), W, py(v), width=0.5)
        g.line(0, oy, W + 14, oy, arrow=True, width=1.4)
        g.line(ox, H, ox, -14, arrow=True, width=1.4)
        at_origin = ox == px(0) and oy == py(0)  # 原点在图里：写 O，两条轴上的 0 就不写了
        for v in ticks(X):
            g.line(px(v), oy, px(v), oy + 4, width=1.2)
            if not (at_origin and abs(v) < 1e-12):
                g.text(px(v), oy + 13, _fmt(v), size=11)
        for v in ticks(Y):
            g.line(ox - 4, py(v), ox, py(v), width=1.2)
            if not (at_origin and abs(v) < 1e-12):
                g.text(ox - 7, py(v), _fmt(v), size=11, anchor="end")
        if at_origin:
            g.text(ox - 8, oy + 12, "O", size=11, italic=True)
        if X["label"]:
            g.text(W + 20, oy, X["label"], size=12, anchor="start")
        if Y["label"]:
            g.text(ox + 8, -14, Y["label"], size=12, anchor="start")
        clip = lambda x, y: X["min"] <= x <= X["max"] and Y["min"] - 1e-9 <= y <= Y["max"] + 1e-9  # noqa: E731
        for s in spec["series"]:
            if "fn" in s:
                f, segs, cur = compile_fn(s["fn"]), [], []
                for i in range(241):
                    x = s["from"] + (s["to"] - s["from"]) * i / 240
                    try:
                        y = float(f(x))
                    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
                        y = math.nan
                    if math.isfinite(y) and clip(x, y):
                        cur.append((px(x), py(y)))
                    elif cur:
                        segs.append(cur)
                        cur = []
                if cur:
                    segs.append(cur)
            else:
                segs = [[(px(x), py(y)) for x, y in s["points"]]]
            for seg in segs:
                if len(seg) > 1:
                    g.poly(seg, close=False, dashed=s.get("dashed", False), width=2)
            if s.get("label") and segs and segs[-1]:
                x, y = segs[-1][-1]
                g.text(x + 6, y - 8, s["label"], size=12, anchor="start", italic=True)
        for m in spec["marks"]:
            x, y = px(m["at"][0]), py(m["at"][1])
            if m.get("guides"):
                g.line(x, y, x, oy, dashed=True, width=1)
                g.line(x, y, ox, y, dashed=True, width=1)
            g.circle(x, y, 3, fill=None if m.get("open") else 1)
            if m.get("label"):
                g.text(x + 8, y - 9, m["label"], size=12, anchor="start", italic=True)
        return g.out()
