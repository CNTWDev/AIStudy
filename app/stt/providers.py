"""各家厂商的适配器。每家一个子类，用 caps 声明自己会什么；新厂商写一个子类注册上就行，别处不用改。

转文字：volcengine（字节火山 豆包录音文件识别极速版）· aliyun（阿里云百炼 Qwen3-ASR）· cartesia（ink-whisper）·
        azure（微软语音，只认一种语言，但有词级时间）· mock（测试用）
发音评测：azure（跟读 + 自由说，美式有语调分）· mock
没写的（腾讯口语评测、讯飞、SpeechSuper）按同样的接口加。各家接口细节以官方文档为准，接好后在后台「语音引擎 · 试一下」里验。
"""
import base64
import json
import re
import uuid

import httpx

from .base import Assessment, STTError, Transcript, Word, WordScore

TRANSCRIBERS: dict[str, type] = {}
ASSESSORS: dict[str, type] = {}


def transcriber(name):
    def deco(cls):
        cls.name = name
        TRANSCRIBERS[name] = cls
        return cls
    return deco


def assessor(name):
    def deco(cls):
        cls.name = name
        ASSESSORS[name] = cls
        return cls
    return deco


class _Base:
    name = ""
    caps: frozenset = frozenset()
    need: tuple = ()            # 配置里必须填的几项
    label = ""

    def __init__(self, cfg: dict, timeout: float = 30):
        self.cfg, self.timeout = cfg, timeout

    def ready(self) -> tuple[bool, str]:
        miss = [k for k in self.need if not str(self.cfg.get(k) or "").strip()]
        return (False, f"config/stt.toml 的 [{self.name}] 还没填 {'、'.join(miss)}") if miss else (True, "")

    def _post(self, url, **kw):
        try:
            r = httpx.post(url, timeout=self.timeout, **kw)
        except httpx.HTTPError as e:
            raise STTError(f"{self.label or self.name} 连不上：{type(e).__name__}") from e
        if r.status_code >= 400:
            msg = r.headers.get("X-Api-Message") or r.text[:300]
            code = r.headers.get("X-Api-Status-Code")
            raise STTError(f"{self.label or self.name} 返回 HTTP {r.status_code}{f'（{code}）' if code else ''}：{msg}{_logid(r)}")
        return r


def _logid(r) -> str:
    """厂商给的请求编号：找厂商客服查问题时要用。"""
    for k in ("X-Tt-Logid", "X-Request-Id", "apim-request-id", "X-Acs-Request-Id"):
        if r.headers.get(k):
            return f"（请求编号 {r.headers[k]}）"
    return ""


class Transcriber(_Base):
    def transcribe(self, wav: bytes, langs: list[str], expect: str = "") -> Transcript:
        """langs：可能出现的语言（第一个是主要的，比如 ["en", "zh"]）；expect：应该说的话（跟读时），能用的厂商拿去当提示。"""
        raise NotImplementedError


class Assessor(_Base):
    def assess(self, wav: bytes, lang: str, reference: str = "") -> Assessment:
        """reference 为空 = 自由说评测（要有 unscripted 能力）。"""
        raise NotImplementedError


# ------------------------------------------------------------------ mock：测试和本地演示，不联网、不花钱

MOCK_SAMPLE = "Uh so the new app we will 推迟 two weeks because in the test we find some problem in the payment don't worry we use these two weeks to 把它修好"


def _mock_words(text: str, start: float, gap_after: int = -1) -> list[Word]:
    out, t = [], start
    for i, w in enumerate(text.split()):
        out.append(Word(w, round(t, 2), round(t + 0.3, 2)))
        t += 0.38 + (2.6 if i == gap_after else 0)
    return out


@transcriber("mock")
class MockTranscriber(Transcriber):
    caps = frozenset({"word_times", "mixed"})
    label = "假引擎（测试用）"
    text: str | None = None       # 测试里可以直接改成想要的识别结果

    def transcribe(self, wav, langs, expect=""):
        if self.text is not None:
            text = self.text
        elif expect:   # 跟读：照着原句「认出来」，漏掉第 3 个词（演示「灰色的词没被听到」）
            ws = expect.split()
            text = " ".join(w for i, w in enumerate(ws) if i != 2 or len(ws) < 4)
        else:
            text = str(self.cfg.get("text") or MOCK_SAMPLE)
        words = _mock_words(text, 1.2 if expect else 4.1, gap_after=-1 if expect else 3)
        return Transcript(text=text, words=words, duration=(words[-1].end + 0.5) if words else 1.0, provider="mock")


@assessor("mock")
class MockAssessor(Assessor):
    caps = frozenset({"scripted", "unscripted", "phoneme"})
    label = "假评测（测试用）"

    def assess(self, wav, lang, reference=""):
        ws = (reference or "hello world").split()
        out = [WordScore(w, 92.0) for w in ws]
        if len(ws) > 1:
            out[1] = WordScore(ws[1], 55.0, "mispronounced", [{"p": "ɝ", "score": 40}])
        return Assessment(words=out, scores={"accuracy": 84, "fluency": 80, "completeness": 95}, provider="mock")


