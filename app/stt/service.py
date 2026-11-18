"""对外的入口：按配置选好两家（转文字、发音评测），统一做时长限制和能力判断。限额记账交给调用方（它有数据库）。"""
from .base import Assessment, STTError, Transcript
from .providers import ASSESSORS, TRANSCRIBERS
from .settings import STTSettings
from .wav import wav_info


class Speech:
    def __init__(self, settings: STTSettings):
        self.s = settings
        self.tr = self._make(TRANSCRIBERS, settings.transcribe)
        self.asr = self._make(ASSESSORS, settings.assess)

    def _make(self, reg, name):
        if not name or name == "none":
            return None
        cls = reg.get(name)
        return cls(self.s.of(name), self.s.timeout) if cls else None

    # ---------------------------------------------------------------- 状态
    def status(self) -> dict:
        """给后台和页面看：两种能力各自能不能用、用的哪家、会什么。"""
        def one(kind, name, obj, reg):
            if self.s.error:
                return {"on": False, "name": name, "why": self.s.error, "caps": []}
            if not name or name == "none":
                return {"on": False, "name": "", "why": "没配置", "caps": []}
            if obj is None:
                return {"on": False, "name": name, "why": f"不认识的{kind}厂商「{name}」，可选：{'、'.join(sorted(reg))}", "caps": []}
            ok, why = obj.ready()
            return {"on": ok, "name": name, "label": obj.label or name, "why": why, "caps": sorted(obj.caps)}
        return {"transcribe": one("转文字", self.s.transcribe, self.tr, TRANSCRIBERS),
                "assess": one("发音评测", self.s.assess, self.asr, ASSESSORS)}

    @property
    def ready(self) -> bool:
        return bool(self.tr) and self.tr.ready()[0] and not self.s.error

    def can(self, cap: str) -> bool:
        """现在配的厂商会不会这一样（word_times、mixed 看转文字；scripted、unscripted、phoneme、prosody 看评测）。"""
        if cap in ("word_times", "mixed"):
            return self.ready and cap in self.tr.caps
        return bool(self.asr) and self.asr.ready()[0] and cap in self.asr.caps

    # ---------------------------------------------------------------- 调用
    def check_wav(self, wav: bytes) -> float:
        info = wav_info(wav)
        if info["seconds"] > self.s.max_seconds + 1:
            raise STTError(f"一段最长 {self.s.max_seconds} 秒")
        return info["seconds"]

    def transcribe(self, wav: bytes, langs=("en",), expect: str = "") -> Transcript:
        if not self.ready:
            raise STTError(self.status()["transcribe"]["why"] or "语音识别还没配置")
        self.check_wav(wav)
        return self.tr.transcribe(wav, list(langs), expect)

    def assess(self, wav: bytes, lang: str, reference: str = "") -> Assessment | None:
        """配了评测、而且会这一种（跟读 / 自由说）才评；否则返回 None，调用方照常往下走。"""
        if not self.can("scripted" if reference else "unscripted"):
            return None
        self.check_wav(wav)
        return self.asr.assess(wav, lang, reference)
