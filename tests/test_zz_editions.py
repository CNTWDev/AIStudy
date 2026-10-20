"""课标主干 + 教材目录：知识点、题、掌握度属于主干，一套教材只决定哪一册哪一单元按什么顺序教（docs/DESIGN.md 4.2）。"""
import copy
import json

from fastapi.testclient import TestClient

from app import curriculum_admin as manager, db, engine, explore, ladder, schoolyear
from app.catalog import catalog
from app.main import app

from tests.test_zz_mix import DAY, _setup


def test_same_point_different_textbooks():
    with TestClient(app):
        # 周长：上海三年级教，苏教新版四年级上册教；是同一个知识点，题和讲解只有一份
        assert catalog.kp_pack["MSH-GEO-09"] == "math-cn"
        assert catalog.stage_in("math-shanghai", "MSH-GEO-09") == "G3"
        assert catalog.stage_in("math-sujiao", "MSH-GEO-09") == "G4"
        assert catalog.kp("MSH-GEO-09")["stage"] == "G3"            # 主干里的学段只用来排先后、往回找
        assert {"math-cn", "math-shanghai", "math-sujiao"} <= set(catalog.kp_packs["MSH-GEO-09"])
        # 一个点可以在几个单元里出现（螺旋上升），学段取第一次出现的位置
        units = [u["name"] for b in catalog.packs["math-sujiao"].books for u in b["units"] if "MSH-NUM-01" in u["kps"]]
        assert len(units) == 3 and catalog.stage_in("math-sujiao", "MSH-NUM-01") == "G1"
        g4 = catalog.chapters("math-sujiao", "G4")
        assert g4[0]["name"] == "周长与面积" and g4[0]["kps"] == ["MSH-GEO-09", "MSH-GEO-10"] and g4[0]["rank"] == 0
        # 主干不能直接选；家长选的是建在它上面的教材
        assert catalog.packs["math-cn"].standard and catalog.packs["math-cn"] not in catalog.courses()
        assert all(not p.standard for packs in catalog.by_subject().values() for p in packs)
        assert [p.id for p in catalog.editions_of("math-cn")] == ["math-shanghai", "math-sujiao", "math-suke"]


def test_unverified_books_keep_every_point():
    """目录只核实了一部分：没出现的点放进后面第一本「目录待核实」的册，不会丢；比这套书起点还早的是地基，不算它的。"""
    with TestClient(app):
        sj = catalog.packs["math-sujiao"]
        pending = [b for b in sj.books if b["status"] == "pending"]
        assert pending and all("目录待核实" in b["name"] for b in pending)
        assert all(k in sj.kp_ids for k in catalog.packs["math-cn"].kp_ids if catalog.kp(k)["stage"] in ("G1", "G2", "G3", "G4", "G5"))
        suke = catalog.packs["math-suke"]
        assert "MSH-NUM-01" not in suke.kp_ids and "MSH-NUM-38" in suke.kp_ids
        assert catalog.stage_in("math-suke", "MSH-NUM-38") == "G7"        # 有理数：上海六年级（预备），苏科七上
        found = {f["pack_id"]: f for f in ladder.foundations({"grade": "G7"}, [{"pack_id": "math-suke", "stage": "G7", "track": None}])}
        assert found["math-cn"]["upto"] == 7
        # 苏州试点包已并回主干
        assert "math-sujiao-g4-pilot" not in catalog.packs and not any(k.startswith("SZ-M4-") for k in catalog.kps)
        assert db.one("SELECT new_id FROM kp_renames WHERE old_id='SZ-M4-PERIMETER'")["new_id"] == "MSH-GEO-09"


