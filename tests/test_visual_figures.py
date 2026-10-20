"""题目配图（app/figures/）：各种图的检查和画法、图文不符的题不要、AI 出题 / 校对 / 改编带上图，
试卷导入时从原卷照片上裁图、重新框、只给自己家看。"""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import bank, db, figures, itemtypes, papers
from app.catalog import catalog
from app.figures import photo
from app.llm import tasks
from app.main import app

SAMPLES = {
    "plane": {"kind": "plane", "points": {"A": [0, 0], "B": [4, 0], "C": [4, 3]},
              "shapes": [{"type": "polygon", "points": ["A", "B", "C"]}, {"type": "angle", "at": "B", "from": "A", "to": "C"},
                         {"type": "segment", "from": "A", "to": "C", "label": "5 cm"}, {"type": "arrow", "from": "A", "to": [2, 2]},
                         {"type": "surface", "from": [0, -1], "to": [4, -1]}, {"type": "lens", "at": [6, 0], "h": 3},
                         {"type": "rect", "at": [5, 1], "w": 1, "h": 1, "label": "木块"}, {"type": "circle", "center": "A", "r": 1},
                         {"type": "line", "through": ["A", "C"]}, {"type": "ray", "from": "B", "through": "C"}],
              "checks": [{"dist": ["A", "C"], "is": 5}, {"angle": ["A", "B", "C"], "is": 90}, {"perp": [["A", "B"], ["B", "C"]]}]},
    "grid": {"kind": "plane", "grid": True, "points": {"_a": [1, 1], "_b": [5, 1], "_c": [5, 3]},
             "shapes": [{"type": "polygon", "points": ["_a", "_b", "_c"], "fill": True}]},
    "solid": {"kind": "solid", "shape": "cube", "a": 2, "points": {"M": {"mid": ["A", "B"]}}, "segments": [["A", "C1"], ["M", "D1"]],
              "checks": [{"dist": ["A", "C1"], "is": 3.4641}]},
    "pyramid": {"kind": "solid", "shape": "pyramid", "n": 4, "a": 2, "h": 2, "apex": "A"},
    "cylinder": {"kind": "solid", "shape": "cylinder", "r": 1, "h": 2, "segments": [{"from": "O", "to": "O1", "dashed": True}]},
    "graph": {"kind": "graph", "x": {"label": "t / s", "min": 0, "max": 10, "step": 2}, "y": {"label": "v / m/s", "min": 0, "max": 20},
              "series": [{"points": [[0, 0], [4, 12], [10, 12]]}, {"fn": "0.2*x^2"}], "marks": [{"at": [4, 12], "label": "P", "guides": True}]},
    "numberline": {"kind": "graph", "x": {"min": -3, "max": 3, "step": 1}, "marks": [{"at": -1.5, "label": "A"}]},
    "chart": {"kind": "chart", "style": "bar", "categories": ["一班", "二班"], "series": [{"name": "借书", "values": [30, 45]}]},
    "clock": {"kind": "clock", "time": "3:45"},
    "scale": {"kind": "scale", "tool": "cylinder", "min": 0, "max": 50, "major": 10, "minor": 5, "unit": "mL", "value": 32},
    "ruler": {"kind": "scale", "tool": "ruler", "min": 0, "max": 6, "from": 1, "to": 4.35, "unit": "cm"},
    "circuit": {"kind": "circuit", "loop": ["cell", "switch:S", {"parallel": [["lamp:L1"], ["lamp:L2", "ammeter:A2"]]}, "ammeter:A1"],
                "meters": [{"type": "voltmeter", "label": "V", "across": "L1"}]},
    "lab": {"kind": "lab", "setup": "gas_heat", "collect": "water", "labels": {"lamp": "a", "trough": "b"}},
    "distillation": {"kind": "lab", "setup": "distillation", "labels": {"condenser": "A", "water_in": "B"}},
}
for _s in ("gas_flask", "filtration", "evaporation", "titration", "electrolysis", "chromatography"):
    SAMPLES[_s] = {"kind": "lab", "setup": _s}


@pytest.mark.parametrize("name", list(SAMPLES))
def test_each_kind_draws(name):
    spec = figures.normalize({**SAMPLES[name], "alt": "示例图"})
    assert spec and spec["alt"] == "示例图", name
    out = figures.present({"fig": spec})
    assert out["svg"].startswith("<svg") and "currentColor" in out["svg"] and "<script" not in out["svg"]
    assert figures.describe(spec)


