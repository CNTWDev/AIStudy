# tts：带缓存的朗读服务

不依赖 AIStudy 其他代码，复制 `app/tts/` 到别的项目就能用（需要 `httpx`；Python 3.10 还需要 `tomli`）。

```python
from tts import TTS, load_settings, SQLiteIndex
tts = TTS(load_settings("config/tts.toml"), SQLiteIndex("data/tts.db"), "data/tts")
clip = tts.prepare("Once upon a time…", "en", "passage", user=42)   # 查索引 / 登记，不花钱
for chunk in tts.open(clip.key, user=42):                              # 有文件读文件，没有就边生成边返回
    ...
```

- **key** = MD5(提供方 · 模型 · 声音 · 语言 · 语速 · 格式 · 规整后的文字)。同一句话只生成一次。
- **文件**：`<root>/ab/cd/<key>.mp3`；生成中是 `<key>.mp3.part`，用 `O_EXCL` 抢占，多进程也只生成一次。
- **索引**：实现 `Index` 协议的 5 个方法即可接自己的数据库；自带 `SQLiteIndex`、`MemoryIndex`。
- **用途 / 语速**：`word` / `sentence` / `passage`，在配置的 `[speed]` 里改。
- **限额**：每人每天、全站每天新生成的字数；读缓存不算。
- **新厂商**：写 `Provider` 子类，`@register("名字")`。
- 配置格式见 AIStudy 的 `config/tts.example.toml`。
