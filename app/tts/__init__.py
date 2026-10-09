"""朗读服务（TTS）：文字 → 语音，生成一次、落盘、以后都读本地文件。

这个包不依赖 AIStudy 的其他代码（只用标准库和 httpx），可以整个复制到别的项目里用：

    from tts import TTS, load_settings, SQLiteIndex
    tts = TTS(load_settings("config/tts.toml"), SQLiteIndex("data/tts.db"), "data/tts")
    clip = tts.prepare("Hello, world.", "en", "sentence")   # 查索引，没有就登记一条（还不花钱）
    ctype, chunks = tts.open(clip.key)                       # 有文件读文件；没有就边生成边返回，同时写盘

流程（和文字的 MD5 一一对应）：
1. 文字先规整（空白、引号），再连同 提供方/模型/声音/语言/语速/格式 一起算 MD5 作为 key；
   声音和语速也算进去，改了配置不会和旧音频混在一起。
2. 索引里没有这个 key 就登记一条（状态 pending）。
3. 第一次播放时才真正调用服务：后台线程把音频写进 <key>.part，写完改名为正式文件并标记 ready；
   前端同时从 .part 边读边播。别的请求（包括别的进程）遇到同一个 key 正在生成，就跟着读同一个文件，不会重复花钱。
4. 以后再播放直接读本地文件。

不支持用户自定义声音：每种语言固定一个声音，命中率才高。
"""
from .settings import TTSSettings, load_settings, parse_settings
from .text import normalize, cache_key, split_text
from .index import Index, SQLiteIndex, MemoryIndex
from .service import TTS, Clip, TTSError
from .providers import Provider, register, PROVIDERS

__all__ = ["TTS", "Clip", "TTSError", "TTSSettings", "load_settings", "parse_settings", "normalize", "cache_key",
           "split_text", "Index", "SQLiteIndex", "MemoryIndex", "Provider", "register", "PROVIDERS"]
