"""具体的 AI 任务：每个函数拼好提示词，交给 service.ask_json。
提示词只在这里改；换模型/换厂商改 config/llm.toml，不用动这里。"""
from .service import ask_json


TUTOR = (
    "你是一位耐心、鼓励式的中国家庭学习辅导老师，面向上海的中小学生。"
    "讲解要短、具体、一步一步，先给最容易的一步，避免一次给太多。"
)


def _audience(grade: str, pack) -> str:
    from ..catalog import stage_label
    s = f"学生年级：{stage_label(grade)}。学科：{pack.subject_name}（{pack.edition}）。"
    if pack.international:
        s += "学生在国际学校，这一科用英文授课和考试，但英文基础较弱：题干用英文，同时给中文翻译，讲解用中文并标出关键英文术语。"
    elif pack.subject == "english":
        s += "题目用英文，题目要求和讲解用中文。"
    else:
        s += "全部用中文。"
    return s


ITEM_SCHEMA = (
    '{"items":[{"type":"mcq|num|fill|short","difficulty":1-3,"q":"题干","zh":"中文翻译(可选)",'
    '"options":["仅mcq，4个选项"],"answer":"mcq为正确选项下标(整数)；num为数值；fill为可接受答案字符串列表；short省略",'
    '"unit":"num题单位(可选)","tol":"num题允许误差(可选)","model":"short题参考答案","points":["short题得分要点"],'
    '"hint":"一句提示，不直接给答案","explain":"中文讲解，2-3句"}]}'
)


def generate_items(kp: dict, pack, grade: str, n=3, purpose="practice", user_id=None) -> list[dict]:
    purpose_txt = {
        "practice": f"生成 {n} 道练习题，难度从易到难（1,2,3）。",
        "diagnose": "生成 1 道诊断题，只测这一个知识点的核心，难度 2，最好是选择题或填空题，便于自动判分。",
        "preview": f"生成 {n} 道预习用的入门题，难度都为 1，题干尽量短，并在 hint 里给出思路。",
    }[purpose]
    user = (
        f"{_audience(grade, pack)}\n知识点：{kp['name']}（{kp.get('name_en','')}）\n说明：{kp.get('desc','')}\n"
        f"诊断思路参考：{kp.get('probe','')}\n关键词：{', '.join(kp.get('terms', []))}\n\n"
        f"{purpose_txt}\n优先用 mcq/num/fill（能自动判分），short 最多 1 道。不要照搬教材原文。"
        f"答案必须正确且唯一，请自己检查一遍。\n输出格式：{ITEM_SCHEMA}"
    )
    data = ask_json("items", TUTOR + "你也是严谨的出题人。", user, user_id=user_id, effort="medium", cache=False)
    items = data.get("items", []) if isinstance(data, dict) else data
    return [i for i in items if isinstance(i, dict) and i.get("q") and i.get("type") in ("mcq", "num", "fill", "short")]


def teach(kp: dict, pack, grade: str, user_id=None) -> dict:
    user = (
        f"{_audience(grade, pack)}\n知识点：{kp['name']}（{kp.get('name_en','')}）\n说明：{kp.get('desc','')}\n"
        f"已有学习方法：{kp.get('method','')}\n\n"
        "请把这个知识点拆成 3-4 个很小的步骤教给学生，每步一两句话，配一个生活化的小例子；"
        "最后给一个「一句话记住」。如有英文术语，给出英文和中文。\n"
        '输出：{"steps":[{"title":"..","text":".."}],"example":{"q":"..","a":".."},"remember":"..","terms":[{"en":"..","zh":".."}]}'
    )
    return ask_json("teach", TUTOR, user, user_id=user_id, effort="medium")


def lookup(query: str, context: str, lang: str, grade: str, user_id=None) -> dict:
    from ..catalog import stage_label
    if lang == "en":
        want = (
            '{"word":"原形","phonetic":"音标","pos":"词性","meaning":"在这句话里的中文意思",'
            '"simple_en":"简单英文解释","other_meanings":["常见其他意思"],"example":"一个简单例句","example_zh":"例句翻译",'
            '"tip":"记忆小窍门(可选)"}'
        )
    else:
        want = (
            '{"word":"词语","pinyin":"拼音","meaning":"在这句话里的意思（用孩子听得懂的话）","other_meanings":["其他常见意思"],'
            '"near":["近义词"],"opposite":["反义词"],"example":"一个例句","tip":"记忆或书写提示(可选)"}'
        )
    user = (
        f"学生（{stage_label(grade)}）在阅读时不懂这个{'英文单词/短语' if lang == 'en' else '字词'}：「{query}」\n"
        f"所在句子：{context or '（无）'}\n请按学生水平解释，意思要贴合这句话。输出：{want}"
    )
    return ask_json("lookup", TUTOR, user, user_id=user_id, effort="low", max_tokens=1500)


def explain_sentence(sentence: str, lang: str, grade: str, user_id=None) -> dict:
    from ..catalog import stage_label
    user = (
        f"学生（{stage_label(grade)}）读不懂这句话：「{sentence}」\n"
        "请：1) 给出通顺的意思（英文句子给中文翻译）；2) 拆开句子结构，指出主干和难点；3) 点出 1-2 个值得积累的词或表达。\n"
        '输出：{"meaning":"..","structure":"..","points":[{"text":"..","note":".."}]}'
    )
    return ask_json("explain", TUTOR, user, user_id=user_id, effort="low", max_tokens=1500)


def sentence_feedback(word: str, meaning: str, sentence: str, lang: str, grade: str, user_id=None) -> dict:
    from ..catalog import stage_label
    user = (
        f"学生（{stage_label(grade)}）用「{word}」（意思：{meaning}）造了一个句子：\n{sentence}\n"
        "请判断用法是否正确、句子是否通顺。先肯定做得好的地方，再指出最重要的一个问题（如果有）。"
        "给一个改正后的句子，以及一个更好的示范句。语气鼓励，简短。\n"
        '输出：{"ok":true/false,"praise":"..","issue":"没有问题时为空","corrected":"..","better":"..","score":1-5}'
    )
    return ask_json("sentence", TUTOR, user, user_id=user_id, effort="low", max_tokens=1200, cache=False)


def make_passage(lang: str, grade: str, topic: str, length: str, review_words: list[str], user_id=None) -> dict:
    from ..catalog import stage_label
    words = f"尽量自然地用上这些正在复习的词：{', '.join(review_words[:8])}。" if review_words else ""
    if lang == "en":
        spec = f"写一篇适合{stage_label(grade)}中国学生的英文短文，用词比他的水平略高一点点（i+1），{length}。"
    else:
        spec = f"写一篇适合{stage_label(grade)}学生的中文短文，{length}，语言规范优美但不难懂。"
    user = (
        f"{spec}主题：{topic or '孩子感兴趣的日常、自然或科学话题'}。{words}不要照搬已有出版物。\n"
        "再出 3 道阅读理解选择题（考概括、细节、词义各一）。\n"
        '输出：{"title":"..","body":"正文，段落之间用\\n\\n分隔","questions":[{"q":"..","options":["..","..","..",".."],"answer":0,"explain":"中文解析"}]}'
    )
    return ask_json("passage", TUTOR + "你也是儿童读物作者。", user, user_id=user_id, effort="medium", cache=False)
