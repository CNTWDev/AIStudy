"""Versioned curriculum overrides: draft, validate, publish, and restore without editing Git files."""
import copy
import json
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import config, db
from .catalog import Catalog, catalog, SUBJECTS, SYSTEMS, STAGE_RANK

_LOCK = threading.Lock()
_loaded = None
_checked = 0.0
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}')


class Conflict(ValueError):
    pass


def empty():
    return {key: {} for key in ('packs', 'editions', 'books', 'mappings', 'concepts')}


def unpack(value):
    data = db.jload(value, {})
    return {**empty(), **data}


def workspace():
    return dict(db.one('SELECT * FROM curriculum_workspace WHERE id=1'))


def baseline():
    packs = {}
    for path in sorted(config.CURRICULA_DIR.glob('*.json')):
        doc = json.loads(path.read_text(encoding='utf-8'))
        packs[doc['pack']['id']] = doc
    meta = config.CURRICULA_DIR / '_meta' / 'textbooks.json'
    textbooks = json.loads(meta.read_text(encoding='utf-8')) if meta.exists() else {'schema_version': 1, 'editions': [], 'books': [], 'mappings': []}
    concepts, links = {}, []
    for path in sorted((config.CURRICULA_DIR / '_links').glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        for entry in data.get('concepts', []):
            entry = copy.deepcopy(entry)
            if entry['id'] in concepts:
                entry['kps'] = list(dict.fromkeys(concepts[entry['id']]['kps'] + entry['kps']))
            concepts[entry['id']] = entry
        links.extend(data.get('links', []))
    return {'schema_version': 1, 'packs': packs, 'textbooks': textbooks, 'concepts': concepts, 'links': links}


def effective(overrides):
    doc = baseline()
    doc['packs'].update(copy.deepcopy(overrides['packs']))
    meta = doc['textbooks']
    for key in ('editions', 'books'):
        entries = {e['id']: e for e in meta.get(key, [])}
        entries.update(copy.deepcopy(overrides[key]))
        meta[key] = list(entries.values())
    meta['mappings'] = [m for m in meta.get('mappings', []) if m['kp_id'] not in overrides['mappings']]
    meta['mappings'] += [m for entries in overrides['mappings'].values() for m in entries]
    # A concept is overridden by ID; moving a KP requires editing both concepts explicitly.
    doc['concepts'].update(copy.deepcopy(overrides['concepts']))
    return doc


def validate_document(doc):
    """Use the exact runtime loader in an isolated directory; never mutate the live catalog."""
    try:
        with tempfile.TemporaryDirectory(prefix='aistudy-curricula-') as tmp:
            root = Path(tmp)
            (root / '_meta').mkdir()
            (root / '_links').mkdir()
            for path in (config.CURRICULA_DIR / '_meta').glob('*.json'):
                if path.name != 'textbooks.json':
                    (root / '_meta' / path.name).write_bytes(path.read_bytes())
            for index, pack in enumerate(doc['packs'].values()):
                (root / f'pack-{index}.json').write_text(db.jdump(pack), encoding='utf-8')
            (root / '_meta' / 'textbooks.json').write_text(db.jdump(doc['textbooks']), encoding='utf-8')
            (root / '_links' / 'all.json').write_text(db.jdump({'concepts': list(doc['concepts'].values()), 'links': doc['links']}), encoding='utf-8')
            result = Catalog().load(root, register_meta=False)
        errors = list(result.errors)
        for pid, pack in doc['packs'].items():
            selection = pack['pack'].get('selection', {})
            if not isinstance(selection, dict) or any(not isinstance(selection.get(k, ''), str) or len(selection.get(k, '')) > n for k, n in (('name', 44), ('hint', 240), ('warning', 240))):
                errors.append(f'{pid}: 家长选课名称或提示格式不正确 / 过长')
            if pack['pack']['id'] != pid:
                errors.append('教材 ID 与数据键不一致')
            strands = [s['id'] for s in pack.get('strands', [])]
            if len(strands) != len(set(strands)):
                errors.append(f'{pid}: 板块 ID 重复')
            if not result.packs[pid].stages:
                errors.append(f'{pid}: 请先设置学段或添加知识点')
            for kp in pack['kps']:
                if kp['stage'] not in result.packs[pid].stages:
                    errors.append(f"{kp['id']}: 学段不在教材范围内")
                if kp['strand'] not in strands:
                    errors.append(f"{kp['id']}: 板块不存在")
                for pre in kp.get('prereqs', []):
                    if isinstance(pre, dict) and pre.get('strength', '必须') not in ('必须', '有帮助'):
                        errors.append(f"{kp['id']}: 前置关系强度不正确")
        for book in doc['textbooks']['books']:
            if not isinstance(book.get('order', 0), int):
                errors.append(f"{book['id']}: 册次顺序必须是整数")
            edition = result.textbooks.editions.get(book.get('edition_id'), {})
            pack = result.packs.get(edition.get('pack_id'))
            if book.get('stage') not in STAGE_RANK or pack and book['stage'] not in pack.stages:
                errors.append(f"{book['id']}: 册次学段不存在或不在教材范围内")
        if errors:
            raise ValueError('；'.join(errors[:12]))
        return result
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError('教材结构不完整，请检查字段与引用') from exc


def draft_view():
    state = workspace()
    doc = effective(unpack(state['draft']))
    return state, doc, validate_document(doc)


def identifier(value):
    value = str(value or '').strip()
    if not ID.fullmatch(value):
        raise ValueError('ID 只能用字母、数字、点、横线或下划线，最多 100 字符')
    return value


def text(form, key, required=False, limit=12000):
    value = str(form.get(key) or '').strip()
    if required and not value:
        raise ValueError(f'请填写 {key}')
    if len(value) > limit:
        raise ValueError(f'{key} 内容过长')
    return value


def lines(value):
    return list(dict.fromkeys(x.strip() for x in str(value or '').splitlines() if x.strip()))


def sources(value):
    values = lines(value)
    if any(urlparse(x).scheme not in ('http', 'https') or not urlparse(x).netloc for x in values):
        raise ValueError('核实来源请填写完整 http/https 链接，每行一个')
    return values


def _version(state, expected):
    if state['version'] != expected:
        raise Conflict('其他管理员已更新草稿，请刷新页面后重新操作；本次未覆盖对方修改')


def _lock_state(tx):
    return tx.one('SELECT * FROM curriculum_workspace WHERE id=1' + (' FOR UPDATE' if db.IS_PG else ''))


def save(actor_id, expected, action, form):
    state = workspace()
    _version(state, expected)
    overrides = unpack(state['draft'])
    doc = effective(overrides)
    apply_edit(overrides, doc, action, form)
    validate_document(effective(overrides))
    with db.tx(write=True) as tx:
        _version(_lock_state(tx), expected)
        tx.run('UPDATE curriculum_workspace SET draft=?,version=version+1,actor_id=?,updated_at=? WHERE id=1',
               db.jdump(overrides), actor_id, db.now())
        from .auth import log_event
        log_event('curriculum.draft.' + action, user_id=actor_id, detail=str(form.get('id', '')))
    return expected + 1


def apply_edit(overrides, doc, action, form):
    key = identifier(form.get('id')) if action != 'mapping' else identifier(form.get('kp_id'))
    if action == 'pack':
        old = doc['packs'].get(key)
        pack = copy.deepcopy(old or {'pack': {'id': key}, 'strands': [], 'kps': []})
        meta = pack['pack']
        subject, system = text(form, 'subject', True), text(form, 'system', True)
        if subject not in SUBJECTS or system not in SYSTEMS:
            raise ValueError('请选择有效学科和学制')
        if old and (subject != meta['subject'] or system != meta['system']):
            raise ValueError('已有教材的学科和学制不能修改；新版本请新建教材，保留历史学习记录')
        meta.update(subject=subject, system=system, edition=text(form, 'edition', True, 200), region=text(form, 'region', limit=200), notes=text(form, 'notes'))
        # Older clients can edit a pack without erasing its parent-facing copy.
        if any('selection_' + k in form for k in ('name', 'hint', 'warning')):
            meta['selection'] = {k: text(form, 'selection_' + k, limit=n) for k, n in (('name', 44), ('hint', 240), ('warning', 240))}
        meta['stages'] = lines(form.get('stages'))
        if not meta['stages'] or any(stage not in STAGE_RANK for stage in meta['stages']):
            raise ValueError('请选择至少一个有效学段')
        if not old:
            sid = 'core'
            pack['strands'] = [{'id': sid, 'name': '基础'}]
            src = sources(form.get('sources'))
            if not src:
                raise ValueError('新教材请至少填写一个核实来源；自定课程可填写课程介绍链接')
            overrides['editions'][key] = {'id': key, 'pack_id': key, 'name': meta['edition'], 'region': meta['region'], 'school_system': system, 'status': 'partial', 'sources': src, 'coverage': '新建教材，待补齐目录与练习'}
        overrides['packs'][key] = pack
    elif action == 'kp':
        pid = text(form, 'pack_id', True)
        if pid not in doc['packs']:
            raise ValueError('教材不存在')
        pack = copy.deepcopy(doc['packs'][pid])
        old_kp = next((kp for p in doc['packs'].values() for kp in p['kps'] if kp['id'] == key), None)
        if old_kp and not any(kp['id'] == key for kp in pack['kps']):
            raise ValueError('知识点 ID 已用于其他教材，不能移动或覆盖')
        kp = copy.deepcopy(old_kp or {'id': key})
        kp.update({name: text(form, name, name in ('name', 'stage', 'strand'), 12000 if name in ('desc', 'method', 'probe') else 300) for name in ('name', 'name_en', 'stage', 'strand', 'term', 'unit', 'desc', 'method', 'probe')})
        if kp['stage'] not in pack['pack']['stages']:
            raise ValueError('知识点学段不在教材的学段范围内')
        kp['terms'] = lines(form.get('terms'))
        kp['prereqs'] = [{'id': identifier(k), 'strength': '必须'} for k in lines(form.get('required'))] + [{'id': identifier(k), 'strength': '有帮助'} for k in lines(form.get('helpful'))]
        if key in {x['id'] for x in kp['prereqs']}:
            raise ValueError('知识点不能把自己作为前置')
        pack['kps'] = [kp if k['id'] == key else k for k in pack['kps']] if old_kp else pack['kps'] + [kp]
        overrides['packs'][pid] = pack
    elif action == 'edition':
        entry = copy.deepcopy(next((e for e in doc['textbooks']['editions'] if e['id'] == key), {'id': key}))
        entry.update({name: text(form, name, name == 'name', 500) for name in ('name', 'publisher', 'region', 'school_system', 'grades', 'coverage', 'next_action', 'pack_id', 'status')})
        entry['sources'] = sources(form.get('sources'))
        overrides['editions'][key] = entry
    elif action == 'book':
        entry = copy.deepcopy(next((b for b in doc['textbooks']['books'] if b['id'] == key), {'id': key}))
        entry.update({name: text(form, name, name in ('name', 'edition_id', 'stage', 'status'), 500) for name in ('name', 'edition_id', 'stage', 'term', 'isbn', 'status')})
        entry['order'] = int(form.get('order') or 0)
        entry['sources'] = sources(form.get('sources'))
        if entry['stage'] not in STAGE_RANK:
            raise ValueError('册次学段不存在')
        overrides['books'][key] = entry
    elif action == 'mapping':
        if not any(k['id'] == key for p in doc['packs'].values() for k in p['kps']):
            raise ValueError('知识点不存在')
        entries = []
        if form.get('book_id'):
            bid = identifier(form['book_id'])
            entries = [m for m in doc['textbooks']['mappings'] if m['kp_id'] == key and m['book_id'] != bid]
            if form.get('remove') != '1':
                entries.append({'book_id': bid, 'kp_id': key, 'unit_id': identifier(form.get('unit_id')), 'unit_name': text(form, 'unit_name', True, 300), 'order': int(form.get('order') or 0)})
        for row in lines(form.get('rows')):
            cells = [x.strip() for x in row.split('|')]
            if len(cells) != 4:
                raise ValueError('单元映射每行格式：册次ID | 单元ID | 单元名称 | 顺序')
            bid, unit, name, order = cells
            entries.append({'book_id': identifier(bid), 'kp_id': key, 'unit_id': identifier(unit), 'unit_name': name or unit, 'order': int(order)})
        overrides['mappings'][key] = entries
    elif action == 'concept':
        members = lines(form.get('kps'))
        for cid, concept in doc['concepts'].items():
            if cid != key and any(k in members for k in concept['kps']):
                overrides['concepts'][cid] = {**concept, 'kps': [k for k in concept['kps'] if k not in members]}
        overrides['concepts'][key] = {'id': key, 'name': text(form, 'name', True, 200), 'kps': [identifier(k) for k in members]}
    elif action == 'strand':
        pid = text(form, 'pack_id', True)
        pack = copy.deepcopy(doc['packs'].get(pid))
        if not pack:
            raise ValueError('教材不存在')
        entry = {'id': key, 'name': text(form, 'name', True, 200)}
        pack['strands'] = [entry if s['id'] == key else s for s in pack['strands']] if any(s['id'] == key for s in pack['strands']) else pack['strands'] + [entry]
        overrides['packs'][pid] = pack
    elif action == 'import':
        try:
            pack = json.loads(text(form, 'payload', True, 2_000_000))
            if pack['pack']['id'] != key:
                raise ValueError('导入教材 ID 与填写 ID 不一致')
            old = doc['packs'].get(key)
            if old and (old['pack']['subject'] != pack['pack']['subject'] or old['pack']['system'] != pack['pack']['system'] or not {k['id'] for k in old['kps']} <= {k['id'] for k in pack['kps']}):
                raise ValueError('导入不能改变已有教材的学科/学制，也不能删除已有知识点 ID')
            overrides['packs'][key] = pack
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError('请导入包含 pack、strands、kps 的教材 JSON') from exc
    else:
        raise ValueError('不支持的管理操作')


def publish(actor_id, expected, note):
    state = workspace()
    _version(state, expected)
    if not note.strip() or len(note) > 1000:
        raise ValueError('请填写发布说明（最多 1000 字符）')
    data = unpack(state['draft'])
    candidate = validate_document(effective(data))
    # Changed knowledge explanations should regenerate teaching/context, while retaining exercises.
    live = db.one('SELECT payload FROM curriculum_revisions WHERE id=?', state['published_id']) if state['published_id'] else None
    before = validate_document(effective(unpack(live['payload']) if live else empty())).kps
    changed = [kid for kid, kp in candidate.kps.items() if kid in before and any(kp.get(k) != before[kid].get(k) for k in ('name', 'name_en', 'desc', 'method', 'probe', 'terms', 'prereqs'))]
    with db.tx(write=True) as tx:
        _version(_lock_state(tx), expected)
        rid = tx.insert('INSERT INTO curriculum_revisions(payload,note,actor_id,created_at) VALUES(?,?,?,?)', db.jdump(data), note.strip(), actor_id, db.now())
        tx.run('UPDATE curriculum_workspace SET published_id=?,version=version+1,actor_id=?,updated_at=? WHERE id=1', rid, actor_id, db.now())
        for kid in changed:
            tx.run("UPDATE contents SET status='retired' WHERE kp_id=? AND kind IN ('teach','context') AND status='active'", kid)
    refresh(force=True)
    return rid


def restore_draft(actor_id, expected, revision):
    state = workspace()
    _version(state, expected)
    if revision == 0:
        row = db.one('SELECT payload FROM curriculum_revisions WHERE id=?', state['published_id']) if state['published_id'] else None
        data = unpack(row['payload']) if row else empty()
    else:
        row = db.one('SELECT payload FROM curriculum_revisions WHERE id=?', revision)
        if not row:
            raise ValueError('历史版本不存在')
        data = unpack(row['payload'])
        # Retain newly added IDs; reverting an explanation must not orphan learning history.
        live = effective(unpack(db.one('SELECT payload FROM curriculum_revisions WHERE id=?', state['published_id'])['payload'])) if state['published_id'] else baseline()
        target = effective(data)
        for pid, pack in live['packs'].items():
            if pid not in target['packs']:
                data['packs'][pid] = pack
            else:
                missing = [kp for kp in pack['kps'] if kp['id'] not in {k['id'] for k in target['packs'][pid]['kps']}]
                if missing:
                    copy_pack = copy.deepcopy(target['packs'][pid])
                    copy_pack['kps'] += missing
                    for strand in pack['strands']:
                        if strand['id'] not in {s['id'] for s in copy_pack['strands']}:
                            copy_pack['strands'].append(strand)
                    copy_pack['pack']['stages'] = list(dict.fromkeys(copy_pack['pack']['stages'] + pack['pack']['stages']))
                    data['packs'][pid] = copy_pack
        validate_document(effective(data))
    with db.tx(write=True) as tx:
        _version(_lock_state(tx), expected)
        tx.run('UPDATE curriculum_workspace SET draft=?,version=version+1,actor_id=?,updated_at=? WHERE id=1', db.jdump(data), actor_id, db.now())
        from .auth import log_event
        log_event('curriculum.restore_draft', user_id=actor_id, detail=str(revision))


def refresh(force=False):
    """Poll published revision in each Web/worker process; swap a complete catalog atomically."""
    global _loaded, _checked
    now = time.monotonic()
    if not force and now - _checked < 1:
        return
    with _LOCK:
        if not catalog.packs:
            catalog.load()
        if not force and now - _checked < 1:
            return
        row = db.one('SELECT published_id FROM curriculum_workspace WHERE id=1')
        revision = row['published_id'] if row else None
        if force or revision != _loaded:
            overrides = unpack(db.one('SELECT payload FROM curriculum_revisions WHERE id=?', revision)['payload']) if revision else empty()
            candidate = validate_document(effective(overrides))
            catalog.__dict__ = candidate.__dict__
            _loaded = revision
        _checked = now


def history():
    return db.q('SELECT r.id,r.note,r.created_at,u.name AS actor FROM curriculum_revisions r LEFT JOIN users u ON u.id=r.actor_id ORDER BY r.id DESC LIMIT 30')


def coverage():
    return {r['kp_id']: dict(r) for r in db.q("SELECT x.kp_id,COUNT(*) AS total,SUM(CASE WHEN i.status='active' THEN 1 ELSE 0 END) AS active, SUM(CASE WHEN i.status='active' AND (i.verified=1 OR i.source IN ('seed','bank')) THEN 1 ELSE 0 END) AS checked FROM item_kps x JOIN items i ON i.id=x.item_id GROUP BY x.kp_id")}
