"""AI（大模型）模块。业务代码只用这里导出的名字。

- 配置：config/llm.toml（见 config/llm.example.toml）
- 抽象：base.Provider / ChatRequest；新增厂商见 providers.py
- 调用：service.ask_json（缓存、限额、JSON 解析）
- 任务：tasks.py（查词、讲解、出题、造句点评、写短文、试卷解析、问一问小助手）
"""
from .base import ChatRequest, LLMError, Provider, register
from .service import ask_json, check, enabled, ping, reload, settings
from .tasks import ask_tutor, explain_sentence, generate_items, kp_context, lookup, make_passage, parse_paper, sentence_feedback, teach

__all__ = [
    "ChatRequest", "LLMError", "Provider", "register", "ask_json", "check", "enabled", "ping", "reload", "settings",
    "ask_tutor", "explain_sentence", "generate_items", "kp_context", "lookup", "make_passage", "parse_paper", "sentence_feedback", "teach",
]
