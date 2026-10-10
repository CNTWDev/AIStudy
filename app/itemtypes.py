"""题型注册表：一种题型的所有规则都在这里——怎么判对错、怎么显示答案、证据算哪种题型、
蒙对的概率、AI 出的题怎么整理、前端用哪种输入控件。

加一种题型：写一个 ItemType 子类，登记到 TYPES；前端只认 widget（choice / text / self），
新的输入方式才需要在 app/static/app.js 的 renderItem 里加一个分支。
题目都可以带 code 字段（一段代码，前端按代码块显示），编程课的读代码、填代码题用现有题型就行。
"""


def _num(v):
    try:
        return float(str(v).replace(",", "").strip().rstrip("分").split()[0])
    except (TypeError, ValueError, IndexError):
        return None


class ItemType:
    id = ""
    label = ""
    fmt = "other"       # 交叉验证时算哪种题型：choice 选择 / recall 回忆 / calc 计算 / explain 表述
    widget = "text"     # 前端输入控件
    self_rated = False  # 孩子对照参考答案自己判
    schema = ""         # 给 AI 出题时的格式说明

    def check(self, item: dict, answer) -> bool | None:
        raise NotImplementedError

    def display(self, item: dict) -> str:
        a = item.get("answer")
        if isinstance(a, list):
            return " / ".join(map(str, a))
        return f"{a} {item.get('unit', '')}".strip()

    def guess(self, item: dict) -> float:
        """不会也能答对的概率（BKT 的 guess）。"""
        return 0.05

    def normalize(self, it: dict, raw: dict) -> dict | None:
        """AI 给的题整理成题库格式；答案不合格返回 None（调用方会退化成自评题）。"""
        return it


class Choice(ItemType):
    id, label, fmt, widget = "mcq", "选择题", "choice", "choice"
    schema = "mcq：options 为 4 个选项，answer 为正确选项下标（整数）"

    def check(self, item, answer):
        try:
            return int(answer) == int(item["answer"])
        except (TypeError, ValueError):
            return False

    def display(self, item):
        try:
            return f"{'ABCDEFGH'[int(item['answer'])]}. {item['options'][int(item['answer'])]}"
        except (TypeError, ValueError, IndexError, KeyError):
            return str(item.get("answer"))

    def guess(self, item):
        return 1 / max(2, len(item.get("options") or []) or 4)

    def normalize(self, it, raw):
        try:
            a = int(raw.get("answer"))
        except (TypeError, ValueError):
            return None
        if not (isinstance(raw.get("options"), list) and 0 <= a < len(raw["options"])):
            return None
        return {**it, "answer": a}


class Fill(ItemType):
    id, label, fmt = "fill", "填空题", "recall"
    schema = "fill：answer 为可接受答案的字符串列表"

    @staticmethod
    def _norm(s) -> str:
        return "".join(str(s).lower().split()).strip("。.!！")

    def check(self, item, answer):
        accepted = item["answer"] if isinstance(item["answer"], list) else [item["answer"]]
        return self._norm(answer) in {self._norm(a) for a in accepted}

    def normalize(self, it, raw):
        a = raw.get("answer")
        if isinstance(a, str) and a.strip():
            return {**it, "answer": [a.strip()]}
        return {**it, "answer": a} if isinstance(a, list) and a else None


class Number(ItemType):
    id, label, fmt = "num", "计算题", "calc"
    schema = "num：answer 为数值，可带 unit（单位）和 tol（允许误差）"

    def check(self, item, answer):
        val, target = _num(answer), _num(item.get("answer"))
        if val is None or target is None:
            return False
        tol = float(item.get("tol") or 0) or max(abs(target) * 0.01, 1e-9)
        return abs(val - target) <= tol + 1e-12

    def normalize(self, it, raw):
        return it if _num(raw.get("answer")) is not None else None


