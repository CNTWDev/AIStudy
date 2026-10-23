"""主题（DESIGN 5.45）：孩子自己挑一整套样子；学习的骨架和文字清楚程度不跟着变。"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import brand, db, streak, theme
from app.main import app


def _lum(hex_):
    h = hex_.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# 要读的字和它的底：正文、说明、主按钮、选中的选项、品牌色的字（WCAG AA 正文 4.5）
PAIRS = [("ink", "bg"), ("ink", "card"), ("muted", "card"), ("muted", "bg"), ("on-brand", "brand"),
         ("ink", "brand-3"), ("ink", "brand-2"), ("ink", "sun-2"), ("brand-ink", "card"), ("brand-ink", "brand-2")]


@pytest.mark.parametrize("tid", list(theme.LOOKS))
def test_theme_complete_and_readable(tid):
    t = theme.LOOKS[tid]
    need = set(theme.TOKENS) - {"k-card-shadow"}
    for mode in ("light", "dark"):
        vals = t[mode]
        assert need <= set(vals), (tid, mode, sorted(need - set(vals)))  # 浅色、深色都写全
        for fg, bg in PAIRS:
            r = _ratio(vals[fg], vals[bg])
            assert r >= 4.5, f"{tid} {mode}: --{fg} 在 --{bg} 上对比度 {r:.2f} < 4.5"
    assert t["mascot"] in brand.ALL and brand.mascot_svg(t["mascot"])
    assert t["play"].get("wave") in (None, *theme.PLAY_WAVES)


def test_first_batch():
    assert list(theme.LOOKS)[0] == theme.DEFAULT == "honey"
    assert [t["id"] for t in theme.LOOKS.values() if not t["unlock"]] == ["honey", "space", "stage"]  # 3 个直接能选
    assert theme.LOOKS["ocean"]["unlock"] == 7                                                          # 第 4 个连续 7 天
    css = theme.CSS
    assert '[data-look="space"]{' in css and ":root[data-theme=dark] [data-look=\"space\"]" in css
    assert "--ok" not in css and "--coral" not in css  # 对错的颜色不跟主题


def _login_kid(c, email):
    if c.get("/login").text.count("创建网站管理员账号"):
        c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
    else:
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
    c.post("/admin/users/create", data={"email": f"p-{email}", "password": "secret1", "name": "主题家长", "role": "parent"})
    c.get("/logout")
    c.post("/login", data={"email": f"p-{email}", "password": "secret1"})
    c.post("/parent/kids/save", data={"name": "小主题", "email": email, "password": "2580", "grade": "G4",
                                      "daily_minutes": "60", "subj_math": "math-shanghai", "game_minutes": "20", "game_unlock": "free"})
    c.get("/logout")
    c.post("/login", data={"email": email, "password": "2580"})
    return dict(db.one("SELECT * FROM users WHERE email=?", email))


def test_kid_switches_theme(monkeypatch):
    monkeypatch.setattr(streak, "RULE_FROM", "2000-01-01")
    with TestClient(app) as c:
        kid = _login_kid(c, "look@x.com")
        today = c.get("/today").text
        assert 'data-look="honey"' in today and 'href="/theme.css?v=' in today and "准备好了吗？我陪你一起开始！" in today
        page = c.get("/settings").text
        assert page.count('name="look"') == 4 and "还差 7 天" in page and "连续学习 7 天解锁" in page
        assert 'value="space"' in page and page.count('name="mascot"') == 7  # 五个 beejoy 的 + 太空站、练习生的小伙伴
        assert c.get("/theme.css").headers["content-type"].startswith("text/css")

        assert "太空站" in c.post("/settings/look", data={"look": "space"}).text
        st = db.jload(db.one("SELECT settings FROM users WHERE id=?", kid["id"])["settings"], {})
        assert st["theme"] == "space" and st["mascot"] == "space"  # 换主题顺便换上它的小伙伴
        today = c.get("/today").text
        assert 'data-look="space"' in today and "太空站准备就绪" in today and 'href="/brand/space.svg"' in today
        assert '"wave": "square"' in today  # 乐园的音色
        icon = c.get("/brand/space.svg")
        assert icon.status_code == 200 and icon.headers["content-type"].startswith("image/svg")
        assert c.get("/brand/nope.svg").status_code == 404
        game = c.get("/arena/stickman").text
        assert 'data-look="space"' in game and "data-skin" not in game  # 游戏页不套孩子风格，但描边和音色跟主题

        # 第 4 个要连续 7 天；补签卡补上的日子不断
        assert "还差 7 天" in c.post("/settings/look", data={"look": "ocean"}).text
        assert "没有这个形象" in c.post("/settings/mascot", data={"mascot": "ocean"}).text
        d0 = db.today()
        for i in range(1, 9):
            if i != 4:
                db.run("INSERT INTO days(user_id, day, plan, minutes) VALUES(?,?,?,?)", kid["id"], (d0 - timedelta(days=i)).isoformat(), "[]", 20)
        db.run("INSERT INTO streak_freezes(user_id, day, kind, created_at) VALUES(?,?,?,?)",
               kid["id"], (d0 - timedelta(days=4)).isoformat(), "used", db.now())
        assert theme.best_streak(kid["id"]) == 7
        assert "深海探险" in c.post("/settings/look", data={"look": "ocean"}).text
        # 之后断了也不收回
        db.run("DELETE FROM days WHERE user_id=? AND day>=?", kid["id"], (d0 - timedelta(days=3)).isoformat())
        assert 'data-look="ocean"' in c.get("/today").text and 'value="ocean" checked' in c.get("/settings").text
        assert "没有这个主题" in c.post("/settings/look", data={"look": "dragon"}).text
        c.get("/logout")

        # 家长看孩子：看到孩子的主题；家长自己的页面、成人不用主题
        c.post("/login", data={"email": "p-look@x.com", "password": "secret1"})
        page = c.get("/parent").text
        assert "data-look" not in page and "/theme.css" not in page
        assert 'name="look"' not in c.get("/settings").text
        assert "没有这个主题" in c.post("/settings/look", data={"look": "space"}).text
