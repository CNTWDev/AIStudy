"""等 AI 回话：按下就锁住、显示在做什么、边写边看（docs/DESIGN.md 5.40，app/llm/stream.py，web.streamed）。"""
import json
import pathlib
from contextlib import contextmanager

import httpx
from fastapi.testclient import TestClient

from app import llm
from app.llm.base import ChatRequest, ProviderConfig
from app.llm.providers import MockProvider, OpenAICompatProvider
from app.llm.stream import listen, partial_fields, sink_for
from app.main import app
from test_zz_discuss import KP, _item_id, _login

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"
NDJSON = {"Accept": "application/x-ndjson, application/json"}


def _lines(r) -> list[dict]:
    return [json.loads(x) for x in r.text.splitlines() if x.strip()]


def test_partial_fields_reads_strings_still_being_written():
    assert partial_fields('{"steelman": "你抓住了', ("steelman", "challenge")) == {"steelman": "你抓住了"}
    assert partial_fields('{"steelman":"一\\n二","answer":"","challenge":"如果\\"对手\\"', ("steelman", "answer", "challenge")) == \
        {"steelman": "一\n二", "challenge": '如果"对手"'}
    assert partial_fields('{"steelman": "半个转义\\', ("steelman",)) == {"steelman": "半个转义"}
    assert partial_fields('{"steelman": "\\u4f60\\u59', ("steelman",)) == {"steelman": "你"}
    assert partial_fields('{"care": true}', ("steelman",)) == {}


def test_sink_only_for_tasks_people_wait_on():
    got = []
    assert sink_for("discuss") is None  # 没人在等
    with listen(got.append):
        assert sink_for("items") is None  # 出题（带答案）不边写边给
        on_text = sink_for("review", every=0)
        on_text('{"verdict":"partial","points":[{"hit":true}],"feedback":"抓得准')
        on_text('{"verdict":"partial","points":[{"hit":true}],"feedback":"抓得准')  # 没变化不重复送
    assert got == [{"feedback": "抓得准"}]  # verdict、points 等写完校验过再给


def test_discuss_answer_streams_parts_then_the_same_result():
    with TestClient(app) as c:
        _login(c)
        iid = _item_id()
        r = c.post("/api/answer", json={"item_id": iid, "kp_id": KP, "answer": "不该停招，90 分会变成行业底线"}, headers=NDJSON)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
        assert r.headers["x-accel-buffering"] == "no"  # nginx 不攒着
        lines = _lines(r)
        parts, done = [m["part"] for m in lines if "part" in m], lines[-1]["done"]
        assert parts and parts[0]["steelman"].startswith("示例") and set(parts[-1]) <= {"steelman", "answer", "challenge"}
        assert done["discuss"] and done["ai"] and done["challenge"] and done["can_continue"]
        # 收尾也一样；不带 Accept 的照常整份返回
        end = _lines(c.post("/api/answer", json={"item_id": iid, "kp_id": KP, "answer": "", "discussion_id": done["discussion_id"],
                                                  "conclusion": "先建能力再定人数"}, headers=NDJSON))
        assert any("shift" in m.get("part", {}) for m in end) and end[-1]["done"]["done"]
        plain = c.post("/api/answer", json={"item_id": iid, "kp_id": KP, "answer": "第二次想：小公司不一样"})
        assert plain.headers["content-type"].startswith("application/json") and plain.json()["discuss"]


def test_streamed_errors_come_back_as_a_line():
    with TestClient(app) as c:
        _login(c)
        iid = _item_id()
        lines = _lines(c.post("/api/answer", json={"item_id": iid, "kp_id": KP, "answer": "嗯"}, headers=NDJSON))
        assert lines == [{"error": "先写下你的想法", "status": 400}]


def test_review_card_answer_streams_too():
    from app import engine
    from test_zz_discuss import ITEM
    with TestClient(app) as c:
        uid = _login(c)
        iid = _item_id()
        cid = engine.add_card(uid, "mistake", ITEM["q"], "旧的参考答案", {"item_id": iid}, KP)
        lines = _lines(c.post(f"/api/review/{cid}/answer", json={"item_id": iid, "answer": "不该停招，先建能力"}, headers=NDJSON))
        assert any("part" in m for m in lines) and lines[-1]["done"]["discuss"] and lines[-1]["done"]["redo"]


def test_providers_stream_text_as_it_comes(monkeypatch):
    seen = []
    req = ChatRequest(task="discuss", system="s", user="u")
    assert MockProvider(ProviderConfig(name="m", type="mock")).stream(req, seen.append) == seen[-1] and len(seen) > 1

    sse = ['data: {"choices":[{"delta":{"content":"{\\"steelman\\": \\"你"}}]}', "",
           'data: {"choices":[{"delta":{"content":"好\\"}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}',
           "data: [DONE]"]
    sent = {}

    class Resp:
        status_code = 200

        def iter_lines(self):
            return iter(sse)

    @contextmanager
    def fake_stream(method, url, json=None, **kw):
        sent.update(json)
        yield Resp()
    monkeypatch.setattr(httpx, "stream", fake_stream)
    p = OpenAICompatProvider(ProviderConfig(name="o", type="openai_compat", model="x", api_key="k", base_url="http://ai"))
    seen.clear()
    req = ChatRequest(task="discuss", system="s", user="u")
    assert p.stream(req, seen.append) == '{"steelman": "你好"}'
    assert sent["stream"] is True and seen == ['{"steelman": "你', '{"steelman": "你好"}']
    assert req.usage == {"input_tokens": 3, "output_tokens": 2, "truncated": False}


def test_every_ai_reply_button_uses_the_one_wait():
    """思辨题（开始、接着说、写结论）和 AI 点评（提交、回答追问）的按钮都走 aiWait；做题的页面都把 onPart 交给 api。"""
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for call in ("aiWait(btn, $('.fbbox', box), W === 'discuss' ? 'discuss' : 'review', ans)", "aiWait(fu, fb, 'review', ta)",
                 "aiWait(go, ctl, 'discuss', ta)", "aiWait($('.dsc-done', ctl), ctl, 'discuss_close'"):
        assert call in js, call
    for page in ("learn.html", "mix.html", "thinking.html", "warmup.html", "review.html"):
        assert "{onPart}" in (ROOT / "templates" / page).read_text(encoding="utf-8"), page
    assert llm.stream.FIELDS.keys() == {"discuss", "discuss_close", "review"}
