"""边写边看：学员在等的长回答，AI 写出来一段就先送到页面上一段（docs/DESIGN.md 5.40）。

只对学员点了按钮、正盯着等的几种回答开（思辨题的一轮和收尾、开放题点评），也只送这几栏给人读的话；
判断、编号、思考方式这些等整份写完、校验过，跟着正式结果一起给。
用法：路由用 web.streamed 包一下，它在 listen(...) 里跑业务；ask_json 用 sink_for(task) 看这次有没有人在等。
"""
import json
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar

FIELDS = {
    "discuss": ("steelman", "answer", "challenge"),
    "discuss_close": ("shift", "own", "watch"),
    "review": ("feedback", "followup"),
}
_sink: ContextVar = ContextVar("llm_stream_sink", default=None)


@contextmanager
def listen(fn):
    """在这段里调用的 AI，写出新内容就调用 fn({栏: 目前写到的字})。"""
    token = _sink.set(fn)
    try:
        yield
    finally:
        _sink.reset(token)


def sink_for(task: str, every: float = 0.08):
    """这次调用有人在等、而且这种回答可以边写边看：返回 on_text(目前全部输出)；否则 None。"""
    fn, keys = _sink.get(), FIELDS.get(task)
    if fn is None or not keys:
        return None
    last, at = {}, 0.0

    def on_text(text: str):
        nonlocal last, at
        part = partial_fields(text, keys)
        if part and part != last and time.monotonic() - at >= every:
            last, at = part, time.monotonic()
            fn(part)
    return on_text


def partial_fields(text: str, keys) -> dict:
    """从还没写完的 JSON 里取出这几栏已经写出来的字（字符串值写到一半也取）。"""
    out = {}
    for k in keys:
        m = re.search(r'"%s"\s*:\s*"' % re.escape(k), text)
        if not m:
            continue
        i, n, raw = m.end(), len(text), []
        while i < n and text[i] != '"':
            if text[i] == "\\":
                if i + 1 >= n or (text[i + 1] == "u" and i + 6 > n):
                    break  # 转义写到一半：等下一段
                step = 6 if text[i + 1] == "u" else 2
                raw.append(text[i:i + step])
                i += step
            else:
                raw.append(text[i])
                i += 1
        try:
            val = json.loads('"' + "".join(raw) + '"')
        except ValueError:
            continue
        if val.strip():
            out[k] = val
    return out
