"""闪电赛跑（单机）：答对一题冲刺一段，和「上次的我」赛跑。没有关卡。前端：app/static/play/race.js。"""
from . import Game, register


@register
class Race(Game):
    id, name, icon, color, tag = "race", "闪电赛跑", "⚡", "#2f6fdc", "竞速"
    desc = "答对一题冲刺一段，和「上次的我」赛跑，看谁先到终点。"
    css = True
    target, max_seconds = 0.8, 300

    def match_stickers(self, result, stats):
        return ["race_win"] if result == "win" else []
