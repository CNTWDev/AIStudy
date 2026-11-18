"""口语「开口练」（docs/DESIGN.md §5.67；讨论稿 /mnt/project-files/aistudy/docs/成人英语口语开口练-讨论稿.md）。

一个独立功能，和平台核心松耦合：只用账号、AI（app/llm）、朗读（app/tts）、语音引擎（app/stt）这几样公共服务；
平台其他地方只通过 today_card()（今天页的入口）用到它。把这个模块拿掉，别的照常运行。

一条主线：拿一件真事说一遍，分四步。
  0 复习：表达库里到期的表达，只给中文，开口说英文（1/2/4/7 天；每天最多 REVIEW_CAP 个，多的往后排，多练不变成明天的债）
  1 先说：英文为主，卡住直接说中文，这一遍不评
  2 地道版：AI 改的是「他自己刚说的话」，最多 3 个表达块，只改 1 处
  3 跟读：一句一句，只看哪个词没被识别出来（配了发音评测再多看哪个音没读准）
  4 再说一遍：不看稿，和第一遍对比（开口用时、卡顿、用上几个新表达、说了几处中文）
练完不说「完成」，给三个选择：再说一件 / 和 AI 聊下去（角色扮演）/ 先到这里。

内容分两份：
  公共的：口语课程包（content/speaking/*.json：场景、参考说法、表达块、聊天的局）和表达块词典（speak_chunks）
  只属于本人的：录音、转出来的文字、他那句话的地道版、常犯的错、「我有件事要说」里的真实内容（永远不进公共库）
"""
import difflib
import json
import logging
import os
import re
import secrets
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from . import config, db, llm
from .catalog import is_adult
from .stt import STTError, Speech, Transcript, load_settings
from .stt.providers import norm_word

PACK_DIR = config.BASE_DIR / "content" / "speaking"
STT_CONFIG_FILE = Path(os.environ.get("STT_CONFIG_FILE", config.BASE_DIR / "config" / "stt.toml"))
CLIP_DIR = config.DATA_DIR / "speaking"

REVIEW_CAP = 5                         # 每天最多复习几个表达（多的排队往后，哪天多练了，第二天也不会变多）
STEPS = (1, 2, 4, 7, 14, 30, 60)       # 第几次说出来以后隔几天再来（前四步和单词一样 1/2/4/7）
PAUSE = 2.0                            # 两个词之间停多久算「卡住」
CHAT_MAX = 8                           # 聊一局最多几轮
CLIP_MAX = 4 * 1024 * 1024             # 一段录音最大字节数（16kHz 16 位单声道 ≈ 2 分钟）
_CJK = re.compile(r"[㐀-鿿]")
log = logging.getLogger(__name__)


class SpeakError(HTTPException):
    pass


# ------------------------------------------------------------------ 语音引擎（app/stt）

_svc: Speech | None = None


def engine() -> Speech:
    global _svc
    if _svc is None:
        s = load_settings(STT_CONFIG_FILE)
        prov = os.environ.get("STT_PROVIDER")
        if prov and not STT_CONFIG_FILE.exists():   # 测试 / 本地演示
            s.transcribe = prov
            s.assess = os.environ.get("STT_ASSESS", prov)
        _svc = Speech(s)
    return _svc


def reload_engine() -> Speech:
    global _svc
    _svc = None
    return engine()


# ------------------------------------------------------------------ 口语课程包（公共内容）

@lru_cache(maxsize=1)
def _load() -> dict:
    out = {}
    for f in sorted(PACK_DIR.glob("*.json")):
        p = json.loads(f.read_text(encoding="utf-8"))
        for g in p["groups"]:
            for s in g["scenes"]:
                s["pack"], s["group"] = p["id"], g["name"]
        out[p["id"]] = p
    return out


def packs(user=None) -> list[dict]:
    """这个人能用的口语课程包（以后孩子的包按 audience 开放）。"""
    ps = sorted(_load().values(), key=lambda p: p.get("order", 99))
    if user is not None:
        adult = is_adult(user["grade"])
        ps = [p for p in ps if (p.get("audience", "adult") == "adult") == adult]
    return ps


def pack(pid: str) -> dict:
    p = _load().get(pid)
    if not p:
        raise SpeakError(404, "没有这个口语课程包")
    return p


def scenes(pid: str) -> list[dict]:
    return [s for g in pack(pid)["groups"] for s in g["scenes"]]


def scene(pid: str, sid: str) -> dict:
    for s in scenes(pid):
        if s["id"] == sid:
            return s
    raise SpeakError(404, "没有这个场景")


def play(play_id: str) -> dict:
    for p in _load().values():
        for x in p.get("plays", []):
            if x["id"] == play_id:
                return {**x, "pack": p["id"]}
    raise SpeakError(404, "没有这一局")


def available(user) -> bool:
    return bool(user) and user["role"] == "kid" and bool(packs(user))