@pytest.mark.parametrize("bad", [
    {"kind": "nope"},
    {"kind": "plane", "points": {"A": [0, 0], "B": [4, 0]}, "shapes": [{"type": "segment", "from": "A", "to": "C"}]},  # 没有 C
    {**SAMPLES["plane"], "checks": [{"dist": ["A", "C"], "is": 6}]},   # 题目说 6，图上是 5：图文不符
    {**SAMPLES["solid"], "checks": [{"dist": ["A", "C1"], "is": 3}]},
    {"kind": "graph", "x": {"min": 0, "max": 5}, "y": {"min": 0, "max": 5}, "series": [{"fn": "__import__('os').system('x')"}]},
    {"kind": "graph", "x": {"min": 0, "max": 5}, "y": {"min": 0, "max": 5}, "series": [{"fn": "x.real"}]},
    {"kind": "clock", "time": "25:99"},
    {"kind": "scale", "tool": "thermometer", "min": 0, "max": 50, "value": 80},
    {"kind": "chart", "categories": ["a", "b"], "series": [{"values": [1]}]},
    {"kind": "circuit", "loop": ["cell", "lamp:L1"], "meters": [{"type": "voltmeter", "across": "L9"}]},
    {"kind": "circuit", "loop": ["cell", "toaster"]},
    {"kind": "lab", "setup": "spaceship"},
    {"kind": "lab", "setup": "filtration", "labels": {"burette": "a"}},              # 过滤装置里没有滴定管
    {"kind": "lab", "setup": "gas_heat", "collect": "up", "labels": {"trough": "a"}},  # 向上排空气法没有水槽
    {"kind": "lab", "setup": "gas_flask", "collect": "sideways"},
])
def test_bad_figures_rejected(bad):
    assert figures.normalize(bad) is None


def test_function_power_does_not_hang():
    spec = figures.normalize({"kind": "graph", "x": {"min": 0, "max": 2}, "y": {"min": 0, "max": 5}, "series": [{"fn": "9^9^9^x"}]})
    assert spec and "<svg" in figures.present({"fig": spec})["svg"]


def test_prompt_only_for_subjects_that_draw():
    assert "circuit" in figures.prompt_for("physics") and "clock" not in figures.prompt_for("physics")
    assert "clock" in figures.prompt_for("math") and "solid" in figures.prompt_for("math")
    assert figures.prompt_for("english") == "" and not any(k.id == "photo" for k in figures.drawable_for("math"))
    assert "distillation" in figures.prompt_for("chemistry") and "lab " not in figures.prompt_for("math")


def test_item_with_figure():
    raw = {"type": "num", "q": "如图，求 AC 的长度。", "answer": 5, "unit": "cm", "fig": SAMPLES["plane"]}
    it = itemtypes.normalize(raw)
    assert it["fig"]["kind"] == "plane" and it["fig"]["checks"]
    # 图文不符 → 整道题不要
    assert itemtypes.normalize({**raw, "fig": {**SAMPLES["plane"], "checks": [{"dist": ["A", "C"], "is": 7}]}}) is None
    # 给前端的：画好的图，没有画图说明（checks 里可能就是答案）
    pub = itemtypes.public({**it, "id": "X1"})
    assert pub["fig"]["svg"].startswith("<svg") and "checks" not in str(pub["fig"]) and "answer" not in pub
    assert itemtypes.view({**it, "id": "X1"})["fig"]["svg"]
    # 文字一样、图不一样是两道题；没图的题哈希和以前一样
    other = itemtypes.normalize({**raw, "fig": SAMPLES["grid"]})
    assert bank.item_hash(it) != bank.item_hash(other)
    plain = {"type": "num", "q": "1+1=?", "answer": 2}
    assert bank.item_hash(plain) == bank.fingerprint("num", "1+1=?", "", 2)
    # 游戏里不出带图的题
    assert not bank.is_quick("mcq", {"q": "如图", "options": ["1", "2", "3", "4"], "fig": it["fig"]})


