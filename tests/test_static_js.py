"""前端脚本的静态检查（这里没有浏览器测试，靠读源码兜住容易犯的错）。"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"


def test_top_level_const_not_read_from_window():
    """顶层 const/let 不会挂到 window 上：写 window.Ask 永远是 undefined。
    划词菜单「问小助理」就是因为判断了 window.Ask，点了没反应。"""
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    names = set(re.findall(r"^(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=", js, re.M))
    assigned = set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=[^=]", js))
    files = list((ROOT / "static").rglob("*.js")) + list((ROOT / "templates").rglob("*.html"))
    bad = []
    for f in files:
        for n in sorted(names - assigned):
            if re.search(rf"window\.{re.escape(n)}\b", f.read_text(encoding="utf-8")):
                bad.append(f"{f.relative_to(ROOT)}: window.{n}")
    assert not bad, bad


def test_selection_menu_ask_passes_selected_text():
    """点「问…」后焦点进输入框、选区就没了，所以要把选中的文字直接交给 Ask.open。"""
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "Ask.open(t)" in js
    assert "Ask.sel ||" in js


def test_review_wrong_card_comes_back_only_once():
    """单词复习答错/想不起来：这一轮最后再来一次，只来一次。
    以前每次答错都塞回队尾，只有 1 个词时一直点「想不起来」就永远做不完。"""
    html = (ROOT / "templates" / "review.html").read_text(encoding="utf-8")
    assert "cards.push(c)" in html and html.count("cards.push(") == 1
    assert "again.has(c.id)" in html
