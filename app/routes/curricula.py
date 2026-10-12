"""Administrator curriculum workspace, structured editors and publishing."""
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from .. import auth, curriculum_admin as manager, db
from ..catalog import SUBJECTS, SYSTEMS, STAGE_LABEL, stage_rank
from ..web import render

router = APIRouter(prefix='/admin/curricula')
LABELS = {'verified': '已核实', 'partial': '部分核实', 'pending': '待核实', 'legacy': '原有课程'}


def redirect(msg='', **values):
    return RedirectResponse('/admin/curricula?' + urlencode({'msg': msg, **values}), 303)


def fail(exc):
    return HTTPException(409 if isinstance(exc, manager.Conflict) else 400, str(exc))


def context():
    state, doc, draft = manager.draft_view()
    return {'state': state, 'doc': doc, 'draft': draft, 'labels': LABELS, 'stages': sorted(STAGE_LABEL.items(), key=lambda pair: stage_rank(pair[0])),
            'subjects': SUBJECTS, 'systems': SYSTEMS, 'history': manager.history()}


@router.get('', response_class=HTMLResponse)
def index(request: Request, q: str = '', subject: str = '', status: str = '', pack_id: str = '', stage: str = '', book_id: str = '', gap: str = '', msg: str = ''):
    auth.require_admin(request)
    ctx = context()
    draft, doc = ctx['draft'], ctx['doc']
    stats = manager.coverage()
    query = q.strip().lower()
    editions = draft.textbooks.inventory(draft)
    editions = [e for e in editions if (not pack_id or e.get('pack_id') == pack_id) and (not status or e['status'] == status) and (not query or query in ' '.join(str(e.get(k) or '') for k in ('id', 'name', 'publisher', 'region')).lower())]
    packs = [p for p in draft.packs.values() if (not subject or p.subject == subject) and (not query or query in (p.id+' '+p.edition+' '+p.region).lower() or any(query in (draft.kps[k]['name']+' '+k).lower() for k in p.kp_ids))]
    packs.sort(key=lambda p: (p.subject_name, p.edition))
    pack = draft.packs.get(pack_id)
    rows = []
    mapped = {m['kp_id'] for m in doc['textbooks']['mappings'] if not book_id or m['book_id'] == book_id}
    for kp in draft.kps.values():
        if pack_id and kp['pack'] != pack_id or subject and draft.packs[kp['pack']].subject != subject or stage and kp['stage'] != stage:
            continue
        if query and query not in (kp['id']+' '+kp['name']+' '+kp.get('desc', '')+' '+draft.packs[kp['pack']].edition+' '+draft.packs[kp['pack']].region).lower():
            continue
        cov = stats.get(kp['id'], {'total': 0, 'active': 0, 'checked': 0})
        if gap == 'empty' and cov['active'] or gap == 'few' and cov['active'] >= 3 or gap == 'unmapped' and kp['id'] in mapped or book_id and kp['id'] not in mapped:
            continue
        rows.append({'kp': kp, 'coverage': cov, 'concept': draft.concepts.get(draft.concept_of.get(kp['id']))})
    books = [b for b in doc['textbooks']['books'] if not pack_id or any(e['id'] == b['edition_id'] and e.get('pack_id') == pack_id for e in doc['textbooks']['editions'])]
    chapters = [(sid, draft.chapters(pack_id, sid)) for sid in pack.stages] if pack else []
    return render(request, 'curricula_admin.html', **ctx, packs=packs, editions=editions, rows=rows[:200], row_count=len(rows), coverage=stats,
                  selected=pack, books=books, chapters=chapters, q=q, subject=subject, status=status, pack_id=pack_id, stage=stage, book_id=book_id, gap=gap, msg=msg)


