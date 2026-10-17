"""化学实验装置图：课本上常见的几套装置（制气体、过滤、蒸发、蒸馏、滴定、电解、纸层析），程序按固定的样子画，
AI 只选装置、选收集方法，给某些仪器 / 药品标上名字或字母（「仪器 a 的名称是……」）。
AI 不自己摆仪器——随手摆出来的装置图连管子都对不上，固定几套样子才画得像课本。"""
import math

from .base import FigError, Kind, label, register
from .svg import Svg

SETUPS = {
    "gas_heat": "加热固体制气体",
    "gas_flask": "固液不加热制气体",
    "filtration": "过滤",
    "evaporation": "蒸发",
    "distillation": "蒸馏",
    "titration": "滴定",
    "electrolysis": "电解",
    "chromatography": "纸层析",
}
COLLECT = {"water": "排水法", "up": "向上排空气法", "down": "向下排空气法"}
# 每套装置能标名字的部位（标签的引线从这里引出）
PARTS = {
    "gas_heat": {"tube": "试管", "reactant": "试管里的固体", "lamp": "酒精灯", "stand": "铁架台", "delivery": "导管",
                 "jar": "集气瓶", "trough": "水槽"},
    "gas_flask": {"funnel": "长颈漏斗", "flask": "锥形瓶", "reactant": "瓶里的药品", "delivery": "导管",
                  "jar": "集气瓶", "trough": "水槽"},
    "filtration": {"rod": "玻璃棒", "funnel": "漏斗", "paper": "滤纸", "stand": "铁架台", "beaker": "烧杯", "filtrate": "滤液"},
    "evaporation": {"rod": "玻璃棒", "dish": "蒸发皿", "solution": "溶液", "ring": "铁圈", "lamp": "酒精灯"},
    "distillation": {"thermometer": "温度计", "flask": "蒸馏烧瓶", "mixture": "烧瓶里的液体", "lamp": "酒精灯",
                     "condenser": "冷凝管", "water_in": "进水口", "water_out": "出水口", "receiver": "接收器", "distillate": "馏出液"},
    "titration": {"burette": "滴定管", "tap": "活塞", "flask": "锥形瓶", "solution": "瓶里的溶液", "stand": "铁架台"},
    "electrolysis": {"power": "电源", "anode": "阳极（接正极）", "cathode": "阴极（接负极）", "electrolyte": "电解液",
                     "beaker": "烧杯"},
    "chromatography": {"paper": "滤纸（层析纸）", "baseline": "起始线（铅笔画）", "spot": "样品点", "solvent": "溶剂",
                       "front": "溶剂前沿", "beaker": "烧杯"},
}
GAS = ("gas_heat", "gas_flask")


def _rot(pts, ox, oy, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(ox + (x - ox) * c - (y - oy) * s, oy + (x - ox) * s + (y - oy) * c) for x, y in pts]


# ---------------------------------------------------------------- 仪器（屏幕坐标，y 向下）

def _beaker(g, x, yb, w, h, level=0.0):
    g.poly([(x - 4, yb - h - 3), (x, yb - h), (x, yb), (x + w, yb), (x + w, yb - h)], close=False)
    if level:
        g.rect(x, yb - h * level, w, h * level, fill=0.15, stroke=False)
        g.line(x, yb - h * level, x + w, yb - h * level, width=0.9)


def _conical(g, cx, yb, w, h, level=0.0):
    nw, nh = w * 0.3, h * 0.32
    top = yb - h
    g.poly([(cx - nw / 2 - 2, top - 2), (cx - nw / 2, top), (cx - nw / 2, top + nh), (cx - w / 2, yb), (cx + w / 2, yb),
            (cx + nw / 2, top + nh), (cx + nw / 2, top), (cx + nw / 2 + 2, top - 2)], close=False)
    if level:
        ly = yb - (h - nh) * level
        half = nw / 2 + (w / 2 - nw / 2) * (ly - (top + nh)) / (yb - (top + nh))
        g.poly([(cx - half, ly), (cx + half, ly), (cx + w / 2, yb), (cx - w / 2, yb)], fill=0.15, stroke=False)
        g.line(cx - half, ly, cx + half, ly, width=0.9)
    return top


