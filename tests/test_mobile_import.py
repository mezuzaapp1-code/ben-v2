"""Upload -> immutable original -> real worker -> fenced publication -> delivery."""
import hashlib
import uuid
from unittest.mock import Mock

from fastapi import HTTPException
import httpx
import pytest
import pytest_asyncio

from routers import media
from services.media import mobile_import
from services.media.mobile_video import MobileVideoError
from tests.test_media_repository import repository, ORG, OTHER, THREAD
from tests.test_media_migration import migration_sql
from tests.test_narration_replacement import setup
from tests.test_narration_api import app_for, identity, DENIED
from tests.test_mobile_video import make_video


@pytest_asyncio.fixture
async def mobile_setup(setup, monkeypatch):
    svc, admin, _, _, _, _, sound = setup
    await admin.execute(migration_sql(filename='035_mobile_video_import.py'))
    workspace = await admin.fetchval('SELECT workspace_id FROM ben.workspace_files WHERE id=$1', sound)
    svc.mobile_import = True
    identity(monkeypatch)
    monkeypatch.setenv('BEN_MEDIA_MOBILE_IMPORT_ENABLED', '1')
    monkeypatch.setattr(mobile_import, 'pilot_principals', lambda: {(ORG, 'tester')})
    app = app_for(svc)
    app.dependency_overrides[media.mobile_service] = lambda: svc
    return svc, admin, workspace, app


def query(workspace, key='phone-upload'):
    return dict(workspace_id=str(workspace), conversation_id=str(THREAD), idempotency_key=key)


@pytest.mark.asyncio
async def test_full_upload_retry_worker_readback(mobile_setup, tmp_path):
    svc, admin, workspace, app = mobile_setup
    original = make_video(tmp_path, codec='libx265')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        first = await client.post('/api/media/video-imports', params=query(workspace), content=original)
        assert first.status_code == 202, first.text
        retry = await client.post('/api/media/video-imports', params=query(workspace), content=original)
        assert retry.json() == first.json()
        assert 'storage_key' not in first.json()
        assert first.json()['resource_id'] is None
        execution = uuid.UUID(first.json()['execution_id'])
        # A worker with no import path must not claim it.
        assert await svc.repo.claim(ORG, 'unsupported', local=True) is None
        assert await svc.tick(ORG)
        ready = await svc.repo.read(ORG, 'tester', execution=execution)
        assert ready['state'] == 'succeeded'
        ref = ready['request_payload']['input_resource_refs'][0]
        assert uuid.UUID(ref['file_id']) != execution
        assert ref['checksum'] == hashlib.sha256(original).hexdigest()
        assert await mobile_import.source_bytes(svc.repo, ORG, 'tester', ref) == original
        response = await client.get('/api/media/resources/' + str(ready['resource_id']) + '/content')
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == ready['checksum']
        assert '/attempts/' in ready['storage_key']
        assert ready['submit_attempts'] == 0
        for org, user in ((OTHER, 'tester'), (ORG, 'outsider')):
            with pytest.raises(HTTPException):
                await svc.resource_bytes(org, user, ready['resource_id'])


@pytest.mark.asyncio
async def test_corrupted_original_never_published(mobile_setup, tmp_path):
    svc, admin, workspace, _ = mobile_setup
    row = await mobile_import.admit(svc, ORG, 'tester', 'corrupt', THREAD, workspace, make_video(tmp_path))
    ref = row['request_payload']['input_resource_refs'][0]
    _, path = mobile_import.source_path(ORG, workspace, uuid.UUID(ref['file_id']))
    path.write_bytes(b'corruption')
    await svc.tick(ORG)
    result = await svc.repo.read(ORG, 'tester', execution=row['execution_id'])
    assert result['state'] == 'failed' and result['storage_key'] is None


@pytest.mark.asyncio
async def test_expired_import_cannot_transition(mobile_setup, tmp_path):
    svc, admin, workspace, _ = mobile_setup
    await mobile_import.admit(svc, ORG, 'tester', 'lease', THREAD, workspace, make_video(tmp_path))
    alpha = await svc.repo.claim(ORG, 'alpha', mobile=True)
    await admin.execute("UPDATE ben.media_executions SET lease_expires_at=now()-interval '1 second' WHERE execution_id=$1", alpha['execution_id'])
    assert await svc.repo.local_transition(alpha, 'running') is None
    assert await svc.tick(ORG)
    assert (await svc.repo.read(ORG, 'tester', execution=alpha['execution_id']))['state'] == 'succeeded'


@pytest.mark.asyncio
async def test_wrong_workspace_rejected_before_probe(mobile_setup, monkeypatch):
    _, _, _, app = mobile_setup
    probe = Mock(side_effect=AssertionError('must not inspect bytes'))
    monkeypatch.setattr(mobile_import, 'preflight', probe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post('/api/media/video-imports', params=query(uuid.uuid4()), content=b'bad')
    assert response.status_code == 404
    probe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('outside', [False, True])
async def test_disabled_or_outside_has_no_side_effects(monkeypatch, outside):
    auth = identity(monkeypatch)
    monkeypatch.setenv('BEN_MEDIA_MOBILE_IMPORT_ENABLED', '1' if outside else '0')
    if outside:
        auth.return_value.user_id = 'outsider'
    service = Mock(side_effect=AssertionError('no service construction'))
    monkeypatch.setattr(media, 'MediaService', service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for()), base_url='http://test') as client:
        response = await client.post('/api/media/video-imports', params=query(uuid.uuid4()), content=b'bad')
    assert response.status_code == 404 and response.json() == DENIED
    service.assert_not_called()


@pytest.mark.asyncio
async def test_same_key_different_source_conflict(mobile_setup, tmp_path):
    svc, _, workspace, _ = mobile_setup
    await mobile_import.admit(svc, ORG, 'tester', 'same-source', THREAD, workspace, make_video(tmp_path))
    with pytest.raises(HTTPException) as error:
        await mobile_import.admit(svc, ORG, 'tester', 'same-source', THREAD, workspace, make_video(tmp_path, seconds=2))
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_size_limit_before_preflight(mobile_setup, monkeypatch):
    _, _, workspace, app = mobile_setup
    monkeypatch.setattr(media, 'MAX_VIDEO_BYTES', 10)
    probe = Mock(side_effect=AssertionError('no probe'))
    monkeypatch.setattr(mobile_import, 'preflight', probe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post('/api/media/video-imports', params=query(workspace), content=b'x'*11)
    assert response.status_code == 413 and response.json()['detail']['code'] == 'VIDEO_SIZE_EXCEEDED'
    probe.assert_not_called()
