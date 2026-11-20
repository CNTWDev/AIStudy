"""读取 stt.toml（格式见 AIStudy 的 config/stt.example.toml）。任何字符串值都可以写成 "${环境变量名}"。"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


@dataclass
class STTSettings:
    transcribe: str = "none"              # 转文字用哪家：volcengine / aliyun / cartesia / azure / mock / none
    assess: str = "none"                  # 发音评测用哪家：azure / mock / none（不配也能用）
    providers: dict = field(default_factory=dict)   # 名字 → 这家的配置（api_key、region……）
    max_seconds: int = 90                 # 一段录音最长多少秒
    daily_seconds_per_user: int = 10800    # 每人每天最多送去识别多少秒（评测另算）
    daily_seconds_total: int = 180000      # 全站每天
    timeout: float = 30
    source: str = ""
    error: str = ""

    def of(self, name: str) -> dict:
        return dict(self.providers.get(name) or {})


def _env(v):
    if isinstance(v, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, dict):
        return {k: _env(x) for k, x in v.items()}
    return v


def parse_settings(data: dict, source: str = "") -> STTSettings:
    s = STTSettings(source=source)
    tr, asx = data.get("transcribe") or {}, data.get("assess") or {}
    s.transcribe = str(_env(tr.get("provider", "none")) or "none").lower()
    s.assess = str(_env(asx.get("provider", "none")) or "none").lower()
    for k in ("max_seconds", "daily_seconds_per_user", "daily_seconds_total"):
        if k in data:
            setattr(s, k, int(data[k]))
    if "timeout" in data:
        s.timeout = float(data["timeout"])
    s.providers = {k: _env(v) for k, v in data.items() if isinstance(v, dict) and k not in ("transcribe", "assess")}
    return s


def load_settings(path) -> STTSettings:
    p = Path(path)
    if not p.exists():
        return STTSettings(source=str(p))
    try:
        with p.open("rb") as f:
            return parse_settings(tomllib.load(f), str(p))
    except (OSError, ValueError) as e:
        return STTSettings(source=str(p), error=f"配置文件读不了：{e}")