# ------------------------------------------------------------------ 个人设置（users.settings.speak）

def prefs(user) -> dict:
    st = (db.jload(user["settings"], {}) or {}).get("speak") or {}
    mine = [p["id"] for p in packs(user)]
    chosen = [p for p in st.get("packs") or [] if p in mine] or mine[:1]   # 默认只开第一个包，避免一上来太多
    return {"packs": chosen, "daily": bool(st.get("daily"))}


def set_prefs(user, packs_on=None, daily=None) -> dict:
    u = db.one("SELECT settings FROM users WHERE id=?", user["id"])
    st = db.jload(u["settings"], {}) or {}
    sp = st.get("speak") or {}
    if packs_on is not None:
        mine = {p["id"] for p in packs(user)}
        sp["packs"] = [p for p in packs_on if p in mine]
    if daily is not None:
        sp["daily"] = bool(daily)
    st["speak"] = sp
    db.run("UPDATE users SET settings=? WHERE id=?", db.jdump(st), user["id"])
    return sp


# ------------------------------------------------------------------ 表达块（公共词典 + 个人记忆）

def chunk_key(en: str) -> str:
    s = en.lower().replace("…", "...").replace("’", "'")
    s = re.sub(r"\s*\.\.\.\s*", " ... ", s)
    return re.sub(r"\s+", " ", s).strip(" .?!,")


def chunk_upsert(en: str, zh: str = "", example: str = "", source: str = "") -> int:
    """一个表达在公共词典里只存一份；已有就用已有的（不覆盖：课程包写的中文意思优先于 AI 写的）。"""
    key = chunk_key(en)
    if not key:
        raise ValueError("empty chunk")
    row = db.one("SELECT id, zh, example FROM speak_chunks WHERE key=?", key)
    if row:
        if (not row["zh"] and zh) or (not row["example"] and example):
            db.run("UPDATE speak_chunks SET zh=?, example=? WHERE id=?", row["zh"] or zh, row["example"] or example, row["id"])
        return row["id"]
    db.run("INSERT INTO speak_chunks(key,en,zh,example,source,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT (key) DO NOTHING",
           key, en.strip().replace("…", "..."), zh.strip(), example.strip(), source, db.now())
    return db.one("SELECT id FROM speak_chunks WHERE key=?", key)["id"]


_IRREG = {"get": "get|gets|got|gotten|getting", "go": "go|goes|went|gone|going", "make": "make|makes|made|making",
          "take": "take|takes|took|taken|taking", "fall": "fall|falls|fell|fallen|falling", "put": "put|puts|putting",
          "be": "be|is|are|was|were|been|being|'m|'s|'re", "is": "is|'s|was", "are": "are|'re|were", "i'm": "i'm|i am",
          "we've": "we've|we have", "that's": "that's|that is", "bring": "bring|brings|brought|bringing",
          "have": "have|has|had|having", "do": "do|does|did|done|doing", "sb": r"\S+(?:\s+\S+){0,2}?", "sth": r"\S+(?:\s+\S+){0,2}?",
          "run": "run|runs|ran|running", "stick": "stick|sticks|stuck|sticking", "hit": "hit|hits|hitting",
          "set": "set|sets|setting", "drop": "drop|drops|dropped|dropping"}


@lru_cache(maxsize=2048)
def chunk_regex(en: str):
    """表达块在一段话里的样子：... 可以隔 0 到 4 个词，动词可以变形（push back → pushing the launch back）。"""
    gap, toks = object(), []
    for w in chunk_key(en).split():
        if w == "...":
            toks.append(gap)
            continue
        w = w.strip(",.?!:;")
        if not w:
            continue
        if w in _IRREG:
            toks.append(_IRREG[w])
        elif len(w) >= 3 and w.isalpha():
            stem = w[:-1] if w.endswith("e") else w
            toks.append(re.escape(stem) + r"(?:e|es|s|ed|d|ing|" + re.escape(w[-1]) + r"ed|" + re.escape(w[-1]) + "ing)?")
        else:
            toks.append(re.escape(w))
    pat = ""
    for i, t in enumerate(toks):
        pat += r"(?:\s+\S+){0,4}?" if t is gap else ("" if i == 0 else r"\s+") + "(?:" + t + ")"
    return re.compile(r"(?<![\w'])" + pat + r"(?![\w'])", re.I)


def used_chunks(text: str, chunks: list[dict]) -> list[int]:
    t = " " + re.sub(r"[^\w'\s㐀-鿿]", " ", (text or "").replace("’", "'")) + " "
    t = re.sub(r"\s+", " ", t)
    return [c["id"] for c in chunks if chunk_regex(c["en"]).search(t)]


def _due(days: int) -> str:
    return (db.today() + timedelta(days=days)).isoformat()


