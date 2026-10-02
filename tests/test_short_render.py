"""No paid calls. Real MP4s, mocked transports and fail-closed admission."""
import io
import json
import subprocess
import uuid
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from services.media.short_contract import ScenePlan, quote, verify_quote
from services.media.creatomate import CreatomateAdapter, render_script
from services.media.contracts import MediaProviderError
from services.media.short_validation import validate
from services.media.short_staging import ShortStaging


def plan():
    return ScenePlan(conversation_id=uuid.uuid4(), image_resource_ids=[uuid.uuid4() for _ in range(5)],
        narration_workspace_id=uuid.uuid4(), narration_file_id=uuid.uuid4(),
        captions=[{'start_ms':0,'end_ms':7000,'text':'מה שלומך? BEN 2026'}])


@pytest.fixture
def pricing(monkeypatch):
    for key,value in {'BEN_SHORT_QUOTE_SECRET':'synthetic-test-secret-'*3,
                      'BEN_SHORT_ESTIMATE_USD':'0.04','BEN_SHORT_RESERVATION_USD':'0.05',
                      'BEN_SHORT_DAILY_BUDGET_USD':'0.05','BEN_SHORT_PRICING_VERSION':'synthetic-v1'}.items():
        monkeypatch.setenv(key,value)


def test_closed_plan_quote_owner_integrity_and_expiry(pricing, monkeypatch):
    p=plan(); snap=p.model_dump(mode='json'); org=uuid.uuid4()
    token=quote(org,'alice',snap)
    assert verify_quote(token,org,'alice',snap)['reserved']=='0.05'
    for owner,body in [('bob',snap),('alice',{**snap,'profile_id':'changed'})]:
        with pytest.raises(HTTPException): verify_quote(token,org,owner,body)
    with pytest.raises(HTTPException):verify_quote(token+'a',org,'alice',snap)
    monkeypatch.setattr('services.media.short_contract.time.time',lambda:10**12)
    with pytest.raises(HTTPException):verify_quote(token,org,'alice',snap)
    with pytest.raises(ValidationError):ScenePlan(**{**snap,'url':'http://localhost'})
    with pytest.raises(ValidationError):ScenePlan(**{**snap,'image_resource_ids':[]})
    with pytest.raises(ValidationError):ScenePlan(**{**snap,'captions':[{'start_ms':3,'end_ms':2,'text':'x'}]})


def test_builder_preserves_rtl_and_fixed_scene_times():
    p=plan().model_dump(mode='json'); body=render_script(p,['https://example.test/'+str(i) for i in range(6)])
    assert [e['time'] for e in body['elements'][:5]]==[0,7,14,21,28]
    assert body['elements'][-1]['text']=='מה שלומך? BEN 2026'
    assert body['duration']==35 and body['frame_rate']==30
    assert all(e['fit']=='contain' for e in body['elements'][:5])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['timeout','malformed','http500'])
async def test_submit_uncertainty_never_retries(kind):
    calls=[]
    def respond(request):
        calls.append(request)
        if kind=='timeout':raise httpx.ReadTimeout('synthetic')
        return httpx.Response(500 if kind=='http500' else 200,json={})
    adapter=CreatomateAdapter('fake',httpx.MockTransport(respond))
    with pytest.raises(MediaProviderError) as exc:
        await adapter.submit(plan().model_dump(mode='json'),['https://example.test/x']*6)
    assert exc.value.submission_unknown and len(calls)==1


@pytest.mark.asyncio
async def test_poll_and_download_do_not_forward_secret_or_redirect():
    ref=str(uuid.uuid4());calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(200,json={'id':ref,'status':'succeeded','url':f'https://cdn.creatomate.com/renders/{ref}.mp4'}) if request.url.host=='api.creatomate.com' else httpx.Response(302,headers={'Location':'http://127.0.0.1'})
    a=CreatomateAdapter('synthetic-secret',httpx.MockTransport(respond))
    status,url=await a.poll(ref);assert status=='succeeded'
    with pytest.raises(MediaProviderError):await a.download(ref,url)
    assert len(calls)==2 and 'authorization' not in calls[-1].headers
    for url in ['http://127.0.0.1','https://cdn.creatomate.com.evil/renders/x.mp4','https://user@cdn.creatomate.com/renders/x.mp4']:
        with pytest.raises(MediaProviderError):await a.download(ref,url)
    assert len(calls)==2


