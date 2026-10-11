"""题目录音（听力题）：题目可以带 audio 字段，存的是「录音稿」而不是音频文件，和题目文字一起存一次、反复用。

    {"lang": "en", "lines": [{"who": "F", "text": "Excuse me, how much is this T-shirt?"},
                             {"who": "M", "text": "It's 45 yuan."}], "plays": 2}

- who 是角色：N 旁白 / F 女声 / M 男声 / C 孩子。播放时前端一句一句交给朗读服务（app/tts，每个角色可以配不同的声音，
  见 config/tts.example.toml 的 [roles.*]），第一次读完的音频存下来，以后谁再听都不花钱；朗读服务没开时用浏览器自带的声音。
- plays：作答前最多听几遍（IGCSE 每段放两遍）；答完以后不限。
- 作答前页面上只有播放按钮，不显示稿子；答完稿子在「带答案」的部分出现（itemtypes.reveal 的 transcript）。

和配图（app/figures/）是并列的一类「题目素材」，接口相同：normalize（不合格这道题就不要）/ present（给页面）/
describe（给 AI 校对、改编看的文字）/ prompt_for（出题提示词里的写法，只给标了 audio 的板块）。
"""
ROLES = {"N": "旁白", "F": "女", "M": "男", "C": "孩子"}
MAX_LINES = 12
MAX_LINE_CHARS = 300
MAX_CHARS = 1500
PLAYS = 2

__all__ = ["ROLES", "normalize", "present", "transcript", "describe", "wanted", "prompt_for"]


def normalize(raw) -> dict | None:
    """检查、整理一段录音稿；不合格返回 None。一段文字也行（当成旁白读）。"""
    if isinstance(raw, str):
        raw = {"lines": [{"who": "N", "text": raw}]}
    if not isinstance(raw, dict) or not isinstance(raw.get("lines"), list):
        return None
    lines = []
    for ln in raw["lines"]:
        if isinstance(ln, str):
            ln = {"text": ln}
        text = " ".join(str((ln or {}).get("text") or "").split()) if isinstance(ln, dict) else ""
        if not text:
            continue
        if len(text) > MAX_LINE_CHARS:
            return None
        who = str(ln.get("who") or "N").strip().upper()[:1]
        lines.append({"who": who if who in ROLES else "N", "text": text})
    if not lines or len(lines) > MAX_LINES or sum(len(x["text"]) for x in lines) > MAX_CHARS:
        return None
    try:
        plays = min(3, max(1, int(raw.get("plays") or PLAYS)))
    except (TypeError, ValueError):
        plays = PLAYS
    return {"lang": str(raw.get("lang") or "en").lower()[:5], "lines": lines, "plays": plays}


def present(item: dict) -> dict | None:
    """给页面播放用的：语言、能听几遍、每句的角色和文字（文字交给朗读服务，页面上不显示）。"""
    a = (item or {}).get("audio")
    if not isinstance(a, dict) or not a.get("lines"):
        return None
    return {"lang": a.get("lang", "en"), "plays": a.get("plays", PLAYS),
            "lines": [{"who": x.get("who", "N"), "text": x.get("text", "")} for x in a["lines"]]}


def transcript(item: dict) -> list[dict]:
    """答完以后显示的听力原文：[{who: "女", text}]；只有旁白一个人说时不标角色。"""
    a = (item or {}).get("audio")
    if not isinstance(a, dict):
        return []
    solo = all(x.get("who", "N") == "N" for x in a.get("lines", []))
    return [{"who": "" if solo else ROLES.get(x.get("who"), ""), "text": x.get("text", "")} for x in a.get("lines", [])]


def describe(a) -> str:
    """给 AI 校对 / 改编看的文字版。"""
    if not isinstance(a, dict) or not a.get("lines"):
        return ""
    return "听力原文（学生只能听录音，看不到这段文字）：\n" + "\n".join(f"{x['who']}: {x['text']}" for x in a["lines"])


def wanted(pack, kp: dict) -> bool:
    """这个知识点的题要不要带录音：教材包里板块标了 "audio": true（目前是英语的「听说」板块）。"""
    return pack is not None and any(s.get("id") == kp.get("strand") and s.get("audio") for s in pack.strands)


def prompt_for(pack, kp: dict) -> str:
    """出题提示词里的录音写法：只给标了 audio 的板块。"""
    if not wanted(pack, kp):
        return ""
    lang = pack.lang or "en"
    return (
        "\n听力题：这个知识点练的是听，每道题都要带 audio（录音稿），学生只能听，看不到稿子："
        '"audio":{"lang":"' + lang + '","lines":[{"who":"F|M|N|C","text":"一句话"}],"plays":2}。'
        "who：F 女声、M 男声（对话用这两个）、N 旁白（独白、广播、讲座）、C 孩子。"
        "题干只写要回答的问题（如 What time does the film start?），不要把录音内容写进题干、选项或提示；"
        "答案必须能从录音里听出来，干扰项最好也在录音里出现过（比如说过又改掉的时间、价格）。"
        "长度按年级：小学 2-4 句、每句不超过 12 个词；初中 4-8 句；数字、时间、拼写类信息要说清楚。"
    )
