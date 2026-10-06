"""AI（大模型）模块。业务代码只用这里导出的名字。

- 配置：config/llm.toml（见 config/llm.example.toml）
- 抽象：base.Provider / ChatRequest；新增厂商见 providers.py
- 调用：service.ask_json（缓存、限额、JSON 解析）
- 任务：tasks.py（查词、讲解、出题、造句点评、写短文）
"""
from .base import ChatRequest, LLMError, Provider, register
from .service import ask_json, check, enabled, ping, reload, settings
from .tasks import explain_sentence, generate_items, lookup, make_passage, sentence_feedback, teach

__all__ = [
    "ChatRequest", "LLMError", "Provider", "register", "ask_json", "check", "enabled", "ping", "reload", "settings",
    "explain_sentence", "generate_items", "lookup", "make_passage", "sentence_feedback", "teach",
]