def test_staging_requires_private_bucket_and_lifecycle(monkeypatch):
    monkeypatch.setenv('BEN_SHORT_STAGE_BUCKET','test-private')
    client=Mock(); client.get_public_access_block.return_value={'PublicAccessBlockConfiguration':{}}
    stage=ShortStaging(client)
    with pytest.raises(ValueError):stage.prepare(uuid.uuid4(),[(b'a','image/png')])
    client.put_object.assert_not_called()
    client.get_public_access_block.return_value={'PublicAccessBlockConfiguration':dict.fromkeys(
        ['BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets'],True)}
    client.get_bucket_lifecycle_configuration.return_value={'Rules':[{'Status':'Enabled','Filter':{'Prefix':'ben-short/'},'Expiration':{'Days':1}}]}
    stage.prepare(uuid.uuid4(),[(b'a','image/png')])
    assert client.generate_presigned_url.call_args.kwargs['ExpiresIn']==1800
    assert client.put_object.call_args.kwargs['ServerSideEncryption']=='AES256'


@pytest.fixture(scope='module')
def videos(tmp_path_factory):
    root=tmp_path_factory.mktemp('short-fixtures');out={}
    for name,size,audio in [('valid','720x1280',True),('wrong','1280x720',True),('silent','720x1280',False)]:
        path=root/(name+'.mp4')
        args=['ffmpeg','-v','error','-f','lavfi','-i',f'color=c=blue:s={size}:r=30:d=35']
        if audio:args+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        args+=['-t','35','-c:v','libx264','-preset','ultrafast','-threads','1','-pix_fmt','yuv420p']
        if audio:args+=['-c:a','aac']
        subprocess.run(args+['-movflags','+faststart',str(path)],check=True,capture_output=True,timeout=90)
        out[name]=path.read_bytes()
    return out


def test_full_decode_valid_and_corrupt_output(videos):
    assert validate(videos['valid'])['full_decode'] is True
    for data in [videos['wrong'],videos['silent'],videos['valid'][:len(videos['valid'])//2],b'not mp4']:
        with pytest.raises(ValueError):validate(data)


@pytest.mark.asyncio
async def test_disabled_route_has_zero_asset_or_service_calls(monkeypatch):
    import routers.media as module
    monkeypatch.setenv('BEN_MEDIA_SHORT_RENDER_ENABLED','0')
    factory=Mock(side_effect=AssertionError('must not create service'))
    monkeypatch.setattr(module,'media_service',factory)
    app=FastAPI();app.include_router(module.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
        result=await client.post('/api/media/short-render-quotes',json=plan().model_dump(mode='json'))
    assert result.status_code==404 and result.json()['detail']['code']=='MEDIA_UNAVAILABLE'
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_nonpilot_route_has_zero_service_calls(monkeypatch):
    import routers.media as module
    monkeypatch.setenv('BEN_MEDIA_SHORT_RENDER_ENABLED','1')
    monkeypatch.setattr(module,'require_pilot',AsyncMock(side_effect=HTTPException(403,'private detail')))
    factory=Mock(side_effect=AssertionError('must not create service'))
    monkeypatch.setattr(module,'media_service',factory)
    app=FastAPI();app.include_router(module.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
        result=await client.post('/api/media/short-render-quotes',json=plan().model_dump(mode='json'))
    assert result.status_code==404 and result.json()['detail']['code']=='MEDIA_UNAVAILABLE'
    factory.assert_not_called()
