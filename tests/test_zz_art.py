"""「工作台」样子（DESIGN 5.68）：页面自己声明打开，第一批只有开口练；没声明的页面不受影响；颜色够清楚；练习页是专注模式；电脑上有快捷键、能按住空格说。"""
import re
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app import auth
from app.main import app

ROOT = Path(__file__).resolve().parent.parent / "app"


def _adult(c):
    email = f"{uuid.uuid4().hex[:8]}@art.example"
    auth.create_user(email, "secret123", "老板", role="kid", grade="ADULT")
    c.post("/login", data={"email": email, "password": "secret123"})


def test_studio_only_where_declared():
    with TestClient(app) as c:
        _adult(c)
        for url in ("/speak", "/speak/records", "/speak/review"):
            page = c.get(url).text
            assert 'data-art="studio"' in page and "/static/art.css" in page, url
        today = c.get("/today").text
        assert "data-art=" not in today and "/static/art.css" not in today


def _lum(hexc):
    h = hexc.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_studio_tokens_readable_and_fonts_local():
    css = (ROOT / "static" / "art.css").read_text(encoding="utf-8")
    blocks = re.findall(r"body\[data-art=studio\]\{([^}]*--bg:[^}]*)\}", css)
    assert len(blocks) == 3   # 浅色，深色（跟系统），深色（手动）
    for b in blocks:
        tok = dict(re.findall(r"--([\w-]+):(#[0-9a-f]{6})", b))
        for fg in ("ink", "muted", "faint", "brand-ink", "ok"):
            assert _ratio(tok[fg], tok["bg"]) >= 4.5, (fg, tok[fg], tok["bg"])
        assert _ratio(tok["on-brand"], tok["brand"]) >= 4.5
        assert tok["bg"] not in ("#ffffff", "#000000")
    # 字体都在自己服务器上（服务器在海外、孩子在国内，不能靠谷歌字体）
    for f in re.findall(r"url\((/static/fonts/[^)]+)\)", css):
        assert (ROOT / f.removeprefix("/")).exists(), f
    assert "googleapis" not in css
    # 减少动态效果时不动
    assert "prefers-reduced-motion" in css


def test_studio_practice_screens_are_quiet():
    """练习屏上没有顶栏、底栏；话筒在电脑上能按住空格；声纹够细够多"""
    css = (ROOT / "static" / "art.css").read_text(encoding="utf-8")
    assert "body[data-art=studio]:has(.sk-top .linkbtn) header.top" in css
    assert "[data-key]::after" in css   # 快捷键写在按钮上
    js = (ROOT / "static" / "speak.js").read_text(encoding="utf-8")
    assert "e.code !== 'Space'" in js and "removeEventListener('keydown', kd)" in js
    tpl = (ROOT / "templates" / "_speak.html").read_text(encoding="utf-8")
    assert "range(40)" in tpl
    assert "/api/speak/goals" in (ROOT / "templates" / "speak_scene.html").read_text(encoding="utf-8")


def test_one_page_width_rule():
    """版心只有一处：顶栏和内容都用 --page-w，开口练电脑版只改变量。"""
    css = open("app/static/app.css", encoding="utf-8").read()
    art = open("app/static/art.css", encoding="utf-8").read()
    assert "--page-w:" in css and "--read-w:" in css and "--nav-room:" in css
    assert ".wrap{max-width:var(--page-w)" in css
    assert "calc(50% - var(--page-w) / 2 + var(--gutter))" in css
    assert "424px" not in css and "1160px" not in art
    assert "--page-w:1120px" in art
    # 设置里的开关一行一个，不被通用 label 规则挤成两列
    assert "label.sk-switch:has(>input[type=checkbox]){display:flex" in css
