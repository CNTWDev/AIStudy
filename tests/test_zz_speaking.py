"""口语「开口练」（app/speaking.py、app/stt，DESIGN 5.67）：语音引擎的配置和能力、四步主线、复习、和 AI 聊、记录页。"""
import json
import tomllib
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, db, speaking
from app.main import app
from app.stt import Speech, Transcript, Word, load_settings, parse_settings
from app.stt.providers import ASSESSORS, TRANSCRIBERS
from app.stt.wav import make_wav, wav_info

WAV = make_wav(3.0, 16000)


def _adult(c):
    email = f"{uuid.uuid4().hex[:8]}@speak.example"
    auth.create_user(email, "secret123", "老板", role="kid", grade="ADULT")
    c.post("/login", data={"email": email, "password": "secret123"})
    return db.one("SELECT id FROM users WHERE email=?", email)["id"]


def _post_clip(c, url, **fields):
    return c.post(url, files={"file": ("clip.wav", WAV, "audio/wav")}, data={k: str(v) for k, v in fields.items()})


def _sid(r):
    return int(r.headers["location"].rsplit("/", 1)[1])


# ------------------------------------------------------------------ 语音引擎


def test_settings_and_capabilities(tmp_path):
    s = parse_settings(tomllib.loads("""
[transcribe]
provider = "volcengine"
[assess]
provider = "none"
[volcengine]
api_key = "${NOPE_NOT_SET}"
"""))
    assert s.transcribe == "volcengine" and s.assess == "none" and s.of("volcengine")["api_key"] == ""
    st = Speech(s).status()
    assert not st["transcribe"]["on"] and "word_times" in st["transcribe"]["caps"]   # 没填 key：不可用，但能说清会什么
    assert not st["assess"]["on"] and st["assess"]["why"] == "没配置"
    assert not Speech(s).can("scripted")
    # 不认识的厂商、坏掉的配置文件：说清楚，不崩
    assert "可选" in Speech(parse_settings({"transcribe": {"provider": "nobody"}})).status()["transcribe"]["why"]
    bad = tmp_path / "stt.toml"
    bad.write_text("[transcribe\nprovider=", encoding="utf-8")
    assert load_settings(bad).error
    # 能力声明：只有 Azure 会评发音；阿里云的千问识别没有词级时间
    assert set(ASSESSORS) >= {"azure", "mock"} and "aliyun" not in ASSESSORS and "volcengine" not in ASSESSORS
    assert "word_times" not in TRANSCRIBERS["aliyun"].caps and "word_times" in TRANSCRIBERS["volcengine"].caps
    assert "prosody" in ASSESSORS["azure"].caps


def test_wav_check_and_metrics():
    assert wav_info(WAV)["rate"] == 16000
    sp = speaking.engine()
    with pytest.raises(Exception):
        sp.check_wav(b"not a wav at all")
    tr = Transcript(text="We need to 推迟 the launch", words=[
        Word("We", 3.2, 3.4), Word("need", 3.5, 3.7), Word("to", 3.8, 3.9), Word("推迟", 6.5, 7.0),
        Word("the", 7.1, 7.2), Word("launch", 7.3, 7.8)], duration=8)
    m = speaking.metrics(tr)
    assert m["latency"] == 3.2 and m["pauses"] == 1 and m["zh"] == 1 and m["words"] == 5 and m["timed"]
    m = speaking.metrics(Transcript(text="我们 need more 时间", words=[], duration=4))
    assert m["latency"] is None and m["pauses"] is None and m["zh"] == 2


def test_chunk_matching_with_gaps():
    used = speaking.used_chunks("We're pushing the launch back two weeks, and we'll get it fixed.",
                                [{"id": 1, "en": "push ... back"}, {"id": 2, "en": "get it fixed"}, {"id": 3, "en": "turn up an issue"}])
    assert used == [1, 2]
    assert speaking.used_chunks("make it easy for our team to start", [{"id": 9, "en": "make it easy for sb to"}]) == [9]



def test_chunk_said_loosely():
    """核心意思说出来就算：口头词、冠词、变形和识别听错一个字母都不影响；意思不对还是不算。"""
    said = lambda text, en: speaking.used_chunks(text, [{"id": 1, "en": en}]) == [1]
    assert said("uh, We bring AI to H devices.", "Basically, we bring AI to ...")
    assert said("We need to get it fix by Friday.", "get it fixed")
    assert said("Did they turn up a issue?", "turn up an issue")
    assert said("We brought AI to phones", "we bring AI to ...")
    assert not said("We want AI everywhere.", "Basically, we bring AI to ...")
    assert not said("I got it.", "get it fixed")
    assert not said("It is easy.", "make it easy for sb to")

