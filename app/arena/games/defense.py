"""星星守卫（单机守城）：小怪兽一波波来，答对放星星魔法。没有关卡。前端：app/static/play/defense.js。"""
from . import Game, register


@register
class Defense(Game):
    id, name, icon, color, tag = "defense", "星星守卫", "🏰", "#6d4fd8", "守城"
    desc = "小怪兽一波波来了！答对放星星魔法，守住城堡，打败大怪兽。"
    css = True
    target, max_seconds = 0.72, 600

    def match_stickers(self, result, stats):
        return (["guard5"] if (stats.get("wave") or 0) >= 5 else []) + (["boss"] if stats.get("boss") else [])
