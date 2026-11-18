"""统一的结果格式：不管哪家厂商，交出来都是这几样。时间一律是秒（float）。"""
import re
from dataclasses import asdict, dataclass, field

# 能力（适配器在 caps 里声明自己有哪几样）
CAPS = {
    "word_times": "每个词的开始、结束时间（算开口用时、卡顿要用）",
    "mixed": "一句话里中英夹说也能认",
    "scripted": "跟读评测：给定应该说的话，评每个词、每个音",
    "unscripted": "自由说评测：不给参考文本，边认边评",
    "phoneme": "音素（单个音）级的分数",
    "prosody": "语调、节奏分（目前只有美式英语）",
}

_CJK = re.compile(r"[㐀-鿿]")


class STTError(Exception):
    """转文字 / 评测失败。message 是给人看的原因。"""


@dataclass
class Word:
    text: str
    start: float | None = None
    end: float | None = None

    @property
    def zh(self) -> bool:
        return bool(_CJK.search(self.text))


@dataclass
class Transcript:
    text: str
    words: list[Word] = field(default_factory=list)
    duration: float = 0.0
    provider: str = ""

    @property
    def timed(self) -> bool:
        return bool(self.words) and all(w.start is not None for w in self.words)

    def to_dict(self) -> dict:
        return {"text": self.text, "words": [asdict(w) for w in self.words], "duration": self.duration, "provider": self.provider}

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        d = d or {}
        return cls(text=d.get("text", ""), words=[Word(**w) for w in d.get("words") or []],
                   duration=float(d.get("duration") or 0), provider=d.get("provider", ""))


@dataclass
class WordScore:
    text: str
    score: float | None = None        # 0-100，这个词读得准不准
    error: str = ""                   # "" / mispronounced / omitted / inserted / break / monotone
    phonemes: list[dict] = field(default_factory=list)   # [{"p": "æ", "score": 62}]


@dataclass
class Assessment:
    words: list[WordScore] = field(default_factory=list)
    scores: dict = field(default_factory=dict)          # accuracy / fluency / completeness / prosody / overall（有哪几样给哪几样）
    provider: str = ""

    def to_dict(self) -> dict:
        return {"words": [asdict(w) for w in self.words], "scores": self.scores, "provider": self.provider}