def _lamp(g, cx, yb):
    """酒精灯：灯身、灯芯、火焰。返回火焰顶的 y。"""
    g.poly([(cx - 24, yb), (cx - 18, yb - 26), (cx - 6, yb - 32), (cx + 6, yb - 32), (cx + 18, yb - 26), (cx + 24, yb)])
    g.rect(cx - 5, yb - 40, 10, 8)
    g.line(cx, yb - 40, cx, yb - 44, width=1.2)
    flame = [(cx, yb - 66)] + [(cx + 7 * math.sin(math.radians(t)), yb - 48 + 4 * math.cos(math.radians(t)))
                               for t in range(30, 331, 30)]
    g.poly(flame, fill=0.12, width=1.1)
    return yb - 66


def _stand(g, x, yb, top, w=70):
    g.rect(x - 10, yb - 6, w, 6, fill=0.25)
    g.line(x, yb - 6, x, top, width=2.6)


def _jar_up(g, cx, yb, w=34, h=62):
    g.poly([(cx - w / 2, yb - h), (cx - w / 2, yb), (cx + w / 2, yb), (cx + w / 2, yb - h)], close=False)
    g.line(cx - w / 2 - 6, yb - h - 3, cx + w / 2 + 6, yb - h - 3, width=2.2)  # 玻璃片
    return yb - h


# ---------------------------------------------------------------- 各套装置：画图，返回 {部位: 引线起点}

def _collector(g, kind, x, yb, tube_from):
    """收集装置：x 是收集装置左边，tube_from 是导管出口前的点（导管已经画到这里）。"""
    a = {}
    tx, ty = tube_from
    if kind == "water":
        tw, th = 120, 46
        _beaker(g, x, yb, tw, th, level=0.8)
        jx, jt = x + 76, yb - th - 26          # 倒放的集气瓶，瓶口在水下
        g.poly([(jx - 17, yb - 14), (jx - 17, jt), (jx + 17, jt), (jx + 17, yb - 14)], close=False)
        g.rect(jx - 17, jt + 4, 34, yb - 14 - jt - 4 - 22, fill=0.0, stroke=False)
        g.line(tx, ty, x + 30, ty, width=2.4)
        g.poly([(x + 30, ty), (x + 30, yb - 8), (jx - 6, yb - 8), (jx - 6, yb - 22)], close=False, width=2.4)
        for i in range(3):
            g.circle(jx - 6 + (i % 2) * 6, yb - 30 - i * 10, 2.4, width=0.9)
        a.update(jar=(jx + 17, jt + 12), trough=(x + tw, yb - 10), delivery=(x + 30, ty + 18))
    elif kind == "up":
        jx = x + 50
        top = _jar_up(g, jx, yb)
        g.line(tx, ty, jx - 4, ty, width=2.4)
        g.line(jx - 4, ty, jx - 4, yb - 8, width=2.4)  # 导管伸到瓶底
        a.update(jar=(jx + 17, top + 30), delivery=((tx + jx) / 2, ty))
    else:  # down：瓶口朝下，导管伸到瓶里靠上
        jx = x + 50
        g.poly([(jx - 17, yb), (jx - 17, yb - 62), (jx + 17, yb - 62), (jx + 17, yb)], close=False)
        g.line(tx, ty, jx - 30, ty, width=2.4)
        g.poly([(jx - 30, ty), (jx - 30, yb + 14), (jx - 4, yb + 14), (jx - 4, yb - 54)], close=False, width=2.4)
        a.update(jar=(jx + 17, yb - 30), delivery=(jx - 30, (ty + yb) / 2))
    return a


