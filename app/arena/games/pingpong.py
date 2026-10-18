"""乒乓球对战（家里两个人联网对战）：每局 7 分（6 平以后要多赢 2 分，最多打到 10 分），三局两胜；输了一局的人答对 5 道题，再接着打下一局。
前端：app/static/play/pingpong.js。

- 没有关卡、没有星星；一场打完记在 game_sessions 里（赢的人 winner、局分），只和家里人比，不排名。
- 让球（team_setup）：年级差得多，小的那个球拍大一些、飞向他的球慢一些；之后按两个人最近几场谁赢得多自动调，
  老输的那个越来越好打、老赢的那个越来越难（目标是两个人各赢一半左右，docs/DESIGN.md 5.12 的公平原则）。
- 答完 5 道题，下一局球拍再大一点（答题有用，也让输的人追得上）。
- 球由接球的那台设备说了算：球飞到我这边，我的设备判断接没接到（操作没有延迟），接到了把球的速度方向发给对方；
  没接到就告诉对方得分。
"""
from ... import db
from ...catalog import stage_rank
from . import Game, register

POINTS, CAP, TO_WIN = 7, 10, 2  # 每局几分（平了要多赢 2 分，先到 CAP 分的赢）、赢几局算赢这一场
HISTORY = 6                    # 看最近几场来调让球
STEP = 0.1                     # 最近每多输一场，让球多 0.1


@register
class PingPong(Game):
    id, name, icon, color, tag = "pingpong", "乒乓球对战", "🏓", "#e8553d", "联网对战"
    desc = "和家里人打乒乓球，每局 7 分、三局两胜。输了一局答对 5 道题，再接着打！"
    players, freeze, max_seconds = (2, 2), False, 900
    record = ""

    def team_setup(self, team):
        ids = [int(i) for i in team.split(",") if i]
        users = {u["id"]: dict(u) for u in db.q(f"SELECT id, grade FROM users WHERE id IN ({','.join('?' * len(ids))})", *ids)} if ids else {}
        ranks = {i: min(12.0, stage_rank((users.get(i) or {}).get("grade") or "G3")) for i in ids}
        top = max(ranks.values(), default=0)
        net = dict.fromkeys(ids, 0)
        for s in db.q("SELECT data FROM game_sessions WHERE game=? AND team=? AND result='win' AND ended_at IS NOT NULL "
                      "ORDER BY id DESC LIMIT ?", self.id, team, HISTORY):
            w = db.jload(s["data"], {}).get("winner")
            if w in net:
                for i in ids:
                    net[i] += -1 if i == w else 1
        assist = {str(i): round(max(0.0, min(1.0, min(0.8, (top - ranks[i]) * 0.16) + STEP * net[i])), 2) for i in ids}
        return {"points": POINTS, "cap": CAP, "to_win": TO_WIN, "assist": assist}

    def clean(self, lv, rep):
        out = super().clean(lv, rep)
        try:
            out["winner"] = int(rep.get("winner") or 0)
            out["won"] = max(0, min(TO_WIN, int(rep.get("won") or 0)))
            out["lost"] = max(0, min(TO_WIN, int(rep.get("lost") or 0)))
        except (TypeError, ValueError):
            out.update(winner=0, won=0, lost=0)
        return out
