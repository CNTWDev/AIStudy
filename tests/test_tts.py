"""朗读服务：同一段文字只生成一次，之后读本地文件；多个请求同时要同一段也只生成一次；网站接口和退回浏览器朗读。"""
import tempfile
import threading

from fastapi.testclient import TestClient

from app.tts import TTS, MemoryIndex, SQLiteIndex, TTSError, normalize, parse_settings, split_text
from app.tts.providers import Mock


def _svc(**over):
    d = tempfile.mkdtemp()
    cfg = {"provider": "mock", "voices": {"en": "v-en"}, "speed": {"passage": 0.8}, **over}
    return TTS(parse_settings(cfg), SQLiteIndex(d + "/i.db"), d)


def test_normalize_and_key():
    assert normalize("  Hello\n “world”  ") == 'Hello "world"'
    assert normalize("Apple") == "apple" and normalize("I am") == "I am"
    s = _svc()
    a = s.prepare("Hello   world.", "en", "sentence")
    b = s.prepare("Hello world.", "en", "sentence")
    c = s.prepare("Hello world.", "en", "passage")  # 语速不同 = 另一个文件
    assert a.key == b.key != c.key and not a.cached


def test_generate_once_then_cache():
    s = _svc()
    clip = s.prepare("The cat sat on the mat.", "en", "passage", user=1)
    before = Mock.calls
    first = b"".join(s.open(clip.key, user=1))
    assert first[:4] == b"RIFF" and Mock.calls == before + 1
    assert s.path(clip.key).exists() and s.index.get(clip.key)["status"] == "ready"
    assert s.prepare("The cat sat on the mat.", "en", "passage").cached
    assert b"".join(s.open(clip.key)) == first and Mock.calls == before + 1  # 第二次读文件，不再调用服务
    assert s.index.get(clip.key)["hits"] == 2


def test_concurrent_requests_generate_once():
    s = _svc()
    clip = s.prepare("Many readers, one recording.", "en", "passage")
    before, out = Mock.calls, []
    ths = [threading.Thread(target=lambda: out.append(b"".join(s.open(clip.key)))) for _ in range(4)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert Mock.calls == before + 1 and len(set(out)) == 1 and len(out[0]) > 100


def test_limits_and_disabled():
    s = _svc(daily_chars_per_user=30, max_chars=50)
    try:
        s.prepare("x" * 51, "en")
        assert False
    except TTSError as e:
        assert "50" in str(e)
    try:
        s.prepare("你好", "zh")  # 没给中文配声音
        assert False
    except TTSError:
        pass
    c1 = s.prepare("Twenty characters!!", "en", user=7)
    b"".join(s.open(c1.key, user=7))
    c2 = s.prepare("Another twenty chars", "en", user=7)
    try:
        s.open(c2.key, user=7)
        assert False
    except TTSError as e:
        assert "明天" in str(e)
    assert b"".join(s.open(c1.key, user=7))  # 已有的缓存不受限额影响
    off = TTS(parse_settings({"provider": "none"}), MemoryIndex(), tempfile.mkdtemp())
    assert not off.ready and off.langs() == []
    bad = TTS(parse_settings({"provider": "cartesia", "voices": {"en": "v"}}), MemoryIndex(), tempfile.mkdtemp())
    assert not bad.ready and "api_key" in bad.problem


def test_split_text():
    parts = split_text("This is one sentence. " * 40, 100)
    assert all(len(p) <= 100 for p in parts) and "".join(parts).replace(" ", "") == ("This is one sentence. " * 40).replace(" ", "")
    zh = split_text("今天天气很好。我们去公园玩吧！" * 20, 50)
    assert all(len(p) <= 50 for p in zh) and "".join(zh) == "今天天气很好。我们去公园玩吧！" * 20


def test_web_api():
    with TestClient(app_()) as c:
        if c.get("/login").text.count("创建网站管理员账号"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        else:
            c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        page = c.get("/admin?tab=system").text
        assert "朗读" in page and "挑声音" in page
        r = c.post("/api/tts", json={"text": "Good morning.", "lang": "en", "use": "sentence"}).json()
        assert r["url"].startswith("/tts/") and r["cached"] is False
        a = c.get(r["url"])
        assert a.status_code == 200 and a.headers["content-type"].startswith("audio/")
        r2 = c.post("/api/tts", json={"text": "Good   morning.", "lang": "en"}).json()
        assert r2["url"] == r["url"] and r2["cached"] is True
        assert c.get(r["url"]).content == a.content
        assert c.post("/api/tts", json={"text": "", "lang": "en"}).json()["fallback"]
        assert c.get("/tts/" + "0" * 32).status_code == 404
        assert "TTS_LANGS" in c.get("/admin").text
        c.get("/logout")
        assert c.post("/api/tts", json={"text": "hi", "lang": "en"}).status_code in (401, 403, 303)


def app_():
    from app.main import app
    return app


def test_failure_reason_is_visible(monkeypatch):
    """厂商报错时：/tts/<key> 返回 503 带原因，/api/tts/status 也能查到原因（试听页显示出来）。"""
    from app.tts.providers import TTSProviderError

    def boom(self, text, **kw):
        raise TTSProviderError("Cartesia 401：Invalid API key")
        yield b""  # noqa
    with TestClient(app_()) as c:
        if c.get("/login").text.count("创建网站管理员账号"):
            c.post("/register", data={"email": "admin@x.com", "password": "secret1", "name": "站长"})
        else:
            c.post("/login", data={"email": "admin@x.com", "password": "secret1"})
        monkeypatch.setattr(Mock, "stream", boom)
        r = c.post("/api/tts", json={"text": "This one will fail.", "lang": "en"}).json()
        assert "url" in r, r
        a = c.get(r["url"])
        assert a.status_code == 503 and "Invalid API key" in a.text, (a.status_code, a.text[:200])
        st = c.get("/api/tts/status/" + r["key"]).json()
        assert st["status"] == "failed" and "Invalid API key" in st["error"] and not st["file"]
        monkeypatch.undo()
        assert c.get(r["url"]).status_code == 200  # 修好后再点一次就重新生成
        assert c.get("/api/tts/status/" + r["key"]).json()["status"] == "ready"