def _gas_heat(g, spec):
    yb = 230
    _stand(g, 0, yb, 90)
    # 试管口略向下倾斜，夹在铁架台上，酒精灯在试管底下
    mouth, bottom = (150, 148), (40, 138)
    ang = math.degrees(math.atan2(mouth[1] - bottom[1], mouth[0] - bottom[0]))
    L, r = math.dist(mouth, bottom), 11
    local = [(bottom[0] + L, bottom[1] - r), (bottom[0], bottom[1] - r)]
    local += [(bottom[0] - r * math.sin(math.radians(t)), bottom[1] - r * math.cos(math.radians(t))) for t in range(0, 181, 15)]
    local += [(bottom[0] + L, bottom[1] + r)]
    g.poly(_rot(local, *bottom, ang), close=False)
    solid = _rot([(bottom[0] - 4, bottom[1] + 4), (bottom[0] + 34, bottom[1] + 9), (bottom[0] + 34, bottom[1] + r - 1),
                  (bottom[0] - 4, bottom[1] + r - 1)], *bottom, ang)
    g.poly(solid, fill=0.35, stroke=False)
    cx, cy = _rot([(bottom[0] + L * 0.75, bottom[1])], *bottom, ang)[0]
    g.line(0, cy - 2, cx, cy - 2, width=2.2)       # 铁夹
    g.rect(cx - 5, cy - 14, 10, 24, width=1.2)
    lx = bottom[0] + 30
    _lamp(g, lx, yb)
    stop = _rot([(bottom[0] + L - 6, bottom[1])], *bottom, ang)[0]
    g.rect(stop[0] - 3, stop[1] - 9, 12, 18, fill=0.35, stroke=False)  # 橡皮塞
    ty = stop[1]
    g.line(stop[0] + 8, ty, 200, ty, width=2.4)
    a = {"tube": _rot([(bottom[0] + L * 0.45, bottom[1] - r)], *bottom, ang)[0], "reactant": (bottom[0] + 14, bottom[1] + 8),
         "lamp": (lx - 20, yb - 12), "stand": (0, 140)}
    a.update(_collector(g, spec.get("collect", "water"), 200, yb, (200, ty)))
    return a


def _gas_flask(g, spec):
    yb = 230
    cx = 60
    top = _conical(g, cx, yb, 90, 110, level=0.3)
    g.rect(cx - 15, top - 4, 30, 14, fill=0.35, stroke=False)  # 橡皮塞
    g.poly([(cx - 24, top - 60), (cx - 6, top - 40), (cx + 6, top - 40), (cx + 24, top - 60)], close=False)  # 长颈漏斗
    g.line(cx - 6, top - 40, cx - 6, yb - 12)
    g.line(cx + 6, top - 40, cx + 6, top + 14)
    g.line(cx + 6, top + 14, cx + 6, yb - 12)
    ty = top - 18
    g.poly([(cx + 10, top + 8), (cx + 10, ty), (170, ty)], close=False, width=2.4)
    a = {"funnel": (cx - 20, top - 56), "flask": (cx - 38, yb - 30), "reactant": (cx + 20, yb - 10), "delivery": (130, ty)}
    a.update(_collector(g, spec.get("collect", "up"), 170, yb, (170, ty)))
    return a


