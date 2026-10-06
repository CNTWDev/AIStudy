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
        try:
            if self.cfg.extra.get("server_fallback", True):
                resp = self.client().beta.messages.create(
                    **kw, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            else:
                resp = self.client().messages.create(**kw)
        except anthropic.AuthenticationError as e:
            raise LLMError("AI 密钥无效，请管理员检查 config/llm.toml 里的 api_key") from e
        except anthropic.RateLimitError as e:
            raise LLMError("AI 服务太忙，请稍后再试") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"AI 服务出错（{e.status_code}）") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("连不上 AI 服务，请检查服务器网络") from e
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
        last = None
        for _ in range(self.cfg.max_retries + 1):
            try:
                r = httpx.post(url, json=body, timeout=self.cfg.timeout,
                               headers={"Authorization": f"Bearer {self.cfg.api_key}"})
            except httpx.HTTPError as e:
                last = LLMError("连不上 AI 服务，请检查服务器网络")
                last.__cause__ = e
                continue
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
            try:
                return r.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as e:
                raise LLMError("AI 返回格式不对，请再试一次") from e
        raise last


@register("mock")
class MockProvider(Provider):
    """离线假数据，只用于本地演示和自动测试。"""

    def ready(self):
        return True, ""

    def complete(self, req: ChatRequest) -> str:
        if req.task == "paper":  # 把示例题对应到提示词里给出的第一个候选知识点
            import re
            ids = re.findall(r"^- (\S+)：", req.user, re.M)
            data = json.loads(json.dumps(MOCK["paper"]))
            for i, q in enumerate(data["questions"]):
                q["kp_id"] = ids[i % len(ids)] if ids and i < 2 else "NOT-A-KP"
            return json.dumps(data, ensure_ascii=False)
        return json.dumps(MOCK.get(req.task, {}), ensure_ascii=False)


MOCK = {
    "ask": {"reply": "我们一步一步来：你先说说，这道题在问什么？", "reveals_answer": False},
    "paper": {"title": "示例测验", "subject_guess": "", "notes": "示例数据",
              "questions": [
                  {"label": "1", "type": "mcq", "q": "示例：1 m = ? cm", "options": ["10", "100", "1000", "0.1"], "answer": 1,
                   "explain": "1 米 = 100 厘米。", "score": 2, "page": 1, "student_answer": "C", "marked": "wrong"},
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
              "example": {"q": "示例题", "a": "示例答案"}, "remember": "一句话记住：多练习。", "terms": []},
    "lookup": {"word": "example", "phonetic": "/ɪɡˈzɑːmpəl/", "pos": "n.", "meaning": "例子",
               "simple_en": "something that shows what others are like", "synonyms": ["sample", "instance"], "other_meanings": ["榜样"],
               "example": "Give me an example.", "example_zh": "给我举个例子。", "pinyin": "lì zi"},
    "explain": {"meaning": "这句话的意思是……", "structure": "主干：……", "points": [{"text": "example", "note": "例子"}]},
    "sentence": {"ok": True, "praise": "用法正确！", "issue": "", "corrected": "", "better": "This is a good example.", "score": 4},
    "passage": {"title": "A Small Seed", "body": "A small seed fell on the ground.\n\nIt rained, and the seed began to grow.",
                "questions": [{"q": "What fell on the ground?", "options": ["A leaf", "A seed", "A stone", "A bird"],
                               "answer": 1, "explain": "第一句就说了 seed。"}]},
    "context": {"story": "很久以前，人们为了公平地交换东西，需要一个大家都认可的办法。", "uses": ["买东西算账的时候", "做实验记录数据的时候"],
                "fun": "古埃及人用身体的一部分当尺子。", "next": "学会它，就能去解决更复杂的问题。"},
    "ping": {"ok": True},
}
