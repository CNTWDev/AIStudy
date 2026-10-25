"""具体的模型提供方。新增厂商：照着写一个类并 @register 即可。"""
import json

import httpx

from .base import ChatRequest, LLMError, Provider, register


@register("anthropic")
class AnthropicProvider(Provider):
    """Claude 官方 API。配置项：api_key、model、base_url（可选）、timeout、max_retries、
    server_fallback（模型过载时由服务端自动切换备用模型，默认开）、use_effort（默认开）。"""

    _client = None

    def client(self):
        if self._client is None:
            import anthropic
            kw = {"api_key": self.cfg.api_key, "timeout": self.cfg.timeout, "max_retries": self.cfg.max_retries}
            if self.cfg.base_url:
                kw["base_url"] = self.cfg.base_url
            self._client = anthropic.Anthropic(**kw)
        return self._client

    def complete(self, req: ChatRequest) -> str:
        return self._send(req)

    def stream(self, req: ChatRequest, on_text) -> str:
        return self._send(req, on_text)

    def _send(self, req: ChatRequest, on_text=None) -> str:
        import anthropic
        kw = {
            "model": req.model or self.cfg.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": [{"role": "user", "content": req.user}],
        }
        if req.images:
            kw["messages"][0]["content"] = [
                {"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}} for mt, data in req.images
            ] + [{"type": "text", "text": req.user}]
        if self.cfg.extra.get("use_effort", True) and req.effort:
            kw["output_config"] = {"effort": req.effort}
        if self.cfg.extra.get("server_fallback", True):
            api, kw = self.client().beta.messages, {**kw, "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        else:
            api = self.client().messages
        try:
            if on_text is None:
                resp = api.create(**kw)
            else:
                with api.stream(**kw) as s:
                    text = ""
                    for delta in s.text_stream:
                        text += delta
                        on_text(text)
                    resp = s.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMError("AI 密钥无效，请管理员检查 config/llm.toml 里的 api_key") from e
        except anthropic.RateLimitError as e:
            raise LLMError("AI 服务太忙，请稍后再试") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"AI 服务出错（{e.status_code}）") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("连不上 AI 服务，请检查服务器网络") from e
        req.usage.update(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
        req.usage["truncated"] = resp.stop_reason == "max_tokens"
        if resp.stop_reason == "refusal":
            raise LLMError("AI 没有回答这个请求，换个说法试试")
        return "".join(b.text for b in resp.content if b.type == "text")


@register("openai_compat")
class OpenAICompatProvider(Provider):
    """OpenAI 兼容的 /chat/completions 接口：DeepSeek、通义千问、Kimi、智谱等。"""

    def ready(self):
        ok, why = super().ready()
        if ok and not self.cfg.base_url:
            return False, f"提供方 {self.cfg.name} 没有填写 base_url"
        return ok, why

    def complete(self, req: ChatRequest) -> str:
        return self._send(req)

    def stream(self, req: ChatRequest, on_text) -> str:
        return self._send(req, on_text)

    def _send(self, req: ChatRequest, on_text=None) -> str:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": req.model or self.cfg.model,
            "max_tokens": req.max_tokens,
            "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}],
        }
        if req.images:  # 需要支持看图的模型，例如 qwen-vl-max、gpt-4o；DeepSeek 目前不支持
            body["messages"][1]["content"] = [
                {"type": "image_url", "image_url": {"url": f"data:{mt};base64,{data}"}} for mt, data in req.images
            ] + [{"type": "text", "text": req.user}]
        if req.json_mode and self.cfg.extra.get("json_mode", True):
            body["response_format"] = {"type": "json_object"}
        if on_text is not None:
            body["stream"] = True
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
        last = None
        for _ in range(self.cfg.max_retries + 1):
            try:
                with httpx.stream("POST", url, json=body, timeout=self.cfg.timeout, headers=headers) as r:
                    if r.status_code in (429, 500, 502, 503, 504):
                        last = LLMError("AI 服务太忙，请稍后再试")
                        continue
                    if r.status_code in (401, 403):
                        raise LLMError("AI 密钥无效，请管理员检查 config/llm.toml 里的 api_key")
                    if r.status_code >= 400:
                        if req.images:
                            raise LLMError(f"AI 服务出错（{r.status_code}）：这个模型可能不支持看图片。请在 config/llm.toml 的 "
                                           "[tasks.paper] 里换成支持图片的模型，或者改用「粘贴题目文字」")
                        raise LLMError(f"AI 服务出错（{r.status_code}）")
                    return self._read(r, req, on_text)
            except httpx.HTTPError as e:
                last = LLMError("连不上 AI 服务，请检查服务器网络")
                last.__cause__ = e
                continue
        raise last

    @staticmethod
    def _read(r, req: ChatRequest, on_text) -> str:
        """读回答：普通请求一整份 JSON；流式请求是一行行 data: {...}，最后 data: [DONE]。"""
        try:
            if on_text is None:
                payload = json.loads(r.read())
                usage = payload.get("usage") or {}
                req.usage.update(input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"))
                req.usage["truncated"] = payload["choices"][0].get("finish_reason") == "length"
                return payload["choices"][0]["message"]["content"]
            text = ""
            for line in r.iter_lines():
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    continue
                chunk = json.loads(line[5:])
                if chunk.get("usage"):
                    req.usage.update(input_tokens=chunk["usage"].get("prompt_tokens"), output_tokens=chunk["usage"].get("completion_tokens"))
                for ch in chunk.get("choices") or []:
                    if ch.get("finish_reason"):
                        req.usage["truncated"] = ch["finish_reason"] == "length"
                    delta = (ch.get("delta") or {}).get("content")
                    if delta:
                        text += delta
                        on_text(text)
            return text
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError("AI 返回格式不对，请再试一次") from e


@register("mock")
class MockProvider(Provider):
    """离线假数据，只用于本地演示和自动测试。"""

    def ready(self):
        return True, ""

    def stream(self, req: ChatRequest, on_text) -> str:
        """演示边写边看：一小段一小段地给（extra.stream_delay 秒一段，默认不等）。"""
        import time
        text = self.complete(req)
        for i in range(8, len(text) + 8, 8):
            time.sleep(float(self.cfg.extra.get("stream_delay", 0)))
            on_text(text[:i])
        return text

    def complete(self, req: ChatRequest) -> str:
        if req.task == "paper":  # 把示例题对应到提示词里给出的第一个候选知识点
            import re
            ids = re.findall(r"^- (\S+)：", req.user, re.M)
            data = json.loads(json.dumps(MOCK["paper"]))
            for i, q in enumerate(data["questions"]):
                q["kp_id"] = ids[i % len(ids)] if ids and i < 2 else "NOT-A-KP"
            return json.dumps(data, ensure_ascii=False)
        if req.task == "word_examples":  # 照提示词里的词各写两句（app/words.py 只收把这个词原样标出来的句子）
            import re
            ws = re.findall(r"^- ([A-Za-z][A-Za-z '\-]*)（", req.user, re.M)
            return json.dumps({"words": [{"w": w, "sentences": [{"en": f"Look, I can see the [[{w}]] today.", "zh": f"看，今天我看到了 {w}。"},
                                                                  {"en": f"We talk about the [[{w}]] at school.", "zh": f"我们在学校说起 {w}。"}]}
                                         for w in ws]}, ensure_ascii=False)
        if req.task in ("word_usage", "word_hooks"):   # 照提示词里的词各写一份（用法题的答案在选项里）
            import re
            ws = re.findall(r"^- ([A-Za-z][A-Za-z '\-]*)（", req.user, re.M)
            if req.task == "word_hooks":
                return json.dumps({"words": [{"w": w, "hook": f"把 {w} 拆开记"} for w in ws]}, ensure_ascii=False)
            return json.dumps({"words": [{"w": w, "phrases": [{"en": f"a {w} day", "zh": f"{w} 的一天"}],
                                          "family": [{"w": f"{w}ful", "pos": "adj.", "zh": f"{w} 的"}],
                                          "confuse": [{"w": w[::-1], "zh": "反过来", "tip": "字母顺序不一样"}],
                                          "use": [{"kind": "collocation", "q": f"We have ___ {w}.", "options": ["a", "an", "the"], "answer": "a", "explain": "示例"}],
                                          "icon": "🍎" if w.startswith("a") else ""} for w in ws]}, ensure_ascii=False)
        return json.dumps(MOCK.get(req.task, {}), ensure_ascii=False)


MOCK = {
    "book_guide": {"gist": "示例：主人公出发去冒险，路上交到了第一个朋友。", "hook": "读读看：主人公为什么要出发？",
                   "words": [{"w": "kite", "zh": "风筝"}, {"w": "hill", "zh": "小山"}],
                   "quiz": [{"q": "示例：主人公带了什么去小山？", "options": ["风筝", "皮球", "书包"], "answer": 0, "explain": "第一段就写了。"},
                            {"q": "示例：那天天气怎么样？", "options": ["下雨", "刮大风", "下雪"], "answer": 1, "explain": "风很大。"}]},
    "verify": {"answer": 1, "ok": True, "problem": "", "why": "示例"},
    "variant": {"items": [{"type": "mcq", "difficulty": 2, "q": "示例改编题：1 km = ? m", "options": ["10", "100", "1000", "10000"],
                           "answer": 2, "hint": "千米和米", "explain": "1 千米 = 1000 米。"}]},
    "ask": {"reply": "我们一步一步来：你先说说，这道题在问什么？", "reveals_answer": False},
    "paper": {"title": "示例测验", "subject_guess": "", "notes": "示例数据",
              "questions": [
                  {"label": "1", "type": "mcq", "q": "示例：1 m = ? cm", "options": ["10", "100", "1000", "0.1"], "answer": 1,
                   "explain": "1 米 = 100 厘米。", "score": 2, "page": 1, "student_answer": "C", "marked": "wrong",
                   "fig": {"page": 1, "box": [120, 250, 630, 540], "alt": "示例：一把刻度尺"}},
                  {"label": "2", "type": "num", "q": "示例：2 + 3 = ?", "answer": 5, "explain": "2 加 3 等于 5。",
                   "score": 2, "page": 1, "student_answer": "5", "marked": "right"},
                  {"label": "3", "type": "short", "q": "示例：说说你是怎么想的。", "model": "言之有理即可",
                   "points": ["说出思路"], "score": 3, "explain": "", "page": 1, "student_answer": "", "marked": "unknown"},
              ]},
    "items": {"items": [
        {"type": "mcq", "difficulty": 1, "q": "示例选择题：1 + 1 = ?", "options": ["1", "2", "3", "4"], "answer": 1,
         "hint": "数一数", "explain": "1 加 1 等于 2。"},
        {"type": "num", "difficulty": 2, "q": "示例数值题：2 × 3 = ?", "answer": 6, "unit": "", "tol": 0.001,
         "hint": "乘法口诀", "explain": "二三得六。"},
        {"type": "fill", "difficulty": 2, "q": "示例填空：apple 的复数是 ____", "answer": ["apples"],
         "hint": "直接加 s", "explain": "apple → apples。"},
    ]},
    "teach": {"steps": [{"title": "第一步", "text": "先看懂这个概念说的是什么。"}, {"title": "第二步", "text": "再看一个例子。"}],
              "example": {"q": "示例题", "a": "示例答案", "fig": {"kind": "scale", "tool": "cylinder", "min": 0, "max": 50, "major": 10,
                                                                  "minor": 5, "unit": "mL", "value": 32, "alt": "量筒里有 32 mL 水"}},
              "remember": "一句话记住：多练习。", "terms": []},
    "lookup": {"word": "example", "phonetic": "/ɪɡˈzɑːmpəl/", "pos": "n.", "meaning": "例子",
               "simple_en": "something that shows what others are like", "synonyms": ["sample", "instance"], "other_meanings": ["榜样"],
               "example": "Give me an example.", "example_zh": "给我举个例子。", "pinyin": "lì zi"},
    "explain": {"meaning": "这句话的意思是……", "structure": "主干：……", "points": [{"text": "example", "note": "例子"}]},
    "review": {"verdict": "partial", "points": [{"hit": True, "note": "示例：说到了主要矛盾"}, {"hit": False, "note": "示例：没有提到第二层后果"}],
               "feedback": "示例点评：主要矛盾抓得准，但还没想到留下的人会怎么反应。", "followup": "示例追问：如果最好的人先走了，会怎样？"},
    "discuss": {"steelman": "示例：你抓住了一个比讲义更进一步的点，AI 抬高的是全行业的底线。", "answer": "示例：小公司可以先试再定，因为……",
                "challenge": "示例：如果 90 分人人都有，你们靠什么拿到 95？", "moves": [{"id": "CEO-T-05", "note": "示例：想到了行业都会跟进"}]},
    "discuss_close": {"shift": "示例：一开始你担心的是成本，后来你更关心能力怎么建。", "near": [2], "own": "示例：先建能力再定人数的顺序。",
                      "watch": "示例：如果现金只够三个月，先建能力可能来不及。", "moves": [{"id": "CEO-T-01", "note": "示例：抓住了能力建设"}]},
    "sentence": {"ok": True, "praise": "用法正确！", "issue": "", "corrected": "", "better": "This is a good example.", "score": 4},
    "passage": {"title": "A Small Seed", "body": "A small seed fell on the ground.\n\nIt rained, and the seed began to grow.",
                "questions": [{"q": "What fell on the ground?", "options": ["A leaf", "A seed", "A stone", "A bird"],
                               "answer": 1, "explain": "第一句就说了 seed。"}],
                "topic": "一颗种子遇到雨水发了芽", "learn": [{"text": "It rained, and the seed began to grow.", "note": "and 连起两件事，前因后果一下就清楚"}],
                "think": "如果一直不下雨，种子会怎样？"},
    "passage_check": {"ok": True},
    "context": {"story": "很久以前，人们为了公平地交换东西，需要一个大家都认可的办法。", "question": "怎样才能让每个人量出来的都一样？",
                "fig": {"kind": "story", "beats": [{"when": "很久以前", "icon": "🦶", "text": "人们用脚和手臂当尺子，各地都不一样"},
                                                    {"when": "1790 年", "icon": "📏", "text": "法国科学家决定造一把大家都认的尺子"},
                                                    {"when": "今天", "icon": "🛒", "text": "买东西、看病、盖房子都用同一套单位"}]}, "uses": ["买东西算账的时候", "做实验记录数据的时候"],
                "fun": "古埃及人用身体的一部分当尺子。", "next": "学会它，就能去解决更复杂的问题。"},
    "ping": {"ok": True},
    "news_pick": {"secondary": [{"i": 0, "why": "示例：和经济课有关", "topic": "economy", "subjects": ["经济"]},
                                {"i": 1, "why": "示例备选", "topic": "science", "subjects": ["物理"]}],
                  "primary": [{"i": 1, "why": "示例：孩子感兴趣的科学新闻", "topic": "science", "subjects": ["科学"]}]},
    "news_write": {"title": "A New Kind of Battery", "body": "Scientists made a new battery this week.\n\nIt can charge a phone in five minutes.",
                   "glossary": [{"w": "battery", "zh": "电池"}, {"w": "charge", "zh": "充电"}],
                   "background": "示例：电池是手机和电动车的心脏。", "links": [{"subject": "物理", "point": "电能和化学能的转化"}],
                   "discuss": {"q": "Would you buy a phone that charges in five minutes? Why?", "zh": "你会买吗？说说理由。"},
                   "questions": [{"q": "What did scientists make?", "options": ["A car", "A battery", "A phone", "A robot"],
                                  "answer": 1, "explain": "第一句就说了 battery。"}]},
}