def learn(user_id: int, chunk_ids: list[int], scene_ref: str) -> int:
    """新学的表达进表达库：明天开始复习。已经在库里的不动（又学一遍不重置进度）。"""
    n = 0
    for cid in chunk_ids:
        n += db.run("INSERT INTO speak_memory(user_id,chunk_id,box,due,scene,learned_at) VALUES(?,?,0,?,?,?) "
                    "ON CONFLICT (user_id, chunk_id) DO NOTHING", user_id, cid, _due(1), scene_ref, db.now())
    return n


def _known(user_id: int) -> list[dict]:
    """今天以前学过的表达（自由说时主动用出来，才算真正会了）。"""
    return [dict(r) for r in db.q("SELECT c.id, c.en FROM speak_memory m JOIN speak_chunks c ON c.id=m.chunk_id "
                                  "WHERE m.user_id=? AND m.learned_at<?", user_id, db.today().isoformat())]


def _count_use(user_id: int, ids: list[int]) -> None:
    for cid in ids:
        db.run("UPDATE speak_memory SET used=used+1 WHERE user_id=? AND chunk_id=?", user_id, cid)


def reviews_today(user_id: int) -> int:
    return db.one("SELECT COUNT(*) AS n FROM speak_memory WHERE user_id=? AND seen_at>=?",
                  user_id, db.today().isoformat())["n"]


def due_chunks(user_id: int, limit: int | None = None) -> list[dict]:
    """今天要复习的表达：到期的、按到期早晚排，每天最多 REVIEW_CAP 个（今天已经复习过的算在里面）。"""
    left = REVIEW_CAP - reviews_today(user_id) if limit is None else limit
    if left <= 0:
        return []
    rows = db.q("SELECT c.id, c.en, c.zh, c.example, m.box, m.used, m.scene FROM speak_memory m JOIN speak_chunks c ON c.id=m.chunk_id "
                "WHERE m.user_id=? AND m.due<=? AND (m.seen_at IS NULL OR m.seen_at<?) ORDER BY m.due, m.learned_at LIMIT ?",
                user_id, db.today().isoformat(), db.today().isoformat(), left)
    return [dict(r) for r in rows]


def grade(user_id: int, chunk_id: int, ok: bool) -> dict:
    m = db.one("SELECT * FROM speak_memory WHERE user_id=? AND chunk_id=?", user_id, chunk_id)
    if not m:
        raise SpeakError(404, "表达库里没有这一个")
    box = min(m["box"] + 1, len(STEPS) - 1) if ok else 0
    days = STEPS[box] if ok else 1
    db.run("UPDATE speak_memory SET box=?, due=?, reps=reps+1, lapses=lapses+?, seen_at=? WHERE user_id=? AND chunk_id=?",
           box, _due(days), 0 if ok else 1, db.now(), user_id, chunk_id)
    return {"ok": ok, "next_in": days, "left": len(due_chunks(user_id))}


def sentence_for(chunk: dict) -> str:
    """复习时给的参考说法：课程包里用到这个表达的那句（没有就用词典里的例句，再没有就是表达本身）。"""
    if chunk.get("example"):
        return chunk["example"]
    for p in _load().values():
        for s in scenes(p["id"]):
            if any(chunk_key(c["en"]) == chunk_key(chunk["en"]) for c in s.get("chunks", [])):
                for sent in sentences(s["model"]):
                    if chunk_regex(chunk["en"]).search(" " + sent + " "):
                        return sent
    return chunk["en"]


# ------------------------------------------------------------------ 从转出来的文字算几个数

def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def metrics(tr: Transcript) -> dict:
    """开口用时（按下录音到第一个英文词）、卡住超过 PAUSE 秒的次数、说了几处中文、英文词数。
    转文字的厂商没有词级时间时，前两个是 None（页面上标「这家识别服务给不出」）。"""
    words = [w for w in tr.words if w.text.strip()]
    en = [w for w in words if not w.zh and norm_word(w.text)]
    zh_spans, prev_zh = 0, False
    for w in words:
        if w.zh and not prev_zh:
            zh_spans += 1
        prev_zh = w.zh
    if not words:   # 厂商只给了整段文字
        zh_spans = len(re.findall(r"[㐀-鿿]+", tr.text or ""))
        en_n = len(re.findall(r"[A-Za-z']+", tr.text or ""))
    else:
        en_n = len(en)
    timed = tr.timed and bool(words)
    latency = round(en[0].start, 1) if timed and en else None
    pauses = sum(1 for a, b in zip(words, words[1:]) if b.start - a.end > PAUSE) if timed else None
    speak_secs = (words[-1].end - words[0].start) if timed else tr.duration
    return {"latency": latency, "pauses": pauses, "zh": zh_spans, "words": en_n, "secs": round(speak_secs or 0, 1), "timed": timed}


