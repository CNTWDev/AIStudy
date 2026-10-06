"""读取 config/llm.toml。"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from .. import config
from .base import ProviderConfig

_KNOWN = {"type", "model", "api_key", "base_url", "timeout", "max_retries"}


@dataclass
class TaskRoute:
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    max_tokens: int | None = None


@dataclass
class LLMSettings:
    default: str = "none"
    daily_limit_per_kid: int = 300
    cache: bool = True
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    tasks: dict[str, TaskRoute] = field(default_factory=dict)
    source: str = ""
    error: str = ""


def _env(v):
    if isinstance(v, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), v)
    return v


def parse(data: dict, source: str = "") -> LLMSettings:
    s = LLMSettings(source=source)
    s.default = str(_env(data.get("default", "none")) or "none")
    s.daily_limit_per_kid = int(data.get("daily_limit_per_kid", 300))
    s.cache = bool(data.get("cache", True))
    for name, p in (data.get("providers") or {}).items():
        p = {k: _env(v) for k, v in p.items()}
        s.providers[name] = ProviderConfig(
            name=name, type=str(p.get("type", "")), model=str(p.get("model", "")),
            api_key=str(p.get("api_key", "")), base_url=str(p.get("base_url", "")),
            timeout=float(p.get("timeout", 120)), max_retries=int(p.get("max_retries", 2)),
            extra={k: v for k, v in p.items() if k not in _KNOWN})
    for name, t in (data.get("tasks") or {}).items():
        s.tasks[name] = TaskRoute(provider=t.get("provider"), model=t.get("model"),
                                  effort=t.get("effort"), max_tokens=t.get("max_tokens"))
    return s


def _from_env() -> dict:
    """没有 llm.toml 时，兼容老的 .env 写法（LLM_PROVIDER=anthropic 等）。"""
    kind = os.environ.get("LLM_PROVIDER", "none").lower()
    providers = {
        "anthropic": {"type": "anthropic", "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
                      "model": os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5"), "server_fallback": True},
        "openai_compat": {"type": "openai_compat", "api_key": os.environ.get("OPENAI_COMPAT_API_KEY", ""),
                          "base_url": os.environ.get("OPENAI_COMPAT_BASE_URL", "https://api.deepseek.com/v1"),
                          "model": os.environ.get("OPENAI_COMPAT_MODEL", "deepseek-chat")},
        "mock": {"type": "mock"},
    }
    return {"default": kind if kind in providers else "none", "providers": providers,
            "daily_limit_per_kid": int(os.environ.get("LLM_DAILY_LIMIT", "300"))}


def load(path: Path | None = None) -> LLMSettings:
    path = path or config.LLM_CONFIG_FILE
    if os.environ.get("LLM_PROVIDER") and not path.exists():
        return parse(_from_env(), "环境变量 LLM_PROVIDER")
    if not path.exists():
        return LLMSettings(source=str(path), error=f"没有找到 {path}（可从 config/llm.example.toml 复制）")
    try:
        with open(path, "rb") as f:
            return parse(tomllib.load(f), str(path))
    except (tomllib.TOMLDecodeError, ValueError, TypeError) as e:
        return LLMSettings(source=str(path), error=f"{path} 格式错误：{e}")
