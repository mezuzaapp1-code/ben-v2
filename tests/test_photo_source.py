import hashlib
import io
import uuid
from unittest.mock import AsyncMock
from types import SimpleNamespace
import httpx
import pytest
from PIL import Image
from fastapi import HTTPException
from services.media import photo_source
from services.media.contracts import VideoRequest, VEO_VIDEO_MODEL, MediaProviderError
from tests.test_mobile_import import mobile_setup
from tests.test_media_repository import repository, ORG, OTHER, THREAD
from tests.test_narration_replacement import setup
from tests.test_narration_api import identity, app_for


def photo(color='red', fmt='JPEG'):
    stream=io.BytesIO();Image.new('RGB',(320,180),color).save(stream,format=fmt);return stream.getvalue()


def test_normalization_and_original():
    original=photo();checksum=hashlib.sha256(original).hexdigest()
    png,w,h=photo_source.normalize(original)
    assert (w,h)==(320,180) and png.startswith(b'\x89PNG')
    assert hashlib.sha256(original).hexdigest()==checksum
    with pytest.raises(HTTPException):photo_source.normalize(b'broken')
    stream=io.BytesIO();Image.new('RGB',(10,10)).save(stream,format='GIF')
    with pytest.raises(HTTPException):photo_source.normalize(stream.getvalue())


@pytest.mark.asyncio
async def test_upload_idempotency_owned_source_and_worker(mobile_setup,monkeypatch):
    svc,admin,workspace,app=mobile_setup;monkeypatch.setenv('BEN_MEDIA_VEO_ENABLED','1')
    original=photo();query=dict(conversation_id=str(THREAD),workspace_id=str(workspace),idempotency_key='photo-test')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
        first=await client.post('/api/media/photo-sources',params=query,content=original)
        assert first.status_code==201,first.text
        again=await client.post('/api/media/photo-sources',params=query,content=original)
        assert first.json()==again.json() and 'storage_key' not in first.json()
        conflict=await client.post('/api/media/photo-sources',params=query,content=photo('blue'))
        assert conflict.status_code==409
        file_id=uuid.UUID(first.json()['file_id'])
        assert await photo_source.read(svc.repo,ORG,'tester',workspace,file_id)==original
        for org,user in [(OTHER,'tester'),(ORG,'outsider')]:
            with pytest.raises(HTTPException):await photo_source.read(svc.repo,org,user,workspace,file_id)
        payload=dict(conversation_id=str(THREAD),idempotency_key='animate-photo',model=VEO_VIDEO_MODEL,
            source_file_id=str(file_id),workspace_id=str(workspace),prompt='Clouds move slowly')
        response=await client.post('/api/media/executions',json=payload)
        assert response.status_code==202,response.text
        retry=await client.post('/api/media/executions',json=payload)
        assert retry.json()['execution_id']==response.json()['execution_id']
        svc.veo_adapter=SimpleNamespace(submit=AsyncMock(side_effect=MediaProviderError('test_provider_declined')))
        assert await svc.tick(ORG)
        svc.veo_adapter.submit.assert_awaited_once()
        assert svc.veo_adapter.submit.call_args.args[1]==photo_source.normalize(original)[0]
        assert await photo_source.read(svc.repo,ORG,'tester',workspace,file_id)==original


@pytest.mark.asyncio
async def test_corruption_blocks_provider(mobile_setup,monkeypatch):
    svc,admin,workspace,app=mobile_setup;monkeypatch.setenv('BEN_MEDIA_VEO_ENABLED','1')
    source=await photo_source.upload(svc.repo,ORG,'tester',THREAD,workspace,'corrupt-photo',photo())
    file_id=uuid.UUID(source['file_id'])
    request=VideoRequest(VEO_VIDEO_MODEL,'gentle motion',str(file_id))
    row=await svc.create(ORG,'tester','corrupt-animation',THREAD,request,photo_workspace=workspace)
    _,path=photo_source.path_for(ORG,workspace,file_id);path.write_bytes(b'bad')
    svc.veo_adapter=SimpleNamespace(submit=AsyncMock(side_effect=AssertionError('no provider call')))
    assert await svc.tick(ORG)
    svc.veo_adapter.submit.assert_not_called()
    failed=await svc.repo.read(ORG,'tester',execution=row['execution_id'])
    assert failed['state']=='failed'
    with pytest.raises(HTTPException):await svc.create(ORG,'tester','bad-admission',THREAD,request,photo_workspace=workspace)


@pytest.mark.asyncio
async def test_gate_before_assets(monkeypatch):
    from routers import media
    monkeypatch.setenv('BEN_MEDIA_VEO_ENABLED','0');monkeypatch.setenv('BEN_MEDIA_KLING_ENABLED','0')
    probe=AsyncMock(side_effect=AssertionError('no assets'))
    monkeypatch.setattr(media.mobile_import,'destination',probe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for()),base_url='http://test') as client:
        response=await client.post('/api/media/photo-sources',params=dict(conversation_id=str(THREAD),workspace_id=str(uuid.uuid4()),idempotency_key='disabled'),content=b'bad')
    assert response.status_code==404;probe.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_destination_before_decode(mobile_setup,monkeypatch):
    from unittest.mock import Mock
    _,_,_,app=mobile_setup;monkeypatch.setenv('BEN_MEDIA_VEO_ENABLED','1')
    decode=Mock(side_effect=AssertionError('no decode'));monkeypatch.setattr(photo_source,'normalize',decode)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
        response=await client.post('/api/media/photo-sources',params=dict(conversation_id=str(THREAD),workspace_id=str(uuid.uuid4()),idempotency_key='wrong'),content=b'bad')
    assert response.status_code==404;decode.assert_not_called()
