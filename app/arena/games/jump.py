"""往上跳（1～4 人，闯关）：小蜜蜂一直往上弹，左右控制落在哪块板上，背景一直往下沉，掉出屏幕底边就倒下；最先到塔顶就赢。
前端：app/static/play/jump.js；设计：docs/往上跳-讨论稿（项目文件），高保真稿 ui-2026-10-31-jump/。

单位：一屏高 = 1（H）。板子 [x, y, 种类]：x 是天空柱宽度的几分之几（0～1，左右穿屏），y 是离地多高（H）。
种类：n 普通、s 弹簧（弹 0.6 H）、m 左右移动（幅度、速度在 move）、x 易碎（踩一次就碎）、b 一闪一闪（亮的时候才踩得住）。
n / s / m 是「靠得住的」：从地面到塔顶，靠得住的板一块接一块，上下间隔不超过 GAP_MAX（普通一跳 0.22 H 的 85%），
所以只靠它们就一定上得去；x、b 是多出来的路，踩不踩都行。
题板（gates 里的高度 g）：g 处是一整条软云（掉不下去），软云上方 RISE 处是 2～4 块答案板（前端按选项个数摆），
再往上一大段云带（BAND）普通跳、普通弹簧都过不去，只有对的那块板底下的超级弹簧弹得过去；云带里不放板子。
连错两次亮出答案，踩上去是普通弹簧，刚好够到云带上面的第一块板（g + BAND）。
题就是玩法：落在哪块答案板上就是选哪个（shape = "choices"）；倒下答题复活也是同一种题，和所有游戏一样答对 1 道就起来。
背景下沉：scroll（H / 秒）匀速往上走，人跳到屏幕中线以上镜头就跟上去；题板、开局、复活时停住。
用时：坐在软云上想题的前 GATE_FREE 秒不算（前端报上来的 seconds 已经扣掉）。
几个人一起：同一座塔各跳各的、各答各的题，别人画成半透明（互不碰撞），右边的高度条上看得到大家；
谁先到顶谁第一，大家都到顶才算这一关过（开房间的人报：用时是最后一个人的，题板一次答对的加起来）。
- 三颗星：大家都到顶；没倒下（几个人一起，每多 1 人多放宽 1 次）；题板一次答对（几个人一起，每多 1 人多放宽 1 道）。
"""
import random

from . import Game, register

GAP_MAX = 0.19         # 靠得住的板之间最大上下间隔（普通一跳 0.22 H）
RISE = 0.16            # 软云到答案板
BAND = 0.70            # 软云到云带上面第一块板（普通弹簧 0.6 H 从答案板弹起，最高到 RISE + 0.6）
GATE_FREE = 8          # 题板上想题，前几秒不算用时（前端用）
SOLID = set("nsm")

# 前两个世界逐关手调（第 1 关一分多钟）：(高度 H, 题板数, 间隔下限, 间隔上限, 板宽, 弹簧比例, 下沉速度 H/秒, 移动板比例)
TUNED = (
    (12, 2, 0.09, 0.12, 0.26, 0.14, 0.050, 0),
    (13, 2, 0.09, 0.13, 0.25, 0.13, 0.055, 0),
    (14, 2, 0.10, 0.13, 0.25, 0.12, 0.060, 0),
    (15, 2, 0.10, 0.14, 0.24, 0.12, 0.062, 0),
    (16, 3, 0.10, 0.14, 0.24, 0.11, 0.065, 0),
    (17, 3, 0.11, 0.15, 0.23, 0.11, 0.068, 0),
    (17, 2, 0.10, 0.14, 0.24, 0.12, 0.066, 0.15),   # 糖果塔：移动板来了，先放慢一点
    (18, 3, 0.10, 0.14, 0.24, 0.11, 0.068, 0.18),
    (19, 3, 0.11, 0.15, 0.23, 0.11, 0.070, 0.20),
    (20, 3, 0.11, 0.15, 0.23, 0.10, 0.072, 0.22),
    (21, 3, 0.11, 0.155, 0.22, 0.10, 0.075, 0.24),
    (22, 3, 0.12, 0.16, 0.22, 0.10, 0.078, 0.26),
)


