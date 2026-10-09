"""语音提供方。要接新的厂商：写一个 Provider 子类，用 @register("名字") 注册，配置里 provider = "名字"。"""
import io
import json
import wave
from typing import Iterator

PROVIDERS: dict[str, type] = {}


class TTSProviderError(Exception):
    pass


def register(name: str):
    def deco(cls):
        PROVIDERS[name] = cls
        cls.name = name
        return cls
    return deco


class Provider:
    name = ""
    ext = "mp3"
    content_type = "audio/mpeg"

    def __init__(self, settings):
        self.s = settings

    @property
    def fmt(self) -> str:
        """输出格式的简短描述，参与缓存 key（换格式 = 新文件）。"""
        return self.ext

    def check(self) -> str:
        """配置有问题时返回原因，没问题返回空串（不联网）。"""
        return ""

    def stream(self, text: str, *, lang: str, voice: str, speed: float) -> Iterator[bytes]:
        raise NotImplementedError

    def voices(self, lang: str = "") -> list[dict]:
        """可选：列出可用的声音 [{id, name, description, gender}]（管理员挑声音用）。"""
        return []


@register("cartesia")
class Cartesia(Provider):
    """Cartesia Sonic：POST /tts/bytes 直接返回 mp3，边下边播、整段就是一个文件。
    （WebSocket 接口只能输出原始 PCM，适合实时对话，不适合存成文件，所以这里不用。）"""

    def __init__(self, settings):
        super().__init__(settings)
        f = settings.format or {}
        self.container = f.get("container", "mp3")
        self.sample_rate = int(f.get("sample_rate", 24000))
        self.bit_rate = int(f.get("bit_rate", 64000))
        self.ext = "wav" if self.container == "wav" else "mp3"
        self.content_type = "audio/wav" if self.ext == "wav" else "audio/mpeg"
        self.base = settings.base_url or "https://api.cartesia.ai"
        self.version = settings.api_version or "2026-08-14"

    @property
    def fmt(self):
        return f"{self.container}-{self.sample_rate}-{self.bit_rate}"

    def check(self):
        if not self.s.api_key:
            return "还没有填 Cartesia 的 api_key"
        if not self.s.model:
            return "还没有填 model"
        return ""

    def _headers(self):
        return {"Authorization": f"Bearer {self.s.api_key}", "Cartesia-Version": self.version,
                "Content-Type": "application/json"}

    def stream(self, text, *, lang, voice, speed):
        import httpx
        out = {"container": self.container, "sample_rate": self.sample_rate}
        if self.container == "mp3":
            out["bit_rate"] = self.bit_rate
        else:
            out["encoding"] = "pcm_s16le"
        body = {"model_id": self.s.model, "transcript": text, "voice": {"id": voice}, "language": lang,
                "output_format": out, "generation_config": {"speed": speed}}
        try:
            with httpx.stream("POST", f"{self.base}/tts/bytes", headers=self._headers(), json=body,
                              timeout=httpx.Timeout(self.s.timeout, connect=15)) as r:
                if r.status_code >= 400:
                    r.read()
                    raise TTSProviderError(f"Cartesia {r.status_code}：{_err(r.text)}")
                for chunk in r.iter_bytes():
                    if chunk:
                        yield chunk
        except httpx.HTTPError as e:
            raise TTSProviderError(f"连不上 Cartesia：{e}") from e

    def voices(self, lang=""):
        import httpx
        out, after = [], None
        for _ in range(10):
            params = {"limit": 100, **({"language": lang} if lang else {}), **({"starting_after": after} if after else {})}
            r = httpx.get(f"{self.base}/voices", headers=self._headers(), params=params, timeout=30)
            if r.status_code >= 400:
                raise TTSProviderError(f"Cartesia {r.status_code}：{_err(r.text)}")
            d = r.json()
            out += [{"id": v["id"], "name": v.get("name", ""), "description": v.get("description", ""),
                     "gender": v.get("gender", ""), "tagline": v.get("tagline", "")} for v in d.get("data", [])]
            if not d.get("has_more") or not d.get("next_page"):
                break
            after = d["next_page"]
        return out


def _err(text: str) -> str:
    try:
        d = json.loads(text)
        return str(d.get("message") or d.get("title") or d.get("error") or text)[:300]
    except ValueError:
        return text[:300]


@register("mock")
class Mock(Provider):
    """不联网的假声音（一小段静音 wav），用于本地演示和自动测试。"""
    ext = "wav"
    content_type = "audio/wav"
    calls = 0

    def stream(self, text, *, lang, voice, speed):
        type(self).calls += 1
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"\0\0" * min(8000, 200 * max(1, len(text))))
        data = buf.getvalue()
        for i in range(0, len(data), 4096):
            yield data[i:i + 4096]