class Short(ItemType):
    id, label, fmt, widget, self_rated = "short", "简答题", "explain", "self", True
    schema = "short：不给 answer，给 model（参考答案）和 points（得分要点列表）"

    def check(self, item, answer):
        return None  # 孩子对照参考答案自评

    def display(self, item):
        return item.get("model", "")

    def guess(self, item):
        return 0.15  # 自己判的，有一定「以为会了」的可能

    def normalize(self, it, raw):
        if not it.get("model"):
            ans = raw.get("answer")
            if raw.get("type") == "mcq" and isinstance(raw.get("options"), list):
                ans = "；".join(raw["options"]) + f"（参考：{ans}）" if ans not in (None, "") else ""
            it["model"] = str(ans or "（AI 没有给出参考答案，请对照老师的讲评）")
        it.pop("answer", None)
        if not isinstance(it.get("points"), list):
            it.pop("points", None)
        return it


TYPES: dict[str, ItemType] = {t.id: t for t in (Choice(), Fill(), Number(), Short())}
FALLBACK = TYPES["short"]
FIELDS = ("q", "zh", "code", "options", "answer", "unit", "tol", "model", "points", "explain", "hint")


def of(item: dict | str) -> ItemType:
    return TYPES.get(item if isinstance(item, str) else (item or {}).get("type"), FALLBACK)


def fmt(type_id: str | None) -> str:
    return TYPES[type_id].fmt if type_id in TYPES else "other"


def check(item: dict, answer) -> bool | None:
    t = of(item)
    if not t.self_rated and (answer is None or str(answer).strip() == ""):
        return False
    return t.check(item, answer)


def display(item: dict) -> str:
    return of(item).display(item)


def normalize(raw: dict) -> dict:
    """AI 给的一道题 → 题库格式。答案不合格的退化成自评的简答题（不丢题）。"""
    it = {k: raw.get(k) for k in FIELDS if raw.get(k) not in (None, "", [])}
    t = TYPES.get(raw.get("type"))
    out = t.normalize(dict(it), raw) if t else None
    if out is None:
        t, out = FALLBACK, FALLBACK.normalize(dict(it), raw)
    out["type"] = t.id
    return out


def public(item: dict) -> dict:
    """给前端的：加上 widget，去掉答案。"""
    t = of(item)
    return {**{k: v for k, v in item.items() if k not in ("answer", "model", "points", "explain")},
            "widget": t.widget, "self_rated": t.self_rated}


def reveal(item: dict) -> dict:
    """作答以后给前端的「带答案」部分：答案、选择题的正确选项下标、解析、要点。各处判完题都用它，显示才一致。"""
    out = {"answer": display(item), "explain": item.get("explain", ""), "points": item.get("points") or []}
    if of(item).widget == "choice":
        try:
            out["answer_index"] = int(item.get("answer"))
        except (TypeError, ValueError):
            pass
    return out


def view(item: dict, mine=None, with_answer=True) -> dict:
    """一道题的统一展示结构（页面上所有显示题目的地方都用它，见 _ui.html 的 question 和 app.js 的 qView）：
    题干（q / zh / code / src）、选项（A B C… 带「正确」「我选的」标记）、答案、我的答案、解析、要点、提示。
    with_answer=False 是「不带答案」的版本（去掉答案、解析和对错标记），用来出题。"""
    t = of(item)
    ans_i = reveal(item).get("answer_index") if with_answer else None
    mine_i = None
    if t.widget == "choice" and mine not in (None, "") and str(mine).strip().lstrip("-").isdigit():
        mine_i = int(str(mine).strip())
    opts = [{"key": "ABCDEFGH"[i] if i < 8 else str(i + 1), "text": str(o), "right": with_answer and i == ans_i,
             "mine": with_answer and i == mine_i} for i, o in enumerate(item.get("options") or [])]
    v = {"type": t.id, "label": t.label, "q": item.get("q", ""), "zh": item.get("zh", ""), "code": item.get("code", ""),
         "src": item.get("src", ""), "unit": item.get("unit", ""), "options": opts, "hint": item.get("hint", "")}
    if with_answer:
        r = reveal(item)
        mine_text = ""
        if mine not in (None, ""):
            mine_text = (f"{opts[mine_i]['key']}. {opts[mine_i]['text']}" if mine_i is not None and 0 <= mine_i < len(opts)
                         else str(mine))
        v.update(answer=r["answer"], explain=r["explain"], points=r["points"], mine=mine_text,
                 mine_right=bool(mine_text) and mine_i is not None and mine_i == ans_i)
    return v


def schema_text() -> str:
    return "；".join(t.schema for t in TYPES.values())
