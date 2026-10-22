"""前端脚本的静态检查（这里没有浏览器测试，靠读源码兜住容易犯的错）。"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

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


def _review_order(cards: list[str], wrong: set[str] = frozenset()) -> list[str]:
    """把复习页排顺序的那段脚本拿出来，用 node 跑一轮：cards 里 N* 是新词（学习卡），别的是旧词；wrong 里的第一次答错。
    返回出现的顺序：新词学习卡是 N1，回来认它是 N1?，答错再来是 X!，没隔开、留到明天的是 N1~。"""
    if not shutil.which("node"):
        pytest.skip("没有 node")
    html = (ROOT / "templates" / "review.html").read_text(encoding="utf-8")
    js = html[html.index("/* 一轮里的顺序"):html.index("function head(")].replace("{{ group|tojson }}", "null").replace("const post =", "const _post =")
    sim = js + f"""
const out = [], post = (c, body) => out.push(c.id + '~');
const cards = {json.dumps(cards)}, wrong = new Set({json.dumps(sorted(wrong))});
const fresh = cards.filter(x => x[0] === 'N').map(id => ({{c: {{id}}, learn0: true}})), old = cards.filter(x => x[0] !== 'N').map(id => ({{c: {{id}}}}));
while (fresh.length || old.length) {{ if (fresh.length) queue.push(fresh.shift()); if (old.length) queue.push(old.shift()); }}
for (let s; (s = nextStep());) {{
  if (s.learn0) {{ out.push(s.c.id); later(s.c, {{}}, {{learn: {{}}}}); continue; }}
  const back = s.learn ? '?' : again.has(s.c.id) ? '!' : '';
  out.push(s.c.id + back);
  if (wrong.has(s.c.id) && !again.has(s.c.id)) retry(s.c, {{}});
}}
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", sim], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_review_same_word_never_back_to_back():
    """新词学完不当场考：隔开几个别的再回来认一次；同一个词两次之间至少隔 3 张（LAG）。
    以前学习卡揭晓完马上「用一次」（反方向四选一），考的只是刚才那几秒的印象。"""
    def gaps(order):
        seen = {}
        for i, x in enumerate(order):
            k = x.rstrip("?!~")
            if k in seen:
                yield i - seen[k] - 1
            seen[k] = i
    five = _review_order(["N1", "N2", "N3", "N4", "N5"])
    assert five == ["N1", "N2", "N3", "N4", "N5", "N1?", "N2?", "N3?", "N4?", "N5?"]
    mixed = _review_order(["N1", "N2", "N3", "a", "b", "c", "d", "e", "f"], wrong={"N1", "b"})
    assert sorted(x for x in mixed if x.endswith("?")) == ["N1?", "N2?", "N3?"] and "b!" in mixed and "N1!" in mixed
    assert min(gaps(mixed)) >= 3
    # 只有一个新词、没有别的能隔开：不马上考，留到明天（mode=seen）
    assert _review_order(["N1"]) == ["N1", "N1~"]
    # 答错只再来一次；只剩它的时候不连着再出
    assert _review_order(["a"], wrong={"a"}) == ["a"]


def test_revive_rule_is_one_correct_answer_for_every_game():
    """倒下答题的规矩只在外壳里写一次：不管倒下几次、答错几次，永远答对 1 道就起来。
    以前答错一道多欠一道（最多 3 道），乒乓球输一局要答对 5 道；一直答错的孩子越欠越多，越玩越挫败。"""
    shell = (ROOT / "static" / "play" / "shell.js").read_text(encoding="utf-8")
    assert "const REVIVE_NEED = 1;" in shell
    assert "RV.need" not in shell and "RV.grow" not in shell and "ctx.need + 1" not in shell
    for f in (ROOT / "static" / "play").glob("*.js"):
        js = f.read_text(encoding="utf-8")
        assert not re.search(r"revive\s*:\s*\{[^}]*\b(need|grow)\s*:", js), f"{f.name} 自己改了复活要答几道题"
        assert "多欠" not in js, f"{f.name} 还在说答错多欠一道"
