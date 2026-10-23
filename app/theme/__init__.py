"""主题：孩子自己挑的一整套「衣服」——颜色和质感、陪学的小伙伴、乐园的描边和音色、小伙伴开场的一句话（docs/DESIGN.md 5.45）。

主题只换样子，不换怎么学：一页一个主按钮、能按的东西有描边有厚度、读题时页面不动、对错的颜色、题目和讲解的说法，
都不跟主题走。只有孩子（ui_tone 是 kid）用主题；成人、家长、管理员和登录页保持原样。

一个主题是一个文件夹：theme.toml（名字、颜色、乐园、开场白）+ 可选的 mascot.svg（这个主题的小伙伴）。
内置的在 app/theme/<id>/，不进公开仓库的（比如授权来的 IP 主题）放在 DATA_DIR/themes/<id>/，部署时装进去，格式一样。
颜色只能改 TOKENS 里这些；没写的都用 app.css 的默认值。每个主题浅色、深色两套都要写全，测试会检查文字对比度。
"""
import hashlib
import tomllib
from datetime import date, timedelta
from pathlib import Path

from .. import brand, config, db

BUILTIN_DIR = Path(__file__).parent
PRIVATE_DIR = config.DATA_DIR / "themes"
DEFAULT = "honey"

# 主题能改的颜色（app.css 里的同名变量）。对错（--ok、--mint、--coral…）的颜色含义固定，不在这里。
TOKENS = ("bg", "card", "ink", "muted", "faint", "line", "line-2",
          "brand", "brand-d", "brand-2", "brand-3", "brand-ink", "brand-ink-d", "on-brand", "sun-2",
          "k-ink", "k-card-edge", "k-card-shadow", "heat-0", "heat-1", "heat-2", "heat-3", "heat-4")
# 乐园（play.css、art.js、engine.js）：描边颜色；音色——旋律类音效换成哪种波形、整体升降多少
PLAY_WAVES = ("sine", "triangle", "square")


def _load(d: Path) -> dict | None:
    f = d / "theme.toml"
    if not f.is_file():
        return None
    t = tomllib.loads(f.read_text(encoding="utf-8"))
    look = {"id": d.name, "name": t["name"], "desc": t.get("desc", ""), "order": t.get("order", 99),
            "unlock": int(t.get("unlock", 0)), "greet": t.get("greet", ""),
            "light": {k: v for k, v in t.get("light", {}).items() if k in TOKENS},
            "dark": {k: v for k, v in t.get("dark", {}).items() if k in TOKENS},
            "play": t.get("play", {})}
    m = t.get("mascot", {})
    if (d / "mascot.svg").is_file():  # 主题自带的小伙伴，id 和主题一样
        brand.add_mascot(d.name, m.get("name", t["name"]), m.get("desc", ""), (d / "mascot.svg").read_text(encoding="utf-8"))
        look["mascot"] = d.name
    else:
        look["mascot"] = m.get("id", brand.DEFAULT)
    return look


def _load_all() -> dict[str, dict]:
    out = {}
    for root in (BUILTIN_DIR, PRIVATE_DIR):
        if root.is_dir():
            for d in sorted(p for p in root.iterdir() if p.is_dir()):
                if (look := _load(d)):
                    out[look["id"]] = look
    return dict(sorted(out.items(), key=lambda kv: (kv[1]["order"], kv[0])))


LOOKS = _load_all()


# ------------------------------------------------------------------ 谁能用哪个

def best_streak(user_id: int) -> int:
    """历史上最长的连续天数（补签卡补上的日子不断也不加，规则同 streak）。主题解锁看它，所以解锁了就一直在。"""
    from .. import streak
    counted, used = streak._days(user_id), streak._used(user_id)
    if not counted:
        return 0
    d, end = date.fromisoformat(min(counted)), db.today()
    n = top = 0
    while d <= end:
        k = d.isoformat()
        if counted.get(k):
            n += 1
            top = max(top, n)
        elif k not in used and d != end:  # 今天还没做完不算断
            n = 0
        d += timedelta(days=1)
    return top


def applies(learner: dict | None) -> bool:
    """主题只给孩子：上中小学的孩子账号（成人学习者、家长、管理员不用）。"""
    from ..web import ui_tone
    return bool(learner) and ui_tone(learner) == "kid"


