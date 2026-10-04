import base64
import hashlib
import io
import subprocess
from unittest.mock import AsyncMock
import uuid
import av
from PIL import Image
import pytest
from fastapi import HTTPException
from services.media.edit_export import render, timeline, validate_frames, export_revision
from services.media.mobile_video import _SLOT

def doc():
    return {'source':{'resource_id':str(uuid.uuid4()),'duration_seconds':1},
            'body':{'cues':[{'start':0,'end':.5,'text':'test'}],'textLayers':[]}}

def payload(document,width=1280,height=720):
    frames=[]
    for start,end in timeline(document):
        img=Image.new('RGBA',(width,height),(0,0,0,0))
        if start==0:
            img.paste((255,0,0,255),(500,200,780,500))
        buf=io.BytesIO();img.save(buf,format='PNG')
        frames.append(dict(start=start,end=end,png=base64.b64encode(buf.getvalue()).decode()))
    return dict(width=width,height=height,frames=frames)

def test_real_export_pixels_timing_audio_and_original(tmp_path):
    path=tmp_path/'audio-source.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=1280x720:rate=30',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','1','-c:v','libx264',
                    '-threads','2','-c:a','aac',str(path)],check=True,capture_output=True,timeout=30)
    original=path.read_bytes()
    checksum=hashlib.sha256(original).hexdigest()
    result=render(original,doc(),payload(doc()))
    with av.open(io.BytesIO(result)) as c:
        assert c.streams.audio and c.streams.audio[0].codec_context.name=='aac'
        frames=list(c.decode(video=0))
        early=frames[2].to_image().getpixel((640,300))
        late=frames[-2].to_image().getpixel((640,300))
        assert early[0]>200 and early[1]<30 and early[2]<30
        assert not (late[0]>200 and late[1]<30 and late[2]<30)
    assert hashlib.sha256(original).hexdigest()==checksum

@pytest.mark.parametrize('mutation',['time','size','url','corrupt','too_many','nan'])
def test_invalid_overlays_rejected(tmp_path,mutation):
    p=payload(doc())
    if mutation=='time':p['frames'][0]['end']=.8
    if mutation=='size':p['width']=4096
    if mutation=='url':p['frames'][0]['png']='https://private.invalid/source'
    if mutation=='corrupt':p['frames'][0]['png']=base64.b64encode(b'bad').decode()
    if mutation=='too_many':p['frames']*=400
    if mutation=='nan':p['frames'][0]['start']=float('nan')
    with pytest.raises((ValueError,OSError)):
        validate_frames(p,doc(),tmp_path,1280,720)

def test_export_busy_does_not_render():
    assert _SLOT.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as caught:render(b'',doc(),{})
        assert caught.value.status_code==503
    finally:_SLOT.release()

@pytest.mark.asyncio
async def test_export_denied_before_reader():
    from types import SimpleNamespace
    service=SimpleNamespace(repo=SimpleNamespace(read=AsyncMock(side_effect=HTTPException(404)),media=None),reader=AsyncMock())
    with pytest.raises(HTTPException):await export_revision(service,None,'other',uuid.uuid4(),uuid.uuid4(),{})
    service.reader.assert_not_called()

@pytest.mark.asyncio
async def test_revocation_after_render_blocks_delivery(monkeypatch):
    from types import SimpleNamespace
    from services.media import edit_export
    data=b'verified source'
    revision={'document':doc(),'source_checksum':hashlib.sha256(data).hexdigest()}
    service=SimpleNamespace(repo=SimpleNamespace(read=AsyncMock(side_effect=[revision,HTTPException(404)]),media=None),reader=AsyncMock(return_value=data))
    monkeypatch.setattr(edit_export,'render',lambda *a:b'mp4')
    with pytest.raises(HTTPException):await export_revision(service,None,'owner',uuid.uuid4(),uuid.uuid4(),{})
    assert service.repo.read.await_count==2

@pytest.mark.asyncio
async def test_export_http_boundaries(monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    import httpx
    from routers import edit_documents as routes
    from services.media import edit_export
    app=FastAPI();app.include_router(routes.router,prefix='/api/media')
    svc=SimpleNamespace(repo=SimpleNamespace(read=AsyncMock(return_value={})))
    app.dependency_overrides[routes.edit_identity]=lambda:(uuid.uuid4(),'owner')
    app.dependency_overrides[routes.edit_service]=lambda:svc
    export=AsyncMock(return_value=b'verified mp4')
    monkeypatch.setattr(edit_export,'export_revision',export)
    path=f'/api/media/edit-documents/{uuid.uuid4()}/revisions/{uuid.uuid4()}/export'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as c:
        r=await c.post(path,json={})
        assert r.status_code==200 and r.content==b'verified mp4'
        assert r.headers['cache-control']=='private, no-store'
        assert r.headers['content-type']=='video/mp4'
        assert 'attachment' in r.headers['content-disposition']
        assert (await c.post(path,content=b'bad')).status_code==415
        monkeypatch.setattr(edit_export,'MAX_EXPORT_REQUEST',4)
        assert (await c.post(path,json={'large':True})).status_code==413
        svc.repo.read.side_effect=HTTPException(404)
        assert (await c.post(path,content=b'bad')).status_code==404
    assert export.await_count==1