def test_ai_items_keep_good_figures(monkeypatch):
    seen = {}

    def fake(task, system, user, **kw):
        seen[task] = user
        if task == "items":
            return {"items": [
                {"type": "num", "q": "如图，AC = ?", "answer": 5, "fig": SAMPLES["plane"]},
                {"type": "num", "q": "如图，AC = ?", "answer": 6, "fig": {**SAMPLES["plane"], "checks": [{"dist": ["A", "C"], "is": 6}]}},
                {"type": "mcq", "q": "1+1=?", "options": ["1", "2", "3", "4"], "answer": 1},
            ]}
        if task == "variant":
            return {"items": [{"type": "num", "q": "改编：如图，读数是多少？", "answer": 3}]}  # 原题有图，改编题没图
        return {"answer": 5, "ok": True}
    monkeypatch.setattr(tasks, "ask_json", fake)
    if not catalog.kps:
        catalog.load()
    kp = catalog.kps["PHY-IG-4.5.6-01"]
    pack = catalog.packs[kp["pack"]]
    items = tasks.generate_items(kp, pack, "G8")
    assert [i["q"] for i in items] == ["如图，AC = ?", "1+1=?"] and items[0]["fig"]["kind"] == "plane"
    assert "circuit 电路图" in seen["items"]
    # 校对时 AI 能「看到」图
    tasks.solve_item({**items[0], "type": "num"}, pack, "G8")
    assert "平面图（画图说明）" in seen["verify"] and '"C": [4.0, 3.0]' in seen["verify"]
    # 原题有图、改编题画不出图：不改编
    assert tasks.make_variant(items[0], kp, pack, "G8") is None
    assert "改编题也必须带 fig" in seen["variant"]
    # 要看图的知识点（电路）：要求一组题里至少一半带图；不用看图的知识点不要求
    assert "至少一半要带 fig" in seen["items"] and "电路图" in tasks._figs_note(kp, pack)
    plain = next(k for k in catalog.kps.values() if k["pack"] == "math-cn" and "口算" in k["name"])
    assert tasks._figs_note(plain, catalog.packs["math-cn"]) == ""


def test_items_for_tops_up_figures_once(monkeypatch):
    """要看图的知识点，题库里只有不带图的老题：补出一批带图的题、带图的排前面；补过一次就不再反复请 AI。"""
    from app import engine, llm
    with TestClient(app) as c:
        _setup(c)
        kid = db.one("SELECT id FROM users WHERE email='figk1@x.com'")["id"]
        kp = "MSH-NUM-31"   # 正数和负数初步：要看数轴
        assert figures.wanted_for(catalog.kps[kp], "math")
        bank.save_items(kp, [{"type": "num", "q": f"老题 {i}：-3 + 5 = ?", "answer": 2, "difficulty": 1} for i in range(4)])
        db.run("UPDATE items SET gen_meta=? WHERE id IN (SELECT item_id FROM item_kps WHERE kp_id=?)", "{}", kp)
        calls = []
        line = {"kind": "graph", "x": {"min": -5, "max": 5, "step": 1}, "marks": [{"at": [-3, 0], "label": "A"}], "alt": "数轴"}

        def gen(kp_, pack, grade, n=3, **kw):
            calls.append(1)
            fig = figures.normalize(line)
            return [{"type": "num", "q": f"如图，点 A 表示的数是多少？#{len(calls)}", "answer": -3, "fig": fig, "difficulty": 1},
                    {"type": "num", "q": f"-2 比 -5 大多少？#{len(calls)}", "answer": 3, "difficulty": 1}]
        monkeypatch.setattr(llm, "enabled", lambda: True)
        monkeypatch.setattr(llm, "generate_items", gen)
        items = engine.items_for(kid, kp, n=3, grade="G5")
        assert calls and items[0].get("fig") and len(items) == 3
        engine.items_for(kid, kp, n=3, grade="G5")
        assert len(calls) == 1


def test_prepare_page_turns_photo_upright():
    img = Image.new("RGB", (400, 300), "white")
    exif = img.getexif()
    exif[0x0112] = 6  # 手机竖着拍：要顺时针转 90°
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    mt, data = photo.prepare_page("image/jpeg", buf.getvalue())
    assert Image.open(io.BytesIO(data)).size == (300, 400)
    with pytest.raises(ValueError):
        photo.prepare_page("image/jpeg", b"\xff\xd8\xff\xe0" + b"0" * 100)


def _setup(c):
    if "创建网站管理员账号" in c.get("/login").text:
        c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
    else:
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
    for n in ("1", "2"):
        c.post("/admin/users/create", data={"email": f"figp{n}@x.com", "password": "secret1", "name": "家长", "role": "parent"})
    c.get("/logout")
    for n in ("1", "2"):
        c.post("/login", data={"email": f"figp{n}@x.com", "password": "secret1"})
        c.post("/parent/kids/save", data={"name": "娃", "email": f"figk{n}@x.com", "password": "secret1", "grade": "G3",
                                          "daily_minutes": "60", "subj_math": "math-shanghai"})
        c.get("/logout")


