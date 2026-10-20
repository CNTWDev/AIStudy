"""Curriculum authoring is durable, isolated from learners until publication, and concurrency safe."""
import json
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import auth, curriculum_admin as manager, db, engine, evidence
from app.catalog import catalog
from app.main import app


@pytest.fixture
def admin():
    db.init()
    db.run("UPDATE curriculum_workspace SET draft='{}',version=0,published_id=NULL WHERE id=1")
    db.run('DELETE FROM curriculum_revisions')
    with TestClient(app) as client:
        email = uuid.uuid4().hex + '@example.test'
        actor = auth.create_user(email, 'secret123', '教材管理员', role='admin', is_admin=True)
        client.post('/login', data={'email': email, 'password': 'secret123'})
        yield client, actor
    db.run("UPDATE curriculum_workspace SET draft='{}',version=0,published_id=NULL WHERE id=1")
    db.run('DELETE FROM curriculum_revisions')
    manager.refresh(force=True)


def version():
    return manager.workspace()['version']


def kpform(name='新的知识说明', kid='MSH-GEO-09'):
    _, _, draft = manager.draft_view()
    kp = draft.kps[kid]
    return {**kp, 'id': kid, 'pack_id': kp['pack'], 'name': name, 'terms': '\n'.join(kp.get('terms', [])),
            'required': '\n'.join(p['id'] for p in kp['prereqs'] if p.get('strength') == '必须'),
            'helpful': '\n'.join(p['id'] for p in kp['prereqs'] if p.get('strength') == '有帮助')}


def test_navigation_and_structured_editors(admin):
    client, _ = admin
    assert '教材与知识点' in client.get('/admin').text
    for path in ['/admin?tab=curricula', '/admin/curricula?pack_id=math-sujiao', '/admin/curricula?pack_id=math-cn',
                 '/admin/curricula?q=周长', '/admin/curricula?gap=empty',
                 '/admin/curricula/edit/pack/math-sujiao', '/admin/curricula/edit/pack/math-cn', '/admin/curricula/edit/kp/MSH-GEO-09',
                 '/admin/curricula/edit/concept/math.pythagoras', '/admin/curricula/edit/edition/sz-math-primary',
                 '/admin/curricula/edit/strand/GEO?pack_id=math-cn',
                 '/admin/curricula/edit/pack/_new', '/admin/curricula/edit/edition/_new', '/admin/curricula/edit/book/_new',
                 '/admin/curricula/edit/kp/_new?pack_id=math-cn', '/admin/curricula/edit/import/_new']:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text[:400])
    page = client.get('/admin/curricula/edit/kp/MSH-GEO-09').text
    assert '22' in page and '跨教材共通知识' in page and '关联题目' in page
    assert '一年级上册' in client.get('/admin/curricula?pack_id=math-sujiao').text
    doc = manager.effective(manager.empty())
    eid = next(e['id'] for e in doc['textbooks']['editions'] if e.get('pack_id') == 'math-sujiao')
    assert client.get('/admin/curricula/edit/edition/'+eid).status_code == 200


def exported(client, scope=''):
    kps = client.get('/admin/curricula/export.json?' + ('scope=draft&' if scope else '') + 'pack_id=math-cn').json()['kps']
    return next(kp['name'] for kp in kps if kp['id'] == 'MSH-GEO-09')


def test_draft_publish_restart_and_export(admin):
    client, actor = admin
    original = catalog.kps['MSH-GEO-09']['name']
    result = client.post('/admin/curricula/save/kp', data={**kpform('周长：计算与解释'), 'version': version()})
    assert result.status_code == 200 and '草稿已保存' in result.text
    assert catalog.kps['MSH-GEO-09']['name'] == original
    assert exported(client, 'draft') == '周长：计算与解释'
    rid = manager.publish(actor, version(), '补充周长说明')
    assert catalog.kps['MSH-GEO-09']['name'] == '周长：计算与解释'
    catalog.load()  # Simulate a fresh process starting from repository files.
    assert catalog.kps['MSH-GEO-09']['name'] == original
    manager.refresh(force=True)
    assert catalog.kps['MSH-GEO-09']['name'] == '周长：计算与解释'
    assert manager.workspace()['published_id'] == rid
    assert exported(client) == '周长：计算与解释'
    assert manager.history()[0]['actor'] == '教材管理员'
    # A second Web/worker process loads the published content from the same database.
    probe = subprocess.run([sys.executable, '-c',
        "from app import curriculum_admin as m; from app.catalog import catalog; "
        "m.refresh(force=True); print(catalog.kps['MSH-GEO-09']['name'])"],
        capture_output=True, text=True, timeout=20, check=True)
    assert probe.stdout.strip() == '周长：计算与解释'


