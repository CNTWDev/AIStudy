"""生成主题插画：pattern.svg（整页底纹，240 一块）和 horizon.svg（页面底部的场景，1200x330），见 docs/DESIGN.md 5.45。
以后可以换成画师画的图，文件名不变就行。用法：python tools/gen_theme_art.py"""
import math
import random
OUT = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "app" / "theme")
INK = "#2B2118"

def svg(w, h, body, extra=""):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"{extra}>{body}</svg>\n'

def star4(x, y, r, fill, op=1):
    return (f'<path d="M{x} {y-r} Q{x+r*.18} {y-r*.18} {x+r} {y} Q{x+r*.18} {y+r*.18} {x} {y+r} '
            f'Q{x-r*.18} {y+r*.18} {x-r} {y} Q{x-r*.18} {y-r*.18} {x} {y-r}Z" fill="{fill}" opacity="{op}"/>')

def star5(x, y, r, fill, stroke=None, sw=0, op=1):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * .45
        pts.append(f"{x + rr*math.cos(a):.1f},{y + rr*math.sin(a):.1f}")
    s = f' stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"' if stroke else ""
    return f'<polygon points="{" ".join(pts)}" fill="{fill}"{s} opacity="{op}"/>'

def heart(x, y, s, fill, op=1):
    return (f'<path d="M{x} {y+s*.9} C{x-s*1.4} {y-s*.1} {x-s*.7} {y-s*1.1} {x} {y-s*.35} '
            f'C{x+s*.7} {y-s*1.1} {x+s*1.4} {y-s*.1} {x} {y+s*.9}Z" fill="{fill}" opacity="{op}"/>')

def note(x, y, s, fill, op=1):
    return (f'<g opacity="{op}" fill="{fill}"><ellipse cx="{x}" cy="{y}" rx="{s*.55}" ry="{s*.42}" transform="rotate(-20 {x} {y})"/>'
            f'<rect x="{x+s*.42}" y="{y-s*1.7}" width="{s*.16}" height="{s*1.7}"/>'
            f'<path d="M{x+s*.5} {y-s*1.7} q{s*.6} {s*.3} {s*.7} {s*.9} q-{s*.2} -{s*.45} -{s*.7} -{s*.45}z"/></g>')

def hexagon(x, y, r, stroke, op):
    p = " ".join(f"{x + r*math.cos(math.pi/6 + i*math.pi/3):.1f},{y + r*math.sin(math.pi/6 + i*math.pi/3):.1f}" for i in range(6))
    return f'<polygon points="{p}" fill="none" stroke="{stroke}" stroke-width="2" opacity="{op}"/>'

def fish(x, y, s, fill, op=1, flip=False):
    t = f' transform="translate({2*x} 0) scale(-1 1)"' if flip else ""
    return (f'<g opacity="{op}"{t}><ellipse cx="{x}" cy="{y}" rx="{s}" ry="{s*.6}" fill="{fill}"/>'
            f'<path d="M{x+s*.8} {y} l{s*.7} -{s*.5} v{s} z" fill="{fill}"/><circle cx="{x-s*.45}" cy="{y-s*.1}" r="{s*.12}" fill="#fff"/></g>')

# ------------------------------------------------------------------ 底纹
def pattern(kind):
    R = random.Random(kind)
    b = []
    if kind == "space":
        for _ in range(7): b.append(star4(R.uniform(10, 230), R.uniform(10, 230), R.uniform(3, 7), "#7C8CC4", .28))
        for _ in range(10): b.append(f'<circle cx="{R.uniform(0,240):.0f}" cy="{R.uniform(0,240):.0f}" r="1.6" fill="#7C8CC4" opacity=".3"/>')
        b.append('<g opacity=".22"><circle cx="170" cy="170" r="11" fill="#7C8CC4"/><ellipse cx="170" cy="170" rx="20" ry="5.5" fill="none" stroke="#7C8CC4" stroke-width="2.4" transform="rotate(-18 170 170)"/></g>')
        b.append('<g opacity=".22" transform="rotate(35 60 70)" fill="#7C8CC4"><path d="M60 52 q7 9 7 22 h-14 q0-13 7-22z"/><path d="M53 70 l-5 7 h5z M67 70 l5 7 h-5z"/></g>')
    elif kind == "stage":
        for i in range(4): b.append(heart(R.uniform(15, 225), R.uniform(15, 225), R.uniform(5, 8), "#E88BB8", .26))
        for i in range(3): b.append(note(R.uniform(20, 220), R.uniform(30, 225), R.uniform(9, 12), "#A98BD8", .28))
        for i in range(5): b.append(star4(R.uniform(10, 230), R.uniform(10, 230), R.uniform(3, 6), "#E88BB8", .3))
    elif kind == "ocean":
        for _ in range(9):
            r = R.uniform(3, 9)
            b.append(f'<circle cx="{R.uniform(10,230):.0f}" cy="{R.uniform(10,230):.0f}" r="{r:.1f}" fill="none" stroke="#5FB3AA" stroke-width="2" opacity=".3"/>')
        b.append(fish(70, 160, 11, "#5FB3AA", .24))
        b.append(fish(185, 60, 8, "#5FB3AA", .22, flip=True))
    elif kind == "honey":
        for x, y in ((40, 40), (160, 70), (90, 160), (205, 200)): b.append(hexagon(x, y, R.uniform(10, 15), "#D9B25A", .28))
        for _ in range(4): b.append(f'<circle cx="{R.uniform(10,230):.0f}" cy="{R.uniform(10,230):.0f}" r="2" fill="#D9B25A" opacity=".3"/>')
    return svg(240, 240, "".join(b))

