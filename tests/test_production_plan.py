"""Pure contract and default-off API tests; no paid services."""
import copy
import uuid
from unittest.mock import Mock, AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from services.media.production_plan import ProductionPlan, SavePlan


def draft():
    return dict(original_brief='Create a short original story.', scenes=[dict(
        scene_id=str(uuid.uuid4()), duration_ms=5000, narration_text='Hello',
        visual=dict(kind='generated_scene', prompt='A blue sky')) for _ in range(3)])


def test_fixed_timeline_and_frozen_nested_contract():
    plan=ProductionPlan(**draft())
    assert [s['start_ms'] for s in plan.timeline()]==[0,5000,10000]
    assert plan.timeline()[-1]['end_ms']==15000
    with pytest.raises(ValidationError):plan.scenes[0].duration_ms=8000
    with pytest.raises(ValidationError):plan.width=1080


def test_crossfades_count_once_in_timeline():
    value=draft()
    for s in value['scenes'][:2]:
        s['duration_ms']=5500;s['transition_to_next']=dict(kind='crossfade',overlap_ms=500)
    plan=ProductionPlan(**value)
    assert [s['start_ms'] for s in plan.timeline()]==[0,5000,10000]
    assert plan.timeline()[-1]['end_ms']==15000


@pytest.mark.parametrize('case',['duration','duplicate','last_transition','cut_overlap',
    'unknown_reference','untyped_overlay','injected_assets','dimensions','float_duration','provider_url'])
def test_reject_unsafe_or_inconsistent_plan(case):
    value=draft();s=value['scenes'][0]
    if case=='duration':s['duration_ms']=4000
    elif case=='duplicate':value['scenes'][1]['scene_id']=s['scene_id']
    elif case=='last_transition':value['scenes'][-1]['transition_to_next']=dict(kind='crossfade',overlap_ms=100)
    elif case=='cut_overlap':s['transition_to_next']=dict(kind='cut',overlap_ms=100)
    elif case=='unknown_reference':s['visual']=dict(kind='animated_attachment',attachment_id=str(uuid.uuid4()),motion_prompt='move')
    elif case=='untyped_overlay':s['visual']=dict(kind='controlled_composition',background_prompt='sky',overlays=[{'command':'run'}])
    elif case=='injected_assets':value['validated_assets']=[{'checksum':'fake'}]
    elif case=='dimensions':value['width']=1920
    elif case=='float_duration':s['duration_ms']=5000.0
    elif case=='provider_url':s['visual']['url']='http://localhost/private'
    with pytest.raises(ValidationError):ProductionPlan(**value)


def test_attachment_roles_and_typed_composition():
    value=draft();aid=str(uuid.uuid4())
    value['attachments']=[dict(attachment_id=aid, source_id=str(uuid.uuid4()),source_kind='chat_photo',role='animate_source')]
    value['scenes'][0]['visual']=dict(kind='animated_attachment',attachment_id=aid,motion_prompt='gentle motion')
    value['scenes'][2]['visual']=dict(kind='controlled_composition',background_prompt='empty sky',
        overlays=[dict(kind='sprite',attachment_id=aid,count=2),dict(kind='text',text='2')])
    assert ProductionPlan(**value).scenes[2].visual.overlays[0].count==2
    value['attachments'][0]['role']='style_reference'
    with pytest.raises(ValidationError):ProductionPlan(**value)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',['0','1'])
async def test_gate_before_service_creation(monkeypatch,enabled):
    import routers.production_plans as module
    monkeypatch.setenv('BEN_MEDIA_PLAN_ENABLED',enabled)
    monkeypatch.setattr(module,'require_pilot',AsyncMock(side_effect=HTTPException(403,'private')))
    factory=Mock(side_effect=AssertionError('service must not be created'))
    monkeypatch.setattr(module,'MediaService',factory)
    app=FastAPI();app.include_router(module.router,prefix='/api/media')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as c:
        r=await c.post('/api/media/production-plans',json=dict(conversation_id=str(uuid.uuid4()),plan=draft()),headers={'Idempotency-Key':'one'})
    assert r.status_code==404 and r.json()['detail']['code']=='MEDIA_UNAVAILABLE'
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_planner_reads_use_authenticated_owner_and_private_photo_response(monkeypatch):
    import routers.production_plans as module
    from services.media import photo_source
    owner=(uuid.uuid4(), uuid.uuid4())
    conversation, photo=uuid.uuid4(), uuid.uuid4()
    repo=object()
    monkeypatch.setattr(module, 'MediaService', lambda: Mock(repo=repo))
    latest=AsyncMock(return_value=None)
    read=AsyncMock(return_value=b'\x89PNG\r\n\x1a\nimage')
    monkeypatch.setattr(module.plan_store, 'latest', latest)
    monkeypatch.setattr(photo_source, 'chat_read', read)
    app=FastAPI();app.include_router(module.router,prefix='/api/media')
    app.dependency_overrides[module.identity]=lambda: owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as c:
        r=await c.get(f'/api/media/production-plans?conversation_id={conversation}')
        assert r.json()=={'plan':None}
        latest.assert_awaited_once_with(repo,*owner,conversation)
        path=f'/api/media/production-plans/photos/{photo}?conversation_id={conversation}'
        r=await c.get(path)
        assert r.status_code==200 and r.headers['content-type']=='image/png'
        assert r.headers['cache-control']=='private, no-store'
        assert r.headers['x-content-type-options']=='nosniff'
        read.assert_awaited_once_with(repo,*owner,conversation,photo)
        read.side_effect=HTTPException(404,'unavailable')
        assert (await c.get(path)).status_code==404