def test_packs_are_valid():
    for p in speaking.packs():
        assert p["accent"] in ("en-US", "en-GB") and p["groups"] and p["plays"]
        ids = [s["id"] for s in speaking.scenes(p["id"])]
        assert len(ids) == len(set(ids)) >= 10
        for s in speaking.scenes(p["id"]):
            assert s["title"] and s["situation"] and s["goals"] and s["model"] and 1 <= len(s["chunks"]) <= 3
            for ch in s["chunks"]:   # 课程包写好的表达块要能在参考说法里找到（地道版退回参考说法时才对得上）
                assert speaking.chunk_regex(ch["en"]).search(" " + s["model"] + " "), (s["id"], ch["en"])
        for pl in p["plays"]:
            assert pl["who"] and pl["persona"] and pl["opener"]


# ------------------------------------------------------------------ 四步主线


def test_scene_four_steps_then_review():
    with TestClient(app) as c:
        uid = _adult(c)
        home = c.get("/speak")
        assert home.status_code == 200 and "开口练" in home.text and "和 AI 聊下去" in home.text
        r = c.get("/speak/go", follow_redirects=False)       # 表达库是空的：直接进场景
        assert r.status_code == 303 and r.headers["location"].startswith("/speak/s/")
        sid = _sid(r)
        page = c.get(f"/speak/s/{sid}")
        assert page.status_code == 200 and "先说" in page.text
        # 1 先说：夹中文也行
        r = _post_clip(c, "/api/speak/clip", session=sid, step="first").json()
        assert r["clip"] and "推迟" in r["text"] and r["metrics"]["zh"] >= 1 and r["metrics"]["latency"] == 4.1
        assert r["metrics"]["pauses"] == 1
        # 2 地道版：最多 3 个表达块、只改 1 处
        rw = c.post("/api/speak/rewrite", json={"session": sid}).json()
        assert rw["native"] and 1 <= len(rw["chunks"]) <= 3 and rw["fix"]["kind"] and len(rw["sentences"]) >= 2
        # 3 跟读：没被听到的词标出来；配了评测再标读得不准的
        sh = _post_clip(c, "/api/speak/clip", session=sid, step="shadow:0").json()
        assert sh["tries"] == 1 and any(not m["ok"] for m in sh["marks"]) and sh["assessed"]
        assert any(m.get("pron") for m in sh["marks"])
        assert _post_clip(c, "/api/speak/clip", session=sid, step="shadow:9").status_code == 400
        # 4 不看稿再说一遍：和第一遍对比，新表达进表达库
        rt = _post_clip(c, "/api/speak/clip", session=sid, step="retell").json()
        assert rt["learned"] == len(rw["chunks"]) and {x["k"] for x in rt["compare"]} >= {"开口用时", "说中文"}
        assert db.one("SELECT done_at FROM speak_sessions WHERE id=?", sid)["done_at"]
        assert speaking.library_count(uid) == len(rw["chunks"])
        # 录音只有本人能听
        assert c.get(f"/speak/clip/{rt['clip']}").headers["content-type"] == "audio/wav"
        # 练完以后，今天练过了；到期之前没有复习
        assert speaking.today_state(auth.get_user(uid))["practiced"]
        assert not speaking.due_chunks(uid)
        # 第二天：先复习，再进场景
        db.run("UPDATE speak_memory SET due=?, learned_at=? WHERE user_id=?", db.today().isoformat(), "2000-01-01", uid)
        assert c.get("/speak/go", follow_redirects=False).headers["location"] == "/speak/review?then=scene"
        page = c.get("/speak/review?then=scene")
        assert page.status_code == 200 and "复习" in page.text
        cid = speaking.due_chunks(uid)[0]["id"]
        rv = _post_clip(c, "/api/speak/review/clip", chunk=cid).json()
        assert "hit" in rv and rv["say"]
        g = c.post("/api/speak/review/grade", json={"chunk": cid, "ok": True}).json()
        assert g["next_in"] == 2


