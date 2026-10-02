import io,uuid,hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx,pytest,pytest_asyncio
from sqlalchemy import text
from fastapi import HTTPException
from services.media import photo_source
from services.media.service import MediaService
from services.media.contracts import VEO_VIDEO_MODEL,MediaProviderError
from tests.test_photo_source import photo
from tests.test_media_repository import repository,ORG,OTHER,THREAD
from tests.test_media_migration import migration_sql
from tests.test_narration_api import app_for,identity

@pytest_asyncio.fixture
async def chat_setup(repository,monkeypatch,tmp_path):
    repo,admin=repository
    await admin.execute(migration_sql(filename='036_chat_photo_sources.py'))
    async with repo.transaction(ORG) as s:role=await s.scalar(text('SELECT current_user'))
    await admin.execute(f'GRANT SELECT,INSERT ON ben.chat_photo_sources TO {role}')
    monkeypatch.setenv('BEN_PROJECTS_DATA_DIR',str(tmp_path));monkeypatch.setenv('BEN_MEDIA_VEO_ENABLED','1')
    identity(monkeypatch);monkeypatch.setattr('services.media.service.pilot_principals',lambda:{(ORG,'tester')})
    adapter=SimpleNamespace(submit=AsyncMock(side_effect=MediaProviderError('test_declined')))
    svc=MediaService(repo,veo_adapter=adapter)
    return svc,admin,app_for(svc)

@pytest.mark.asyncio
async def test_projectless_upload_animation_and_isolation(chat_setup):
    svc,admin,app=chat_setup;original=photo()
    assert await admin.fetchval("SELECT to_regclass('ben.projects')") is None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as c:
        query=dict(conversation_id=str(THREAD),idempotency_key='chat-photo')
        first=await c.post('/api/media/photo-sources',params=query,content=original)
        assert first.status_code==201,first.text
        assert 'workspace_id' not in first.json() and 'storage_key' not in first.json()
        assert (await c.post('/api/media/photo-sources',params=query,content=original)).json()==first.json()
        assert (await c.post('/api/media/photo-sources',params=query,content=photo('blue'))).status_code==409
        file_id=uuid.UUID(first.json()['file_id'])
        other_thread=uuid.uuid4();await admin.execute('INSERT INTO ben.threads VALUES($1,$2)',other_thread,ORG)
        for org,user,thread in [(OTHER,'tester',THREAD),(ORG,'outsider',THREAD),(ORG,'tester',other_thread)]:
            with pytest.raises(HTTPException):await photo_source.chat_read(svc.repo,org,user,thread,file_id)
        async with svc.repo.transaction(OTHER) as s:assert await s.scalar(text('SELECT count(*) FROM ben.chat_photo_sources'))==0
        payload=dict(conversation_id=str(THREAD),idempotency_key='chat-video',model=VEO_VIDEO_MODEL,source_file_id=str(file_id),prompt='Gentle movement')
        admitted=await c.post('/api/media/executions',json=payload);assert admitted.status_code==202,admitted.text
        claimed=await svc.repo.claim(ORG,"photo-test")
        assert claimed is not None
        await svc._async_result(claimed)
        svc.veo_adapter.submit.assert_awaited_once()
        assert svc.veo_adapter.submit.call_args.args[1]==photo_source.normalize(original)[0]
        assert await photo_source.chat_read(svc.repo,ORG,'tester',THREAD,file_id)==original

@pytest.mark.asyncio
async def test_projectless_corruption_and_deletion(chat_setup):
    svc,admin,app=chat_setup
    result=await photo_source.chat_upload(svc.repo,ORG,'tester',THREAD,'corrupt',photo());fid=uuid.UUID(result['file_id'])
    _,path=photo_source.chat_path(ORG,THREAD,fid);path.write_bytes(b'bad')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as c:
        response=await c.post('/api/media/executions',json=dict(conversation_id=str(THREAD),idempotency_key='bad',model=VEO_VIDEO_MODEL,source_file_id=str(fid),prompt='motion'))
        assert response.status_code==422
    svc.veo_adapter.submit.assert_not_called()
    await admin.execute('DELETE FROM ben.threads WHERE id=$1',THREAD)
    assert await admin.fetchval('SELECT count(*) FROM ben.chat_photo_sources')==0
    with pytest.raises(HTTPException):await photo_source.chat_read(svc.repo,ORG,'tester',THREAD,fid)

@pytest.mark.asyncio
async def test_missing_conversation_before_image_processing(chat_setup,monkeypatch):
    svc,admin,app=chat_setup
    from unittest.mock import Mock
    decode=Mock(side_effect=AssertionError('no processing'));monkeypatch.setattr(photo_source,'normalize',decode)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as c:
        response=await c.post('/api/media/photo-sources',params=dict(conversation_id=str(uuid.uuid4()),idempotency_key='bad'),content=b'bad')
    assert response.status_code==404;decode.assert_not_called()
