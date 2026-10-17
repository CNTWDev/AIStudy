"""电路图：按电流走一圈的顺序写元件，并联部分写成几条支路，程序自动排版。
电压表「并在某个元件两端」用 meters 写，程序把它变成和那个元件并联的支路。"""
from .base import FigError, Kind, label, lst, register
from .svg import Svg

ELEMENTS = {"cell": "电池", "battery": "电池组", "lamp": "灯泡", "resistor": "定值电阻", "rheostat": "滑动变阻器",
            "switch": "开关", "ammeter": "电流表", "voltmeter": "电压表", "motor": "电动机", "diode": "二极管",
            "led": "发光二极管", "fuse": "保险丝", "ldr": "光敏电阻", "thermistor": "热敏电阻", "bell": "电铃"}
EW = 64          # 一个元件占的宽度
GAP = 18         # 并联支路之间的空隙
MAX_ELEMENTS = 24


def _elem(x) -> dict:
    if isinstance(x, str):
        t, _, lb = x.partition(":")
        x = {"type": t, "label": lb}
    if not isinstance(x, dict):
        raise FigError(f"元件写法不对：{x!r}")
    if "parallel" in x:
        branches = [[_elem(e) for e in lst(b, "支路", 8)] for b in lst(x["parallel"], "parallel", 5)]
        if len(branches) < 2 or any(not b for b in branches):
            raise FigError("并联至少两条支路，每条支路至少一个元件")
        return {"parallel": branches}
    t = x.get("type")
    if t not in ELEMENTS:
        raise FigError(f"不认识的元件：{t!r}")
    o = {"type": t}
    if x.get("label"):
        o["label"] = label(x["label"])
    if t == "switch" and x.get("closed"):
        o["closed"] = True
    return o


def _walk(blocks):
    for b in blocks:
        if "parallel" in b:
            for br in b["parallel"]:
                yield from _walk(br)
        else:
            yield b


def _attach(blocks, target, meter) -> bool:
    """把电表并到 target（元件名或元件种类）两端。"""
    for i, b in enumerate(blocks):
        if "parallel" in b:
            if any(_attach(br, target, meter) for br in b["parallel"]):
                return True
        elif b.get("label") == target or (not b.get("label") and b["type"] == target):
            blocks[i] = {"parallel": [[b], [meter]]}
            return True
    return False


def _measure(b):
    """(宽, 线上方高度, 线下方高度)"""
    if "parallel" not in b:
        return EW, 24, 24
    rows = [_series_size(br) for br in b["parallel"]]
    total = sum(u + d for _, u, d in rows) + GAP * (len(rows) - 1)
    w = max(r[0] for r in rows) + 40
    return w, total / 2, total / 2


def _series_size(blocks):
    sizes = [_measure(b) for b in blocks]
    return sum(s[0] for s in sizes), max(s[1] for s in sizes), max(s[2] for s in sizes)


def _symbol(g: Svg, b, x, y, below=False):
    """在 [x, x+EW] 这一段画一个元件（导线在 y 高度）。"""
    t, c = b["type"], x + EW / 2
    if t in ("cell", "battery"):
        plates = [-4, 4] if t == "cell" else [-14, -6, 6, 14]
        g.line(x, y, c + plates[0], y)
        g.line(c + plates[-1], y, x + EW, y)
        for i, px in enumerate(plates):
            long = i % 2 == 0
            g.line(c + px, y - (13 if long else 7), c + px, y + (13 if long else 7), width=1.6 if long else 3.4)
        if t == "battery":
            g.line(c - 3, y, c + 3, y, dashed=True, width=1)
    elif t in ("lamp", "ammeter", "voltmeter", "motor", "bell"):
        r = 11
        g.line(x, y, c - r, y), g.line(c + r, y, x + EW, y)
        g.circle(c, y, r)
        if t == "lamp":
            d = r * 0.7
            g.line(c - d, y - d, c + d, y + d), g.line(c - d, y + d, c + d, y - d)
        else:
            g.text(c, y, {"ammeter": "A", "voltmeter": "V", "motor": "M", "bell": "🔔"}[t], size=13, bold=True)
    elif t in ("resistor", "rheostat", "fuse", "ldr", "thermistor"):
        w, h = 28, 10
        g.line(x, y, c - w / 2, y), g.line(c + w / 2, y, x + EW, y)
        g.rect(c - w / 2, y - h / 2, w, h)
        if t == "rheostat":
            g.line(c - 16, y + 12, c + 16, y - 12, arrow=True, width=1.2)
        elif t == "fuse":
            g.line(c - w / 2, y, c + w / 2, y, width=1)
        elif t == "thermistor":
            g.poly([(c - 20, y + 12), (c - 14, y + 12), (c + 14, y - 12)], close=False, width=1.2)
        elif t == "ldr":
            g.circle(c, y, 19, width=1.2)
            for dx in (-8, 2):
                g.line(c + dx - 12, y - 30, c + dx - 4, y - 20, arrow=True, width=1)
    elif t in ("diode", "led"):
        g.line(x, y, c - 8, y), g.line(c + 8, y, x + EW, y)
        g.poly([(c - 8, y - 8), (c - 8, y + 8), (c + 8, y)], fill=0.15)
        g.line(c + 8, y - 8, c + 8, y + 8)
        if t == "led":
            for dx in (-2, 6):
                g.line(c + dx, y - 12, c + dx + 8, y - 22, arrow=True, width=1)
    elif t == "switch":
        g.line(x, y, c - 14, y), g.line(c + 14, y, x + EW, y)
        g.circle(c - 14, y, 2.4, fill=1)
        g.circle(c + 14, y, 2.4)
        g.line(c - 14, y, c + 14, y - (0 if b.get("closed") else 12))
    if b.get("label"):
        g.text(c, y + (25 if below else -24), b["label"], size=12, italic=True)