def test_review_cap_never_grows():
    """每天最多复习 REVIEW_CAP 个：多的排队往后，复习过的不算回去。"""
    with TestClient(app) as c:
        uid = _adult(c)
        ids = [speaking.chunk_upsert(f"test phrase number {n}", "测试") for n in range(9)]
        speaking.learn(uid, ids, "test")
        db.run("UPDATE speak_memory SET due=?, learned_at=? WHERE user_id=?", db.today().isoformat(), "2000-01-01", uid)
        due = speaking.due_chunks(uid)
        assert len(due) == speaking.REVIEW_CAP
        for x in due[:2]:
            speaking.grade(uid, x["id"], False)
        assert len(speaking.due_chunks(uid)) == speaking.REVIEW_CAP - 2
        for x in speaking.due_chunks(uid):
            speaking.grade(uid, x["id"], True)
        assert speaking.due_chunks(uid) == []


def test_own_scene_is_private():
    with TestClient(app) as c:
        _adult(c)
        r = c.post("/speak/own", data={"text": "下午和供应商开会，要说明年量翻倍，希望单价降 10%。"}, follow_redirects=False)
        sid = _sid(r)
        s = db.one("SELECT ref, data FROM speak_sessions WHERE id=?", sid)
        assert s["ref"] == "own" and json.loads(s["data"])["scene"]["title"]
        page = c.get(f"/speak/s/{sid}").text
        assert "供应商" in page


def test_chat_turns_and_close():
    with TestClient(app) as c:
        uid = _adult(c)
        play = speaking.packs()[0]["plays"][0]
        sid = _sid(c.get(f"/speak/play/{play['id']}", follow_redirects=False))
        page = c.get(f"/speak/s/{sid}")
        assert page.status_code == 200 and play["opener"][:20] in page.text
        t = _post_clip(c, "/api/speak/chat/turn", session=sid).json()
        assert t["me"] and t["reply"] and t["left"] == speaking.CHAT_MAX - 1 and not t.get("over")
        cl = c.post("/api/speak/chat/close", json={"session": sid}).json()
        assert cl["note"] and cl["chunks"] and cl["fix"]["kind"]
        assert speaking.library_count(uid) == len(cl["chunks"])
        assert _post_clip(c, "/api/speak/chat/turn", session=sid).status_code == 400
        # 一句没说就退出：不留记录
        sid2 = _sid(c.get(f"/speak/play/{play['id']}", follow_redirects=False))
        assert c.post("/api/speak/chat/close", json={"session": sid2}).json() == {"empty": True}
        assert not db.one("SELECT id FROM speak_sessions WHERE id=?", sid2)
        # 自己出一局
        sid3 = _sid(c.post("/speak/play", data={"text": "美国客户觉得我们报价太高，我要说服他。"}, follow_redirects=False))
        assert c.get(f"/speak/s/{sid3}").status_code == 200


def test_records_prefs_and_today_entry():
    with TestClient(app) as c:
        uid = _adult(c)
        sid = _sid(c.get("/speak/go", follow_redirects=False))
        _post_clip(c, "/api/speak/clip", session=sid, step="first")
        c.post("/api/speak/rewrite", json={"session": sid})
        _post_clip(c, "/api/speak/clip", session=sid, step="retell")
        r = speaking.records(uid)
        assert len(r["weeks"]) == 4 and r["weeks"][-1]["latency"] == 4.1 and r["tot"]["scenes"] == 1
        assert r["common"]["kind"] == "过去的事用过去式"
        page = c.get("/speak/records")
        assert page.status_code == 200 and "三个数" in page.text and "回听录音" in page.text
        # 每天的任务默认不放；打开以后才是今天页上的一张大卡
        u = auth.get_user(uid)
        assert speaking.today_card(u)["daily"] is False
        prefs = c.post("/api/speak/prefs", json={"packs": ["ceo", "nope"], "daily": True}).json()
        assert prefs == {"packs": ["ceo"], "daily": True}
        assert speaking.next_scene(auth.get_user(uid))["pack"] == "ceo"


def test_school_kids_cannot_open_speaking():
    with TestClient(app) as c:
        pa = auth.create_user(f"{uuid.uuid4().hex[:8]}@p.example", "secret123", "家长", "parent")
        login = f"sk{uuid.uuid4().hex[:6]}"
        kid = auth.create_user(f"{login}@x.example", "2580", "小宝", "kid", parent_id=pa, grade="G4")
        assert not speaking.available(auth.get_user(kid))
        assert speaking.today_card(auth.get_user(kid)) is None


