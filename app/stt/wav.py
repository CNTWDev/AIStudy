"""WAV 小工具：检查录音是不是 16 位 PCM WAV、算时长；测试和 mock 用 make_wav 造一段静音。"""
import io
import struct
import wave

from .base import STTError


def wav_info(data: bytes) -> dict:
    """{rate, channels, width, seconds}；不是能用的 WAV 就抛 STTError。"""
    try:
        with wave.open(io.BytesIO(data)) as w:
            rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    except (wave.Error, EOFError, struct.error) as e:
        raise STTError("录音格式不对（要 WAV）") from e
    if width != 2 or ch not in (1, 2) or not 8000 <= rate <= 48000:
        raise STTError("录音格式不对（要 16 位 PCM）")
    return {"rate": rate, "channels": ch, "width": width, "seconds": n / float(rate)}


def make_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()
