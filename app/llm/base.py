"""大模型调用的抽象层。

业务代码只调用 app.llm 里的任务函数（lookup、teach、generate_items ...），
任务函数把请求包装成 ChatRequest 交给 service.ask_json；service 按配置选出一个 Provider 去调用。
要接入新的模型厂商：写一个 Provider 子类，用 @register("类型名") 注册，然后在 config/llm.toml 里
写 type = "类型名" 即可，业务代码不用改。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class LLMError(Exception):
    """给用户看的错误（中文、简短）。"""


@dataclass
class ChatRequest:
    task: str                    # 任务名，例如 lookup / teach / items
    system: str
    user: str
    max_tokens: int = 4000
    effort: str = "low"          # low / medium / high
    model: str | None = None     # 为空则用提供方配置里的 model
    json_mode: bool = True
    images: list = field(default_factory=list)  # [(media_type, base64 字符串)]，用于看图（试卷拍照）


@dataclass
class ProviderConfig:
    name: str
    type: str
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout: float = 120
    max_retries: int = 2
    extra: dict = field(default_factory=dict)


class Provider(ABC):
    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def ready(self) -> tuple[bool, str]:
        """配置是否齐全。返回 (是否可用, 不可用的原因)。"""
        if not self.cfg.api_key:
            return False, f"提供方 {self.cfg.name} 没有填写 api_key"
        if not self.cfg.model:
            return False, f"提供方 {self.cfg.name} 没有填写 model"
        return True, ""

    @abstractmethod
    def complete(self, req: ChatRequest) -> str:
        """发出请求，返回模型输出的文本。出错时抛 LLMError。"""


PROVIDER_TYPES: dict[str, type[Provider]] = {}


def register(type_name: str):
    def deco(cls):
        PROVIDER_TYPES[type_name] = cls
        return cls
    return deco