def marks(expect: str, said: str, assessment=None) -> list[dict]:
    """跟读：原句的每个词有没有被识别出来（有发音评测就再带上这个词读得准不准）。"""
    exp = expect.split()
    a = [norm_word(w) for w in exp]
    b = [norm_word(w) for w in (said or "").split() if norm_word(w)]
    hit = set()
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        hit.update(range(blk.a, blk.a + blk.size))
    out = [{"w": w, "ok": i in hit or not a[i]} for i, w in enumerate(exp)]
    if assessment and assessment.words:
        aw = [norm_word(w.text) for w in assessment.words]
        for blk in difflib.SequenceMatcher(None, a, aw, autojunk=False).get_matching_blocks():
            for k in range(blk.size):
                ws = assessment.words[blk.b + k]
                if ws.error == "mispronounced" or (ws.score is not None and ws.score < 60):
                    bad = [p["p"] for p in ws.phonemes if p.get("score") is not None and p["score"] < 60][:2]
                    out[blk.a + k]["pron"] = {"score": ws.score, "phonemes": bad}
    return out


# ------------------------------------------------------------------ 录音

def _quota(user_id: int, secs: float) -> None:
    s = engine().s
    today = db.today().isoformat()
    mine = db.one("SELECT COALESCE(SUM(secs),0) AS n FROM speak_clips WHERE user_id=? AND day=?", user_id, today)["n"]
    if mine + secs > s.daily_seconds_per_user:
        raise SpeakError(429, "今天说得够多啦，明天接着练")
    allx = db.one("SELECT COALESCE(SUM(secs),0) AS n FROM speak_clips WHERE day=?", today)["n"]
    if allx + secs > s.daily_seconds_total:
        raise SpeakError(429, "今天全站的语音识别用量到上限了，明天再来")