def _filtration(g, spec):
    yb = 240
    _stand(g, 0, yb, 30)
    fx, ft = 90, 60
    g.line(0, ft + 34, fx - 18, ft + 34, width=2.2)    # 铁圈
    g.poly([(fx - 38, ft), (fx - 5, ft + 50), (fx - 5, ft + 104)], close=False)
    g.poly([(fx + 38, ft), (fx + 5, ft + 50), (fx + 5, ft + 112)], close=False)
    g.poly([(fx - 33, ft + 4), (fx, ft + 46), (fx + 33, ft + 4)], dashed=True, width=1.0)  # 滤纸
    g.poly([(fx - 22, ft + 18), (fx, ft + 46), (fx + 22, ft + 18)], fill=0.15, stroke=False)
    g.line(fx - 30, ft - 46, fx - 8, ft + 34, width=2.0)  # 玻璃棒靠在三层滤纸一边
    _beaker(g, fx - 2, yb, 70, 100, level=0.3)
    g.line(fx + 5, ft + 112, fx + 5, yb - 16, dashed=True, width=0.9)
    return {"rod": (fx - 26, ft - 30), "funnel": (fx + 36, ft + 4), "paper": (fx + 16, ft + 20), "stand": (0, 160),
            "beaker": (fx + 68, yb - 60), "filtrate": (fx + 40, yb - 12)}


def _evaporation(g, spec):
    yb = 230
    _stand(g, 0, yb, 70)
    cx, dy = 90, 120
    g.line(0, dy + 4, cx - 44, dy + 4, width=2.2)
    g.line(cx - 46, dy + 4, cx + 46, dy + 4, width=2.2)    # 铁圈
    g.arc(cx, dy - 14, 42, 18, 180, 360)                    # 蒸发皿
    g.line(cx - 42, dy - 14, cx + 42, dy - 14, width=0.9)
    g.poly([(cx - 34, dy - 6), (cx + 34, dy - 6), (cx + 20, dy + 2), (cx - 20, dy + 2)], fill=0.15, stroke=False)
    g.line(cx - 6, dy - 70, cx + 10, dy - 2, width=2.0)     # 玻璃棒搅拌
    _lamp(g, cx, yb)
    return {"rod": (cx - 4, dy - 64), "dish": (cx + 42, dy - 14), "solution": (cx + 26, dy - 4), "ring": (cx - 46, dy + 4),
            "lamp": (cx + 22, yb - 14)}


def _distillation(g, spec):
    yb = 250
    fx, fy, r = 70, 140, 34
    _stand(g, 0, yb, 50)
    g.line(0, fy - 52, fx - 8, fy - 52, width=2.2)
    g.arc(fx, fy, r, r, 100, 440)
    g.line(fx - 6, fy - r, fx - 6, fy - 80), g.line(fx + 6, fy - r, fx + 6, fy - 80)
    g.rect(fx - 8, fy - 86, 16, 8, fill=0.35, stroke=False)
    g.line(fx, fy - 112, fx, fy - 70, width=1.2)            # 温度计，水银球在支管口
    g.circle(fx, fy - 66, 3, fill=0.7, stroke=False)
    g.poly([(fx - r * 0.85, fy + 16), (fx + r * 0.85, fy + 16), (fx + r * 0.5, fy + 28), (fx - r * 0.5, fy + 28)],
           fill=0.15, stroke=False)
    _lamp(g, fx, yb)
    # 支管 → 冷凝管（向下倾斜），下进上出
    sx, sy = fx + 6, fy - 66
    ex, ey = 260, 150
    g.line(sx, sy, sx + 30, sy + 10, width=2.0)
    ang = math.degrees(math.atan2(ey - sy - 10, ex - sx - 30))
    L = math.dist((sx + 30, sy + 10), (ex, ey))
    ox, oy = sx + 30, sy + 10
    g.line(*_rot([(ox, oy), (ox + L, oy)], ox, oy, ang)[0], *_rot([(ox, oy), (ox + L, oy)], ox, oy, ang)[1], width=2.0)
    jacket = _rot([(ox + 20, oy - 12), (ox + L - 20, oy - 12), (ox + L - 20, oy + 12), (ox + 20, oy + 12)], ox, oy, ang)
    g.poly(jacket)
    win = _rot([(ox + L - 34, oy + 12)], ox, oy, ang)[0]
    wout = _rot([(ox + 34, oy - 12)], ox, oy, ang)[0]
    g.line(win[0], win[1], win[0], win[1] + 18, width=1.6)
    g.line(win[0], win[1] + 28, win[0], win[1] + 18, arrow=True, width=1.2)
    g.line(wout[0], wout[1], wout[0], wout[1] - 18, width=1.6)
    g.line(wout[0], wout[1] - 18, wout[0], wout[1] - 30, arrow=True, width=1.2)
    _conical(g, ex + 26, yb, 60, 76, level=0.2)
    end = _rot([(ox + L, oy)], ox, oy, ang)[0]
    g.line(end[0], end[1], ex + 22, yb - 50, width=2.0)
    return {"thermometer": (fx, fy - 108), "flask": (fx - r, fy), "mixture": (fx - 14, fy + 22), "lamp": (fx - 22, yb - 12),
            "condenser": _rot([(ox + L / 2, oy - 12)], ox, oy, ang)[0], "water_in": (win[0], win[1] + 28),
            "water_out": (wout[0], wout[1] - 30), "receiver": (ex + 56, yb - 24), "distillate": (ex + 30, yb - 6)}