# ------------------------------------------------------------------ 字节火山：豆包大模型录音文件识别（极速版）

@transcriber("volcengine")
class Volcengine(Transcriber):
    """豆包大模型录音文件识别（极速版），两个站点接口一样，钥匙不通用：
      region = "intl" 默认（服务器在日本，就近走国际站）
      region = "cn"   火山引擎（国内站）openspeech.bytedance.com，资源 volc.bigasr.auc_turbo
      region = "intl" BytePlus（国际站，新加坡）voice.ap-southeast-1.bytepluses.com，资源 volc.seedasr.auc_turbo
    新控制台只要 X-Api-Key；老控制台要 app_id + access_token（X-Api-App-Key / X-Api-Access-Key）。时间单位是毫秒。
    不指定语言：中英夹说要靠它自己认。"""
    caps = frozenset({"word_times", "mixed"})
    label = "BytePlus（豆包语音识别 · 国际站）"
    SITES = {"cn": ("https://openspeech.bytedance.com", "volc.bigasr.auc_turbo", "字节火山（豆包语音识别）"),
             "intl": ("https://voice.ap-southeast-1.bytepluses.com", "volc.seedasr.auc_turbo", "BytePlus（豆包语音识别 · 国际站）")}
    PATH = "/api/v3/auc/bigmodel/recognize/flash"
    URL = SITES["intl"][0] + PATH

    def __init__(self, cfg: dict, timeout: float = 30):
        super().__init__(cfg, timeout)
        self.site = self.SITES.get(str(cfg.get("region") or "intl").lower(), self.SITES["intl"])
        self.label = self.site[2]

    def ready(self):
        if str(self.cfg.get("region") or "intl").lower() not in self.SITES:
            return False, f"[volcengine] region 只能是 cn（火山引擎国内站）或 intl（BytePlus 国际站），现在是「{self.cfg['region']}」"
        if self.cfg.get("api_key") or (self.cfg.get("app_id") and self.cfg.get("access_token")):
            return True, ""
        return False, "config/stt.toml 的 [volcengine] 还没填 api_key（或老控制台的 app_id + access_token）"

    def transcribe(self, wav, langs, expect=""):
        h = {"X-Api-Resource-Id": self.cfg.get("resource_id") or self.site[1],
             "X-Api-Request-Id": str(uuid.uuid4()), "X-Api-Sequence": "-1"}
        if self.cfg.get("api_key"):
            h["X-Api-Key"] = str(self.cfg["api_key"]).strip()
        else:
            h["X-Api-App-Key"], h["X-Api-Access-Key"] = str(self.cfg["app_id"]).strip(), str(self.cfg["access_token"]).strip()
        body = {"user": {"uid": str(self.cfg.get("app_id") or "beejoy")},
                "audio": {"data": base64.b64encode(wav).decode(), "format": "wav", "rate": 16000, "bits": 16, "channel": 1},
                "request": {"model_name": self.cfg.get("model") or "bigmodel", "enable_punc": True, "show_utterances": True}}
        r = self._post(self.cfg.get("base_url") or (self.site[0] + self.PATH), headers=h, json=body)
        code = r.headers.get("X-Api-Status-Code", "20000000")
        if code == "20000003":   # 没说话
            return Transcript(text="", provider=self.name)
        if code != "20000000":
            raise STTError(f"{self.label}返回 {code}：{r.headers.get('X-Api-Message', '')}{_logid(r)}")
        d = r.json()
        res = d.get("result") or {}
        words = [Word(w.get("text", ""), w["start_time"] / 1000, w["end_time"] / 1000)
                 for u in res.get("utterances") or [] for w in u.get("words") or [] if w.get("text", "").strip()]
        dur = (d.get("audio_info") or {}).get("duration") or 0
        return Transcript(text=res.get("text", ""), words=words, duration=dur / 1000, provider=self.name)


# ------------------------------------------------------------------ 阿里云百炼：Qwen3-ASR-Flash（国际站 / 国内站）

@transcriber("aliyun")
class Aliyun(Transcriber):
    """文档没写有没有词级时间，所以不声明 word_times：开口用时、卡顿会退成粗略值（页面上标明）。"""
    caps = frozenset({"mixed"})
    need = ("api_key",)
    label = "阿里云百炼（Qwen3-ASR）"
    HOSTS = {"intl": "https://dashscope-intl.aliyuncs.com", "cn": "https://dashscope.aliyuncs.com"}

    def transcribe(self, wav, langs, expect=""):
        host = self.cfg.get("base_url") or self.HOSTS.get(self.cfg.get("region") or "intl", self.HOSTS["intl"])
        body = {"model": self.cfg.get("model") or "qwen3-asr-flash",
                "input": {"messages": [{"role": "system", "content": [{"text": expect or ""}]},
                                       {"role": "user", "content": [{"audio": "data:audio/wav;base64," + base64.b64encode(wav).decode()}]}]},
                "parameters": {"asr_options": {"enable_itn": False}}}
        r = self._post(host + "/api/v1/services/aigc/multimodal-generation/generation",
                       headers={"Authorization": f"Bearer {self.cfg['api_key']}"}, json=body)
        d = r.json()
        try:
            parts = d["output"]["choices"][0]["message"]["content"]
            text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        except (KeyError, IndexError, TypeError) as e:
            raise STTError(f"阿里云返回格式看不懂：{json.dumps(d)[:200]}") from e
        return Transcript(text=text, words=[Word(w) for w in text.split()], provider=self.name)


