"""过关仪式（app/static/play/fanfare.js，DESIGN 5.64）：所有游戏共用一处、不撑出滚动条、动画不会一直转下去、各档都有。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "app"
FX = (ROOT / "static/play/fanfare.js").read_text(encoding="utf-8")
SHELL = (ROOT / "static/play/shell.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/play/play.css").read_text(encoding="utf-8")
JUICE = (ROOT / "static/juice.js").read_text(encoding="utf-8")
PAGE = (ROOT / "templates/arena_game.html").read_text(encoding="utf-8")


def test_every_game_gets_it_through_the_shell():
    """所有游戏一个页面、一个外壳：页面在外壳之前加载 fanfare.js，外壳赢了才叫它，游戏自己不叫。"""
    assert PAGE.index("play/fanfare.js") < PAGE.index("play/shell.js")
    assert 'id="fx-pal"' in PAGE
    assert "Fanfare.play(stageEl" in SHELL and "fx.go(cheer(res))" in SHELL and "fx.stars(endEl)" in SHELL
    for f in (ROOT / "static/play").glob("*.js"):
        if f.name not in ("fanfare.js", "shell.js"):
            assert "Fanfare." not in f.read_text(encoding="utf-8"), f.name


def test_layers_live_in_the_stage_not_the_scroll_panel():
    """光芒、彩带、章都挂在 .pl-stage 上（overflow:hidden），不进能滚动的结算面板（DESIGN 5.62）。"""
    assert "stage.appendChild(back); stage.appendChild(front);" in FX
    rules = re.findall(r"([^{}]+)\{[^}]*\}", CSS)
    for sel in rules:
        if "fx-" in sel and ".pl-end" in sel:
            assert "animation" not in CSS[CSS.index(sel):CSS.index("}", CSS.index(sel))], sel


def test_nothing_loops_forever():
    """动只跟着孩子过关的那一下：没有 infinite 的动画；光芒转一会儿就停。"""
    block = CSS[CSS.index("过关仪式（fanfare.js"):]
    assert "infinite" not in block
    assert "fxrays 9s" in block and "forwards" in block


def test_tiers_skip_and_calm_versions():
    for k in ("'all'", "'world'", "新纪录！", "全部打通！", "世界打通！"):
        assert k in SHELL, k
    assert "pointerdown', skip" in FX and "keydown', skip" in FX       # 点一下就跳过
    assert "J.who() === 'adult'" in FX                                  # 成人安静版
    assert "J.still()" in FX                                            # 减少动态效果
    assert "Play.slow" in FX and "Play.slow ||" in (ROOT / "static/play/engine.js").read_text(encoding="utf-8")


def test_sounds_exist():
    for name in ("fanfare", "starN", "rise", "burst"):
        assert re.search(rf"\b{name}: \(?t", JUICE), name


def test_lose_is_gentle_with_progress():
    """没过关：不撒彩带，写「这一关走到了 N%」。"""
    assert "这一关走到了" in SHELL and "report.far" in SHELL
    for g in ("jump", "fly", "helix", "runner"):
        assert "far:" in (ROOT / f"static/play/{g}.js").read_text(encoding="utf-8"), g