def test_optimistic_concurrency(admin):
    _, actor = admin
    expected = version()
    form = kpform()
    def save(i):
        try:
            manager.save(actor, expected, 'kp', {**form, 'name': f'管理员修改{i}'})
            return True
        except manager.Conflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(save, range(2))) == 1
    assert version() == expected + 1


def test_parent_selection_copy_is_published_with_the_curriculum(admin):
    client, actor = admin
    pid = 'math-sujiao'
    pack = manager.effective(manager.empty())['packs'][pid]['pack']
    assert '江苏' in catalog.selection_guide(catalog.packs[pid])['name']
    form = {**pack, 'stages': '\n'.join(pack['stages']), 'selection_name': '苏教数学（江苏小学·新版）',
            'selection_hint': '请核对课本版权页。', 'selection_warning': '高年级目录待核实。'}
    manager.save(actor, version(), 'pack', form)
    assert catalog.selection_guide(catalog.packs[pid])['name'] == '苏教版数学（江苏小学）'
    assert catalog.packs[pid].base == 'math-cn' and catalog.packs[pid].books   # 改名称提示不会丢掉目录
    manager.publish(actor, version(), '更新家长核对提示')
    assert catalog.selection_guide(catalog.packs[pid])['hint'] == '请核对课本版权页。'
    assert client.get('/admin/curricula/edit/pack/'+pid).status_code == 200
    before = version()
    with pytest.raises(ValueError, match='过长'):
        manager.save(actor, before, 'pack', {**form, 'selection_name': '名' * 45})
    assert version() == before


def test_invalid_cycles_and_import_removal_do_not_save(admin):
    client, actor = admin
    manager.save(actor, version(), 'kp', {**kpform('周长'), 'required': 'MSH-GEO-10'})
    before = version()
    response = client.post('/admin/curricula/save/kp', data={**kpform('面积', 'MSH-GEO-10'), 'required': 'MSH-GEO-09', 'version': before})
    assert response.status_code == 400 and '成环' in response.text
    assert version() == before
    pack = manager.effective(manager.unpack(manager.workspace()['draft']))['packs']['math-cn']
    pack['kps'].pop()
    with pytest.raises(ValueError, match='删除'):
        manager.save(actor, before, 'import', {'id': 'math-cn', 'payload': json.dumps(pack)})
    with pytest.raises(manager.Conflict):
        manager.publish(actor, before - 1, '旧页面不得发布')


def test_new_pack_kp_book_and_mapping(admin):
    client, actor = admin
    manager.save(actor, version(), 'pack', {'id': 'test-pack', 'edition': '测试教材', 'subject': 'math', 'system': 'cn', 'stages': 'G4', 'sources': 'https://example.org/catalog'})
    manager.save(actor, version(), 'strand', {'id': 'geometry', 'pack_id': 'test-pack', 'name': '几何'})
    manager.save(actor, version(), 'kp', {'id': 'TEST-KP', 'pack_id': 'test-pack', 'name': '认识图形', 'stage': 'G4', 'strand': 'geometry'})
    manager.save(actor, version(), 'book', {'id': 'test-book', 'name': '四上', 'edition_id': 'test-pack', 'stage': 'G4', 'status': 'partial', 'sources': 'https://example.org/book', 'order': '1'})
    manager.save(actor, version(), 'mapping', {'kp_id': 'TEST-KP', 'book_id': 'test-book', 'unit_id': 'shapes', 'unit_name': '第一单元 图形', 'order': '1'})
    assert 'test-pack' not in catalog.packs
    manager.publish(actor, version(), '上线测试教材')
    assert catalog.kps['TEST-KP']['name'] == '认识图形'
    chapters = catalog.chapters('test-pack', 'G4')
    assert chapters[0]['key'] == 'test-book:shapes' and chapters[0]['kps'] == ['TEST-KP']
    assert client.get('/admin/curricula?pack_id=test-pack').status_code == 200