def choices(user: dict) -> list[dict]:
    """换主题页：每个主题 + 能不能选、还差几天。"""
    best = best_streak(user["id"]) if any(t["unlock"] for t in LOOKS.values()) else 0
    return [{**t, "open": best >= t["unlock"], "left": max(0, t["unlock"] - best)} for t in LOOKS.values()]


def look_of(learner: dict | None) -> dict | None:
    """这个人现在穿的主题；不用主题的人（成人、家长）返回 None。"""
    if not applies(learner):
        return None
    tid = (db.jload(learner.get("settings"), {}) or {}).get("theme")
    return LOOKS.get(tid) or LOOKS[DEFAULT]


def set_look(user: dict, tid: str) -> str | None:
    """换主题，顺便换上这个主题的小伙伴（之后还能单独换小伙伴）。返回错误说明，成功返回 None。"""
    c = next((t for t in choices(user) if t["id"] == tid), None)
    if not c or not applies(user):
        return "没有这个主题"
    if not c["open"]:
        return f"「{c['name']}」要连续学习 {c['unlock']} 天才能解锁，还差 {c['left']} 天"
    u = db.one("SELECT settings FROM users WHERE id=?", user["id"])
    st = db.jload(u["settings"], {}) if u else {}
    st["theme"], st["mascot"] = tid, c["mascot"]
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), user["id"])
    return None


def mascots_for(user: dict) -> dict[str, tuple[str, str]]:
    """「选小伙伴」能选的：五个 beejoy 小伙伴 + 能用的主题带来的小伙伴（孩子才有）。"""
    out = dict(brand.MASCOTS)
    if applies(user):
        for c in choices(user):
            if c["open"] and c["mascot"] not in out:
                out[c["mascot"]] = brand.ALL[c["mascot"]]
    return out


# ------------------------------------------------------------------ 样子

def _block(vals: dict, play: dict) -> str:
    v = {**vals}
    shadow = v.pop("k-card-shadow", v.get("k-card-edge"))
    decl = [f"--{k}:{x}" for k, x in v.items()]
    if "brand" in v:
        decl.append(f"--ring:0 0 0 4px color-mix(in srgb,{v['brand']} 30%,transparent)")
    if play.get("ink"):
        decl.append(f"--pl-ink:{play['ink']}")
    # 带 var() 的变量在声明它的元素上就算好了，主题块（包括换主题页的小预览）要自己再声明一遍，厚度才跟着描边色走
    decl += ["--k-drop:0 4px 0 var(--k-ink)", "--k-drop-sm:0 3px 0 var(--k-ink)", "--k-press:0 1px 0 var(--k-ink)"]
    if shadow:
        decl.append(f"--k-card-drop:0 5px 0 {shadow}")
    return ";".join(decl)


def css() -> str:
    """所有主题的变量，挂在 [data-look=<id>] 上：body 上是孩子正在用的，换主题页的小预览各挂各的。
    深色两种写法都要：跟系统走（没手动选浅色时），和手动选了深色。"""
    out = []
    for t in LOOKS.values():
        if not t["light"]:
            continue
        sel = f'[data-look="{t["id"]}"]'
        out.append(f"{sel}{{{_block(t['light'], t['play'])}}}")
        dark = _block(t["dark"], {**t["play"], "ink": t["play"].get("ink-dark") or t["play"].get("ink")})
        out.append(f"@media (prefers-color-scheme:dark){{:root:not([data-theme=light]) {sel}{{{dark}}}}}")
        out.append(f":root[data-theme=dark] {sel}{{{dark}}}")
    return "\n".join(out) + "\n"


def client(look: dict | None) -> dict:
    """给页面脚本（乐园的描边和音色）：window.LOOK。"""
    p = (look or {}).get("play", {})
    wave = p.get("wave") if p.get("wave") in PLAY_WAVES else None
    return {"id": look["id"] if look else None, "ink": p.get("ink"), "inkDark": p.get("ink-dark"),
            "wave": wave, "pitch": float(p.get("pitch", 1) or 1)}


CSS = css()
CSS_V = hashlib.sha1(CSS.encode()).hexdigest()[:8]
