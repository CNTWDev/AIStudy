"""Textbook identity and chapter mappings, separate from shared concepts and learner state.

Files are reviewed data. A usage source verifies an edition, not every chapter or ISBN.
Unverified editions remain in the inventory and never become selectable school defaults.
"""
import json
from pathlib import Path

STATES = {'verified', 'partial', 'pending', 'legacy'}


class Textbooks:
    def __init__(self):
        self.editions, self.books, self.mappings, self.errors = {}, {}, [], []

    def load(self, directory: Path, catalog):
        self.__init__()
        path = directory / '_meta' / 'textbooks.json'
        if not path.exists():
            return self
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            self.errors.append(f'textbooks: 无法读取：{type(exc).__name__}')
            return self
        if not isinstance(data, dict) or any(not isinstance(data.get(k, []), list) for k in ('editions', 'books', 'mappings')):
            self.errors.append('textbooks: 数据结构不正确')
            return self
        if data.get('schema_version') != 1:
            self.errors.append('textbooks: 不支持的数据版本')
            return self
        for kind, target in [('editions', self.editions), ('books', self.books)]:
            for entry in data.get(kind, []):
                if not isinstance(entry, dict):
                    self.errors.append(f'textbooks: {kind} 条目必须是对象')
                    continue
                key = entry.get('id')
                if not isinstance(key, str) or not key or key in target:
                    self.errors.append(f'textbooks: {kind} ID 缺失或重复：{key}')
                    continue
                target[key] = entry
                if entry.get('status') not in STATES:
                    self.errors.append(f'textbooks: {key} 核实状态不合法')
                if entry.get('status') in ('verified', 'partial') and not entry.get('sources'):
                    self.errors.append(f'textbooks: {key} 缺少核实来源')
        for eid, edition in self.editions.items():
            pid = edition.get('pack_id')
            if pid and pid not in catalog.packs:
                self.errors.append(f'textbooks: {eid} 引用不存在的教材包 {pid}')
            if edition.get('status') == 'pending' and pid:
                self.errors.append(f'textbooks: 待核实版本 {eid} 不应关联已上线教材包')
        for bid, book in self.books.items():
            if book.get('edition_id') not in self.editions:
                self.errors.append(f'textbooks: {bid} 教材版本不存在')
        seen = set()
        for mapping in data.get('mappings', []):
            if not isinstance(mapping, dict):
                self.errors.append('textbooks: 映射必须是对象')
                continue
            bid, kid = mapping.get('book_id'), mapping.get('kp_id')
            if bid not in self.books or kid not in catalog.kps:
                self.errors.append(f'textbooks: 册次或知识点不存在：{bid}/{kid}')
                continue
            edition = self.editions.get(self.books[bid].get('edition_id'), {})
            if edition.get('pack_id') != catalog.kp_pack[kid] or self.books[bid].get('stage') != catalog.kps[kid]['stage']:
                self.errors.append(f'textbooks: 册次与知识点的教材/年级不匹配：{bid}/{kid}')
                continue
            key = (bid, kid)
            if key in seen or not isinstance(mapping.get('order'), int) or not mapping.get('unit_id'):
                self.errors.append(f'textbooks: 重复映射或单元顺序缺失：{bid}/{kid}')
                continue
            seen.add(key)
            self.mappings.append(mapping)
        return self

    def chapters(self, pack_id, stage, allowed):
        editions = {e['id'] for e in self.editions.values() if e.get('pack_id') == pack_id}
        books = {b['id']: b for b in self.books.values() if b.get('edition_id') in editions and b.get('stage') == stage}
        out = {}
        for m in sorted(self.mappings, key=lambda m: (books.get(m['book_id'], {}).get('order', 0), m['order'])):
            if m['book_id'] not in books or m['kp_id'] not in allowed:
                continue
            key = m['book_id'] + ':' + m['unit_id']
            chapter = out.setdefault(key, {'key': key, 'name': m.get('unit_name', m['unit_id']), 'kps': [],
                                           'book_id': m['book_id'], 'status': books[m['book_id']]['status']})
            chapter['kps'].append(m['kp_id'])
        return list(out.values())

    def inventory(self, catalog):
        return [{**e, 'kp_count': len(catalog.packs[e['pack_id']].kp_ids) if e.get('pack_id') in catalog.packs else 0,
                 'mapped_count': len({m['kp_id'] for m in self.mappings if self.books[m['book_id']]['edition_id'] == eid})}
                for eid, e in self.editions.items()]
