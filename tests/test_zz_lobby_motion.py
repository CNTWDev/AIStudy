"""大厅房间列表能滚动：里面的房间卡片自己不能放大（放大就撑出横向滚动条，裁掉又看不全，DESIGN 5.62）。"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "app/static/play/mp.css").read_text(encoding="utf-8")


def _keyframes(name):
    m = re.search(r"@keyframes\s+%s\s*\{(.*?\})\s*\}" % re.escape(name), CSS, re.S)
    assert m, name
    return m.group(1)


def test_room_cards_in_scroll_list_do_not_grow():
    card_anims = re.findall(r"\.mp-invite(?:\.[\w-]+)*\s*\{[^}]*animation:\s*([\w-]+)", CSS)
    assert card_anims, "家里人的房间应该有「叫你」的动画"
    for name in card_anims:
        if name == "none":
            continue
        assert "transform" not in _keyframes(name), f"房间卡片动画 {name} 不能改尺寸"


def test_scroll_list_reserves_room_for_the_glow():
    rule = re.search(r"\.mp-rooms\s*\{([^}]*)\}", CSS).group(1)
    assert "overflow-y:auto" in rule and "padding:" in rule and "overflow-x" not in rule