def test_admin_try_engine():
    with TestClient(app) as c:
        assert c.post("/api/admin/stt/reload").status_code in (401, 403)
        r = speaking.test_engine(WAV)
        assert r["transcribe"]["text"] and "ms" in r["transcribe"] and r["assess"]["scores"]
        assert speaking.test_engine(b"xx").get("error")


def test_engine_errors_are_kept_for_admin(monkeypatch):
    """厂商报错：页面上的原话会一闪而过，所以每次都记一条，后台系统页「语音引擎」卡片能看到（还有请求编号）。"""
    import httpx
    from app.stt.providers import MockTranscriber, Volcengine
    with TestClient(app) as c:
        uid = _adult(c)
        sid = _sid(c.get("/speak/go", follow_redirects=False))

        def boom(self, wav, langs, expect=""):
            raise speaking.STTError("字节火山返回 45000151：audio format invalid（请求编号 abc123）")
        monkeypatch.setattr(MockTranscriber, "transcribe", boom)
        r = _post_clip(c, "/api/speak/clip", session=sid, step="first")
        assert r.status_code == 503 and "45000151" in r.json()["detail"]
        e = speaking.recent_errors(1)[0]
        assert e["kind"] == "transcribe" and e["step"] == "first" and "abc123" in e["message"]
        assert db.one("SELECT user_id FROM stt_errors ORDER BY id DESC LIMIT 1")["user_id"] == uid
    # 火山的错误码和请求编号都进错误信息
    v = Volcengine({"api_key": "k"}, 5)
    resp = httpx.Response(200, headers={"X-Api-Status-Code": "45000001", "X-Api-Message": "invalid param", "X-Tt-Logid": "LOG9"},
                          request=httpx.Request("POST", Volcengine.URL))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: resp)
    with pytest.raises(speaking.STTError, match="45000001.*invalid param.*LOG9"):
        v.transcribe(WAV, ["en", "zh"])
    resp403 = httpx.Response(403, headers={"X-Api-Status-Code": "45000030", "X-Api-Message": "resource not granted", "X-Tt-Logid": "LOG7"},
                             text="{}", request=httpx.Request("POST", Volcengine.URL))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: resp403)
    with pytest.raises(speaking.STTError, match="HTTP 403（45000030）：resource not granted.*LOG7"):
        v.transcribe(WAV, ["en", "zh"])


