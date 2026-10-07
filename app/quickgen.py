"""游戏用的快题：按知识点和难度级别现场生成的口算、短计算题（答案由程序算出，一定正确且唯一）。

对战和闯关里每道题要在 20 秒内答完，题库里的 AI 题和人工题往往太长，所以游戏用这里的生成器出题：
- 一个题族（family）对应一个知识点，有 1–5 级：级别越高数越大、步骤越多。
- 生成的题不进 items 表（数字不同的题太多，会把题库撑大），作答记录照常进 attempts / events，
  题目 id 形如 gen-<题族>-<指纹>，所以掌握模型的「换题答对」交叉验证照样成立。
- 每个（题族, 级别）的实际难度由 app/arena.py 按全体孩子的作答在线校准。

加一个题族：写一个 (rng, level) -> item 的函数，登记到 FAMILIES。答案必须由程序算出，不要手写。
"""
import hashlib
import random
from fractions import Fraction
from math import gcd

LEVELS = (1, 2, 3, 4, 5)


def _num(q: str, answer, explain: str = "", unit: str = "") -> dict:
    it = {"type": "num", "q": q, "answer": answer, "tol": 1e-6, "explain": explain}
    if unit:
        it["unit"] = unit
    return it


def _frac_str(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def _frac(q: str, value: Fraction, forms=(), explain: str = "") -> dict:
    """分数答案：最简分数，加上题目里自然会写出的其它等值写法（如没约分的同分母结果）。"""
    accepted = [_frac_str(value)] + [s for s in forms if s != _frac_str(value)]
    return {"type": "fill", "q": q, "answer": accepted, "explain": explain, "keypad": "frac"}


def _fmt_dec(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s or "0"


# ------------------------------------------------------------------ 题族（知识点 id 来自 curricula/math-shanghai.json）

def add10(r, lv):
    hi = [5, 7, 9, 10, 10][lv - 1]
    a = r.randint(0, hi)
    if r.random() < 0.5 or lv == 1:
        b = r.randint(0, hi - a)
        return _num(f"{a} + {b} = ?", a + b)
    b = r.randint(0, a)
    return _num(f"{a} − {b} = ?", a - b)


def add20(r, lv):
    if r.random() < 0.5:
        a = r.choice([9, 8, 7, 6, 5][: lv + 1]) if lv < 5 else r.randint(5, 9)
        b = r.randint(11 - a, 9)
        return _num(f"{a} + {b} = ?", a + b, f"凑十法：{a} + {10 - a} = 10，再加 {b - (10 - a)}，得 {a + b}")
    s = r.randint(11, 18)
    b = r.randint(s - 9, 9)
    return _num(f"{s} − {b} = ?", s - b, f"破十法：10 − {b} = {10 - b}，再加 {s - 10}，得 {s - b}")


def add100(r, lv):
    a = r.randint(11, 89)
    if lv <= 2:
        b = r.randint(1, 9) if lv == 1 else r.choice([10, 20, 30, 40])
        b = min(b, 99 - a)
        return _num(f"{a} + {b} = ?", a + b)
    if r.random() < 0.5:
        b = r.randint(2, 99 - a) if a < 97 else 1
        return _num(f"{a} + {b} = ?", a + b)
    b = r.randint(2, a - 1)
    return _num(f"{a} − {b} = ?", a - b)


def addsub2(r, lv):
    top = [50, 70, 99, 99, 99][lv - 1]
    a = r.randint(20, top)
    if r.random() < 0.5:
        b = r.randint(10, 100 - a) if a < 90 else 10
        return _num(f"{a} + {b} = ?", a + b)
    b = r.randint(10, a)
    return _num(f"{a} − {b} = ?", a - b)


_CN = "零一二三四五六七八九"


def _cn(n: int) -> str:
    """100 以内的中文读法（口诀用）：12 → 十二，40 → 四十，72 → 七十二。"""
    t, o = divmod(n, 10)
    if not t:
        return _CN[o]
    return ("" if t == 1 else _CN[t]) + "十" + (_CN[o] if o else "")


def koujue(a: int, b: int) -> str:
    """乘法口诀：小数在前；积是一位数时说「得」（二三得六），两位数直接连读（八九七十二）。"""
    a, b = sorted((a, b))
    p = a * b
    return f"{_CN[a]}{_CN[b]}{'得' if p < 10 else ''}{'一十' if p == 10 else _cn(p)}"


def mult(r, lv):
    tables = [[2, 5], [2, 3, 4, 5], [3, 4, 6, 8], [6, 7, 8, 9], [7, 8, 9]][lv - 1]
    a, b = r.choice(tables), r.randint(2, 9)
    if r.random() < 0.5:
        a, b = b, a
    return _num(f"{a} × {b} = ?", a * b, f"口诀：{koujue(a, b)}")


def divtab(r, lv):
    tables = [[2, 5], [2, 3, 4, 5], [3, 4, 6, 8], [6, 7, 8, 9], [7, 8, 9]][lv - 1]
    d, q = r.choice(tables), r.randint(2, 9)
    return _num(f"{d * q} ÷ {d} = ?", q, f"想口诀：{koujue(d, q)}")


def divrem(r, lv):
    d = r.randint(2, [4, 5, 7, 9, 9][lv - 1])
    q, rem = r.randint(2, 9), r.randint(1, d - 1)
    n = d * q + rem
    return _num(f"{n} ÷ {d} = {q} …… ?（余数是几）", rem, f"{d} × {q} = {d * q}，{n} − {d * q} = {rem}，余数要比除数小")


def add3(r, lv):
    a = r.randint(100, [300, 500, 700, 899, 899][lv - 1])
    if r.random() < 0.5:
        b = r.choice([r.randint(1, 9) * 100, r.randint(10, 99) * 10, r.randint(100, 999 - a)])
        b = min(b, 999 - a)
        return _num(f"{a} + {b} = ?", a + b)
    b = r.randint(10, a - 1)
    return _num(f"{a} − {b} = ?", a - b)


def tens(r, lv):
    a = r.randint(2, 9) * [10, 10, 100, 100, 1000][lv - 1]
    b = r.randint(2, 9)
    if r.random() < 0.5:
        return _num(f"{a} × {b} = ?", a * b, f"先算 {a // 10 ** (len(str(a)) - 1)} × {b}，再在末尾添 0")
    return _num(f"{a * b} ÷ {b} = ?", a)


def mul1(r, lv):
    a = r.randint(*[(11, 30), (12, 99), (100, 300), (100, 999), (100, 999)][lv - 1])
    b = r.randint(2, [3, 5, 6, 9, 9][lv - 1])
    return _num(f"{a} × {b} = ?", a * b)


def div1(r, lv):
    d = r.randint(2, [3, 5, 6, 9, 9][lv - 1])
    q = r.randint(*[(11, 30), (12, 60), (20, 99), (100, 300), (100, 999)][lv - 1])
    return _num(f"{d * q} ÷ {d} = ?", q, f"验算：{q} × {d} = {d * q}")


def mul2(r, lv):
    a = r.randint(*[(11, 13), (11, 20), (12, 40), (20, 70), (40, 99)][lv - 1])
    b = r.randint(*[(10, 12), (11, 15), (11, 30), (20, 60), (30, 99)][lv - 1])
    return _num(f"{a} × {b} = ?", a * b)


def order(r, lv):
    a, b, c = r.randint(2, 9), r.randint(2, 9), r.randint(2, [9, 9, 12, 20, 30][lv - 1])
    forms = [(f"{c} + {a} × {b}", c + a * b, f"先乘：{a} × {b} = {a * b}，再加 {c}"),
             (f"{a * b + c} − {a} × {b}", c, f"先乘：{a} × {b} = {a * b}，再减")]
    if lv >= 3:
        forms.append((f"({c} + {a}) × {b}", (c + a) * b, "有括号先算括号里"))
    if lv >= 4:
        forms.append((f"{a * b * c} ÷ {b} − {a}", a * c - a, f"先除：{a * b * c} ÷ {b} = {a * c}"))
    q, ans, ex = r.choice(forms)
    return _num(f"{q} = ?", ans, ex)


def frac_same(r, lv):
    den = r.randint(*[(3, 5), (4, 7), (5, 9), (6, 10), (7, 12)][lv - 1])
    a = r.randint(1, den - 2)
    b = r.randint(1, den - 1 - a)
    if r.random() < 0.5 or a <= b:
        return _frac(f"{a}/{den} + {b}/{den} = ?（写成 几/几）", Fraction(a + b, den), [f"{a + b}/{den}"],
                     "同分母相加：分母不变，分子相加")
    return _frac(f"{a}/{den} − {b}/{den} = ?（写成 几/几）", Fraction(a - b, den), [f"{a - b}/{den}"],
                 "同分母相减：分母不变，分子相减")


def laws(r, lv):
    pairs = [(25, 4), (125, 8), (5, 20), (50, 2), (25, 8)]
    p, q = r.choice(pairs[: 2 + lv // 2])
    k = r.randint(2, [9, 9, 19, 39, 99][lv - 1])
    if r.random() < 0.5 or lv <= 2:
        return _num(f"{p} × {k} × {q} = ?", p * q * k, f"交换位置先算 {p} × {q} = {p * q}")
    a = r.randint(11, 99)
    n = r.choice([99, 101, 98, 102]) if lv >= 4 else r.choice([99, 101])
    return _num(f"{a} × {n} = ?", a * n, f"把 {n} 看成 100{'+' if n > 100 else '−'}{abs(n - 100)}，用分配律")


def dec_add(r, lv):
    places = 1 if lv <= 2 else 2
    a = round(r.uniform(0.1, [5, 10, 10, 20, 50][lv - 1]), places)
    b = round(r.uniform(0.1, [5, 10, 10, 20, 50][lv - 1]), places)
    if r.random() < 0.5:
        return _num(f"{_fmt_dec(a)} + {_fmt_dec(b)} = ?", round(a + b, places), "小数点对齐再算")
    a, b = max(a, b), min(a, b)
    return _num(f"{_fmt_dec(a)} − {_fmt_dec(b)} = ?", round(a - b, places), "小数点对齐再算")


def dec_mul(r, lv):
    if lv <= 2:
        a, b = r.randint(1, 9) / 10, r.randint(2, 9)
    elif lv <= 4:
        a, b = r.randint(11, 99) / 10, r.randint(2, 9)
    else:
        a, b = r.randint(11, 99) / 10, r.randint(2, 9) / 10
    ans = round(a * b, 4)
    return _num(f"{_fmt_dec(a)} × {_fmt_dec(b)} = ?", ans, "先按整数乘，再数一共有几位小数")


def gcd_lcm(r, lv):
    k = r.randint(2, [4, 6, 8, 12, 15][lv - 1])
    a, b = k * r.randint(1, 5), k * r.randint(2, 6)
    while a == b:
        b += k
    if r.random() < 0.5:
        return _num(f"{a} 和 {b} 的最大公因数是？", gcd(a, b))
    return _num(f"{a} 和 {b} 的最小公倍数是？", a * b // gcd(a, b))


def frac_add(r, lv):
    dens = [(2, 4), (2, 3), (3, 4), (4, 6), (6, 8)][lv - 1]
    d1, d2 = dens if r.random() < 0.5 else dens[::-1]
    a, b = r.randint(1, d1 - 1), r.randint(1, d2 - 1)
    common = d1 * d2 // gcd(d1, d2)
    plus = r.random() < 0.6 or Fraction(a, d1) <= Fraction(b, d2)
    v = Fraction(a, d1) + Fraction(b, d2) if plus else Fraction(a, d1) - Fraction(b, d2)
    raw = f"{v.numerator * (common // v.denominator)}/{common}"
    return _frac(f"{a}/{d1} {'+' if plus else '−'} {b}/{d2} = ?（写成 几/几）", v, [raw],
                 f"先通分，公分母是 {common}，结果能约分要约分")


def int_add(r, lv):
    top = [9, 15, 20, 50, 100][lv - 1]
    a, b = r.randint(-top, top), r.randint(-top, top)
    op = r.choice(["+", "−"])
    ans = a + b if op == "+" else a - b
    bs = f"({b})" if b < 0 else str(b)
    return {**_num(f"{a} {op} {bs} = ?", ans, "减去一个数，等于加上它的相反数"), "keypad": "neg"}


def equation(r, lv):
    x = r.randint(1, [9, 12, 15, 20, 30][lv - 1])
    a = r.randint(2, [3, 5, 7, 9, 12][lv - 1])
    b = r.randint(1, [9, 15, 20, 30, 50][lv - 1])
    if lv <= 2:
        return _num(f"x + {b} = {x + b}，x = ?", x, f"两边同时减去 {b}")
    if r.random() < 0.5:
        return _num(f"{a}x + {b} = {a * x + b}，x = ?", x, f"两边先减 {b}，再除以 {a}")
    return _num(f"{a}x − {b} = {a * x - b}，x = ?", x, f"两边先加 {b}，再除以 {a}")


def percent(r, lv):
    p = r.choice([[10, 50], [10, 20, 25, 50], [5, 15, 20, 25, 75], [12, 15, 35, 40, 60], [8, 12, 35, 45, 120]][lv - 1])
    base = r.choice([20, 40, 60, 80, 100, 120, 200, 300, 400, 500])
    return _num(f"{base} 的 {p}% 是多少？", round(base * p / 100, 4), f"{base} × {p}% = {base} × {p} ÷ 100")


def perimeter(r, lv):
    a, b = r.randint(2, [6, 9, 15, 25, 40][lv - 1]), r.randint(2, [6, 9, 15, 25, 40][lv - 1])
    if r.random() < 0.3 or lv == 1:
        return _num(f"正方形边长 {a} 厘米，周长是多少厘米？", 4 * a, "正方形周长 = 边长 × 4", "厘米")
    a, b = max(a, b) + 1, min(a, b)
    return _num(f"长方形长 {a} 厘米、宽 {b} 厘米，周长是多少厘米？", 2 * (a + b), "长方形周长 = (长 + 宽) × 2", "厘米")


def area(r, lv):
    a, b = r.randint(2, [6, 9, 12, 20, 30][lv - 1]), r.randint(2, [6, 9, 12, 20, 30][lv - 1])
    if r.random() < 0.3:
        return _num(f"正方形边长 {a} 米，面积是多少平方米？", a * a, "正方形面积 = 边长 × 边长", "平方米")
    a, b = max(a, b) + 1, min(a, b)
    return _num(f"长方形长 {a} 米、宽 {b} 米，面积是多少平方米？", a * b, "长方形面积 = 长 × 宽", "平方米")


# 题族 → (知识点, 生成函数, 名字)
FAMILIES: dict[str, tuple[str, callable, str]] = {
    "add10": ("MSH-NUM-02", add10, "10以内加减"),
    "add20": ("MSH-NUM-04", add20, "20以内进位、退位"),
    "add100": ("MSH-NUM-07", add100, "100以内口算"),
    "addsub2": ("MSH-NUM-08", addsub2, "两位数加减"),
    "mult": ("MSH-NUM-10", mult, "乘法口诀"),
    "divtab": ("MSH-NUM-12", divtab, "用口诀求商"),
    "divrem": ("MSH-NUM-13", divrem, "有余数的除法"),
    "add3": ("MSH-NUM-15", add3, "三位数加减"),
    "tens": ("MSH-NUM-16", tens, "整十整百乘除"),
    "mul1": ("MSH-NUM-17", mul1, "乘一位数"),
    "div1": ("MSH-NUM-18", div1, "除以一位数"),
    "mul2": ("MSH-NUM-20", mul2, "两位数乘两位数"),
    "order": ("MSH-NUM-21", order, "运算顺序"),
    "frac_same": ("MSH-NUM-19", frac_same, "同分母分数加减"),
    "laws": ("MSH-NUM-26", laws, "简便运算"),
    "dec_add": ("MSH-NUM-28", dec_add, "小数加减"),
    "dec_mul": ("MSH-NUM-29", dec_mul, "小数乘法"),
    "gcd_lcm": ("MSH-NUM-33", gcd_lcm, "公因数与公倍数"),
    "frac_add": ("MSH-NUM-35", frac_add, "异分母分数加减"),
    "int_add": ("MSH-NUM-39", int_add, "有理数加减"),
    "equation": ("MSH-ALG-02", equation, "简易方程"),
    "percent": ("MSH-APP-08", percent, "百分数"),
    "perimeter": ("MSH-GEO-09", perimeter, "周长"),
    "area": ("MSH-GEO-10", area, "面积"),
}
BY_KP: dict[str, str] = {kp: fam for fam, (kp, _, _) in FAMILIES.items()}


def generate(family: str, level: int, rng: random.Random | None = None) -> dict:
    """生成一道题：{id, type, q, answer, explain, family, level, kp_id}。"""
    kp_id, fn, _ = FAMILIES[family]
    level = min(5, max(1, int(level)))
    it = fn(rng or random.Random(), level)
    h = hashlib.sha1(f"{family}|{it['q']}".encode()).hexdigest()[:10]
    return {**it, "id": f"gen-{family}-{h}", "family": family, "level": level, "kp_id": kp_id}
