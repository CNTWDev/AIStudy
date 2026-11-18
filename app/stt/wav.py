"""WAV 小工具：检查录音是不是 16 位 PCM WAV、算时长；测试和 mock 用 make_wav 造一段静音。"""
import array
import io
import sys
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


def wav_peak(data: bytes) -> float:
    """录音里最响的一下有多响（0~1）。几乎是 0 = 录到的是静音（麦克风没声音、选错了麦克风）。"""
    with wave.open(io.BytesIO(data)) as w:
        frames = w.readframes(w.getnframes())
    a = array.array("h")
    a.frombytes(frames[: len(frames) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    return max((abs(x) for x in a), default=0) / 32768


def make_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()
