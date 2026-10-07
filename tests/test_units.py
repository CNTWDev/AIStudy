"""单元测试：不需要数据库和网页的部分（学习方法模型、学习方式配置、题型、教材包元数据、计划排法）。
改算法、加题型、加教材类型时先跑这里：python -m pytest -q tests/test_units.py"""
import json
import shutil

import pytest

from app import itemtypes
from app.methods import Registry, _build
from app.methods.mastery import BKT, empty_evidence
from app.methods.memory import FSRS


# ------------------------------------------------------------------ 记忆模型

def test_fsrs_spacing_effect():
    m = FSRS()
    s, d = m.first("good")
    assert m.recall(0, s) == 1 and m.recall(10, s) < m.recall(1, s) < 1
    same_day, _ = m.review(s, d, 0, "good")
    later, _ = m.review(s, d, 5, "good")
    assert later > same_day * 1.5  # 快忘的时候想起来，记得更久（间隔效应）
    assert m.review(later, d, 5, "again")[0] < later  # 忘了：稳定性回落
    assert FSRS(target_r=0.9).interval(10) < FSRS(target_r=0.8).interval(10)  # 目标保持率越高，复习越勤


# ------------------------------------------------------------------ 掌握模型

def ev(correct=True, at="2026-10-01T08:00:00", fmt="choice", guess=0.25, item=None, **kw):
    return {"kind": "answer", "correct": correct, "at": at, "fmt": fmt, "guess": guess, "item_id": item, **kw}


def test_bkt_needs_cross_validation():
    model = BKT(FSRS())
    st = model.step(None, ev(item="a"))
    assert st["status"] == "learning" and st["p"] < 0.75  # 一道选择题答对：只升一点
    st = model.step(st, ev(item="b", fmt="recall", guess=0.05))
    st = model.step(st, ev(item="c", fmt="recall", guess=0.05))
    assert st["p"] >= 0.85 and st["status"] == "learning"  # 同一天：没有隔天的证据
    assert "隔天再答对一次" in model.missing(st["ev"], st["p"])
    st = model.step(st, ev(item="d", fmt="recall", guess=0.05, at="2026-10-03T08:00:00"))
    assert st["status"] == "mastered"
    assert model.step(st, ev(False, item="e", at="2026-10-03T09:00:00"))["status"] != "weak"  # 粗心错一次不算薄弱


def test_bkt_weak_needs_two_items_and_params():
    model = BKT(FSRS())
    st = model.step(None, ev(False, item="a", guess=0.05))
    assert st["status"] == "learning"
    assert model.step(st, ev(False, item="b", guess=0.05))["status"] == "weak"
    assert model.step(None, ev(False, dont_know=True))["ev"]["dk"] == 1
    # 推断不算证据：概率再高也不是「掌握」
    assert model.step(None, {"kind": "infer", "value": 0.99, "at": "2026-10-01"})["status"] == "learning"
    strict = BKT(FSRS(), master_p=0.9, min_days=3)
    e = {**empty_evidence(), "days": ["d1", "d2"], "items": ["a", "b"], "fmts": ["choice", "recall"]}
    assert model.judge(0.95, e) == "mastered" and strict.judge(0.95, e) == "learning"


def test_bkt_item_difficulty():
    model = BKT(FSRS())
    easy = {"n_attempts": 20, "n_correct": 19}
    g_easy, _ = model.item_params(ev(stats=easy))
    assert g_easy > 0.25  # 人人都对的题，答对说明得少
    _, s_hard = model.item_params(ev(stats={"n_attempts": 20, "n_correct": 2}))
    assert s_hard > model.slip  # 人人都错的题，答错说明得少


# ------------------------------------------------------------------ 学习方式配置

def test_profiles_load_and_override(tmp_path):
    reg = Registry().load(tmp_path / "none.toml")
    assert {"balanced", "mastery", "exam"} <= set(reg.profiles) and reg.default == "balanced"
    for p in reg.profiles.values():
        assert p.name and p.desc and p.plan.get("flex")
    # 覆盖参数 / 新增一种方式：只写配置文件
    f = tmp_path / "methods.toml"
    f.write_text('[profiles.balanced.memory]\ntarget_r = 0.8\n\n[profiles.slow]\nname = "慢慢来"\ndesc = "x"\n'
                 'mastery = { model = "bkt", min_days = 4 }\n[profiles.slow.plan]\nfixed = ["words"]\nflex = ["review"]\n',
                 encoding="utf-8")
    reg2 = Registry().load(f)
    assert reg2.get("balanced").memory.target_r == 0.8
    assert reg2.get("balanced").key != reg.get("balanced").key  # 参数变了，指纹就变（触发重算）
    assert reg2.get("slow").mastery.min_days == 4 and reg2.get("nope").id == "balanced"
    with pytest.raises(ValueError):
        _build("bad", {"mastery": {"model": "no-such-model"}})