def test_volcengine_sites(monkeypatch):
    """火山引擎国内站和 BytePlus 国际站：接口一样、钥匙不通用，按 region 选地址和资源。"""
    import httpx
    from app.stt.providers import Volcengine
    seen = {}

    def fake(url, headers=None, json=None, **k):
        seen.update(url=url, headers=headers, body=json)
        return httpx.Response(200, json={"result": {"text": "hi", "utterances": [{"words": [{"text": "hi", "start_time": 300, "end_time": 600}]}]},
                                         "audio_info": {"duration": 1000}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", fake)
    tr = Volcengine({"api_key": " k \n", "region": "intl"}, 5).transcribe(WAV, ["en", "zh"])
    assert seen["url"] == "https://voice.ap-southeast-1.bytepluses.com/api/v3/auc/bigmodel/recognize/flash"
    assert seen["headers"]["X-Api-Resource-Id"] == "volc.seedasr.auc_turbo" and seen["headers"]["X-Api-Key"] == "k"
    assert "language" not in seen["body"]["audio"]   # 中英夹说：不指定语言
    assert tr.text == "hi" and tr.words[0].start == 0.3
    assert Volcengine({"api_key": "k"}, 5).site[0].endswith("bytepluses.com")   # 不填 region：默认国际站（服务器在日本）
    Volcengine({"api_key": "k", "region": "cn"}, 5).transcribe(WAV, ["en"])
    assert seen["url"].startswith("https://openspeech.bytedance.com/") and seen["headers"]["X-Api-Resource-Id"] == "volc.bigasr.auc_turbo"
    v = Volcengine({"api_key": "k", "region": "us"}, 5)
    assert not v.ready()[0] and "intl" in v.ready()[1]
    assert "BytePlus" in Volcengine({"api_key": "k", "region": "intl"}, 5).label


def test_admin_try_shows_audio_and_vendor_reason(monkeypatch):
    """后台「试一下」认不出字时要看得出原因：录到几秒、有多响（静音一眼看出），厂商回了什么。"""
    import io as _io
    import struct
    import wave as _wave
    import httpx
    from app.stt.providers import Volcengine
    from app.stt.wav import wav_peak
    assert wav_peak(WAV) == 0
    buf = _io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(struct.pack("<4h", 0, 16384, -8192, 0))
    assert wav_peak(buf.getvalue()) == 0.5
    r = speaking.test_engine(WAV)
    assert r["audio"] == {"secs": 3.0, "peak": 0.0} and "info" in r["transcribe"]
    seen = {}

    def silent(url, headers=None, json=None, **k):
        seen["audio"] = json["audio"]
        return httpx.Response(200, headers={"X-Api-Status-Code": "20000003", "X-Tt-Logid": "L1"}, json={}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", silent)
    tr = Volcengine({"api_key": "k", "language": "en-US"}, 5).transcribe(WAV, ["en"])
    assert tr.text == "" and tr.info == {"code": "20000003", "logid": "L1", "silent": True}
    assert seen["audio"]["language"] == "en-US"   # 配了 language 才带上

    def empty(url, headers=None, json=None, **k):
        return httpx.Response(200, json={"result": {"text": ""}, "audio_info": {"duration": 3000}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", empty)
    tr = Volcengine({"api_key": "k"}, 5).transcribe(WAV, ["en"])
    assert tr.info["code"] == "20000000" and '"duration": 3000' in tr.info["raw"]


def test_keep_talking_never_blocked(monkeypatch):
    """多说多听：识别额度用完也照样录、照样算时长；聊天过了提示轮数也能接着聊；复习做完还能「再多说几个」，不改复习节奏。"""
    with TestClient(app) as c:
        uid = _adult(c)
        ids = [speaking.chunk_upsert(f"keep talking phrase {n}", "测试") for n in range(3)]
        speaking.learn(uid, ids, "test")
        before = {r["chunk_id"]: r["due"] for r in db.q("SELECT chunk_id, due FROM speak_memory WHERE user_id=?", uid)}
        page = c.get("/speak/review?more=1")
        assert page.status_code == 200 and "keep talking phrase" in page.text
        r = _post_clip(c, "/api/speak/review/clip", chunk=ids[0]).json()
        assert "hit" in r and not r["over"]
        assert c.post("/api/speak/review/grade", json={"chunk": ids[0], "ok": True, "free": True}).json()["free"]
        after = {r["chunk_id"]: r["due"] for r in db.q("SELECT chunk_id, due FROM speak_memory WHERE user_id=?", uid)}
        assert after == before
        # 额度用完：不报错，录音存下，时长照算，只是不转文字
        monkeypatch.setattr(speaking.engine().s, "daily_seconds_per_user", 1)
        n0 = speaking.totals(uid)["speak"]["secs"]
        r = _post_clip(c, "/api/speak/review/clip", chunk=ids[1])
        assert r.status_code == 200 and r.json()["over"] and r.json()["text"] == ""
        assert speaking.totals(uid)["speak"]["secs"] >= n0 + 2
        monkeypatch.undo()
        # 聊天过了 CHAT_HINT 轮还能接着说
        play = speaking.packs()[0]["plays"][0]
        sid = _sid(c.get(f"/speak/play/{play['id']}", follow_redirects=False))
        for _ in range(speaking.CHAT_HINT + 1):
            assert _post_clip(c, "/api/speak/chat/turn", session=sid).status_code == 200


def test_listen_and_speak_totals():
    with TestClient(app) as c:
        uid = _adult(c)
        t = c.get("/api/speak/totals").json()
        assert t["speak"]["secs"] == 0 and t["listen"]["next"] == 10
        c.post("/api/speak/listen", json={"secs": 650})   # 一次最多记 5 分钟
        c.post("/api/speak/listen", json={"secs": 330})
        t = c.get("/api/speak/totals").json()
        assert t["listen"]["secs"] == 600 and t["listen"]["today"] == 600 and t["listen"]["reached"] == 10 and t["listen"]["next"] == 30
        assert "一点一点攒起来" in c.get("/speak").text


def test_shadow_marks_are_loose():
    mk = speaking.marks("We'll get it fixed properly.", "we will get it fix properlly")
    assert [m["ok"] for m in mk] == [False, True, True, True, True]