@router.get('/edit/{kind}/{key}', response_class=HTMLResponse)
def editor(request: Request, kind: str, key: str, pack_id: str = '', msg: str = ''):
    auth.require_admin(request)
    if kind not in ('pack', 'kp', 'edition', 'book', 'concept', 'strand', 'import'):
        raise HTTPException(404)
    ctx = context()
    doc, draft = ctx['doc'], ctx['draft']
    new = key == '_new'
    entry = {}
    if kind == 'pack':
        entry = doc['packs'].get(key, {}).get('pack', {})
    elif kind == 'kp':
        entry = draft.kps.get(key, {})
        pack_id = entry.get('pack') or pack_id
    elif kind in ('edition', 'book'):
        entry = next((x for x in doc['textbooks'][kind+'s'] if x['id'] == key), {})
    elif kind == 'concept':
        entry = doc['concepts'].get(key, {})
    elif kind == 'strand':
        entry = next((s for s in doc['packs'].get(pack_id, {}).get('strands', []) if s['id'] == key), {})
    if not new and not entry and kind != 'import':
        raise HTTPException(404, '内容不存在')
    mappings = [m for m in doc['textbooks']['mappings'] if m['kp_id'] == key] if kind == 'kp' else []
    prerequisites = draft.prereqs(key) if kind == 'kp' else []
    following = [draft.kps[k] for k in draft.children.get(key, [])] if kind == 'kp' else []
    concept = draft.concepts.get(draft.concept_of.get(key)) if kind == 'kp' else None
    questions = db.q("SELECT i.* FROM items i JOIN item_kps x ON x.item_id=i.id WHERE x.kp_id=? ORDER BY i.created_at DESC LIMIT 30", key) if kind == 'kp' else []
    from ..bank import row_to_item
    return render(request, 'curricula_edit.html', **ctx, kind=kind, key=key, entry=entry, new=new, pack_id=pack_id, mappings=mappings,
                  prerequisites=prerequisites, following=following, concept=concept, questions=[{**r, 'item': row_to_item(r)} for r in questions], msg=msg)


@router.post('/save/{kind}')
async def save(request: Request, kind: str):
    actor = auth.require_admin(request)
    form = await request.form()
    data = dict(form)
    if kind == 'pack':
        data['stages'] = '\n'.join(form.getlist('stages'))
    try:
        await run_in_threadpool(manager.save, actor['id'], int(form.get('version', -1)), kind, data)
    except ValueError as exc:
        raise fail(exc)
    key = data.get('id', '')
    if kind in ('kp', 'pack', 'edition', 'book', 'concept', 'strand'):
        return RedirectResponse('/admin/curricula/edit/' + kind + '/' + manager.identifier(key) + '?' + urlencode({'pack_id': data.get('pack_id', ''), 'msg': '草稿已保存；发布后才影响学习端'}), 303)
    return redirect('草稿已保存', pack_id=key)


@router.post('/mapping')
async def mapping(request: Request):
    actor = auth.require_admin(request)
    form = dict(await request.form())
    try:
        await run_in_threadpool(manager.save, actor['id'], int(form.get('version', -1)), 'mapping', form)
    except ValueError as exc:
        raise fail(exc)
    return RedirectResponse('/admin/curricula/edit/kp/' + manager.identifier(form.get('kp_id')) + '?msg=' + urlencode({'m': '册次单元映射草稿已保存'}).split('=', 1)[1], 303)


@router.post('/publish')
async def publish(request: Request):
    actor = auth.require_admin(request)
    form = await request.form()
    try:
        revision = await run_in_threadpool(manager.publish, actor['id'], int(form.get('version', -1)), str(form.get('note') or ''))
    except ValueError as exc:
        raise fail(exc)
    return redirect(f'已发布版本 #{revision}；Web 与 worker 自动加载')


@router.post('/restore')
async def restore(request: Request):
    actor = auth.require_admin(request)
    form = await request.form()
    try:
        await run_in_threadpool(manager.restore_draft, actor['id'], int(form.get('version', -1)), int(form.get('revision', 0)))
    except ValueError as exc:
        raise fail(exc)
    return redirect('内容已恢复到草稿，请检查后发布；已保留历史学习需要的知识点 ID')


@router.get('/export.json')
def export(request: Request, pack_id: str = '', scope: str = 'published'):
    auth.require_admin(request)
    if scope not in ('published', 'draft'):
        raise HTTPException(400)
    state = manager.workspace()
    if scope == 'draft':
        overlays = manager.unpack(state['draft'])
    else:
        row = db.one('SELECT payload FROM curriculum_revisions WHERE id=?', state['published_id']) if state['published_id'] else None
        overlays = manager.unpack(row['payload']) if row else manager.empty()
    doc = manager.effective(overlays)
    if pack_id:
        if pack_id not in doc['packs']:
            raise HTTPException(404)
        doc = doc['packs'][pack_id]
    return JSONResponse(doc, headers={'Content-Disposition': 'attachment; filename="curricula-' + scope + '.json"'})
