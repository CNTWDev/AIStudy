"""孩子端少字（docs/KID_UX.md）：孩子看到的每一页都有字数上限；说明文字只给家长和成人学习者看。"""
import re
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from app import db
from app.main import app

# 孩子在页面主体（<main>）里看到的字数上限（不算空白）。新账号、空数据时量的，留了余量；超了就先删字，别加说明
BUDGET = {
    "/today": 450, "/subjects": 200, "/map/math-shanghai": 400, "/learn/MSH-NUM-12": 260,
    "/diagnose/math-shanghai": 80, "/warmup": 100, "/review": 40, "/words": 120, "/words/map": 260, "/reading": 200,
    "/books": 150, "/records": 250, "/arena": 365, "/sprint": 80, "/progress": 450, "/settings": 180,  # /arena：每多一个游戏多一张卡（标签 + 名字）
    "/tools": 80, "/papers": 260, "/mocks": 100,
}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Visible(HTMLParser):
    """<main> 里孩子看得见的字：去掉 script/style、class 带 for-grownups 的、hidden 的、收起来的 <details> 正文。"""
    def __init__(self):
        super().__init__()
        self.stack, self.out, self.in_main = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        a = dict(attrs)
        hide = (tag in ("script", "style", "template") or "for-grownups" in (a.get("class") or "").split()
                or "hidden" in a)
        closed = tag == "details" and "open" not in a
        self.stack.append((tag, hide, closed))
        if tag == "main":
            self.in_main += 1

    def handle_endtag(self, tag):
        while self.stack:
            t = self.stack.pop()[0]
            if t == "main":
                self.in_main -= 1
            if t == tag:
                break

    def handle_data(self, data):
        if not self.in_main or any(h for _, h, _ in self.stack):
            return
        # 收起来的 <details>：只看得见 <summary>
        for i, (t, _, closed) in enumerate(self.stack):
            if closed and not any(x[0] == "summary" for x in self.stack[i + 1:]):
                return
        self.out.append(data)


def visible_chars(html: str) -> int:
    p = _Visible()
    p.feed(html)
    return len(re.sub(r"\s", "", "".join(p.out)))


def _kid(c, email):
    c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
    c.post("/admin/users/create", data={"email": f"p-{email}", "password": "secret1", "name": "家长", "role": "parent"})
    c.get("/logout")
    c.post("/login", data={"email": f"p-{email}", "password": "secret1"})
    c.post("/parent/kids/save", data={"name": "小简", "email": email, "password": "2580", "grade": "G3",
                                      "daily_minutes": "60", "subj_math": "math-shanghai", "subj_english": "eng-shanghai"})
    c.get("/logout")
    c.post("/login", data={"email": email, "password": "2580"})
    return dict(db.one("SELECT * FROM users WHERE email=?", email))


def test_visible_chars_parser():
    html = ('<main><p>你好</p><p class="muted for-grownups">家长看的说明</p><script>var x="很多字"</script>'
            '<details><summary>更多</summary><p>藏起来的</p></details><details open><summary>开</summary><p>看得见</p></details>'
            '<input type="text"><span hidden>隐藏</span></main><footer>外面</footer>')
    assert visible_chars(html) == len("你好更多开看得见")


def test_kid_pages_text_budget():
    with TestClient(app) as c:
        _kid(c, "simple@x.com")
        over = {}
        for path, cap in BUDGET.items():
            r = c.get(path)
            assert r.status_code == 200, path
            assert 'data-simple="1"' in r.text, path
            n = visible_chars(r.text)
            if n > cap:
                over[path] = (n, cap)
        assert not over, f"孩子页面字太多（实际, 上限）：{over}"
        # 说明文字还在页面里（家长视角、成人学习者照样能看到），只是孩子端不显示
        learn = c.get("/learn/MSH-NUM-12").text
        assert "for-grownups" in learn and "从简单的开始；错了会自动进错题本" in learn
        c.get("/logout")

        # 家长替孩子看：不是 simple，任务的「为什么」和说明都在
        c.post("/login", data={"email": "p-simple@x.com", "password": "secret1"})
        kid = db.one("SELECT id FROM users WHERE email='simple@x.com'")["id"]
        c.get(f"/parent/as/{kid}")
        page = c.get("/today").text
        assert 'data-simple' not in page and '<details class="quiet-box" open' in page
        c.get("/logout")