def test_jiangsu_kid_follows_own_textbook(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "jsk@x.com", grade="G4", subj={"subj_math": "math-sujiao", "subj_english": "eng-yilin"})
        e = dict(db.one("SELECT * FROM enrollments WHERE user_id=? AND pack_id='math-sujiao'", kid))
        assert e["stage"] == "G4"
        pv = engine.progress_view(kid, e)
        # 四年级的苏教孩子：周长、年月日是今年的内容（上海三年级就教了）；十月中旬学校刚教完第一单元，下册的还没学
        assert {"MSH-GEO-09", "MSH-GEO-11"} <= {k["id"] for k in pv["stage_kps"]}
        assert "MSH-GEO-09" in pv["learned"] and "MSH-GEO-11" not in pv["learned"]
        assert pv["chapters"][0]["name"] == "周长与面积"
        est = schoolyear.estimate({"grade": "G4"}, e, DAY)
        assert est and est["chapters"][0]["key"] == "周长与面积"
        # 学习页按孩子的教材显示：知识地图链到苏教数学，年级是四年级
        page = c.get("/learn/MSH-GEO-09").text
        assert "/map/math-sujiao" in page and "四年级" in page
        assert engine.pack_of(kid, "MSH-GEO-09") == "math-sujiao"
        # 同一个点的证据，换回上海教材也照样算（不用概念互认）
        engine.update_mastery(kid, "MSH-GEO-09", True)
        assert engine.get_mastery(kid)["MSH-GEO-09"]["attempts"] == 1
        assert explore.past_range(kid, e) is not None
        # 学校模板：江苏公办小学预填苏教数学、译林英语
        pr = catalog.preset("js-public-primary")
        assert pr["packs"]["math"] == "math-sujiao" and pr["packs"]["english"] == "eng-yilin"


def test_old_admin_override_of_split_pack_still_loads():
    """拆分以前在后台改过上海数学某个点（整包覆盖）：改动落到主干，新加的点留在上海目录里，课程照常加载。"""
    with TestClient(app):
        doc = manager.baseline()
        spine = doc["packs"]["math-cn"]
        old = {"pack": {k: v for k, v in doc["packs"]["math-shanghai"]["pack"].items() if k not in ("base", "kind")},
               "strands": spine["strands"], "kps": copy.deepcopy(spine["kps"])}
        old["kps"][0] = {**old["kps"][0], "name": "后台改过的名字", "term": "上", "unit": "旧单元"}
        old["kps"].append({"id": "OLD-ADMIN-KP", "strand": "NUM", "stage": "G1", "name": "后台加的点"})
        old["pack"]["selection"] = {"name": "沪教数学（后台改过）"}
        result = manager.validate_document(manager.effective({**manager.empty(), "packs": {"math-shanghai": json.loads(json.dumps(old))}}))
        assert result.kps[spine["kps"][0]["id"]]["name"] == "后台改过的名字" and result.kps["OLD-ADMIN-KP"]["pack"] == "math-shanghai"
        assert result.packs["math-shanghai"].base == "math-cn" and "OLD-ADMIN-KP" in result.packs["math-shanghai"].kp_ids
        assert result.selection_guide(result.packs["math-shanghai"])["name"] == "沪教数学（后台改过）"


def test_skills_run_through_every_unit():
    """语音、阅读、写作、听说每个单元都在练：算这一学年的内容，但不占一章，不影响按章推演学校进度。"""
    with TestClient(app):
        g7 = next(b for b in catalog.packs["eng-yilin"].books if b["stage"] == "G7" and b["term"] == "上")
        assert "ESH-REA-06" in g7["throughout"] and catalog.stage_in("eng-yilin", "ESH-REA-06") == "G7"
        chs = catalog.chapters("eng-yilin", "G7")
        assert chs[0]["name"] == "Unit 1 This is me!" and not any("ESH-REA-06" in ch["kps"] for ch in chs)
        # 苏科新版每学年重新从第 1 章编号；九年级上册有了四分位数和箱线图（2022 课标新增）
        assert catalog.chapters("math-suke", "G8")[0]["name"] == "第1章 三角形"
        assert catalog.stage_in("math-suke", "MCN-STA-03") == "G9"