def _titration(g, spec):
    yb = 260
    _stand(g, 0, yb, 10)
    bx = 80
    g.line(0, 60, bx - 6, 60, width=2.2)
    g.rect(bx - 6, 10, 12, 150)
    g.rect(bx - 6, 40, 12, 120, fill=0.15, stroke=False)
    for i in range(7):
        g.line(bx - 6, 20 + i * 20, bx + (2 if i % 2 else 4), 20 + i * 20, width=0.8)
    g.line(bx - 14, 166, bx + 14, 166, width=2.4)            # 活塞
    g.poly([(bx - 4, 160), (bx - 2, 184), (bx + 2, 184), (bx + 4, 160)], close=False)
    _conical(g, bx, yb, 80, 70, level=0.35)
    return {"burette": (bx + 6, 90), "tap": (bx + 14, 166), "flask": (bx + 34, yb - 30), "solution": (bx + 20, yb - 10),
            "stand": (0, 220)}


def _electrolysis(g, spec):
    yb = 230
    _beaker(g, 0, yb, 150, 110, level=0.75)
    ax, cx = 40, 110
    g.rect(ax - 5, yb - 140, 10, 110, fill=0.5)
    g.rect(cx - 5, yb - 140, 10, 110, fill=0.5)
    py = yb - 190
    g.line(ax, yb - 140, ax, py), g.line(cx, yb - 140, cx, py)
    g.line(ax, py, 66, py), g.line(84, py, cx, py)
    g.line(66, py - 16, 66, py + 16, width=1.6)              # 电池：长线正极、短线负极
    g.line(84, py - 8, 84, py + 8, width=3)
    g.text(58, py - 22, "+", size=13), g.text(92, py - 22, "−", size=13)
    return {"power": (75, py - 16), "anode": (ax - 5, yb - 60), "cathode": (cx + 5, yb - 60), "electrolyte": (75, yb - 20),
            "beaker": (150, yb - 90)}


def _chromatography(g, spec):
    yb = 240
    _beaker(g, 0, yb, 120, 190, level=0.12)
    g.line(-10, yb - 196, 130, yb - 196, width=2.4)          # 横杆
    px, pw, top = 30, 60, yb - 196
    g.rect(px, top, pw, 186)
    base = yb - 40
    g.line(px, base, px + pw, base, dashed=True, width=0.9)
    front = top + 40
    g.line(px, front, px + pw, front, dashed=True, width=0.9)
    for i, h in enumerate((0.25, 0.6)):
        g.circle(px + 18 + i * 24, base - (base - front) * h, 4, fill=0.5, stroke=False)
    g.circle(px + 18, base, 3.5, fill=0.8, stroke=False)
    g.circle(px + 42, base, 3.5, fill=0.8, stroke=False)
    return {"paper": (px + pw, top + 80), "baseline": (px + pw, base), "spot": (px + 18, base), "solvent": (100, yb - 10),
            "front": (px + pw, front), "beaker": (120, yb - 120)}


