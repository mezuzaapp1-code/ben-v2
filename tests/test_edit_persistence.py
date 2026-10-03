"""Real PostgreSQL app-role acceptance. No remote services or paid calls."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid
from unittest.mock import AsyncMock

import asyncpg
from fastapi import FastAPI, HTTPException
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from routers import edit_documents as routes
from services.media.edit_document import validate_edit_document
from services.media.edit_repository import EditRepository
from services.media.edit_service import EditService
from services.media.video_storage import video_path
from tests.test_media_repository import repository, ORG, OTHER, THREAD, create
from tests.test_media_migration import migration_sql
from tests.test_veo_media import mp4

MIGRATION = 'video_edit_documents_v1.py'


@pytest_asyncio.fixture
async def edits(repository, tmp_path, monkeypatch):
    media, admin = repository
    await admin.execute(migration_sql(filename=MIGRATION))
    async with media.transaction(ORG) as session:
        role = await session.scalar(text('SELECT current_user'))
    await admin.execute(f'''GRANT SELECT,INSERT ON ben.video_edit_documents,ben.video_edit_revisions TO {role};
        GRANT UPDATE(head_revision_id,head_number,updated_at) ON ben.video_edit_documents TO {role}''')
    source = await create(media)
    monkeypatch.setenv('BEN_PROJECTS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('BEN_REQUIRE_DURABLE_FILE_ROOT', '0')
    data = mp4(width=64, height=64, frames=480, audio=False)
    checksum = hashlib.sha256(data).hexdigest()
    key, path = video_path(ORG, source['resource_id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    await admin.execute('''UPDATE ben.media_executions SET state='succeeded',mime_type='video/mp4',
        checksum=$1,byte_size=$2,storage_key=$3,published_at=now() WHERE resource_id=$4''',
        checksum, len(data), key, source['resource_id'])
    document = json.loads((Path(__file__).parent/'fixtures/editor_document_v1.json').read_text(encoding='utf-8'))['document']
    document['document_id'] = str(uuid.uuid4())
    document['source']['resource_id'] = str(source['resource_id'])
    service = EditService(EditRepository(media))
    # A persistence call must never dispatch, quote, reserve or render media.
    def forbidden(*args, **kwargs):
        raise AssertionError('Paid/execution side effects are forbidden')
    monkeypatch.setattr(media, 'create', forbidden)
    monkeypatch.setattr('services.media.service.MediaService.create', forbidden)
    monkeypatch.setattr('services.media.service.MediaService.tick', forbidden)
    yield service, admin, document, checksum, path, role


async def first(edits):
    service, _, doc, *_ = edits
    return await service.create(ORG, 'tester', 'first', doc)


def app_for(service, *, user='tester', org=ORG):
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/media')
    app.dependency_overrides[routes.edit_identity] = lambda: (org, user)
    app.dependency_overrides[routes.edit_service] = lambda: service
    return app


@pytest.mark.asyncio
async def test_api_roundtrip_restore_and_paginated_history(edits):
    service, admin, doc, checksum, path, _ = edits
    original = path.read_bytes()
    url = '/api/media/edit-documents'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for(service)), base_url='http://test') as client:
        a = await client.post(url, json=doc, headers={'Idempotency-Key': 'first'})
        assert a.status_code == 201, a.text
        one = a.json()
        assert one['document'] == validate_edit_document(doc).model_dump(mode='json')
        assert one['source_checksum'] == checksum
        assert a.headers['cache-control'] == 'private, no-store'
        url += '/' + doc['document_id']
        changed = deepcopy(doc)
        changed['body']['style'].update(x=34.125, backgroundMode='none', shadow='depth')
        response = await client.post(url+'/revisions', json={'base_revision_id': one['revision_id'], 'document': changed},
                                     headers={'Idempotency-Key': 'second'})
        assert response.status_code == 201, response.text
        two = response.json()
        assert (await client.get(url)).json() == two
        assert (await client.get(url+'/revisions/'+one['revision_id'])).json() == one
        # Restore is a NEW revision of the current head, never history mutation.
        response = await client.post(url+'/revisions', json={'base_revision_id': two['revision_id'], 'document': one['document']},
                                     headers={'Idempotency-Key': 'restore'})
        assert response.status_code == 201, response.text
        three = response.json()
        assert three['revision_number'] == 3 and three['parent_revision_id'] == two['revision_id']
        assert three['document'] == one['document']
        page = (await client.get(url+'/revisions?limit=2')).json()
        assert [r['revision_number'] for r in page['revisions']] == [3, 2]
        assert page['next_before'] == 2
        page = (await client.get(url+'/revisions?limit=2&before=2')).json()
        assert [r['revision_number'] for r in page['revisions']] == [1] and page['next_before'] is None
        assert (await client.get(url+'/revisions?limit=51')).status_code == 422
        assert set(one) == {'revision_id','parent_revision_id','revision_number','created_at','source_checksum','document'}
    assert path.read_bytes() == original
    assert await admin.fetchval('SELECT count(*) FROM ben.media_executions') == 1


@pytest.mark.asyncio
async def test_concurrent_first_save_and_lost_response_replay(edits):
    service, admin, doc, *_ = edits
    copies = await asyncio.gather(*(service.create(ORG, 'tester', 'first', doc) for _ in range(4)))
    assert len({r['revision_id'] for r in copies}) == 1
    one = copies[0]
    doc_id = uuid.UUID(doc['document_id'])
    two = await service.repo.save(ORG, 'tester', doc_id, one['revision_id'], 'second', doc)
    # First and second saves can both be replayed even after a newer head exists.
    assert (await service.create(ORG, 'tester', 'first', doc)) == one
    three = await service.repo.save(ORG, 'tester', doc_id, two['revision_id'], 'third', doc)
    assert await service.repo.save(ORG, 'tester', doc_id, one['revision_id'], 'second', doc) == two
    assert (await service.repo.read(ORG, 'tester', doc_id)) == three
    assert await admin.fetchval('SELECT count(*) FROM ben.video_edit_revisions') == 3


@pytest.mark.asyncio
async def test_competing_saves_one_wins_other_conflicts(edits):
    service, admin, doc, *_ = edits
    one = await first(edits)
    results = await asyncio.gather(*(service.repo.save(ORG, 'tester', uuid.UUID(doc['document_id']),
        one['revision_id'], f'save-{i}', doc) for i in range(6)), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    conflicts = [r for r in results if isinstance(r, HTTPException)]
    assert len(conflicts) == 5 and all(r.status_code==409 and r.detail['code']=='EDIT_REVISION_CONFLICT' for r in conflicts)
    assert await admin.fetchval('SELECT count(*) FROM ben.video_edit_revisions') == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('org,user', [(OTHER,'tester'), (ORG,'other-user')])
async def test_cross_owner_api_and_direct_rls(edits, org, user):
    service, _, doc, *_ = edits
    one = await first(edits)
    doc_id = uuid.UUID(doc['document_id'])
    calls = [service.repo.read(org,user,doc_id), service.repo.history(org,user,doc_id),
             service.repo.read(org,user,doc_id,one['revision_id']),
             service.repo.save(org,user,doc_id,one['revision_id'],'foreign',doc),
             service.create(org,user,'foreign',doc)]
    for call in calls:
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404
    async with service.repo.transaction(org,user) as session:
        for table in ('video_edit_documents','video_edit_revisions'):
            assert await session.scalar(text(f'SELECT count(*) FROM ben.{table}')) == 0
        assert (await session.execute(text('UPDATE ben.video_edit_documents SET head_number=99'))).rowcount == 0
    with pytest.raises(DBAPIError):
        async with service.repo.transaction(org,user) as session:
            await session.execute(text('''INSERT INTO ben.video_edit_documents
                (document_id,org_id,created_by,resource_id,source_checksum,duration_seconds,head_revision_id,head_number)
                VALUES (:id,:org,:user,:resource,:checksum,20,:revision,1)'''),
                {'id': uuid.uuid4(), 'org': org, 'user': user, 'resource': uuid.UUID(doc['source']['resource_id']),
                 'checksum': one['source_checksum'], 'revision': uuid.uuid4()})


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['source', 'duration', 'key', 'document_id'])
async def test_reject_source_substitution_and_key_reuse(edits, change):
    service, admin, doc, *_ = edits
    one = await first(edits)
    changed, key = deepcopy(doc), 'next'
    if change == 'source':
        changed['source']['resource_id'] = str(uuid.uuid4())
    elif change == 'duration':
        changed['source']['duration_seconds'] = 25
    elif change == 'document_id':
        changed['document_id'] = str(uuid.uuid4())
    else:
        key = 'first'
    with pytest.raises(HTTPException) as exc:
        await service.repo.save(ORG,'tester',uuid.UUID(doc['document_id']),one['revision_id'],key,changed)
    assert exc.value.status_code == (409 if change=='key' else 422)
    assert await admin.fetchval('SELECT count(*) FROM ben.video_edit_revisions') == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['checksum','deleted','conversation'])
async def test_source_revocation_hides_head_and_history(edits, change):
    service, admin, doc, *_ = edits
    one = await first(edits)
    if change=='conversation':
        await admin.execute('DELETE FROM ben.threads WHERE id=$1', THREAD)
    else:
        assignment = "checksum='"+'b'*64+"'" if change=='checksum' else 'deleted_at=now()'
        await admin.execute('UPDATE ben.media_executions SET '+assignment)
    doc_id = uuid.UUID(doc['document_id'])
    for call in (service.repo.read(ORG,'tester',doc_id), service.repo.history(ORG,'tester',doc_id),
                 service.repo.save(ORG,'tester',doc_id,one['revision_id'],'next',doc)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_immutable_history_and_head_integrity_even_with_extra_grants(edits):
    service, admin, doc, _, _, role = edits
    await first(edits)
    await admin.execute(f'GRANT UPDATE,DELETE ON ben.video_edit_documents,ben.video_edit_revisions TO {role}')
    for sql in ("UPDATE ben.video_edit_revisions SET document='{}'", 'DELETE FROM ben.video_edit_revisions',
                'DELETE FROM ben.video_edit_documents', 'UPDATE ben.video_edit_documents SET head_number=head_number+1',
                "UPDATE ben.video_edit_documents SET source_checksum='"+'b'*64+"'"):
        with pytest.raises(DBAPIError):
            async with service.repo.transaction(ORG,'tester') as session:
                await session.execute(text(sql))
    assert await admin.fetchval('SELECT count(*) FROM ben.video_edit_revisions') == 1
    with pytest.raises(asyncpg.PostgresError):
        await admin.execute(migration_sql('downgrade', filename=MIGRATION))


@pytest.mark.asyncio
async def test_no_source_mutation_forged_duration_or_corrupt_bytes(edits):
    service, admin, doc, _, path, _ = edits
    changed = deepcopy(doc)
    changed['source']['duration_seconds'] = 25
    with pytest.raises(HTTPException) as exc:
        await service.create(ORG,'tester','duration',changed)
    assert exc.value.status_code == 422
    path.write_bytes(b'corrupt')
    with pytest.raises(HTTPException) as exc:
        await service.create(ORG,'tester','corrupt',doc)
    assert exc.value.status_code == 503
    assert await admin.fetchval('SELECT count(*) FROM ben.video_edit_documents') == 0


@pytest.mark.asyncio
async def test_limits_replay_and_atomic_rollback(edits, monkeypatch):
    service, admin, doc, *_ = edits
    one = await first(edits)
    monkeypatch.setattr('services.media.edit_repository.MAX_DOCUMENTS',1)
    monkeypatch.setattr('services.media.edit_repository.MAX_REVISIONS',1)
    assert await first(edits) == one
    next_doc = deepcopy(doc)
    next_doc['document_id'] = str(uuid.uuid4())
    for call in (service.create(ORG,'tester','other',next_doc),
                 service.repo.save(ORG,'tester',uuid.UUID(doc['document_id']),one['revision_id'],'next',doc)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code==429
    monkeypatch.setattr('services.media.edit_repository.MAX_REVISIONS',1000)
    original_insert = service.repo.insert
    async def insert_then_fail(*args, **kwargs):
        await original_insert(*args, **kwargs)
        raise RuntimeError('simulated transaction failure')
    monkeypatch.setattr(service.repo,'insert',insert_then_fail)
    with pytest.raises(RuntimeError):
        await service.repo.save(ORG,'tester',uuid.UUID(doc['document_id']),one['revision_id'],'rollback',doc)
    assert await admin.fetchval('SELECT count(*) FROM ben.video_edit_revisions')==1
    assert await service.repo.read(ORG,'tester',uuid.UUID(doc['document_id'])) == one


@pytest.mark.asyncio
async def test_empty_migration_downgrade_and_upgrade(edits):
    _, admin, *_ = edits
    await admin.execute(migration_sql('downgrade', filename=MIGRATION))
    await admin.execute(migration_sql(filename=MIGRATION))
    assert await admin.fetchval('SELECT count(*) FROM ben.video_edit_documents') == 0


@pytest.mark.asyncio
async def test_create_rechecks_checksum_after_read(edits):
    service, admin, doc, *_ = edits
    reader = service.reader
    async def changed(*args):
        data = await reader(*args)
        await admin.execute("UPDATE ben.media_executions SET checksum='"+'b'*64+"'")
        return data
    service.reader = changed
    with pytest.raises(HTTPException) as exc:
        await first(edits)
    assert exc.value.status_code == 409
    assert await admin.fetchval('SELECT count(*) FROM ben.video_edit_revisions') == 0


@pytest.mark.asyncio
async def test_failed_commit_never_claims_saved_or_exposes_sql():
    repo = type('BrokenRepository', (), {'save': AsyncMock(side_effect=OperationalError(
        'private SQL', {'secret':'private/path'}, Exception('credential')))})()
    service = type('BrokenService', (), {'repo':repo})()
    doc = json.loads((Path(__file__).parent/'fixtures/editor_document_v1.json').read_text(encoding='utf-8'))['document']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for(service)), base_url='http://test') as client:
        response = await client.post('/api/media/edit-documents/'+doc['document_id']+'/revisions',
            json={'base_revision_id':str(uuid.uuid4()),'document':doc}, headers={'Idempotency-Key':'uncertain'})
    assert response.status_code==503 and response.json()['detail']['code']=='EDIT_STORAGE_UNAVAILABLE'
    assert all(value not in response.text for value in ('private SQL','private/path','credential'))


def test_jsonb_unsupported_nul_is_rejected_before_storage():
    from services.media.edit_repository import validated
    doc = json.loads((Path(__file__).parent/'fixtures/editor_document_v1.json').read_text(encoding='utf-8'))['document']
    doc['body']['cues'][0]['text']='valid\\u0000 literal'
    assert validated(doc,'save')
    doc['body']['cues'][0]['text']='bad\0character'
    with pytest.raises(HTTPException) as exc:
        validated(doc,'save')
    assert exc.value.status_code==422


@pytest.mark.asyncio
@pytest.mark.parametrize('payload,status', [
    ('{"document_id":"private/path","document_id":"duplicate"}',422),
    ('{"source":{"storage_key":"private/path"}}',422),
    ('['*1100,422),
    ('x'*1000001,413),
], ids=['duplicate-fields','private-field','deep-json','oversize'])
async def test_bad_http_body_is_bounded_and_redacted(payload,status):
    service = type('NoCalls', (), {'create': AsyncMock(side_effect=AssertionError('invalid request reached storage'))})()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for(service)), base_url='http://test') as client:
        result = await client.post('/api/media/edit-documents', content=payload,
            headers={'Idempotency-Key':'bad','Content-Type':'application/json'})
    assert result.status_code == status
    assert 'private/path' not in result.text
    service.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['off','outside','unauthenticated'])
async def test_gate_before_factory(monkeypatch, mode):
    monkeypatch.setenv('BEN_MEDIA_EDIT_DOCUMENTS_ENABLED', '0' if mode=='off' else '1')
    auth = AsyncMock(side_effect=HTTPException(401 if mode=='unauthenticated' else 404,'denied'))
    monkeypatch.setattr(routes,'require_pilot',auth)
    def forbidden():
        raise AssertionError('Gate must precede factory')
    monkeypatch.setattr(routes,'EditService',forbidden)
    app = FastAPI()
    app.include_router(routes.router,prefix='/api/media')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        result = await client.post('/api/media/edit-documents', content=b'not-json')
    assert result.status_code==404 and result.json()['detail']['code']=='MEDIA_UNAVAILABLE'
    if mode=='off':
        auth.assert_not_awaited()
