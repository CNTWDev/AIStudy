"""「这课我熟，考我一下」：只能在这里学的课（engine.SELF_TAUGHT），学员已经懂的课不用从头上（docs/没学就先考-讨论稿.md v2）。

没上过的课，学习页第一张卡上可以点「考我一下」：出 N 道能自动判对错的题（选择、填空……，思辨题不算），
都答对就算上过这一课（lessons.done），今天的「新的一课」打勾；错了一道就照常往下学，错的题和平常一样进错题本，上完课再回来。
题走平常的作答入口（/api/answer），这里只核对：这几道题是这一课的、能自动判的、今天答过而且都答对了。"""
from . import db, engine, itemtypes, lessons
from .catalog import catalog

N = 2


def offered(user_id: int, kp_id: str) -> bool:
    """这一课能不能「考我一下」：只能在这里学的课，还没上过。"""
    pack = catalog.kp_pack.get(kp_id)
    return bool(pack) and engine.self_taught(pack) and kp_id in engine.unlearned(user_id)


def items(user_id: int, kp_id: str, grade: str) -> list[dict]:
    got = [i for i in engine.items_for(user_id, kp_id, n=N + 2, purpose="practice", grade=grade) if itemtypes.auto(i)]
    return got[:N]


def passed(user_id: int, kp_id: str, item_ids: list) -> bool:
    ids = list(dict.fromkeys(str(i) for i in item_ids or []))
    if len(ids) < N:
        return False
    today = db.today().isoformat()
    for iid in ids:
        row = db.one("SELECT * FROM items WHERE id=?", iid)
        if not row:
            return False
        it = engine._item_row_to_dict(row)
        mine = {it["kp_id"], *(it.get("kp_ids") or [])} | {r["kp_id"] for r in db.q("SELECT kp_id FROM item_kps WHERE item_id=?", iid)}
        if kp_id not in mine or not itemtypes.auto(it):
            return False
        last = db.one("SELECT correct FROM attempts WHERE user_id=? AND item_id=? AND kp_id=? AND created_at>=? "
                      "ORDER BY id DESC LIMIT 1", user_id, iid, kp_id, today)
        if not last or not last["correct"]:
            return False
    lessons.done(user_id, kp_id)
    return True
