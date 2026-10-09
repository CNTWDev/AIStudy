"""TTS 服务本体：prepare（查/登记）+ open（读文件 / 边生成边读）。"""
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .providers import PROVIDERS, Provider, TTSProviderError
from .text import cache_key, normalize

STALE_SECONDS = 180      # .part 超过这么久没变化，认为生成它的进程已经没了
CHUNK = 16 * 1024
log = logging.getLogger("tts")


class TTSError(Exception):
    """不能朗读（没开、没配声音、太长、超过每天字数……）。前端收到后退回浏览器自带朗读。"""


@dataclass
class Clip:
    key: str
    text: str
    lang: str
    cached: bool        # True = 已经有文件，播放不花钱
    chars: int
    ext: str


class TTS:
    def __init__(self, settings, index, root):
        self.s = settings
        self.index = index
        self.root = Path(root)
        self.provider: Provider | None = None
        cls = PROVIDERS.get(settings.provider)
        if settings.enabled and cls:
            self.provider = cls(settings)
        self.problem = settings.error or ("" if self.provider else
                                          ("" if settings.provider in ("", "none") else f"不认识的提供方：{settings.provider}"))
        if self.provider and not self.problem:
            self.problem = self.provider.check()

    # ---------------------------------------------------------------- 状态
    @property
    def ready(self) -> bool:
        return bool(self.provider) and not self.problem

    def langs(self) -> list[str]:
        """能朗读的语言（配了声音的）。"""
        return sorted(self.s.voices) if self.ready else []

    # ---------------------------------------------------------------- 查 / 登记
    def prepare(self, text: str, lang: str, use: str = "sentence", *, user=None, speed: float | None = None) -> Clip:
        """speed 只给管理员试听用；平时语速由配置按用途决定。"""
        if not self.ready:
            raise TTSError(self.problem or "朗读服务没有开启")
        lang = (lang or "en").lower()
        voice = self.s.voice(lang)
        if not voice:
            raise TTSError(f"没有给 {lang} 配声音")
        t = normalize(text, lang)
        if not t:
            raise TTSError("没有要读的文字")
        if len(t) > self.s.max_chars:
            raise TTSError(f"一次最多读 {self.s.max_chars} 个字")
        speed = self.s.speed(use) if speed is None else round(min(1.5, max(0.6, float(speed))), 2)
        p = self.provider
        key = cache_key(t, provider=p.name, model=self.s.model, voice=voice, lang=lang, speed=speed, fmt=p.fmt)
        rec = self.index.get(key)
        if not rec:
            self.index.add({"key": key, "text": t, "lang": lang, "voice": voice, "model": self.s.model, "speed": speed,
                            "chars": len(t), "fmt": p.fmt, "status": "pending", "created_by": _s(user),
                            "created_at": time.time()})
        cached = bool(rec and rec.get("status") == "ready" and self.path(key).exists())
        return Clip(key, t, lang, cached, len(t), p.ext)

    def path(self, key: str) -> Path:
        ext = self.provider.ext if self.provider else "mp3"
        return self.root / key[:2] / key[2:4] / f"{key}.{ext}"

    @property
    def content_type(self) -> str:
        return self.provider.content_type if self.provider else "audio/mpeg"

    # ---------------------------------------------------------------- 播放
    def open(self, key: str, *, user=None) -> Iterator[bytes]:
        """返回音频字节流。已有文件直接读；没有就生成（或跟着读别的请求正在生成的那份）。"""
        rec = self.index.get(key)
        if not rec:
            raise KeyError(key)
        final = self.path(key)
        if final.exists():
            self.index.hit(key)
            return _read_file(final)
        if not self.ready:
            raise TTSError(self.problem or "朗读服务没有开启")
        part = final.with_suffix(final.suffix + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        if not part.exists():
            self._check_quota(rec, user)  # 真正要花钱了才检查每天字数
        for _ in range(3):
            try:
                fd = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if time.time() - _mtime(part) > STALE_SECONDS:   # 上次生成到一半就断了
                    _unlink(part)
                    continue
                return self._tail(part, final)                  # 别人正在生成，跟着读
            if final.exists():                                  # 抢到时别人刚好生成完
                os.close(fd)
                _unlink(part)
                self.index.hit(key)
                return _read_file(final)
            reader = os.open(part, os.O_RDONLY)
            first = threading.Event()
            threading.Thread(target=self._generate, args=(rec, fd, part, final, first, user), daemon=True).start()
            first.wait(self.s.timeout)
            if not part.exists() and not final.exists():          # 一个字节都没出来就失败了（先查 .part：改名是原子的）
                os.close(reader)
                raise TTSError((self.index.get(key) or {}).get("error") or "生成失败")
            self.index.hit(key)
            return _tail_fd(reader, part, final, self.s.timeout)
        raise TTSError("生成失败，请稍后再试")

    def _check_quota(self, rec, user):
        day0 = _day_start()
        if self.index.generated_chars(day0) + rec["chars"] > self.s.daily_chars_total or (
                user is not None and self.index.generated_chars(day0, _s(user)) + rec["chars"] > self.s.daily_chars_per_user):
            raise TTSError("今天朗读的新内容太多了，明天再听吧")

    def _generate(self, rec, fd, part, final, first, user):
        n = 0
        try:
            for chunk in self.provider.stream(rec["text"], lang=rec["lang"], voice=rec["voice"], speed=rec["speed"]):
                os.write(fd, chunk)
                n += len(chunk)
                first.set()
            os.close(fd); fd = None
            if n == 0:
                raise TTSProviderError("没有返回音频")
            self.index.update(rec["key"], status="ready", bytes=n, error="", generated_by=_s(user),
                              generated_at=time.time())
            os.replace(part, final)  # 先记索引再改名：读的一方看到文件完成时，记录一定已经是 ready
        except Exception as e:  # noqa: BLE001  生成失败：删掉半截文件，下次重新生成
            if fd is not None:
                os.close(fd)
            _unlink(part)
            self.index.update(rec["key"], status="failed", error=str(e)[:500])
            log.warning("朗读生成失败（%s，%s）：%s", rec["lang"], rec["text"][:40], e)
        finally:
            first.set()

    def _tail(self, part, final) -> Iterator[bytes]:
        try:
            fd = os.open(part, os.O_RDONLY)
        except FileNotFoundError:
            if final.exists():
                return _read_file(final)
            raise TTSError("生成失败，请稍后再试")
        return _tail_fd(fd, part, final, self.s.timeout)

    # ---------------------------------------------------------------- 管理
    def voices(self, lang: str = "") -> list[dict]:
        if not self.ready:
            raise TTSError(self.problem or "朗读服务没有开启")
        return self.provider.voices(lang)


def _read_file(p: Path) -> Iterator[bytes]:
    with open(p, "rb") as f:
        while chunk := f.read(CHUNK):
            yield chunk


def _tail_fd(fd, part: Path, final: Path, timeout: float) -> Iterator[bytes]:
    """边写边读：读到文件末尾就等一下，直到生成完成（.part 改名成正式文件）或失败（.part 被删）。"""
    def gen():
        idle = 0.0
        try:
            while True:
                chunk = os.read(fd, CHUNK)
                if chunk:
                    idle = 0.0
                    yield chunk
                    continue
                if not part.exists():               # 改名完成或失败：把剩下的读完就结束
                    while chunk := os.read(fd, CHUNK):
                        yield chunk
                    return
                if idle > timeout:
                    return
                time.sleep(0.05); idle += 0.05
        finally:
            os.close(fd)
    return gen()


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except FileNotFoundError:
        return time.time()


def _unlink(p: Path):
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def _s(user):
    return None if user is None else str(user)


def _day_start() -> float:
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
