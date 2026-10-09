"""把朗读服务（app/tts，独立的包）接进网站：配置、数据库索引、接口、管理员试听页。

前端只用两个接口：
  POST /api/tts {text, lang, use}  → {url, cached}；不能朗读时 {fallback: true, why}（前端改用浏览器自带朗读）
  GET  /tts/<key>                  → 音频（有文件读文件，没有就边生成边播放并写盘）
"""
import os
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from . import auth, config, db
from .tts import TTS, TTSError, load_settings
from .tts.index import _COLS

TTS_CONFIG_FILE = Path(os.environ.get("TTS_CONFIG_FILE", config.BASE_DIR / "config" / "tts.toml"))
TTS_DIR = config.DATA_DIR / "tts"
USES = ("word", "sentence", "passage")
AUDITION_SPEEDS = (0.6, 0.7, 0.8, 0.9)

router = APIRouter()


class DBIndex:
    """tts.Index 的实现：存在网站自己的数据库里（tts_clips 表，见 migrations/0022_tts.sql）。"""

    def get(self, key):
        r = db.one("SELECT * FROM tts_clips WHERE key=?", key)
        return dict(r) if r else None

    def add(self, rec):
        rec = {k: v for k, v in rec.items() if k in _COLS or k == "key"}
        cols = ",".join(rec)
        db.run(f"INSERT INTO tts_clips({cols}) VALUES({','.join('?' * len(rec))}) ON CONFLICT (key) DO NOTHING",
               *rec.values())

    def update(self, key, **fields):
        fields = {k: v for k, v in fields.items() if k in _COLS}
        if fields:
            db.run(f"UPDATE tts_clips SET {','.join(k + '=?' for k in fields)} WHERE key=?", *fields.values(), key)

    def hit(self, key):
        db.run("UPDATE tts_clips SET hits=hits+1, last_played_at=? WHERE key=?", time.time(), key)

    def generated_chars(self, since, user=None):
        sql, args = "SELECT COALESCE(SUM(chars),0) AS n FROM tts_clips WHERE generated_at>=?", [since]
        if user is not None:
            sql += " AND generated_by=?"
            args.append(str(user))
        return int(db.one(sql, *args)["n"])


_svc: TTS | None = None


def service() -> TTS:
    global _svc
    if _svc is None:
        s = load_settings(TTS_CONFIG_FILE)
        if os.environ.get("TTS_PROVIDER") and not TTS_CONFIG_FILE.exists():  # 测试 / 本地演示
            s.provider = os.environ["TTS_PROVIDER"]
            s.voices = s.voices or {"en": "demo-en", "zh": "demo-zh"}
        _svc = TTS(s, DBIndex(), TTS_DIR)
    return _svc


def reload() -> TTS:
    global _svc
    _svc = None
    return service()


def client_info() -> dict:
    """给前端的：哪些语言能用服务器朗读（其余退回浏览器自带）。"""
    s = service()
    return {"langs": s.langs()}


def check() -> tuple[bool, str]:
    s = service()
    if s.s.provider in ("", "none") and not s.s.error:
        return False, "未开启（config/tts.toml 里 provider = \"none\"，朗读用浏览器自带的声音）"
    if not s.ready:
        return False, s.problem
    return True, f"{s.s.provider} · {s.s.model} · 语言 {', '.join(s.langs()) or '（没配声音）'}"


@router.post("/api/tts")
def api_tts(request: Request, body: dict = Body(...)):
    u = auth.require_user(request)
    use = body.get("use") if body.get("use") in USES else "sentence"
    s = service()
    speed = None
    if body.get("speed") is not None and (u["is_admin"] or u["role"] == "admin"):  # 管理员试听不同语速
        speed = float(body["speed"])
    try:
        clip = s.prepare(str(body.get("text") or "")[:5000], str(body.get("lang") or "en"), use, user=u["id"], speed=speed)
    except TTSError as e:
        return {"fallback": True, "why": str(e)}
    return {"url": f"/tts/{clip.key}", "cached": clip.cached, "key": clip.key}


@router.get("/tts/{key}")
def tts_audio(request: Request, key: str):
    u = auth.require_user(request)
    if len(key) != 32 or not key.isalnum():
        raise HTTPException(404)
    s = service()
    path = s.path(key)
    if path.exists():  # 有文件：普通文件响应（支持拖动进度条），浏览器可以缓存
        s.index.hit(key)
        return FileResponse(path, media_type=s.content_type, headers={"Cache-Control": "private, max-age=31536000, immutable"})
    try:
        chunks = s.open(key, user=u["id"])
    except KeyError:
        raise HTTPException(404)
    except TTSError as e:
        raise HTTPException(503, str(e))
    return StreamingResponse(chunks, media_type=s.content_type, headers={"Cache-Control": "no-store"})


@router.get("/api/admin/tts/voices")
def admin_voices(request: Request, lang: str = ""):
    auth.require_admin(request)
    try:
        return {"voices": service().voices(lang)}
    except Exception as e:  # noqa: BLE001
        return {"voices": [], "error": str(e)}


def stats() -> dict:
    r = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END),0) AS ready, "
               "COALESCE(SUM(CASE WHEN status='ready' THEN chars ELSE 0 END),0) AS chars, COALESCE(SUM(bytes),0) AS bytes, "
               "COALESCE(SUM(hits),0) AS hits FROM tts_clips")
    day0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    return {**dict(r), "today_chars": DBIndex().generated_chars(day0)}
