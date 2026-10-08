"""品牌：beejoy 的标志和五个小伙伴形象。

图形放在 app/static/brand/*.svg（网页图标、手机图标也用它们）；页面里用 `mascot_svg()` 内联，好跟着深色模式和尺寸走。
每个学习者可以在「我的账号」里选一个自己喜欢的形象（存在 users.settings.mascot），顶栏标志和浏览器标签图标都换成它；没选的用 DEFAULT。
"""
import itertools
import re
from pathlib import Path

from markupsafe import Markup

from . import db

BRAND_DIR = Path(__file__).parent / "static" / "brand"

# id: (名字, 一句话介绍)。顺序就是选择页上的顺序。
MASCOTS = {
    "hive": ("蜂巢宝宝", "住在蜂巢里，每天一格一格往上长"),
    "buzzy": ("小蜜蜂", "圆滚滚、嗡嗡嗡，最勤快的那一只"),
    "letter": ("字母 b", "beejoy 的 b，肚子是一张笑脸"),
    "flyer": ("飞飞", "拖着一串小点点，一路往上飞"),
    "drop": ("蜜糖滴", "一滴会飞的蜂蜜，甜甜的"),
}
DEFAULT = "hive"

# 文件里的 <style> 只给单独打开时（浏览器标签图标）用；内联到页面会变成全局样式，去掉，深色模式由 app.css 处理
_SVG = {k: re.sub(r"<style>.*?</style>", "", (BRAND_DIR / f"{k}.svg").read_text().strip()) for k in MASCOTS}
_WORDMARK = re.search(r'd="([^"]+)"', (BRAND_DIR / "wordmark.svg").read_text()).group(1)
_uid = itertools.count(1)


def mascot_of(user: dict | None) -> str:
    m = (db.jload(user.get("settings"), {}) or {}).get("mascot") if user else None
    return m if m in MASCOTS else DEFAULT


def has_chosen(user: dict | None) -> bool:
    return bool(user) and (db.jload(user.get("settings"), {}) or {}).get("mascot") in MASCOTS


def set_mascot(user_id: int, m: str) -> bool:
    if m not in MASCOTS:
        return False
    u = db.one("SELECT settings FROM users WHERE id=?", user_id)
    st = db.jload(u["settings"], {}) if u else {}
    st["mascot"] = m
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), user_id)
    return True


def mascot_svg(m: str, size: int = 28, cls: str = "mascot") -> Markup:
    """内联 SVG。同一页可能放好几个，clipPath 的 id 每次加个后缀免得互相串。"""
    svg = _SVG.get(m) or _SVG[DEFAULT]
    n = next(_uid)
    svg = re.sub(r'id="(\w+)"', rf'id="\1-{n}"', svg)
    svg = re.sub(r'url\(#(\w+)\)', rf'url(#\1-{n})', svg)
    svg = svg.replace('<svg xmlns="http://www.w3.org/2000/svg"',
                      f'<svg class="{cls}" width="{size}" height="{size}" aria-hidden="true" focusable="false"', 1)
    return Markup(svg)


def wordmark_svg(height: int = 20) -> Markup:
    """「beejoy」字标：Fredoka 字体转成的路径，颜色跟文字走（currentColor），不用加载字体。"""
    w = round(height * 3125 / 985)
    return Markup(f'<svg class="wordmark" viewBox="20 -745 3125 985" width="{w}" height="{height}" role="img" aria-label="beejoy">'
                  f'<path fill="currentColor" d="{_WORDMARK}"/></svg>')
