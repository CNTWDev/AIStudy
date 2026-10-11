"""听力题（app/audio/）：录音稿的检查、题目带录音的出题 / 显示 / 答完看原文、朗读服务按角色换声音。"""
import tempfile

from app import audio, bank, itemtypes
from app.arena import sources
from app.catalog import catalog
from app.llm import tasks
from app.tts import TTS, SQLiteIndex, parse_settings

DIALOGUE = {"lang": "en", "lines": [{"who": "f", "text": "Excuse me, how much is this T-shirt?"},
                                    {"who": "M", "text": "It was 50 yuan, but today it's 45."}], "plays": 5}


def _item(**over):
    return {"type": "mcq", "q": "How much is the T-shirt today?", "options": ["40 yuan", "45 yuan", "50 yuan"], "answer": 1,
            "explain": "店员说原价 50，今天 45。", "audio": DIALOGUE, **over}


def test_normalize():
    a = audio.normalize(DIALOGUE)
    assert a == {"lang": "en", "plays": 3, "lines": [{"who": "F", "text": "Excuse me, how much is this T-shirt?"},
                                                     {"who": "M", "text": "It was 50 yuan, but today it's 45."}]}
    assert audio.normalize("Good morning, class.")["lines"] == [{"who": "N", "text": "Good morning, class."}]
    assert audio.normalize({"lines": [{"who": "X", "text": "Hi"}]})["lines"][0]["who"] == "N"  # 不认识的角色当旁白
    assert audio.normalize({"lines": []}) is None and audio.normalize({"lines": [{"text": " "}]}) is None
    assert audio.normalize({"lines": [{"text": "word " * 80}]}) is None  # 一句太长
    assert audio.normalize({"lines": [{"text": "Hello."}] * 13}) is None  # 句子太多
    assert audio.normalize(None) is None


def test_item_with_audio():
    it = itemtypes.normalize(_item())
    assert it["audio"]["lines"][0]["who"] == "F"
    assert itemtypes.normalize(_item(audio={"lines": []})) is None  # 录音稿不合格：这道题不要
    pub = itemtypes.public(it)
    assert pub["audio"]["plays"] == 3 and "answer" not in pub and "explain" not in pub
    r = itemtypes.reveal(it)
    assert r["transcript"] == [{"who": "女", "text": "Excuse me, how much is this T-shirt?"},
                               {"who": "男", "text": "It was 50 yuan, but today it's 45."}]
    v = itemtypes.view(it, mine=2)
    assert v["audio"] and v["transcript"] and v["options"][1]["right"]
    assert "transcript" not in itemtypes.view(it, with_answer=False)
    solo = itemtypes.normalize(_item(audio="The film starts at seven."))
    assert itemtypes.reveal(solo)["transcript"] == [{"who": "", "text": "The film starts at seven."}]  # 只有旁白不标角色
    assert itemtypes.reveal(itemtypes.normalize(_item(audio=None)))["transcript"] == []
    # 文字一样、录音不一样是两道题
    other = itemtypes.normalize(_item(audio={"lines": [{"who": "F", "text": "It's 45 yuan."}]}))
    assert bank.item_hash(it) != bank.item_hash(other) != bank.item_hash(itemtypes.normalize(_item(audio=None)))
    assert not sources._short({**it, "options": ["a", "b", "c", "d"]})  # 游戏里不出听力题


def test_prompts():
    if not catalog.packs:
        catalog.load()
    pack = catalog.packs["eng-cambridge"]
    lis = next(k for k in catalog.kps.values() if k["id"].startswith("ENG-LIS-"))
    rea = next(k for k in catalog.kps.values() if k["id"].startswith("ENG-REA-"))
    assert audio.wanted(pack, lis) and not audio.wanted(pack, rea)
    assert audio.wanted(catalog.packs["eng-shanghai"], catalog.kp("ESH-LIS-05"))
    assert "audio" in tasks._item_schema(pack, lis) and "听力题" in tasks._item_schema(pack, lis)
    assert "听力题" not in tasks._item_schema(pack, rea) and "听力题" not in tasks._item_schema(pack)
    assert "45" in tasks._fig_text(itemtypes.normalize(_item()))  # 校对、改编时稿子一起给 AI


def test_tts_roles():
    d = tempfile.mkdtemp()
    s = TTS(parse_settings({"provider": "mock", "voices": {"en": "v-en"}, "roles": {"en": {"M": "v-man", "F": ""}}}),
            SQLiteIndex(d + "/i.db"), d)
    assert s.s.voice("en", "M") == "v-man" and s.s.voice("en", "F") == "v-en" and s.s.voice("en-GB", "M") == "v-man"
    man, plain, woman = (s.prepare("It's 45 yuan.", "en", role=r) for r in ("M", "", "F"))
    assert man.key != plain.key == woman.key  # 没配声音的角色用默认声音，缓存也共用
    assert s.index.get(man.key)["voice"] == "v-man"
