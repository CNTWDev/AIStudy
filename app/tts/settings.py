"""读取 tts.toml（格式见 config/tts.example.toml）。任何字符串值都可以写成 "${环境变量名}"。"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

DEFAULT_SPEEDS = {"word": 0.7, "sentence": 0.7, "passage": 0.8}


@dataclass
class TTSSettings:
    provider: str = "none"                 # cartesia / mock / none（none = 关闭，前端退回浏览器自带朗读）
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    api_version: str = ""
    timeout: float = 60
    voices: dict[str, str] = field(default_factory=dict)      # 语言 → 声音 id；没配的语言不朗读
    speeds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SPEEDS))  # 用途 → 语速
    format: dict = field(default_factory=dict)                  # 输出格式（提供方自己解释）
    max_chars: int = 1200                  # 一次最多读多少字（长文章由前端按句子切开）
    daily_chars_per_user: int = 20000      # 每人每天新生成的字数上限（读缓存不算）
    daily_chars_total: int = 200000        # 全站每天新生成的字数上限
    extra: dict = field(default_factory=dict)
    source: str = ""
    error: str = ""

    @property
    def enabled(self) -> bool:
        return self.provider not in ("", "none") and not self.error

    def voice(self, lang: str) -> str:
        return self.voices.get(lang) or self.voices.get(lang.split("-")[0], "")

    def speed(self, use: str) -> float:
        v = self.speeds.get(use, self.speeds.get("sentence", 1.0))
        return round(float(v), 2)


def _env(v):
    if isinstance(v, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), v)
    return v


def parse_settings(data: dict, source: str = "") -> TTSSettings:
    s = TTSSettings(source=source)
    s.provider = str(_env(data.get("provider", "none")) or "none").lower()
    p = {k: _env(v) for k, v in (data.get(s.provider) or {}).items()} if isinstance(data.get(s.provider), dict) else {}
    s.api_key = str(p.pop("api_key", ""))
    s.model = str(p.pop("model", ""))
    s.base_url = str(p.pop("base_url", "")).rstrip("/")
    s.api_version = str(p.pop("api_version", ""))
    s.timeout = float(p.pop("timeout", 60))
    s.format = dict(p.pop("format", {}) or {})
    s.extra = p
    s.voices = {str(k): str(_env(v)) for k, v in (data.get("voices") or {}).items() if _env(v)}
    s.speeds = dict(DEFAULT_SPEEDS, **{str(k): float(v) for k, v in (data.get("speed") or {}).items()})
    s.max_chars = int(data.get("max_chars", s.max_chars))
    s.daily_chars_per_user = int(data.get("daily_chars_per_user", s.daily_chars_per_user))
    s.daily_chars_total = int(data.get("daily_chars_total", s.daily_chars_total))
    return s


def load_settings(path) -> TTSSettings:
    """读配置文件；文件不存在 = 关闭（不报错），格式错 = 关闭并在 error 里写原因。"""
    path = Path(path)
    if not path.exists():
        return TTSSettings(source=str(path))
    try:
        return parse_settings(tomllib.loads(path.read_text(encoding="utf-8")), str(path))
    except (ValueError, TypeError) as e:
        return TTSSettings(source=str(path), error=f"{path.name} 格式不对：{e}")
