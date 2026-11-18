# stt：语音引擎（转文字 + 发音评测）

不依赖 AIStudy 其他代码，复制 `app/stt/` 到别的项目就能用（需要 `httpx`；Python 3.10 还需要 `tomli`）。

```python
from stt import Speech, load_settings
sp = Speech(load_settings("config/stt.toml"))
t = sp.transcribe(wav, langs=["en", "zh"])          # Transcript(text, words[Word(text,start,end)], duration)
a = sp.assess(wav, "en-US", reference="...")        # Assessment，或 None（没配评测 / 这家不会）
sp.can("word_times"); sp.status()
```

- **两种能力分开配**：`[transcribe] provider` 和 `[assess] provider`，可以混搭（火山转文字 + 微软评测）。
- **能力声明**：每个适配器的 `caps` 写清楚会什么（`word_times` `mixed` `scripted` `unscripted` `phoneme` `prosody`），
  调用方按 `can()` 决定页面显示什么。发音评测只有少数厂商有，没配时调用方自己退回「哪个词没被识别出来」。
- **录音格式**：16kHz 单声道 16 位 WAV（浏览器端录好），各家都收，不转码。
- **新厂商**：在 `providers.py` 写 `Transcriber` / `Assessor` 子类，`@transcriber("名字")` / `@assessor("名字")`。
- 配置格式见 AIStudy 的 `config/stt.example.toml`。
