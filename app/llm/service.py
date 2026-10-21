"""统一入口：选提供方、缓存、每日次数限制、解析 JSON。"""
import hashlib
import json
import re
import threading
import time
from .validation import validate

from .. import db
from . import providers, stream  # noqa: F401  注册内置提供方
from .base import PROVIDER_TYPES, ChatRequest, LLMError, Provider
from .settings import LLMSettings, load

_lock = threading.Lock()
_settings: LLMSettings | None = None
_instances: dict[str, Provider] = {}


def settings() -> LLMSettings:
    global _settings
    if _settings is None:
        with _lock:
            if _settings is None:
                _settings = load()
    return _settings


def reload(new: LLMSettings | None = None) -> LLMSettings:
    """重新读取配置（测试或改了 llm.toml 之后用；正常改配置后重启服务即可）。"""
    global _settings
    with _lock:
        _settings = new or load()
        _instances.clear()
    return _settings


def provider(name: str | None = None) -> Provider:
    s = settings()
    name = name or s.default
    if name in ("", "none"):
        raise LLMError("还没有开启 AI（管理员需要在 config/llm.toml 里设置 default 和 api_key）")
    if name not in _instances:
        cfg = s.providers.get(name)
        if not cfg:
            raise LLMError(f"config/llm.toml 里没有名为 {name} 的提供方")
        cls = PROVIDER_TYPES.get(cfg.type)
        if not cls:
            raise LLMError(f"不支持的提供方类型：{cfg.type}（可选：{', '.join(PROVIDER_TYPES)}）")
        _instances[name] = cls(cfg)
    return _instances[name]


def check(name: str | None = None) -> tuple[bool, str]:
    """检查配置（不发请求）。"""
    s = settings()
    if s.error:
        return False, s.error
    try:
        p = provider(name)
    except LLMError as e:
        return False, str(e)
    ok, why = p.ready()
    return ok, why or f"{p.cfg.name}（{p.cfg.type}，{p.cfg.model or '-'}）"


def model_of(task: str) -> dict:
    """这个任务现在用哪个提供方 / 模型（存进题库的生成记录里，以后好比较质量）。"""
    try:
        s = settings()
        route = s.tasks.get(task)
        p = provider(route.provider if route and route.provider else None)
        return {"provider": p.cfg.name, "model": (route.model if route and route.model else None) or p.cfg.model or ""}
    except LLMError:
        return {}


def enabled() -> bool:
    return check()[0]


def ping(name: str | None = None) -> str:
    """真实调用一次，确认密钥和网络都通。"""
    p = provider(name)
    ok, why = p.ready()
    if not ok:
        raise LLMError(why)
    text = p.complete(ChatRequest(task="ping", system="只输出 JSON。", user='输出 {"ok": true}', max_tokens=50))
    return text.strip()[:200]


def extract_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise LLMError("AI 返回格式不对，请再试一次")
    for end in range(len(text), start, -1):
        try:
            return json.loads(text[start:end])
        except ValueError:
            continue
    raise LLMError("AI 返回格式不对，请再试一次")


MAX_RETRY_TOKENS = 16000


def _complete_json(p, req: ChatRequest, on_text=None):
    """调用一次并解析 JSON。回答写到一半被长度上限截断、或 JSON 坏了：放宽长度再试一次。
    on_text：有人在等着看（llm/stream.py），边写边给。"""
    call = (lambda: p.stream(req, on_text)) if on_text else (lambda: p.complete(req))
    try:
        return extract_json(call())
    except LLMError:
        truncated = req.usage.get("truncated")
        req.max_tokens = min(max(req.max_tokens * 2, 4000), MAX_RETRY_TOKENS)
        try:
            return extract_json(call())
        except LLMError:
            if truncated and req.usage.get("truncated"):
                raise LLMError("AI 写得太长被截断了，请稍后再试") from None
            raise


def _count_usage(user_id: int | None):
    if not user_id:
        return
    if settings().daily_limit_per_kid <= 0:
        raise LLMError("今天 AI 使用次数已用完，明天再来")
    day = db.today().isoformat()
    with db.tx(write=True) as t:
        row = t.one("INSERT INTO llm_usage(user_id,day,calls) VALUES(?,?,1) "
                    "ON CONFLICT(user_id,day) DO UPDATE SET calls=llm_usage.calls+1 "
                    "WHERE llm_usage.calls<? RETURNING calls", user_id, day, settings().daily_limit_per_kid)
        if not row:
            raise LLMError("今天 AI 使用次数已用完，明天再来")


def ask_json(task: str, system: str, user: str, *, user_id=None, effort="low", max_tokens=4000, cache=True, images=None):
    s = settings()
    if s.error:
        raise LLMError("AI 配置有误：" + s.error)
    route = s.tasks.get(task)
    p = provider(route.provider if route and route.provider else None)
    ok, why = p.ready()
    if not ok:
        raise LLMError("AI 还没配置好：" + why)
    req = ChatRequest(task=task, system=system + "\n只输出一个 JSON 对象，不要输出其他文字。", user=user,
                      max_tokens=(route and route.max_tokens) or max_tokens,
                      effort=(route and route.effort) or effort, model=route.model if route else None,
                      images=list(images or []))
    cache = cache and s.cache and not images
    key = hashlib.sha256(f"{task}\n{p.cfg.name}\n{req.model or p.cfg.model}\n{req.effort}\n{req.max_tokens}\n{system}\n{user}".encode()).hexdigest()
    if cache:
        row = db.one("SELECT value FROM llm_cache WHERE key=?", key)
        if row:
            return validate(task, json.loads(row["value"]))
    from ..jobs import check_active
    check_active()
    _count_usage(user_id)
    started, status, error = time.monotonic(), "failed", ""
    try:
        data = validate(task, _complete_json(p, req, stream.sink_for(task)))
        check_active()
        status = "ok"
    except Exception as exc:
        error = type(exc).__name__  # do not persist prompts, child text or credentials
        raise
    finally:
        db.run("INSERT INTO llm_calls(user_id,task,provider,model,status,elapsed_ms,input_tokens,output_tokens,error_code,created_at) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)", user_id, task, p.cfg.name, req.model or p.cfg.model, status,
               int((time.monotonic() - started) * 1000), req.usage.get("input_tokens"), req.usage.get("output_tokens"), error, db.now())
    if cache:
        db.run("INSERT INTO llm_cache(key, value, created_at) VALUES(?,?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value, created_at=excluded.created_at",
               key, db.jdump(data), db.now())
    return data