def test_plan_policy_sources_exist():
    from app import plan
    reg = Registry().load()
    for p in reg.profiles.values():
        for group in p.plan.get("fixed", []) + p.plan.get("flex", []):
            for kind in group.split("+"):
                assert kind in plan.SOURCES, f"{p.id}: 任务类型 {kind} 没有来源"
    assert plan.interleave([1, 2, 3], ["a"]) == [1, "a", 2, 3]


# ------------------------------------------------------------------ 题型

def test_itemtypes():
    mcq = {"type": "mcq", "options": ["a", "b", "c"], "answer": 1}
    assert itemtypes.check(mcq, "1") and not itemtypes.check(mcq, "0") and itemtypes.display(mcq) == "B. b"
    assert itemtypes.of(mcq).guess(mcq) == pytest.approx(1 / 3)
    assert itemtypes.check({"type": "num", "answer": 9.8, "unit": "m/s²"}, "9.81")
    assert not itemtypes.check({"type": "num", "answer": 9.8}, "")
    assert itemtypes.check({"type": "fill", "answer": ["print"]}, " Print ")
    assert itemtypes.check({"type": "short", "model": "x"}, "") is None
    # AI 给的题：答案格式不对的退化成自评题，不丢题；代码字段保留
    bad = itemtypes.normalize({"type": "mcq", "q": "选哪个", "options": ["x", "y"], "answer": 5})
    assert bad["type"] == "short" and bad["model"]
    code = itemtypes.normalize({"type": "fill", "q": "输出是什么？", "code": "print(1+1)", "answer": "2"})
    assert code["answer"] == ["2"] and code["code"] == "print(1+1)"
    pub = itemtypes.public({"type": "short", "q": "q", "model": "m", "points": ["p"]})
    assert pub["widget"] == "self" and "model" not in pub and "points" not in pub
    assert all(t.fmt and t.widget and t.schema for t in itemtypes.TYPES.values())


# ------------------------------------------------------------------ 教材包：没有统一教材的课程、大学、语言

def test_course_pack_with_levels(tmp_path):
    from app.catalog import Catalog, config, stage_rank
    shutil.copytree(config.CURRICULA_DIR / "_meta", tmp_path / "_meta")
    (tmp_path / "_meta" / "presets.json").write_text('{"presets": []}', encoding="utf-8")  # 学校模板引用的是真教材
    (tmp_path / "py-kids.json").write_text(json.dumps({
        "pack": {"id": "py-kids", "subject": "programming", "edition": "少儿 Python（自定课程）", "system": "course",
                 "levels": [{"id": "PY-L1", "label": "入门", "year": 3}, {"id": "PY-L2", "label": "进阶", "year": 5}],
                 "prompt_note": "代码用 Python 3。"},
        "strands": [{"id": "basics", "name": "基础"}],
        "kps": [{"id": "PY-1", "name": "print 输出", "stage": "PY-L1", "strand": "basics"},
                {"id": "PY-2", "name": "for 循环", "stage": "PY-L2", "strand": "basics", "prereqs": ["PY-1"]}],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "la-uni.json").write_text(json.dumps({
        "pack": {"id": "la-uni", "subject": "math", "edition": "线性代数", "system": "uni", "kind": "course"},
        "strands": [{"id": "m", "name": "矩阵"}],
        "kps": [{"id": "LA-1", "name": "矩阵乘法", "stage": "U1", "strand": "m"}],
    }, ensure_ascii=False), encoding="utf-8")
    c = Catalog().load(tmp_path)
    assert not c.errors, c.errors
    py, la = c.packs["py-kids"], c.packs["la-uni"]
    assert py.kind == "course" and py.stages == ["PY-L1", "PY-L2"] and py.subject_name == "编程"
    assert py.item_lang == "zh" and not py.international and py.domain == "computing"
    assert stage_rank("PY-L1") < stage_rank("G4") < stage_rank("PY-L2") < stage_rank("U1")
    assert la.kind == "course" and c.default_stage("la-uni", "U2") == "U1"
    assert c.subject("programming")["icon"] and c.subject("nope")["icon"] == "📘"


def test_pack_languages():
    from app.catalog import catalog
    if not catalog.packs:
        catalog.load()
    assert catalog.packs["phy-cambridge"].item_lang == "en" and catalog.packs["phy-cambridge"].international
    assert catalog.packs["eng-shanghai"].item_lang == "en" and catalog.packs["eng-shanghai"].teach_lang == "zh"
    assert catalog.packs["chn-igcse"].item_lang == "zh"  # 国际学校的中文课：题目和讲解都是中文
    assert catalog.packs["math-shanghai"].kind == "textbook" and catalog.packs["math-cambridge"].kind == "syllabus"
