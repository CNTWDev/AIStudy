"""stt：语音引擎（转文字 + 发音评测），和朗读服务 app/tts 一样是独立的小包，不依赖 AIStudy 其他代码。

两种能力分开配，可以混搭（docs：成人英语口语开口练-讨论稿 §9.2、§9.3）：
- 转文字（Transcriber）：几乎每家都有。返回整段文字、每个词的时间（厂商支持才有）、哪几段是中文。
- 发音评测（Assessor）：只有少数几家有（微软 Azure、腾讯、讯飞、SpeechSuper……）。可以不配，不配时调用方退回「哪个词没被识别出来」。

每个适配器用 caps 声明自己会什么，调用方按能力决定页面上显示什么，不会出现「封装了却不支持」：

    from stt import Speech, load_settings
    sp = Speech(load_settings("config/stt.toml"))
    t = sp.transcribe(wav_bytes, langs=["en", "zh"])        # Transcript
    if sp.can("scripted"):
        a = sp.assess(wav_bytes, "en-US", reference="Testing turned up an issue.")   # Assessment

录音一律是 16kHz 单声道 16 位 WAV（浏览器端录成这个格式，见 app/static/speak.js），各家都收，不用再转码。
加厂商：在 providers.py 里写一个 Transcriber / Assessor 子类，用 @transcriber("名字") / @assessor("名字") 注册。
"""
from .base import CAPS, Assessment, STTError, Transcript, Word, WordScore
from .providers import ASSESSORS, TRANSCRIBERS, Assessor, Transcriber, assessor, transcriber
from .service import Speech
from .settings import STTSettings, load_settings, parse_settings
from .wav import wav_info, make_wav

__all__ = ["CAPS", "Assessment", "STTError", "Transcript", "Word", "WordScore", "ASSESSORS", "TRANSCRIBERS",
           "Assessor", "Transcriber", "assessor", "transcriber", "Speech", "STTSettings", "load_settings", "parse_settings",
           "wav_info", "make_wav"]
