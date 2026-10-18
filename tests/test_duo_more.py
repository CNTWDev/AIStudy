"""双人闯关自动生成的关卡（app/arena/games/levels/duo_more.json，tools/gen_duo_levels.py 生成）：
格式对、地图不重复、抽查能过关（宝石都拿得到、不会卡死）、越往后越难。"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import duo_fast  # noqa: E402
from duo_solver import solve as slow_solve  # noqa: E402

from app.arena.games.duo import Duo  # noqa: E402

PACK = ROOT / "app/arena/games/levels/duo_more.json"
HANDMADE = sorted((ROOT / "app/arena/games/levels/duo").glob("*.json"))
LEVELS = json.loads(PACK.read_text(encoding="utf-8"))
FIRST = len(HANDMADE) + 1          # 第 17 关
N = len(LEVELS)


def _sample():
    """抽查哪些关：前 5 关、最后 5 关、中间每 25 关一关。"""
    return sorted(set(range(5)) | set(range(N - 5, N)) | set(range(0, N, 25)))


def test_count_and_format():
    assert N >= 300
    for i, lv in enumerate(LEVELS):
        name = f"duo_more/{i + FIRST}"
        assert set(lv) == {"name", "par", "tip", "map", "objects"}, name
        assert lv["name"] and lv["tip"] and isinstance(lv["par"], int) and lv["par"] > 0, name
        Duo.check(None, copy.deepcopy(lv), name)
        for o in lv["objects"]:   # 机关整个都在地图里
            w, h = o.get("w", 1), o.get("h", 1)
            assert 0 < o["x"] and o["x"] + w <= 31 and 0 < o["y"] and o["y"] + h <= 14, (name, o)
            if o["k"] == "lift":
                assert 0 < o["to"] < 14 and o["to"] != o["y"], (name, o)
        gems = sum(r.count("f") + r.count("w") for r in lv["map"])
        assert gems >= 2, name


def test_unique():
    maps = [tuple(json.loads(p.read_text(encoding="utf-8"))["map"]) for p in HANDMADE]
    maps += [tuple(lv["map"]) for lv in LEVELS]
    assert len(set(maps)) == len(maps)
    for w in range(0, N, 4):   # 同一个世界（4 关）里名字不重样
        names = [lv["name"] for lv in LEVELS[w:w + 4]]
        assert len(set(names)) == len(names), names


@pytest.mark.parametrize("i", _sample())
def test_sample_solvable(i):
    r = duo_fast.solve(LEVELS[i], strict=True)
    assert r["ok"] and r["gems_ok"] and not r["dead"], (i + FIRST, r)


def test_fast_solver_matches_slow():
    """快速版（不加保守跳跃时）和 duo_solver 结果一样：手做的几关 + 生成的前两关。"""
    lvs = [json.loads(HANDMADE[k].read_text(encoding="utf-8")) for k in (4, 5, 12, 15)] + LEVELS[:2]
    for lv in lvs:
        a, b = slow_solve(lv), duo_fast.solve(lv, strict=False, live=False)
        assert (a["ok"], a["steps"], a["gems_ok"], a["states"]) == (b["ok"], b["steps"], b["gems_ok"], b["states"])


def test_harder_later():
    first = [duo_fast.solve(LEVELS[i], live=False)["steps"] for i in range(0, 50, 10)]
    last = [duo_fast.solve(LEVELS[i], live=False)["steps"] for i in range(N - 50, N, 10)]
    assert sum(last) / len(last) > sum(first) / len(first)
    par = [lv["par"] for lv in LEVELS]   # 三星时间跟着步数走
    assert sum(par[-50:]) > sum(par[:50])