def clip_file(user_id: int, clip_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{24}", clip_id or ""):
        raise SpeakError(404, "没有这段录音")
    return CLIP_DIR / str(int(user_id)) / f"{clip_id}.wav"


def transcribe(user, wav: bytes, step: str, session_id=None, expect: str = "", accent: str = "en-US") -> dict:
    """存下录音、转文字、算几个数。返回 {clip, text, words, metrics, transcript}。"""
    sp = engine()
    if not sp.ready:
        raise SpeakError(503, "语音识别还没配置好：" + (sp.status()["transcribe"]["why"] or "请管理员在 config/stt.toml 里选一家"))
    if not wav or len(wav) > CLIP_MAX:
        raise SpeakError(400, "录音太长了，分几段说")
    try:
        secs = sp.check_wav(wav)
    except STTError as e:
        raise SpeakError(400, str(e))
    _quota(user["id"], secs)
    try:
        tr = sp.transcribe(wav, [accent.split("-")[0], "zh"], expect)
    except STTError as e:
        record_error("transcribe", str(e), step, user["id"])
        raise SpeakError(503, f"语音识别出错了：{e}")
    cid = secrets.token_hex(12)
    f = clip_file(user["id"], cid)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(wav)
    m = metrics(tr)
    db.run("INSERT INTO speak_clips(id,user_id,session_id,step,day,secs,transcript,metrics,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
           cid, user["id"], session_id, step, db.today().isoformat(), secs, db.jdump(tr.to_dict()), db.jdump(m), db.now())
    return {"clip": cid, "text": tr.text, "metrics": m, "transcript": tr}


def _assess(clip: dict, wav: bytes, accent: str, reference: str):
    try:
        a = engine().assess(wav, accent, reference)
    except STTError as e:
        record_error("assess", str(e), "shadow")
        return None   # 评测是锦上添花：出错就当没配（记下来，后台看得到）
    if a is not None:
        db.run("UPDATE speak_clips SET assess=? WHERE id=?", db.jdump(a.to_dict()), clip["clip"])
    return a


# ------------------------------------------------------------------ 一次练习

def record_error(kind: str, message: str, step: str = "", user_id=None) -> None:
    """语音引擎出错：写进服务器日志，再存一条（后台系统页「语音引擎」卡片上看最近的）。"""
    sp = engine()
    prov = sp.s.transcribe if kind == "transcribe" else sp.s.assess
    log.warning("STT %s error (%s, step=%s, user=%s): %s", kind, prov, step, user_id, message)
    try:
        db.run("INSERT INTO stt_errors(user_id,kind,provider,step,message,created_at) VALUES(?,?,?,?,?,?)",
               user_id, kind, prov, step[:40], message[:1000], db.now())
    except Exception:   # 记错误本身不能再让请求失败
        log.exception("could not store STT error")


def recent_errors(limit: int = 10) -> list[dict]:
    return [dict(r) for r in db.q("SELECT kind, provider, step, message, created_at FROM stt_errors ORDER BY id DESC LIMIT ?", limit)]


def _session(user_id: int, sid: int) -> dict:
    r = db.one("SELECT * FROM speak_sessions WHERE id=? AND user_id=?", sid, user_id)
    if not r:
        raise SpeakError(404, "没有这次练习")
    return {**dict(r), "data": db.jload(r["data"], {}) or {}}


def _save(s: dict, done: bool = False) -> None:
    db.run("UPDATE speak_sessions SET data=?, updated_at=?" + (", done_at=?" if done else "") + " WHERE id=?",
           db.jdump(s["data"]), db.now(), *([db.now()] if done else []), s["id"])


def _new(user_id: int, kind: str, pack_id: str, ref: str, data: dict) -> int:
    return db.insert("INSERT INTO speak_sessions(user_id,day,kind,pack_id,ref,data,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                     user_id, db.today().isoformat(), kind, pack_id, ref, db.jdump(data), db.now(), db.now())


def scene_of(s: dict) -> dict:
    """这次练习的场景：课程包里的，或者「我有件事要说」（只存在这次练习里）。"""
    if s["ref"] == "own":
        return {**s["data"]["scene"], "id": "own", "pack": s["pack_id"], "group": "我有件事要说", "chunks": [], "model": ""}
    return scene(s["pack_id"], s["ref"])


def accent_of(s: dict) -> str:
    return pack(s["pack_id"]).get("accent", "en-US")


def next_scene(user) -> dict | None:
    """系统路线的下一个场景：开着的几个包轮流，每个包按顺序取还没练过的；都练过了从最早练的那个再来。"""
    pr = prefs(user)
    done = {r["ref"]: r["at"] for r in db.q("SELECT ref, MAX(done_at) AS at FROM speak_sessions WHERE user_id=? AND kind='scene' "
                                             "AND done_at IS NOT NULL GROUP BY ref", user["id"])}
    last_pack = db.one("SELECT pack_id FROM speak_sessions WHERE user_id=? AND kind='scene' AND ref<>'own' ORDER BY id DESC LIMIT 1", user["id"])
    order = pr["packs"]
    if last_pack and last_pack["pack_id"] in order and len(order) > 1:   # 轮到下一个包
        i = order.index(last_pack["pack_id"])
        order = order[i + 1:] + order[:i + 1]
    for pid in order:
        for s in scenes(pid):
            if s["id"] not in done:
                return s
    allx = [s for pid in pr["packs"] for s in scenes(pid)]
    return min(allx, key=lambda s: done.get(s["id"], "")) if allx else None


def open_scene(user, pid: str, sid: str, again: int | None = None) -> dict:
    """开始（或接着）练一个场景：今天这个场景还没练完的那次接着练。"""
    scene(pid, sid)
    r = db.one("SELECT id FROM speak_sessions WHERE user_id=? AND day=? AND kind='scene' AND pack_id=? AND ref=? AND done_at IS NULL "
               "ORDER BY id DESC LIMIT 1", user["id"], db.today().isoformat(), pid, sid)
    if r:
        return _session(user["id"], r["id"])
    data = {}
    if again:
        old = _session(user["id"], int(again))
        if old["ref"] == sid and old["data"].get("first"):
            data["again"] = {"session": old["id"], "day": old["day"], "first": old["data"]["first"]}
    return _session(user["id"], _new(user["id"], "scene", pid, sid, data))


def own_scene(user, text: str) -> dict:
    """「我有件事要说」：中文写一句，整理成场景卡。内容只属于本人。"""
    text = (text or "").strip()
    if not 4 <= len(text) <= 400:
        raise SpeakError(400, "用中文写一两句就行：在什么场合、要说什么")
    pid = prefs(user)["packs"][0]
    try:
        sc = llm.speak_scene(text, pack(pid).get("accent", "en-US"), user_id=user["id"])
    except llm.LLMError:
        sc = {"title": text[:12], "situation": text, "goals": []}
    sc = {"title": str(sc.get("title") or text[:12])[:30], "situation": str(sc.get("situation") or text)[:400],
          "goals": [str(g)[:30] for g in (sc.get("goals") or [])][:3], "said": text}
    return _session(user["id"], _new(user["id"], "scene", pid, "own", {"scene": sc}))


def scene_chunks(s: dict) -> list[dict]:
    sc = scene_of(s)
    return [{"id": chunk_upsert(c["en"], c.get("zh", ""), "", "pack:" + sc["pack"]), **c} for c in sc.get("chunks", [])]


def clip(user, sid: int, step: str, wav: bytes) -> dict:
    """一段录音进来：按是哪一步处理。"""
    s = _session(user["id"], sid)
    acc = accent_of(s)
    d = s["data"]
    if step == "first":
        c = transcribe(user, wav, step, s["id"], accent=acc)
        used = used_chunks(c["text"], _known(user["id"]))
        _count_use(user["id"], used)
        c["metrics"]["used"] = used
        d["first"] = {"clip": c["clip"], "text": c["text"], "metrics": c["metrics"]}
        d.pop("rewrite", None)
        _save(s)
        return {"clip": c["clip"], "text": c["text"], "metrics": c["metrics"], "again": d.get("again")}
    if step.startswith("shadow:"):
        rw = d.get("rewrite") or {}
        i = int(step.split(":")[1])
        sents = rw.get("sentences") or []
        if not 0 <= i < len(sents):
            raise SpeakError(400, "没有这一句")
        c = transcribe(user, wav, step, s["id"], expect=sents[i], accent=acc)
        a = _assess(c, wav, acc, sents[i])
        mk = marks(sents[i], c["text"], a)
        sh = d.setdefault("shadow", {})
        prev = sh.get(str(i)) or {"tries": 0}
        sh[str(i)] = {"tries": prev["tries"] + 1, "clip": c["clip"], "missed": sum(1 for m in mk if not m["ok"])}
        _save(s)
        return {"clip": c["clip"], "text": c["text"], "marks": mk, "tries": sh[str(i)]["tries"],
                "scores": (a.scores if a else None), "assessed": a is not None}
    if step == "retell":
        if not d.get("rewrite"):
            raise SpeakError(400, "先看地道版")
        c = transcribe(user, wav, step, s["id"], accent=acc)
        chunks = d["rewrite"]["chunks"]
        used_new = used_chunks(c["text"], chunks)
        known_used = [x for x in used_chunks(c["text"], _known(user["id"])) if x not in used_new]
        _count_use(user["id"], known_used)
        c["metrics"]["used"] = used_new
        d["retell"] = {"clip": c["clip"], "text": c["text"], "metrics": c["metrics"]}
        learned = learn(user["id"], [x["id"] for x in chunks], s["ref"] if s["ref"] != "own" else "own")
        d["compare"] = compare(d["first"]["metrics"], c["metrics"], len(chunks))
        _save(s, done=True)
        return {"clip": c["clip"], "text": c["text"], "compare": d["compare"], "learned": learned,
                "used": used_new, "first_clip": d["first"]["clip"]}
    raise SpeakError(400, "不认识这一步")


def compare(first: dict, now: dict, n_chunks: int) -> list[dict]:
    rows = [{"k": "开口用时", "a": first.get("latency"), "b": now.get("latency"), "unit": "秒", "better": "low"},
            {"k": f"卡住超过 {int(PAUSE)} 秒", "a": first.get("pauses"), "b": now.get("pauses"), "unit": "次", "better": "low"},
            {"k": "新表达用上", "a": 0, "b": len(now.get("used") or []), "of": n_chunks, "better": "high"},
            {"k": "说中文", "a": first.get("zh"), "b": now.get("zh"), "unit": "处", "better": "low"}]
    return [r for r in rows if r["a"] is not None and r["b"] is not None]


def rewrite(user, sid: int) -> dict:
    """第 2 步：地道版。AI 不可用时退回课程包里写好的参考说法（自己的场景就只能等 AI）。"""
    s = _session(user["id"], sid)
    d = s["data"]
    if not d.get("first"):
        raise SpeakError(400, "先说一遍")
    sc = scene_of(s)
    pack_chunks = scene_chunks(s)
    try:
        r = llm.speak_rewrite(sc, d["first"]["text"], accent_of(s), pack_chunks, user_id=user["id"])
        native = str(r.get("native") or "").strip()
        raw = [c for c in r.get("chunks") or [] if isinstance(c, dict) and c.get("en")][:3]
        fix = r.get("fix") if isinstance(r.get("fix"), dict) and r["fix"].get("right") else None
        src = "ai"
    except llm.LLMError as e:
        if not sc.get("model"):
            raise SpeakError(503, str(e))
        native, raw, fix, src = sc["model"], [{**c, "yours": ""} for c in sc["chunks"]], None, "pack"
    chunks = []
    for c in raw:
        en = str(c["en"]).strip()
        ex = next((x for x in sentences(native) if chunk_regex(en).search(" " + x + " ")), "")
        cid = chunk_upsert(en, str(c.get("zh") or ""), ex if src == "pack" else "", "pack" if src == "pack" else "ai")
        chunks.append({"id": cid, "en": en, "zh": str(c.get("zh") or ""), "yours": str(c.get("yours") or "")})
    d["rewrite"] = {"native": native, "sentences": sentences(native), "chunks": chunks, "fix": fix, "src": src}
    _save(s)
    return d["rewrite"]


# ------------------------------------------------------------------ 和 AI 聊下去

def open_chat(user, play_id: str = "", custom: str = "") -> dict:
    pid = prefs(user)["packs"][0]
    if custom:
        custom = custom.strip()
        if not 4 <= len(custom) <= 300:
            raise SpeakError(400, "一句话写清楚：对方是谁、要聊什么")
        pl = {"id": "custom", "title": custom[:16], "who": "对方", "setup": custom, "goal": "按你写的情况聊下去",
              "persona": f"The learner described this role-play in Chinese: {custom}. Play the other person in it.",
              "opener": ""}
    else:
        pl = play(play_id)
        pid = pl["pack"]
    data = {"play": {k: pl.get(k, "") for k in ("id", "title", "who", "role", "setup", "goal", "persona")}, "turns": []}
    s = _session(user["id"], _new(user["id"], "chat", pid, pl["id"], data))
    opener = pl.get("opener") or ""
    if not opener:
        try:
            opener = llm.speak_chat(data["play"], [], accent_of(s), user_id=user["id"])["reply"]
        except llm.LLMError as e:
            raise SpeakError(503, str(e))
    s["data"]["turns"].append({"who": "ai", "text": opener})
    _save(s)
    return s


def chat_turn(user, sid: int, wav: bytes) -> dict:
    s = _session(user["id"], sid)
    if s["kind"] != "chat" or s["done_at"]:
        raise SpeakError(400, "这一局已经结束了")
    turns = s["data"]["turns"]
    if sum(1 for t in turns if t["who"] == "me") >= CHAT_MAX:
        raise SpeakError(400, "这一局聊得够多了，看看学到了什么吧")
    c = transcribe(user, wav, "chat", s["id"], accent=accent_of(s))
    used = used_chunks(c["text"], _known(user["id"]))
    _count_use(user["id"], used)
    turns.append({"who": "me", "text": c["text"], "clip": c["clip"], "metrics": c["metrics"]})
    _save(s)
    try:
        reply = llm.speak_chat(s["data"]["play"], turns, accent_of(s), user_id=user["id"])["reply"]
    except llm.LLMError as e:
        raise SpeakError(503, str(e))
    turns.append({"who": "ai", "text": reply})
    _save(s)
    left = CHAT_MAX - sum(1 for t in turns if t["who"] == "me")
    return {"me": c["text"], "reply": reply, "left": left, "used": used}


def chat_close(user, sid: int) -> dict:
    s = _session(user["id"], sid)
    if s["done_at"]:
        return s["data"].get("close") or {}
    turns = s["data"]["turns"]
    if not any(t["who"] == "me" for t in turns):
        db.run("DELETE FROM speak_sessions WHERE id=?", s["id"])   # 一句没说就退出：不留记录
        return {"empty": True}
    try:
        r = llm.speak_chat_close(s["data"]["play"], turns, accent_of(s), user_id=user["id"])
    except llm.LLMError as e:
        raise SpeakError(503, str(e))
    chunks = []
    for c in [c for c in r.get("chunks") or [] if isinstance(c, dict) and c.get("en")][:3]:
        cid = chunk_upsert(str(c["en"]), str(c.get("zh") or ""), "", "ai")
        chunks.append({"id": cid, "en": str(c["en"]), "zh": str(c.get("zh") or ""), "yours": str(c.get("yours") or "")})
    fix = r.get("fix") if isinstance(r.get("fix"), dict) and r["fix"].get("right") else None
    learn(user["id"], [c["id"] for c in chunks], "chat:" + s["ref"])
    s["data"]["close"] = {"note": str(r.get("note") or ""), "chunks": chunks, "fix": fix}
    _save(s, done=True)
    return s["data"]["close"]


# ------------------------------------------------------------------ 页面要的汇总

def today_state(user) -> dict:
    today = db.today().isoformat()
    done = db.q("SELECT kind FROM speak_sessions WHERE user_id=? AND day=? AND done_at IS NOT NULL", user["id"], today)
    rv = reviews_today(user["id"])
    return {"scenes": sum(1 for r in done if r["kind"] == "scene"), "chats": sum(1 for r in done if r["kind"] == "chat"),
            "reviewed": rv, "review_left": len(due_chunks(user["id"])), "practiced": bool(done) or rv > 0}


def today_card(user) -> dict | None:
    """今天页的入口。打开了「放进每天的任务」才是一张大卡（daily），否则是快捷入口里的一个小格子。"""
    if not available(user):
        return None
    st = today_state(user)
    nxt = next_scene(user)
    return {"daily": prefs(user)["daily"], "scene": nxt, "state": st}


def library_count(user_id: int) -> int:
    return db.one("SELECT COUNT(*) AS n FROM speak_memory WHERE user_id=?", user_id)["n"]


def records(user_id: int) -> dict:
    """记录页：最近 4 周的三个数 + 几个总数 + 这周最常犯的 1 个 + 一个月前的你。"""
    today = db.today()
    weeks = []
    for w in range(3, -1, -1):
        end = today - timedelta(days=7 * w)
        start = end - timedelta(days=6)
        rows = db.q("SELECT step, metrics FROM speak_clips WHERE user_id=? AND day>=? AND day<=? AND step IN ('first','chat')",
                    user_id, start.isoformat(), end.isoformat())
        ms = [db.jload(r["metrics"], {}) for r in rows]
        firsts = [m for r, m in zip(rows, ms) if r["step"] == "first" and m.get("latency") is not None]
        timed = [m for m in ms if m.get("pauses") is not None and m.get("secs")]
        mins = sum(m["secs"] for m in timed) / 60
        weeks.append({"start": start.isoformat(), "end": end.isoformat(),
                      "latency": round(sum(m["latency"] for m in firsts) / len(firsts), 1) if firsts else None,
                      "pauses": round(sum(m["pauses"] for m in timed) / mins, 1) if mins >= 0.25 else None,
                      "used": sum(len(m.get("used") or []) for m in ms)})
    tot = {"chunks": library_count(user_id),
           "scenes": db.one("SELECT COUNT(*) AS n FROM speak_sessions WHERE user_id=? AND kind='scene' AND done_at IS NOT NULL", user_id)["n"],
           "chats": db.one("SELECT COUNT(*) AS n FROM speak_sessions WHERE user_id=? AND kind='chat' AND done_at IS NOT NULL", user_id)["n"]}
    # 这周最常犯的 1 个：地道版和聊天收尾里「只改一处」的 kind
    kinds: dict[str, int] = {}
    for r in db.q("SELECT data FROM speak_sessions WHERE user_id=? AND day>=?", user_id, (today - timedelta(days=6)).isoformat()):
        d = db.jload(r["data"], {}) or {}
        for fix in ((d.get("rewrite") or {}).get("fix"), (d.get("close") or {}).get("fix")):
            if fix and fix.get("kind"):
                kinds[fix["kind"]] = kinds.get(fix["kind"], 0) + 1
    common = max(kinds.items(), key=lambda kv: kv[1]) if kinds else None
    recent = [dict(r) for r in db.q("SELECT c.id, c.en, c.zh, m.used, m.box FROM speak_memory m JOIN speak_chunks c ON c.id=m.chunk_id "
                                    "WHERE m.user_id=? ORDER BY m.learned_at DESC LIMIT 12", user_id)]
    return {"weeks": weeks, "tot": tot, "common": {"kind": common[0], "n": common[1]} if common else None,
            "recent": recent, "month_ago": month_ago(user_id)}


def month_ago(user_id: int) -> dict | None:
    """一个月前练过、之后没再练的一个场景：再说一次，和那时的录音放在一起听。"""
    today = db.today()
    rows = db.q("SELECT id, pack_id, ref, day, data FROM speak_sessions WHERE user_id=? AND kind='scene' AND ref<>'own' "
                "AND done_at IS NOT NULL AND day<=? AND day>=? ORDER BY day", user_id,
                (today - timedelta(days=28)).isoformat(), (today - timedelta(days=90)).isoformat())
    recent = {r["ref"] for r in db.q("SELECT ref FROM speak_sessions WHERE user_id=? AND kind='scene' AND day>?",
                                     user_id, (today - timedelta(days=28)).isoformat())}
    for r in rows:
        if r["ref"] in recent:
            continue
        d = db.jload(r["data"], {}) or {}
        if not d.get("first"):
            continue
        try:
            sc = scene(r["pack_id"], r["ref"])
        except HTTPException:
            continue
        return {"session": r["id"], "day": r["day"], "scene": sc, "first": d["first"]}
    return None


def history(user_id: int, limit: int = 20) -> list[dict]:
    rows = db.q("SELECT id, day, kind, pack_id, ref, data, done_at FROM speak_sessions WHERE user_id=? AND done_at IS NOT NULL "
                "ORDER BY id DESC LIMIT ?", user_id, limit)
    out = []
    for r in rows:
        d = db.jload(r["data"], {}) or {}
        if r["kind"] == "chat":
            title = (d.get("play") or {}).get("title", "和 AI 聊")
        elif r["ref"] == "own":
            title = (d.get("scene") or {}).get("title", "我有件事要说")
        else:
            try:
                title = scene(r["pack_id"], r["ref"])["title"]
            except HTTPException:
                title = r["ref"]
        out.append({"id": r["id"], "day": r["day"], "kind": r["kind"], "title": title,
                    "first": (d.get("first") or {}).get("clip"), "retell": (d.get("retell") or {}).get("clip")})
    return out


def owns_clip(user_id: int, clip_id: str) -> bool:
    return bool(db.one("SELECT 1 AS x FROM speak_clips WHERE id=? AND user_id=?", clip_id, user_id))


def test_engine(wav: bytes) -> dict:
    """后台「语音引擎 · 试一下」：不记账、不存录音，只看识别结果、用时和评测。"""
    import time
    sp = engine()
    out = {"status": sp.status()}
    try:
        sp.check_wav(wav)
        t0 = time.monotonic()
        tr = sp.transcribe(wav, ["en", "zh"])
        out["transcribe"] = {"text": tr.text, "ms": int((time.monotonic() - t0) * 1000), "metrics": metrics(tr),
                             "words": [w.__dict__ for w in tr.words[:60]]}
        if sp.can("unscripted"):
            t0 = time.monotonic()
            a = sp.assess(wav, "en-US", "")
            out["assess"] = {"ms": int((time.monotonic() - t0) * 1000), **(a.to_dict() if a else {})}
    except STTError as e:
        out["error"] = str(e)
        record_error("transcribe" if "transcribe" not in out else "assess", str(e), "admin-test")
    return out