# ------------------------------------------------------------------ 底部场景
def horizon(kind):
    R = random.Random(kind + "h")
    W, H = 1200, 330  # 底下多出一截地面：手机上底栏盖住的那一段
    b = []
    if kind == "space":   # 一颗小星球的地面：坑、太空站、旗子、远处带环的行星
        b.append('<g opacity=".9"><circle cx="1010" cy="70" r="34" fill="#C9D5F2"/><ellipse cx="1010" cy="70" rx="60" ry="13" fill="none" stroke="#9FB2E6" stroke-width="5" transform="rotate(-15 1010 70)"/></g>')
        b.append('<circle cx="150" cy="60" r="16" fill="#F3D9A4" opacity=".9"/>')
        for _ in range(14): b.append(star4(R.uniform(20, 1180), R.uniform(10, 150), R.uniform(3, 7), "#9FB2E6", .7))
        b.append(f'<path d="M0 200 Q300 150 600 165 T1200 185 V{H} H0Z" fill="#D7DEF0"/>')
        b.append(f'<path d="M0 225 Q350 195 700 210 T1200 215 V{H} H0Z" fill="#C7D0E8"/>')
        for x, y, r in ((120, 225, 18), (420, 205, 12), (760, 230, 22), (1050, 220, 14), (300, 245, 9), (930, 245, 10)):
            b.append(f'<ellipse cx="{x}" cy="{y}" rx="{r}" ry="{r*.35}" fill="#B4BFDD"/>')
        # 太空站（放在左边：电脑上露在内容两边，手机上只看得到中间那段，中间留旗子和火箭）
        b.append('<g transform="translate(-160 0)"><g stroke="#8392BD" stroke-width="3" stroke-linejoin="round">'
                 '<rect x="230" y="128" width="70" height="40" rx="14" fill="#EEF2FB"/><rect x="300" y="138" width="34" height="22" rx="6" fill="#E1E8F7"/>'
                 '<path d="M265 128 V104" fill="none"/><circle cx="265" cy="100" r="5" fill="#FFD23F"/>'
                 '<rect x="180" y="140" width="44" height="16" rx="3" fill="#A9C6F5"/><path d="M224 148 H230" fill="none"/></g>'
                 '<circle cx="255" cy="148" r="6" fill="#5EF2FF" stroke="#8392BD" stroke-width="2.5"/><circle cx="278" cy="148" r="6" fill="#5EF2FF" stroke="#8392BD" stroke-width="2.5"/></g>')
        # 火箭
        b.append('<g transform="translate(470 118) rotate(12)" stroke="#8392BD" stroke-width="3" stroke-linejoin="round">'
                 '<path d="M0 -40 Q14 -22 14 10 H-14 Q-14 -22 0 -40Z" fill="#EEF2FB"/><circle cx="0" cy="-12" r="6" fill="#A9C6F5"/>'
                 '<path d="M-14 0 L-24 16 H-14Z M14 0 L24 16 H14Z" fill="#F7A9C4"/><path d="M-7 12 Q0 32 7 12Z" fill="#FFD23F" stroke="none"/></g>')
        b.append('<g stroke="#8392BD" stroke-width="3"><path d="M620 168 V122" /><path d="M620 122 H648 V138 H620" fill="#F7A9C4"/></g>')
    elif kind == "stage":   # 舞台前沿和台下一排举着应援棒的观众
        for i, x in enumerate((120, 420, 780, 1080)):
            b.append(f'<path d="M{x} 0 L{x-70} 200 H{x+70}Z" fill="#FFE3F1" opacity=".55"/>')
        b.append(f'<rect x="0" y="150" width="{W}" height="22" fill="#E9C9E7"/><rect x="0" y="146" width="{W}" height="6" fill="#D7B0DA"/>')
        for i in range(0, W + 40, 46):
            x = i + R.uniform(-6, 6); y = 210 + R.uniform(-6, 6)
            col = R.choice(["#FF8EC0", "#B79BEA", "#FFD23F", "#8FD3FF"])
            if R.random() < .7:
                b.append(f'<path d="M{x+10} {y-14} L{x+22} {y-48}" stroke="#C8AEDB" stroke-width="4" stroke-linecap="round"/>')
                b.append(heart(x + 23, y - 54, 7, col) if R.random() < .5 else star5(x + 23, y - 54, 9, col))
            b.append(f'<circle cx="{x}" cy="{y}" r="17" fill="#DCC6EA"/><rect x="{x-24}" y="{y+12}" width="48" height="{H-y}" rx="20" fill="#DCC6EA"/>')
        for _ in range(16): b.append(star4(R.uniform(20, 1180), R.uniform(15, 130), R.uniform(3, 7), "#F2A7CF", .7))
        for _ in range(5): b.append(note(R.uniform(40, 1160), R.uniform(40, 120), R.uniform(9, 13), "#B79BEA", .6))
    elif kind == "ocean":   # 海底：沙、海草、珊瑚、贝壳、小鱼、泡泡
        for _ in range(16):
            r = R.uniform(3, 10)
            b.append(f'<circle cx="{R.uniform(20,1180):.0f}" cy="{R.uniform(10,170):.0f}" r="{r:.1f}" fill="none" stroke="#7CC9BF" stroke-width="2.4" opacity=".75"/>')
        for x, y, s, f in ((300, 70, 14, False), (340, 95, 9, False), (900, 60, 16, True), (700, 120, 10, True)):
            b.append(fish(x, y, s, "#FFB199" if s > 12 else "#8FD3C8", .9, flip=f))
        for i in range(0, W, 70):
            x = i + R.uniform(0, 40); h = R.uniform(50, 110)
            b.append(f'<path d="M{x} 260 C{x-18} {260-h*.4} {x+18} {260-h*.7} {x} {260-h}" fill="none" stroke="#8ED1B4" stroke-width="7" stroke-linecap="round" opacity=".85"/>')
        for x in (90, 420, 760, 1110):
            b.append(f'<g fill="#FFC2B0"><rect x="{x-5}" y="170" width="10" height="70" rx="5"/><rect x="{x-28}" y="186" width="9" height="40" rx="4.5"/><rect x="{x+19}" y="178" width="9" height="46" rx="4.5"/>'
                     f'<rect x="{x-28}" y="220" width="56" height="10" rx="5"/></g>')
        b.append(f'<path d="M0 225 Q300 205 600 220 T1200 222 V{H} H0Z" fill="#F1E3C4"/>')
        for x in (110, 470, 820, 1140):
            b.append(f'<path d="M{x-12} 240 Q{x} 216 {x+12} 240Z" fill="#FFD7C9" stroke="#E8B4A2" stroke-width="2"/>')
    elif kind == "honey":   # 花和草地，远处一个蜂巢
        b.append(f'<path d="M0 200 Q300 170 600 185 T1200 190 V{H} H0Z" fill="#E7EBC2"/>')
        b.append(f'<path d="M0 230 Q350 210 700 222 T1200 225 V{H} H0Z" fill="#DCE3AE"/>')
        for i in range(0, W, 60):
            x = i + R.uniform(0, 40); y = 200 + R.uniform(0, 30)
            col = R.choice(["#FFD25C", "#FFB3C1", "#FFFFFF", "#F6B400"])
            b.append(f'<path d="M{x} {y+40} V{y}" stroke="#A9C47F" stroke-width="3"/>')
            b.append("".join(f'<circle cx="{x+7*math.cos(a):.1f}" cy="{y+7*math.sin(a):.1f}" r="5" fill="{col}"/>' for a in (0, 1.26, 2.51, 3.77, 5.03)))
            b.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#E5A300"/>')
        b.append('<g stroke="#C99A3A" stroke-width="3"><path d="M1040 40 V70"/><path d="M1000 70 Q1040 60 1080 70 Q1095 110 1080 150 Q1040 165 1000 150 Q985 110 1000 70Z" fill="#FFD25C"/>'
                 '<path d="M995 95 H1085 M992 120 H1088" fill="none"/></g><ellipse cx="1040" cy="138" rx="9" ry="7" fill="#8A5300"/>')
    return svg(W, H, "".join(b), ' preserveAspectRatio="xMidYMax slice"')

for k in ("space", "stage", "ocean", "honey"):
    open(f"{OUT}/{k}/pattern.svg", "w").write(pattern(k))
    open(f"{OUT}/{k}/horizon.svg", "w").write(horizon(k))
print("ok")