DRAW = {"gas_heat": _gas_heat, "gas_flask": _gas_flask, "filtration": _filtration, "evaporation": _evaporation,
        "distillation": _distillation, "titration": _titration, "electrolysis": _electrolysis,
        "chromatography": _chromatography}


def _callouts(g, anchors: dict, labels: dict):
    """标签放在装置左右两边各一列（课本的样子），部位在哪半边就标在哪边，引线不横穿装置；按高低排开，不叠在一起。"""
    if not labels:
        return
    x0, _, x1, _ = g.box
    mid = (x0 + x1) / 2
    for side in ("left", "right"):
        col = x0 - 30 if side == "left" else x1 + 30
        rows = sorted((anchors[k][1], k) for k in labels if (anchors[k][0] < mid) == (side == "left"))
        y = -math.inf
        for ay, k in rows:
            y = max(ay, y + 20)
            ax = anchors[k][0]
            g.line(ax, ay, col + (4 if side == "left" else -4), y, width=0.8)
            g.circle(ax, ay, 1.6, fill=1, stroke=False)
            g.text(col, y, labels[k], size=13, anchor="end" if side == "left" else "start")


_SETUP_TEXT = "；".join(f"{k}={v}（部位：{'、'.join(f'{p}={n}' for p, n in PARTS[k].items())}）" for k, v in SETUPS.items())


@register
class Lab(Kind):
    id, label = "lab", "实验装置图"
    subjects = ("chemistry", "science")
    cues = ("实验装置", "制取", "气体的制取", "收集", "排水法", "排空气", "过滤", "蒸馏", "滴定", "电解", "层析", "色谱", "蒸发皿", "蒸发结晶",
            "分离", "提纯", "apparatus", "filtration", "evaporating", "distillation", "titration", "electrolysis",
            "chromatography", "collecting gas", "separation", "purification")
    prompt = (
        'lab 化学实验装置图（固定几套课本上的装置，程序画）：{"kind":"lab","setup":"gas_heat","collect":"water",'
        '"labels":{"lamp":"a","jar":"b"}}。setup：' + _SETUP_TEXT + '。制气体的两套要写 collect：water=排水法 / up=向上排空气法 / down=向下排空气法。'
        "labels 给某些部位标字母或名字（只能用上面列的部位），问「仪器 a 的名称」就标字母，图上不要标出答案。"
    )

    def normalize(self, spec):
        setup = spec.get("setup")
        if setup not in SETUPS:
            raise FigError(f"不认识的装置：{setup!r}")
        out = {"kind": self.id, "setup": setup}
        if setup in GAS:
            col = spec.get("collect") or ("water" if setup == "gas_heat" else "up")
            if col not in COLLECT:
                raise FigError(f"不认识的收集方法：{col!r}")
            out["collect"] = col
        labels = spec.get("labels") or {}
        if not isinstance(labels, dict):
            raise FigError("labels 应该是 {部位: 文字}")
        bad = [p for p in labels if p not in PARTS[setup] or (p == "trough" and out.get("collect") != "water")]
        if bad:
            raise FigError(f"{SETUPS[setup]}里没有这些部位：{bad}")
        out["labels"] = {p: label(v) for p, v in labels.items() if label(v)}
        return out

    def describe(self, spec):
        s = SETUPS[spec["setup"]] + "装置"
        if spec.get("collect"):
            s += f"（{COLLECT[spec['collect']]}收集）"
        if spec.get("labels"):
            s += "，图上标注：" + "，".join(f"{PARTS[spec['setup']][p]}标「{v}」" for p, v in spec["labels"].items())
        return s

    def svg(self, spec):
        g = Svg()
        anchors = DRAW[spec["setup"]](g, spec)
        _callouts(g, anchors, spec.get("labels") or {})
        return g.out(max_w=440)