# ------------------------------------------------------------------ Cartesia：ink-whisper

@transcriber("cartesia")
class Cartesia(Transcriber):
    """一次只认一种语言（取 langs 的第一个），中英夹说里的中文多半认不出来。"""
    caps = frozenset({"word_times"})
    need = ("api_key",)
    label = "Cartesia（ink-whisper）"

    def transcribe(self, wav, langs, expect=""):
        r = self._post((self.cfg.get("base_url") or "https://api.cartesia.ai") + "/stt",
                       headers={"X-API-Key": self.cfg["api_key"], "Authorization": f"Bearer {self.cfg['api_key']}",
                                "Cartesia-Version": self.cfg.get("api_version") or "2025-04-16"},
                       files={"file": ("speech.wav", wav, "audio/wav")},
                       data={"model": self.cfg.get("model") or "ink-whisper", "language": (langs or ["en"])[0],
                             "timestamp_granularities[]": "word"})
        d = r.json()
        words = [Word(w.get("word", "").strip(), w.get("start"), w.get("end")) for w in d.get("words") or [] if w.get("word", "").strip()]
        return Transcript(text=d.get("text", ""), words=words, duration=float(d.get("duration") or 0), provider=self.name)


# ------------------------------------------------------------------ 微软 Azure 语音：转文字 + 发音评测

class _Azure:
    need = ("api_key", "region")
    label = "微软 Azure 语音"

    def _recognize(self, wav, lang, pa: dict | None):
        url = (f"https://{self.cfg['region']}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
               f"?language={lang}&format=detailed&wordLevelTimestamps=true")
        h = {"Ocp-Apim-Subscription-Key": self.cfg["api_key"], "Accept": "application/json",
             "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000"}
        if pa is not None:
            h["Pronunciation-Assessment"] = base64.b64encode(json.dumps(pa).encode()).decode()
        d = self._post(url, headers=h, content=wav).json()
        if d.get("RecognitionStatus") not in ("Success", None):
            if d.get("RecognitionStatus") in ("NoMatch", "InitialSilenceTimeout"):
                return {}
            raise STTError(f"微软返回 {d.get('RecognitionStatus')}")
        return (d.get("NBest") or [{}])[0] | {"_dur": d.get("Duration", 0)}


def _azure_lang(langs) -> str:
    l0 = (langs or ["en-US"])[0]
    return {"en": "en-US", "zh": "zh-CN"}.get(l0, l0)


@transcriber("azure")
class AzureTranscriber(_Azure, Transcriber):
    caps = frozenset({"word_times"})

    def transcribe(self, wav, langs, expect=""):
        b = self._recognize(wav, _azure_lang(langs), None)
        words = [Word(w["Word"], w["Offset"] / 1e7, (w["Offset"] + w["Duration"]) / 1e7) for w in b.get("Words") or []]
        return Transcript(text=b.get("Display", ""), words=words, duration=b.get("_dur", 0) / 1e7, provider=self.name)


@assessor("azure")
class AzureAssessor(_Azure, Assessor):
    """REST 短音频接口（≤60 秒）。语调分（prosody）只有 en-US。"""
    caps = frozenset({"scripted", "unscripted", "phoneme", "prosody"})
    ERRORS = {"Mispronunciation": "mispronounced", "Omission": "omitted", "Insertion": "inserted",
              "UnexpectedBreak": "break", "MissingBreak": "break", "Monotone": "monotone"}

    def assess(self, wav, lang, reference=""):
        lang = _azure_lang([lang])
        pa = {"ReferenceText": reference, "GradingSystem": "HundredMark", "Granularity": "Phoneme",
              "Dimension": "Comprehensive", "EnableMiscue": bool(reference), "EnableProsodyAssessment": lang == "en-US"}
        b = self._recognize(wav, lang, pa)
        if not b:
            return Assessment(provider=self.name)
        top = b.get("PronunciationAssessment") or b
        scores = {k: top.get(v) for k, v in (("accuracy", "AccuracyScore"), ("fluency", "FluencyScore"),
                                              ("completeness", "CompletenessScore"), ("prosody", "ProsodyScore"),
                                              ("overall", "PronScore")) if top.get(v) is not None}
        words = []
        for w in b.get("Words") or []:
            p = w.get("PronunciationAssessment") or w
            words.append(WordScore(w.get("Word", ""), p.get("AccuracyScore"), self.ERRORS.get(p.get("ErrorType") or "", ""),
                                   [{"p": ph.get("Phoneme", ""), "score": (ph.get("PronunciationAssessment") or ph).get("AccuracyScore")}
                                    for ph in w.get("Phonemes") or []]))
        return Assessment(words=words, scores=scores, provider=self.name)


def norm_word(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower().replace("’", "'"))
