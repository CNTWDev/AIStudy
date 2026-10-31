"""游戏里的题只出自这个人在学的东西：成人没选数学课，往上跳、向下跳、左右飞（所有游戏）都不出口算；
什么都出不了就开局前直说，不拿一年级的「2 - 2 = ?」凑（app/arena/sources.py 开头）。"""
import random

from fastapi.testclient import TestClient

from app import auth, db, quickgen, words
from app.arena import sources
from app.arena.errors import ArenaError
from app.catalog import catalog, stage_rank
from app.main import app

import pytest


def _user(email, grade, packs=(), pw=None):
    pa = auth.by_email("gq-p@x.com") or {"id": auth.create_user("gq-p@x.com", "secret1", "家长", "parent")}
    uid = auth.create_user(email, pw or ("secret1" if grade == "ADULT" else "2580"), "学习者", "kid", parent_id=pa["id"], grade=grade)
    for p in packs:
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active,track) VALUES(?,?,?,1,?)", uid, p, grade, None)
    return dict(db.one("SELECT * FROM users WHERE id=?", uid))


def _srcs(kid, n=40, shape="choices"):
    rng, st, out = random.Random(3), {}, []
    for _ in range(n):
        out.append(sources.next_item(kid, st, 0.75, rng, shape=shape))
    return out


def _stage(it):
    return stage_rank(catalog.kps[it["kp_id"]]["stage"]) if it.get("kp_id") in catalog.kps else None


def test_adult_without_math_gets_no_arithmetic():
    with TestClient(app):
        for i, packs in enumerate(([], ["ceo-core"], ["psy-core"])):
            adult = _user(f"gq-a{i}@x.com", "ADULT", packs)
            assert "math" not in sources.weights(adult)
            items = _srcs(adult)
            assert items and all(it["src"] != "math" for it in items)
            assert not any(it.get("family") in quickgen.FAMILIES or str(it.get("family", "")).split(":")[0] in quickgen.FAMILIES
                           for it in items)


def test_nothing_to_ask_is_said_before_the_game():
    with TestClient(app) as c:
        adult = _user("gq-none@x.com", "ADULT")
        for wl in words.lists(adult["id"]):   # 单词包都停了，也没选课
            words.set_pack(adult["id"], wl["id"], False)
        assert sources.weights(adult) == {}
        with pytest.raises(ArenaError) as e:
            sources.pick(adult, 0.75, random.Random(1), shape="choices")
        assert "在学的课和单词包" in str(e.value)
        c.post("/login", data={"email": "gq-none@x.com", "password": "secret1"})
        for game in ("jump", "helix", "fly"):
            r = c.post("/api/arena/start", json={"game": game})
            assert r.status_code == 409 and "在学的课和单词包" in r.json()["error"], r.text


def test_math_follows_the_learners_grade():
    with TestClient(app):
        g3 = _user("gq-g3@x.com", "G3", ["math-shanghai"])
        g8 = _user("gq-g8@x.com", "G8", ["math-shanghai"])
        small = [it for it in _srcs(g3) if it["src"] == "math"]
        assert small and all(_stage(it) <= 3 for it in small)
        big = [it for it in _srcs(g8) if it["src"] == "math"]
        assert big and all(_stage(it) >= 8 - sources.FAR_BEHIND + 1 for it in big)   # 初二不出一年级的 10 以内加减
        assert not any(it.get("kp_id") == quickgen.FAMILIES["add10"][0] for it in big)


def test_dropped_math_course_stops_arithmetic():
    with TestClient(app):
        kid = _user("gq-drop@x.com", "G5", ["math-shanghai", "eng-cambridge"])
        assert "math" in sources.weights(kid)
        db.run("UPDATE enrollments SET active=0 WHERE user_id=? AND pack_id='math-shanghai'", kid["id"])
        assert "math" not in sources.weights(kid)
        assert all(it["src"] != "math" for it in _srcs(kid, 20))