def _draw(g, b, x, y, below=False):
    if "parallel" not in b:
        _symbol(g, b, x, y, below)
        return
    w, up, _ = _measure(b)
    rows = [_series_size(br) for br in b["parallel"]]
    yy, ys = y - up, []
    for _, u, d in rows:
        ys.append(yy + u)
        yy += u + d + GAP
    xl, xr = x + 10, x + w - 10
    g.line(x, y, xl, y), g.line(xr, y, x + w, y)
    g.line(xl, ys[0], xl, ys[-1]), g.line(xr, ys[0], xr, ys[-1])
    g.circle(xl, y, 2.6, fill=1, stroke=False), g.circle(xr, y, 2.6, fill=1, stroke=False)
    for br, (bw, _, _), by in zip(b["parallel"], rows, ys):
        cx = xl + (xr - xl - bw) / 2
        g.line(xl, by, cx, by)
        for e in br:
            _draw(g, e, cx, by, below)
            cx += _measure(e)[0]
        g.line(cx, by, xr, by)


def _row(g, blocks, y, W, below):
    if not blocks:
        g.line(0, y, W, y)
        return
    total = sum(_measure(b)[0] for b in blocks)
    gap = (W - total) / (len(blocks) + 1)
    cx = 0
    for b in blocks:
        g.line(cx, y, cx + gap, y)
        cx += gap
        _draw(g, b, cx, y, below)
        cx += _measure(b)[0]
    g.line(cx, y, W, y)


@register
class Circuit(Kind):
    id, label = "circuit", "电路图"
    subjects = ("physics", "science")
    cues = ("电路", "串联", "并联", "电流", "电压", "电阻", "欧姆", "circuit", "series", "parallel", "current", "resistance")
    prompt = (
        'circuit 电路图：{"kind":"circuit","loop":["cell","switch:S",{"parallel":[["lamp:L1"],["lamp:L2","ammeter:A2"]]},"ammeter:A1"],'
        '"meters":[{"type":"voltmeter","label":"V","across":"L1"}]}。loop 按电流走一圈的顺序写，"种类:名字"；'
        "并联写 {\"parallel\":[[支路1的元件…],[支路2…]]}；电压表并在某元件两端用 meters 的 across（写元件名字）。"
        "种类：" + " ".join(f"{k}={v}" for k, v in ELEMENTS.items()) + "；闭合的开关写 {\"type\":\"switch\",\"label\":\"S\",\"closed\":true}。"
    )

    def normalize(self, spec):
        loop = [_elem(x) for x in lst(spec.get("loop"), "loop", 12)]
        if len(loop) < 2:
            raise FigError("电路至少两个元件")
        for m in lst(spec.get("meters"), "meters", 6):
            if not isinstance(m, dict) or not m.get("across"):
                raise FigError("meters 要写 across")
            meter = _elem({k: v for k, v in m.items() if k != "across"})
            if not _attach(loop, label(m["across"]), meter):
                raise FigError(f"找不到元件 {m['across']}")
        n = sum(1 for _ in _walk(loop))
        if n > MAX_ELEMENTS:
            raise FigError("元件太多")
        return {"kind": self.id, "loop": loop}

    def svg(self, spec):
        loop = spec["loop"]
        k = (len(loop) + 1) // 2
        top, bottom = loop[:k], loop[k:][::-1]
        tw = sum(_measure(b)[0] for b in top)
        bw = sum(_measure(b)[0] for b in bottom)
        W = max(tw, bw, 160) + 40
        down = max((_measure(b)[2] for b in top), default=0)
        up = max((_measure(b)[1] for b in bottom), default=0)
        H = max(down + up + 30, 110)
        g = Svg()
        _row(g, top, 0, W, False)
        _row(g, bottom, H, W, True)
        g.line(0, 0, 0, H), g.line(W, 0, W, H)
        return g.out()
