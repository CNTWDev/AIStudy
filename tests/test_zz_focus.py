"""一口气（app/focus.py）：按学段起步、第一周只量不加、连续撑住加长、撑不住退一档；离开记录；家长页。"""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import ability, db, focus
from app.main import app

from test_zz_mix import _setup

MIN = focus.MIN
D0 = date(2026, 10, 1)


def _b(day_offset, held=True, ended="target", active=6 * MIN):
    return {"day": (D0 + timedelta(days=day_offset)).isoformat(), "held": held, "ended": ended, "active_ms": active}


def test_profile_by_stage():
    with TestClient(app):   # 学段表（含成人 ADULT）随应用一起加载
        _profiles()


def _profiles():
    assert focus.profile("G3")["start"] == 5 and focus.profile("G3")["move"]
    assert focus.profile("G8")["start"] == 10 and focus.profile("G8")["cap"] == 25
    assert focus.profile("IGCSE")["stage"] == "junior"
    assert focus.profile("G11")["start"] == 15 and focus.profile("G11")["cap"] == 40
    assert focus.profile("ADULT")["stage"] == "adult" and not focus.profile("ADULT")["move"]


def test_first_week_only_measures():
    p = focus.profile("G3")
    lv = focus.level_from([_b(0)] * 9, p, D0 + timedelta(days=3))
    assert lv["measuring"] and lv["target_ms"] == 5 * MIN and lv["days_left"] == 4
    assert focus.level_from([], p, D0)["target_ms"] == 5 * MIN


def test_changing_criterion():
    p = focus.profile("G3")
    week = [_b(0), _b(1), _b(2)]
    today = D0 + timedelta(days=10)
    # 第一周之后连续撑住 3 口 → 加 1 分钟；再 3 口 → 再加
    assert focus.level_from(week + [_b(8)] * 3, p, today)["target_ms"] == 6 * MIN
    assert focus.level_from(week + [_b(8)] * 6, p, today)["target_ms"] == 7 * MIN
    # 连续 2 口没撑住 → 退一档；提前收尾的不加也不减，但打断连续
    assert focus.level_from(week + [_b(8)] * 3 + [_b(9, held=False)] * 2, p, today)["target_ms"] == 5 * MIN
    assert focus.level_from(week + [_b(8)] * 2 + [_b(8, held=False, ended="early")] + [_b(8)], p, today)["target_ms"] == 5 * MIN
    # 上限
    assert focus.level_from(week + [_b(8)] * 300, p, today)["target_ms"] == 20 * MIN
    # 第一周大多撑不住：起步短一档，最短 3 分钟
    bad = [_b(0, held=False), _b(1, held=False), _b(2)]
    assert focus.level_from(bad, p, today)["target_ms"] == 4 * MIN
    assert focus.level_from(bad + [_b(9, held=False)] * 20, p, today)["target_ms"] == focus.FLOOR_MS
    # 打开就走的不算一口气
    assert focus.level_from([_b(0, held=False, ended="quit", active=20000)] * 5, p, D0)["days_left"] == 7


def test_sentence_only_good_news():
    assert "一直在线" in focus.sentence([[300000, 1]], False)
    assert "后半段" in focus.sentence([[60000, 0], [200000, 1]], False)
    assert "3 分钟" in focus.sentence([[180000, 1], [200000, 0], [10000, 1]], False)
    assert "更稳" in focus.sentence([[10000, 1], [200000, 0], [10000, 1]], False)
    assert "%" not in focus.sentence([[10000, 1], [200000, 0]], True)


def test_block_api_and_parent(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "fz1@x.com", grade="G3", subj={"subj_math": "math-shanghai"})
        b = c.post("/api/focus/start", json={}).json()
        assert b["target_ms"] == 5 * MIN and b["move"] and b["rest_s"] == 120
        # 换页面：带着 id 来就接着这口气
        assert c.post("/api/focus/sync", json={"id": b["id"], "active_ms": 90000, "segs": [[60000, 1], [30000, 1]]}).json()["ok"]
        again = c.post("/api/focus/start", json={"resume": b["id"]}).json()
        assert again["id"] == b["id"] and again["active_ms"] == 90000 and again["segs"] == [[90000, 1]]
        r = c.post("/api/focus/end", json={"id": b["id"], "ended": "target", "active_ms": 5 * MIN, "n_items": 8,
                                           "segs": [[5 * MIN, 1]]}).json()
        assert r["held"] and "一直在线" in r["sentence"] and r["minutes"] == 5
        # 第二口：中间离开了 40 秒 → 没撑住
        b2 = c.post("/api/focus/start", json={"resume": b["id"]}).json()
        assert b2["id"] != b["id"]
        c.post("/api/focus/leave", json={"block": b2["id"], "ms": 40000, "kind": "away", "what": "after_wrong", "task": "数学混合练习"})
        c.post("/api/focus/leave", json={"block": b2["id"], "ms": 1000, "kind": "away"})   # 太短，不记
        r2 = c.post("/api/focus/end", json={"id": b2["id"], "ended": "target", "active_ms": 5 * MIN, "segs": [[4 * MIN, 1], [MIN, 0]]}).json()
        assert not r2["held"]
        assert db.one("SELECT COUNT(*) AS n FROM focus_leaves WHERE user_id=?", kid)["n"] == 1
        assert c.post("/api/focus/end", json={"id": b2["id"], "ended": "target"}).json() == {}   # 结束过的不能再结束
        c.get("/logout")
        c.post("/login", data={"email": "mixp@x.com", "password": "secret1"})
        assert c.post("/api/focus/start", json={}).json() == {}   # 家长查看不算
        page = c.get("/parent").text
        assert "一口气：现在每口气 5 分钟" in page and "撑住 1 口" in page and "数学混合练习" in page and "多是刚答错" in page


def test_stale_block_closed(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "fz2@x.com", grade="G8")
        b = c.post("/api/focus/start", json={}).json()
        assert b["target_ms"] == 10 * MIN
        db.run("UPDATE focus_blocks SET updated_at='2020-01-01T00:00:00' WHERE id=?", b["id"])
        b2 = c.post("/api/focus/start", json={"resume": b["id"]}).json()
        assert b2["id"] != b["id"]
        assert db.one("SELECT ended FROM focus_blocks WHERE id=?", b["id"])["ended"] == "quit"
        assert db.one("SELECT COUNT(*) AS n FROM focus_blocks WHERE user_id=? AND ended='open'", kid)["n"] == 1


def test_expect_needs_history(monkeypatch):
    with TestClient(app) as c:
        kid = _setup(c, monkeypatch, "fz3@x.com", grade="G3", subj={"subj_math": "math-shanghai"})
        item = {"family": "add", "level": 2}
        assert ability.expect(kid, "MSH-NUM-17", item) is None
        for _ in range(3):
            ability.observe(kid, "MSH-NUM-17", item, True)
        p = ability.expect(kid, "MSH-NUM-17", item)
        assert p is not None and 0 < p < 1