@register
class Jump(Game):
    id, name, icon, color, tag = "jump", "往上跳", "🐝", "#4fb3ff", "1～4 人"
    desc = "小蜜蜂一直往上弹，踩弹簧飞上去，题板上踩对答案被超级弹簧弹上云霄，看谁先到塔顶！一个人、全家一起都能玩。"
    players = (1, 4)
    roles = {"sun": ("太阳蜂", "🟡"), "berry": ("莓果蜂", "🔴"), "mint": ("薄荷蜂", "🟢"), "sky": ("天空蜂", "🔵")}
    css = True
    target, max_seconds = 0.75, 600
    freeze = False
    shape = "choices"
    record = "seconds"
    count = 1000
    per_world = 6
    stars = (("clear", "大家都到顶"), ("safe", "不倒下"), ("gates", "题板一次答对"))
    # 一个世界加一样新东西：晴空 弹簧 → 糖果 移动板 → 黄昏 易碎板 → 星空 一闪一闪 → 火山 风。第一个世界跟孩子的主题换颜色（DESIGN 5.45）
    worlds = (("晴空塔", "day"), ("糖果塔", "candy"), ("黄昏塔", "sunset"), ("星空塔", "night"), ("火山塔", "lava"))
    PLACES = ("草坡", "云层", "风口", "彩虹", "高空", "塔顶")

    def title(self, n):
        return f"{self.world_of(n)['name']} · {self.PLACES[(n - 1) % self.per_world]}"

    def make(self, n, rng):
        k = n - 1
        if k < len(TUNED):
            height, gates, g0, g1, width, spring, scroll, moving = TUNED[k]
        else:
            height = round(min(80, 22 + (k - 12) * 0.15), 1)
            gates = max(3, min(7, int(height // 11)))
            g0 = min(0.135, 0.12 + (k - 12) * 0.0003)
            g1 = min(GAP_MAX - 0.03, 0.155 + (k - 12) * 0.0004)   # 走到题板前那一步最多再多 0.03
            width = max(0.15, 0.22 - (k - 12) * 0.0004)
            spring = max(0.05, 0.10 - (k - 12) * 0.0003)
            scroll = round(min(0.16, 0.078 + (k - 12) * 0.0006), 4)
            moving = min(0.4, 0.26 + (k - 12) * 0.001)
        breaks = 0 if k < 12 else min(0.35, 0.15 + (k - 12) * 0.002)     # 第 3 个世界起：易碎板
        blink = 0 if k < 18 else min(0.3, 0.12 + (k - 18) * 0.002)       # 第 4 个世界起：一闪一闪的板
        wind = 0 if k < 24 else min(0.5, 0.25 + (k - 24) * 0.002)        # 第 5 个世界起：风
        lv = build(float(height), gates, g0, g1, spring, moving, breaks, blink, wind, rng)
        speed = 0 if not moving else round(min(1.4, 0.6 + k * 0.004), 3)
        return {"name": self.title(n), "par": int(height * 8), "height": float(height), "width": round(width, 3),
                "scroll": scroll, "move": {"amp": 0.18, "speed": speed}, "seed": rng.randrange(1 << 30), **lv}

    def check(self, lv, name):
        h, plats, gates = lv.get("height"), lv.get("plats") or [], lv.get("gates") or []
        if not isinstance(h, (int, float)) or not 4 <= h <= 200:
            raise ValueError(f"{self.id}/{name}: height 要 4–200")
        for p in plats:
            if len(p) != 3 or p[2] not in "nsmxb" or not 0 <= p[0] <= 1 or not 0 < p[1] < h:
                raise ValueError(f"{self.id}/{name}: 板子要是 [0～1, 0～height, n/s/m/x/b]：{p}")
        for g in gates:
            if any(g - 0.02 < p[1] < g + BAND - 0.04 for p in plats):
                raise ValueError(f"{self.id}/{name}: 题板 {g} 的云带里不能有板子")
        # 只靠靠得住的板（和题板的软云、云带上面那一块）一定上得去
        steps = sorted([0.0, h] + [p[1] for p in plats if p[2] in SOLID] + list(gates))
        for a, b in zip(steps, steps[1:]):
            if b - a > GAP_MAX + 1e-6 and not any(abs(a - g) < 1e-6 and b - a <= BAND + 0.02 for g in gates):
                raise ValueError(f"{self.id}/{name}: {a:.2f} 到 {b:.2f} 跳不上去")

    def stage(self, n, prog):
        """开局：带上这一关自己的纪录，高度条上的 👻「上次的我」按它匀速往上走。"""
        out = super().stage(n, prog)
        best = next((lv["best"] for lv in prog.get("levels", []) if lv["n"] == int(n)), None)
        return {**out, "best": best}

    def for_team(self, lv, k):
        return {**lv, "team": max(1, k)} if k > 1 else lv

    def checks(self, lv, rep, focus):
        k, gates = lv.get("team", 1), len(lv.get("gates") or [])   # score：大家题板一次答对的加起来
        return {"clear": True, "safe": rep["deaths"] <= k - 1, "gates": rep["score"] >= gates * k - (k - 1)}


def build(height, gates, g0, g1, spring, moving, breaks, blink, wind, rng: random.Random) -> dict:
    """一关的板子（从下往上）。靠得住的板一块接一块（间隔 g0～g1），题板均匀分在中间；
    另外撒一些易碎、一闪一闪的板当多出来的路，弹簧上方、平时路上放蜂蜜；有风的关卡分几段风。"""
    at = [round((i + 1) * height / (gates + 1), 2) for i in range(gates)]
    plats, honey, y = [], [], 0.0
    gi = 0
    x = 0.5
    while True:
        y += rng.uniform(g0, g1)
        if gi < len(at) and y >= at[gi] - 0.03:       # 走到题板：软云是这一步，云带上面接着走
            g = at[gi]
            gi += 1
            y = g + BAND
        if y >= height:
            break
        x = (x + rng.uniform(0.2, 0.8)) % 1           # 不老在一条竖线上
        kind = "s" if rng.random() < spring else "m" if rng.random() < moving else "n"
        plats.append([round(min(0.92, max(0.08, x)), 3), round(y, 3), kind])
        if kind == "s":
            honey += [[plats[-1][0], round(y + d, 3)] for d in (0.25, 0.33, 0.41)]
        elif rng.random() < 0.18:
            honey.append([round(rng.uniform(0.1, 0.9), 3), round(y + 0.08, 3)])
    windows = [(g - 0.02, g + BAND - 0.04) for g in at]
    free = lambda yy: yy < height - 0.05 and not any(a <= yy <= b for a, b in windows)
    for share, kind in ((breaks, "x"), (blink, "b")):
        for _ in range(int(height * 4 * share)):
            yy = rng.uniform(0.3, height)
            if free(yy):
                plats.append([round(rng.uniform(0.08, 0.92), 3), round(yy, 3), kind])
    plats.sort(key=lambda p: p[1])
    honey = [h for h in honey if free(h[1])]
    winds = []
    if wind:
        yy = 4.0
        while yy < height - 3:
            if rng.random() < wind and free(yy) and free(yy + 1.5):
                winds.append([round(yy, 2), round(yy + 1.5, 2), rng.choice((-1, 1))])
            yy += 3
    return {"plats": plats, "gates": at, "honey": honey, "wind": winds}