def _jpg():
    img = Image.new("RGB", (1000, 800), "white")
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def test_paper_figures_cropped_and_private():
    with TestClient(app) as c:
        _setup(c)
        c.post("/login", data={"email": "figk1@x.com", "password": "secret1"})
        r = c.post("/api/papers", data={"pack_id": "math-shanghai"}, files=[("photos", ("p1.jpg", _jpg(), "image/jpeg"))])
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        rows = papers.rows(pid)
        fig = rows[0]["item"]["fig"]
        assert fig["kind"] == "photo" and fig["page"] == 1 and fig["alt"] == "示例：一把刻度尺"
        assert photo.path(fig["file"]).exists() and "fig" not in rows[1]["item"]
        w, h = Image.open(photo.path(fig["file"])).size
        assert abs(w / h - (510 + 30) * 1000 / ((290 + 30) * 800)) < 0.05  # 框（千分比）加一圈留白，按照片宽高换算
        url = f"/fig/{rows[0]['item_id']}/{fig['file']}"
        page = c.get(f"/papers/{pid}").text
        assert url in page and "调整图" in page and "框出图" in page
        assert c.get(url).status_code == 200
        # 重新框：换一个框就换一张图；框太小不行；去掉图
        pi = rows[0]["id"]
        assert c.post(f"/api/papers/{pid}/fig", json={"pi": pi, "page": 1, "box": [0, 0, 5, 5]}).status_code == 400
        assert c.post(f"/api/papers/{pid}/fig", json={"pi": pi, "page": 1, "box": [0, 0, 500, 500]}).status_code == 200
        fig2 = papers.rows(pid)[0]["item"]["fig"]
        assert fig2["box"] == [0, 0, 500, 500] and fig2["alt"] == fig["alt"]
        assert c.post(f"/api/papers/{pid}/fig", json={"pi": rows[1]["id"], "page": 1, "box": [100, 100, 400, 400]}).status_code == 200
        assert papers.rows(pid)[1]["item"]["fig"]["kind"] == "photo"
        c.get("/logout")
        # 家长能看，别人家的孩子看不到
        c.post("/login", data={"email": "figp1@x.com", "password": "secret1"})
        url2 = f"/fig/{rows[0]['item_id']}/{fig2['file']}"
        assert c.get(url2).status_code == 200
        c.get("/logout")
        c.post("/login", data={"email": "figk2@x.com", "password": "secret1"})
        assert c.get(url2).status_code == 404
        c.get("/logout")
        c.post("/login", data={"email": "figk1@x.com", "password": "secret1"})
        assert c.post(f"/api/papers/{pid}/fig", json={"pi": pi, "remove": True}).status_code == 200
        assert "fig" not in papers.rows(pid)[0]["item"]
        assert c.get(url2).status_code == 404


def test_bank_paper_figures():
    with TestClient(app) as c:
        _setup(c)
        c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        r = c.post("/admin/bank-papers", data={"pack_id": "math-shanghai", "kind": "real"},
                   files=[("photos", ("p1.jpg", _jpg(), "image/jpeg"))])
        bp = r.json()["id"]
        rows = db.q("SELECT * FROM bank_paper_items WHERE bank_paper_id=? ORDER BY seq", bp)
        assert "qc-fig" in c.get(f"/admin/bank-papers/{bp}").text
        assert c.post(f"/admin/bank-papers/{bp}/items/{rows[0]['id']}/fig", json={"page": 2, "box": [0, 0, 500, 500]}).status_code == 400
        assert c.post(f"/admin/bank-papers/{bp}/items/{rows[0]['id']}/fig", json={"remove": True}).status_code == 200
        assert "fig" not in db.jload(db.one("SELECT data FROM items WHERE id=?", rows[0]["item_id"])["data"])
        # 管理员改题时图留着
        c.post(f"/admin/bank-papers/{bp}/items/{rows[1]['id']}/fig", json={"page": 1, "box": [100, 100, 600, 400]})
        c.post(f"/admin/bank-papers/{bp}/items/{rows[1]['id']}", data={"q": "改过的题干", "type": "num", "answer": "5", "explain": ""})
        d = db.jload(db.one("SELECT data FROM items WHERE id=?", rows[1]["item_id"])["data"])
        assert d["q"] == "改过的题干" and d["fig"]["kind"] == "photo"