def test_partial_textbook_does_not_hide_remaining_kps(admin):
    """目录只核实了一部分：没列出的册「目录待核实」，主干里这几年的点一个不少。"""
    _, actor = admin
    pack = catalog.packs['math-sujiao']
    for stage in ('G2', 'G4'):
        chs = catalog.chapters('math-sujiao', stage)
        assert {k for k in pack.kp_ids if catalog.stage_in('math-sujiao', k) == stage} <= {k for ch in chs for k in ch['kps']}   # 螺旋上升：前几年的点也可以再出现
        assert any(ch['status'] == 'pending' for ch in chs) and any(ch['status'] != 'pending' for ch in chs)
    manager.save(actor, version(), 'book', {'id': 'test-sz-book', 'name': '四上', 'edition_id': 'sz-math-primary', 'stage': 'G4',
                                            'status': 'partial', 'sources': 'https://example.org/book', 'order': '1'})
    with pytest.raises(ValueError, match='不匹配'):
        manager.save(actor, version(), 'mapping', {'kp_id': 'MSH-NUM-01', 'book_id': 'test-sz-book', 'unit_id': 'a', 'unit_name': 'a', 'order': '0'})


def test_concept_changes_rebuild_projection_without_deleting_events(admin):
    _, actor = admin
    kid = auth.create_user(uuid.uuid4().hex+'@example.test', 'secret123', '学习者', role='kid')
    evidence.record(kid, 'MSH-GEO-09', True, fmt='recall')
    old_key = catalog.concept_key
    manager.save(actor, version(), 'concept', {'id': 'test-concept', 'name': '精确周长', 'kps': 'MSH-GEO-09'})
    manager.publish(actor, version(), '调整知识等价关系')
    assert catalog.concept_key != old_key
    engine.get_mastery(kid)
    assert db.one('SELECT attempts FROM concept_mastery WHERE user_id=? AND concept_id=?', kid, 'test-concept')['attempts'] == 1
    assert db.one('SELECT COUNT(*) AS n FROM events WHERE user_id=?', kid)['n'] == 1
    assert catalog.concept_of['MSH-GEO-09'] == 'test-concept'


def test_restore_history_is_draft_only_and_preserves_new_ids(admin):
    _, actor = admin
    manager.save(actor, version(), 'kp', kpform('第一版周长'))
    first = manager.publish(actor, version(), '第一版')
    manager.save(actor, version(), 'kp', kpform('第二版周长'))
    manager.save(actor, version(), 'kp', {'id': 'TEST-NEW-KP', 'pack_id': 'math-cn', 'name': '新增内容', 'stage': 'G4', 'strand': 'GEO'})
    second = manager.publish(actor, version(), '第二版')
    manager.restore_draft(actor, version(), first)
    assert catalog.kps['MSH-GEO-09']['name'] == '第二版周长'
    assert manager.workspace()['published_id'] == second
    assert manager.draft_view()[2].kps['MSH-GEO-09']['name'] == '第一版周长'
    manager.publish(actor, version(), '恢复解释，保留新增知识点')
    assert 'TEST-NEW-KP' in catalog.kps


def test_retire_stale_teaching_only_on_publish(admin):
    _, actor = admin
    cid = db.insert("INSERT INTO contents(kind,kp_id,lang,grade,topic,title,body,origin,qhash,uses,created_at,updated_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", 'teach', 'MSH-GEO-09', 'zh', 'G4', '', '旧讲解', '{}', 'ai', uuid.uuid4().hex, 0, db.now(), db.now(), 'active')
    manager.save(actor, version(), 'kp', kpform('更新后的周长'))
    assert db.one('SELECT status FROM contents WHERE id=?', cid)['status'] == 'active'
    manager.publish(actor, version(), '更新说明并重新生成讲解')
    assert db.one('SELECT status FROM contents WHERE id=?', cid)['status'] == 'retired'


def test_permissions_all_mutations_and_exports(admin):
    client, _ = admin
    client.get('/logout')
    email = uuid.uuid4().hex + '@example.test'
    auth.create_user(email, 'secret123', '普通家长', role='parent')
    client.post('/login', data={'email': email, 'password': 'secret123'})
    before = version()
    for path in ['/admin/curricula', '/admin/curricula/export.json', '/admin/curricula/edit/kp/MSH-GEO-09']:
        assert client.get(path).status_code == 403
    for path in ['/admin/curricula/save/kp', '/admin/curricula/mapping', '/admin/curricula/publish', '/admin/curricula/restore']:
        assert client.post(path, data={'version': before}).status_code == 403
    assert version() == before
