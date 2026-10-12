"""Cross-database regression tests for the concept and learning command boundary."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth, db, engine, evidence, learning
from app.catalog import Catalog, catalog
from app.main import app
from app.methods.mastery import BKT
from app.methods.memory import FSRS


@pytest.fixture
def learner():
    with TestClient(app):
        yield auth.create_user(f'{uuid.uuid4().hex}@test.example', 'secret123', '测试孩子', role='kid')


def command():
    row = db.one("SELECT * FROM items WHERE kp_id='MATH-PRE-UNIT' AND type='num' LIMIT 1")
    if row is None:
        row = db.one("SELECT * FROM items WHERE type='num' AND source='seed' LIMIT 1")
    it = engine._item_row_to_dict(row)
    return {'request_id': uuid.uuid4().hex, 'item_id': it['id'], 'kp_id': it['kp_id'], 'answer': it['answer']}


def test_answer_duplicate_and_conflict(learner):
    body = command()
    first = learning.submit(learner, body)
    assert learning.submit(learner, {**body, 'ms': 6000}) == first
    assert db.one('SELECT COUNT(*) AS n FROM attempts WHERE user_id=?', learner)['n'] == 1
    with pytest.raises(HTTPException) as exc:
        learning.submit(learner, {**body, 'answer': 'different'})
    assert exc.value.status_code == 409


def test_concurrent_duplicate_is_one_attempt(learner):
    body = command()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: learning.submit(learner, body), range(4)))
    assert all(x == results[0] for x in results)
    assert db.one('SELECT COUNT(*) AS n FROM events WHERE user_id=?', learner)['n'] >= 1
    assert db.one('SELECT COUNT(*) AS n FROM attempts WHERE user_id=?', learner)['n'] == 1


def test_answer_failure_rolls_back_all_projections(learner, monkeypatch):
    body = command()
    def fail(*args):
        raise RuntimeError('simulated projection failure')
    monkeypatch.setattr(evidence, '_apply_concept', fail)
    with pytest.raises(RuntimeError):
        learning.submit(learner, body)
    for table in ('attempts', 'events', 'mastery', 'concept_mastery', 'answer_receipts'):
        assert db.one(f'SELECT COUNT(*) AS n FROM {table} WHERE user_id=?', learner)['n'] == 0


def test_concept_transfer_requires_new_evidence_and_replays(learner):
    evidence.record(learner, 'MSH-GEO-09', True, mode='practice', fmt='recall')
    target = engine.get_mastery(learner)['SZ-M4-PERIMETER']
    assert target['attempts'] == 0 and target['status'] == 'learning' and target['score'] <= .65
    assert not db.one('SELECT * FROM mastery WHERE user_id=? AND kp_id=?', learner, 'SZ-M4-PERIMETER')
    evidence.record(learner, 'SZ-M4-PERIMETER', False, mode='practice', fmt='recall')
    before = db.one('SELECT * FROM concept_mastery WHERE user_id=? AND concept_id=?', learner, catalog.concept_id('MSH-GEO-09'))
    evidence.replay(learner)
    after = db.one('SELECT * FROM concept_mastery WHERE user_id=? AND concept_id=?', learner, before['concept_id'])
    assert after['attempts'] == 2 and after['score'] == pytest.approx(before['score'])
    assert after['evidence'] == before['evidence']


def test_zero_weight_and_self_ratings_cannot_prove_mastery():
    model = BKT(FSRS())
    event = {'kind': 'answer', 'correct': True, 'at': '2026-01-01', 'fmt': 'recall', 'item_id': 'a'}
    state = model.step(None, event)
    unchanged = model.step(state, {**event, 'at': '2026-01-10', 'weight': 0})
    assert unchanged == state
    for day in range(2, 12):
        state = model.step(state, {**event, 'at': f'2026-01-{day:02}', 'item_id': str(day), 'mode': 'review-self', 'weight': .3})
    assert state['status'] != 'mastered' and len(set(state['ev']['days'])) == 1
    assert FSRS().interval(100000) <= 180


def test_nested_transaction_rollback(learner):
    with pytest.raises(RuntimeError):
        with db.tx(write=True):
            db.run('UPDATE users SET name=? WHERE id=?', 'must rollback', learner)
            with db.tx():
                db.run('UPDATE users SET school=? WHERE id=?', 'must rollback', learner)
            raise RuntimeError('abort')
    row = db.one('SELECT name,school FROM users WHERE id=?', learner)
    assert row['name'] == '测试孩子' and row['school'] == ''


def test_script_failure_is_atomic():
    with pytest.raises(Exception):
        with db.tx(write=True) as tx:
            tx.script('CREATE TABLE foundation_rollback(id INTEGER); INSERT INTO missing_foundation_table VALUES(1);')
    if db.IS_PG:
        assert db.one("SELECT to_regclass('public.foundation_rollback') AS found")['found'] is None
    else:
        assert not db.one("SELECT name FROM sqlite_master WHERE name='foundation_rollback'")


def test_ai_quota_is_atomic_and_bad_output_is_not_cached(learner, monkeypatch):
    from app.llm import service
    from app.llm.base import LLMError
    monkeypatch.setattr(service.settings(), 'daily_limit_per_kid', 2)
    def spend(_):
        try:
            service._count_usage(learner)
            return True
        except LLMError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(spend, range(4))) == 2
    assert db.one('SELECT calls FROM llm_usage WHERE user_id=?', learner)['calls'] == 2
    provider = service.provider()
    monkeypatch.setattr(provider, 'complete', lambda req: '{"items":[{"q":"bad", "type":"mcq", "options":["A","B"], "answer":5}]}')
    before = db.one('SELECT COUNT(*) AS n FROM llm_cache')['n']
    with pytest.raises(LLMError):
        service.ask_json('items', 'test', uuid.uuid4().hex)
    assert db.one('SELECT COUNT(*) AS n FROM llm_cache')['n'] == before
    assert db.one('SELECT status FROM llm_calls ORDER BY id DESC LIMIT 1')['status'] == 'failed'


def test_lease_cannot_release_new_owner():
    from app.jobs import lease
    db.init()
    name = 'test-' + uuid.uuid4().hex
    with pytest.raises(RuntimeError, match="lease lost"):
        with lease(name) as first:
            assert first
            with lease(name) as second:
                assert second is None
            db.run('UPDATE jobs SET owner=? WHERE name=?', 'replacement-owner', name)
    assert db.one('SELECT owner FROM jobs WHERE name=?', name)['owner'] == 'replacement-owner'


def test_curriculum_malformed_and_cycles(tmp_path):
    (tmp_path / 'bad.json').write_text('{bad')
    assert Catalog().load(tmp_path).errors
    (tmp_path / 'bad.json').unlink()
    (tmp_path / 'cycle.json').write_text(json.dumps({'pack': {'id': 'cycle', 'subject': 'math', 'edition': 'x', 'system': 'course'},
        'strands': [{'id': 'x', 'name': 'x'}], 'kps': [
        {'id': 'a', 'name': 'a', 'stage': 'G1', 'strand': 'x', 'prereqs': ['b']},
        {'id': 'b', 'name': 'b', 'stage': 'G1', 'strand': 'x', 'prereqs': ['a']}]}))
    assert any('成环' in error for error in Catalog().load(tmp_path).errors)


def test_admin_inventory_and_operations_permissions():
    with TestClient(app) as client:
        admin_email, kid_email = uuid.uuid4().hex+'@test.example', uuid.uuid4().hex+'@test.example'
        auth.create_user(admin_email, 'secret123', '测试管理', role='admin', is_admin=True)
        auth.create_user(kid_email, 'secret123', '测试学习', role='kid')
        client.post('/login', data={'email': admin_email, 'password': 'secret123'})
        for path, label in [('/admin/curricula', '教材'), ('/admin/operations', 'AI')]:
            response = client.get(path)
            assert response.status_code == 200 and label in response.text
        client.get('/logout')
        client.post('/login', data={'email': kid_email, 'password': 'secret123'})
        assert client.get('/admin/curricula').status_code == 403
        assert client.get('/admin/operations').status_code == 403
