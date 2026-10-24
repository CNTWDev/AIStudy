"""答对一题的即时反馈（app/static/juice.js，docs/DESIGN.md 5.52）：难题答对的标记、初中以上换一套音色、
声音开关的默认值、连对升音、学习页只有答题卡一处管连对。"""
import json
import pathlib
import re
import shutil
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient

from app import ability, auth, db, engine, llm
from app.catalog import catalog
from app.main import app

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"
KP = "PHY-IG-4.5.6-01"


def _kid(c, grade="G8", courses=("phy-cambridge",)):
    email = f"{uuid.uuid4().hex[:8]}@juice.example"
    auth.create_user(email, "secret123", "练习者", role="kid", grade=grade)
    c.post("/login", data={"email": email, "password": "secret123"})
    uid = db.one("SELECT id FROM users WHERE email=?", email)["id"]
    for p in courses:
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active) VALUES(?,?,?,1)", uid, p, catalog.packs[p].stages[0])
    return uid


def _gen(kp, pack, grade, n=3, **kw):
    return [{"type": "num", "q": f"A current of 2 A flows for {j + 5} s. Charge in C? #{uuid.uuid4().hex[:6]}",
             "answer": 2 * (j + 5), "difficulty": 2, "explain": "Q = It。"} for j in range(n)]


def test_hard_item_right_is_flagged(monkeypatch):
    """答之前估的把握低（< HARD_P）还做对了才算难题答对；把握高、答错、看完讲解再做的都不算。"""
    with TestClient(app) as c:
        kid = _kid(c)
        monkeypatch.setattr(llm, "generate_items", _gen)
        its = engine.items_for(kid, KP, n=3)
        ans = lambda it, a, **kw: c.post("/api/answer", json={"item_id": it["id"], "kp_id": KP, "answer": a, **kw}).json()
        monkeypatch.setattr(ability, "expect", lambda *a: 0.2)
        r = ans(its[0], str(its[0]["answer"]))
        assert r["correct"] and r.get("hard") is True
        r = ans(its[1], "999")
        assert not r["correct"] and "hard" not in r
        r = ans(its[2], str(its[2]["answer"]), mode="helped")
        assert "hard" not in r
        monkeypatch.setattr(ability, "expect", lambda *a: 0.9)
        r = ans(its[1], str(its[1]["answer"]))
        assert r["correct"] and "hard" not in r


def test_teen_flag_on_body():
    with TestClient(app) as c:
        _kid(c, "G8")
        assert 'data-teen="1"' in c.get("/today").text
    with TestClient(app) as c:
        _kid(c, "G3", ("math-shanghai",))
        page = c.get("/today").text
        assert 'data-tone="kid"' in page and "data-teen" not in page


def _juice(script: str, tone="kid", teen=False, stored=None):
    if not shutil.which("node"):
        pytest.skip("没有 node")
    src = (ROOT / "static" / "juice.js").read_text(encoding="utf-8")
    boot = f"""
const played = [], buzzed = [], store = {json.dumps(stored or {})};
global.window = global; global.localStorage = {{getItem: k => k in store ? store[k] : null, setItem: (k, v) => {{ store[k] = v; }}}};
global.document = {{body: {{dataset: {json.dumps({"tone": tone, **({"teen": "1"} if teen else {})})}}}}};
Object.defineProperty(global, "navigator", {{value: {{vibrate: p => buzzed.push(p)}}, configurable: true}});
global.matchMedia = () => ({{matches: true}});
global.addEventListener = () => {{}};
class Osc {{ constructor() {{ this.frequency = {{setValueAtTime: f => played.push(Math.round(f)), exponentialRampToValueAtTime() {{}}}}; }} connect() {{}} start() {{}} stop() {{}} }}
class Gain {{ constructor() {{ this.gain = {{value: 1, setValueAtTime() {{}}, exponentialRampToValueAtTime() {{}}}}; }} connect() {{}} }}
global.AudioContext = class {{ constructor() {{ this.currentTime = 0; this.state = 'running'; this.destination = {{}}; }}
  createGain() {{ return new Gain(); }} createOscillator() {{ return new Osc(); }} }};
"""
    r = subprocess.run(["node", "-e", boot + src + script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_sound_defaults_and_old_switch():
    """孩子默认开、成人默认关；以前在游戏里关过声音（play.sfx=0）的照旧是关的；开关改的是全站同一个。"""
    show = "console.log(JSON.stringify([Juice.who(), Juice.sfx.on]));"
    assert _juice(show) == ["kid", True]
    assert _juice(show, teen=True) == ["teen", True]
    assert _juice(show, tone="adult") == ["adult", False]
    assert _juice(show, stored={"play.sfx": "0"}) == ["kid", False]
    assert _juice("Juice.sfx.toggle(); console.log(JSON.stringify([Juice.sfx.on, store.sfx]));", tone="adult") == [True, "1"]


def test_streak_climbs_and_caps():
    """连对一题升一个音，第 8 个封顶；3/5/10 换和弦、震两下；难题答对是更亮的音；答错不震；关了声音也不震。"""
    out = _juice("""
const first = n => { played.length = 0; buzzed.length = 0; Juice.answer(true, {streak: n}); return [played[0], buzzed[0]]; };
const r = [1, 2, 4, 8, 9, 12].map(first);
played.length = 0; buzzed.length = 0; Juice.answer(true, {streak: 3}); const chord = [played.length, buzzed[0]];
played.length = 0; buzzed.length = 0; Juice.answer(true, {streak: 2, hard: true}); const hard = [played.length, buzzed[0]];
buzzed.length = 0; Juice.answer(false, {streak: 0}); const wrong = buzzed.length;
store.sfx = '0'; Juice.answer(true, {streak: 1}); const muted = buzzed.length;
console.log(JSON.stringify({r, chord, hard, wrong, muted}));
""")
    notes = [n for n, _ in out["r"]]
    assert notes[0] < notes[1] < notes[2] < notes[3]
    assert notes[3] == notes[4] == notes[5]          # 第 8 个以后不再升
    assert all(b == 15 for _, b in out["r"])
    assert out["chord"] == [3, [20, 40, 20]]
    assert out["hard"][0] == 5 and out["hard"][1] == [25, 30, 40]
    assert out["wrong"] == 0 and out["muted"] == 0


def test_answer_card_owns_streak():
    """连对、声音、+1 只在答题卡（renderItem）答完时调一次；页面再调就会一题算两次。"""
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    card = js[js.index("function renderItem("):js.index("function walkThrough(")]
    assert card.count("Combo.hit(") == 2      # 判完分一次、「还不会」一次（悄悄归零）
    for f in (ROOT / "templates").rglob("*.html"):
        assert "Combo.hit(" not in f.read_text(encoding="utf-8"), f.name
    # 学习页的「下一题」都走 nextUp（答对自动翻、回车能点）
    for name in ("mix.html", "learn.html", "warmup.html", "trace.html"):
        html = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert "nextUp(" in html and not re.search(r"下一题 →'?}?</button>", html), name


def test_scripts_read_theme_through_window():
    """主题（window.LOOK）只有选了主题的人页面上才有：脚本里直接写 LOOK 要先 const LOOK = window.LOOK || {}，
    不然没主题的人一进游戏就报「LOOK is not defined」，整个游戏白屏（2026-10-24）。"""
    for f in list((ROOT / "static").glob("*.js")) + list((ROOT / "static" / "play").glob("*.js")):
        js = f.read_text(encoding="utf-8")
        bare = re.findall(r"(?<![\w.])LOOK\.", js)
        if bare:
            assert re.search(r"const LOOK = window\.LOOK \|\| \{\}", js), f.name
