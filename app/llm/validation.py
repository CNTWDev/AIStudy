"""Task contracts at the model boundary. Invalid answers never enter the shared cache."""
import math
from .base import LLMError

REQUIRED = {
    'items': {'items': list}, 'variant': {'items': list}, 'paper': {'questions': list},
    'teach': {'steps': list}, 'lookup': {'meaning': str}, 'explain': {'meaning': str},
    'sentence': {'ok': bool}, 'passage': {'body': str, 'questions': list},
    'context': {'story': str}, 'verify': {'ok': bool}, 'ask': {'reply': str},
    'book_guide': {'gist': str}, 'news_write': {'body': str, 'questions': list},
    'review': {'verdict': str, 'points': list},
}


def validate(task, data):
    if not isinstance(data, dict):
        raise LLMError('AI 返回结构不正确，请重试')
    for key, kind in REQUIRED.get(task, {}).items():
        if not isinstance(data.get(key), kind):
            raise LLMError('AI 返回内容不完整，请重试')
    if task in ('items', 'variant'):
        from .. import itemtypes
        if not data['items'] or len(data['items']) > 30:
            raise LLMError('AI 返回题目数量不正确，请重试')
        for item in data['items']:
            if not isinstance(item, dict) or not isinstance(item.get('q'), str) or not item['q'].strip():
                raise LLMError('AI 题干不完整，请重试')
            kind = item.get('type')
            valid = kind in itemtypes.TYPES
            if kind == 'mcq':
                opts, answer = item.get('options'), item.get('answer')
                valid = (isinstance(opts, list) and 2 <= len(opts) <= 8 and all(isinstance(x, str) and x.strip() for x in opts)
                         and len(set(opts)) == len(opts) and type(answer) is int and 0 <= answer < len(opts))
            elif kind == 'num':
                try:
                    valid = not isinstance(item.get('answer'), bool) and math.isfinite(float(item['answer']))
                except (KeyError, ValueError, TypeError):
                    valid = False
            elif kind == 'fill':
                answer = item.get('answer')
                valid = (isinstance(answer, str) and bool(answer.strip())) or (
                    isinstance(answer, list) and bool(answer) and all(isinstance(x, str) and x.strip() for x in answer))
            elif kind == 'short':
                valid = isinstance(item.get('model'), str) and bool(item['model'].strip())
            if not valid:
                raise LLMError('AI 题目或答案格式不正确，已暂停使用，请重试')
    return data
