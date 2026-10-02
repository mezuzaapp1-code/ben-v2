"""Disposable real PostgreSQL: budget races, RLS, restart and publication."""
import asyncio
import hashlib
import json
import uuid
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text

from services.media.service import MediaService
from services.media.short_contract import digest
from services.media.contracts import MediaProviderError
from tests.test_media_repository import repository, ORG, OTHER, THREAD
from tests.test_media_migration import migration_sql
from tests.test_short_render import pricing, videos, plan


@pytest_asyncio.fixture
async def short_repo(repository, pricing, monkeypatch, tmp_path):
    repo,admin=repository
    for filename in ['034_narration_replacement.py','035_mobile_video_import.py','036_chat_photo_sources.py','037_short_render.py']:
        await admin.execute(migration_sql(filename=filename))
    monkeypatch.setenv('BEN_MEDIA_INTERNAL_ENABLED','1')
    monkeypatch.setenv('BEN_MEDIA_INTERNAL_PRINCIPALS',json.dumps([{'org_id':str(ORG),'user_id':'tester'}]))
    monkeypatch.setenv('BEN_MEDIA_SHORT_RENDER_ENABLED','1')
    monkeypatch.setenv('BEN_PROJECTS_DATA_DIR',str(tmp_path))
    return repo,admin


def snap():
    p=plan();p.conversation_id=THREAD
    return {'provider':'creatomate','model':'fixed_5_scene_v1','operation':'short_render',
        'destination':{'conversation_id':str(THREAD)},'plan':p.model_dump(mode='json'),
        'parameters':{'aspect_ratio':'9:16','duration_seconds':35,'validation_profile':'short-v1'},
        'input_checksums':[hashlib.sha256(b'x').hexdigest()]*6}


async def create(repo,key='one',snapshot=None,quote=None):
    p=snapshot or snap()
    q=quote or {'id':str(uuid.uuid4()),'reserved':'0.05','estimate':'0.04','pricing_version':'synthetic-v1'}
    return await repo.create(ORG,'tester',key,p,digest(p),short_quote=q)


@pytest.mark.asyncio
async def test_atomic_budget_idempotency_quote_and_rls(short_repo):
    repo,admin=short_repo;p=snap();q={'id':str(uuid.uuid4()),'reserved':'0.05','estimate':'0.04','pricing_version':'test'}
    rows=await asyncio.gather(*[create(repo,snapshot=p,quote=q) for _ in range(4)])
    assert len({r['execution_id'] for r in rows})==1
    with pytest.raises(HTTPException) as conflict:await create(repo,'other',snapshot=p,quote=q)
    assert conflict.value.status_code==409
    with pytest.raises(HTTPException) as limit:await create(repo,'over-budget')
    assert limit.value.status_code==429
    async with repo.transaction(OTHER) as s:
        assert await s.scalar(text('SELECT count(*) FROM ben.media_executions'))==0
    with pytest.raises(HTTPException):await repo.read(ORG,'outsider',execution=rows[0]['execution_id'])
    assert await admin.fetchval('SELECT sum(reserved_cost) FROM ben.media_executions')==rows[0]['reserved_cost']
    with pytest.raises(Exception):await admin.execute(migration_sql('downgrade','037_short_render.py'))


@pytest.mark.asyncio
async def test_different_keys_race_and_stale_worker_cannot_submit(short_repo):
    repo,admin=short_repo
    results=await asyncio.gather(create(repo,'a'),create(repo,'b'),return_exceptions=True)
    assert sum(isinstance(r,dict) for r in results)==1
    assert sum(isinstance(r,HTTPException) and r.status_code==429 for r in results)==1
    row=await repo.claim(ORG,'alpha',short=True)
    await admin.execute("UPDATE ben.media_executions SET lease_expires_at=now()-interval '1 second' WHERE execution_id=$1",row['execution_id'])
    assert await repo.mark_submitting(row) is None
    beta=await repo.claim(ORG,'beta',short=True)
    assert await repo.mark_submitting(beta)
    assert await repo.mark_submitting(beta) is None


@pytest.mark.asyncio
async def test_uncertain_submit_restart_never_reposts(short_repo,monkeypatch):
    repo,admin=short_repo;row=await create(repo)
    monkeypatch.setattr('services.media.short_render.sources',AsyncMock(return_value=[(b'x','image/png')]*6))
    service=MediaService(repo,short_render=True)
    service.short_stage=Mock();service.short_stage.prepare.return_value=['https://example.test/x']*6
    service.short_adapter=Mock(key='synthetic');service.short_adapter.submit=AsyncMock(side_effect=MediaProviderError('timeout',submission_unknown=True))
    assert await service.tick(ORG)
    result=await repo.read(ORG,'tester',execution=row['execution_id'])
    assert result['state']=='submission_unknown' and result['submit_attempts']==1
    await admin.execute("UPDATE ben.media_executions SET next_reconcile_at=now() WHERE execution_id=$1",row['execution_id'])
    assert await service.tick(ORG)
    service.short_adapter.submit.assert_awaited_once()
    assert (await repo.read(ORG,'tester',execution=row['execution_id']))['reserved_cost']==row['reserved_cost']


@pytest.mark.asyncio
async def test_worker_real_output_and_private_telemetry(short_repo,monkeypatch,videos):
    repo,admin=short_repo;row=await create(repo)
    source=AsyncMock(return_value=[(b'x','image/png')]*6)
    monkeypatch.setattr('services.media.short_render.sources',source)
    service=MediaService(repo,short_render=True)
    service.short_stage=Mock();service.short_stage.prepare.return_value=['https://example.test/x']*6
    service.short_adapter=Mock(key='synthetic')
    ref=str(uuid.uuid4())
    service.short_adapter.submit=AsyncMock(return_value=ref)
    service.short_adapter.poll=AsyncMock(return_value=('succeeded',f'https://cdn.creatomate.com/renders/{ref}.mp4'))
    service.short_adapter.download=AsyncMock(return_value=videos['valid'])
    assert await service.tick(ORG)
    await admin.execute('UPDATE ben.media_executions SET next_reconcile_at=now() WHERE execution_id=$1',row['execution_id'])
    assert await service.tick(ORG)
    done=await repo.read(ORG,'tester',execution=row['execution_id'])
    assert done['state']=='succeeded' and done['short_telemetry']['technical_pass']
    assert done['actual_charge'] is None and done['short_telemetry']['cost_status']=='estimated_only'
    assert await service.resource_bytes(ORG,'tester',done['resource_id'])==videos['valid']
    from services.media.service import public_execution
    public=public_execution(done)
    assert 'short_telemetry' not in public and 'storage_key' not in public and public['actual_charge'] is None
    service.short_adapter.submit.assert_awaited_once()
    await repo.evaluate(ORG,'tester',done['execution_id'],{'acceptance':'accepted'})
    reviewed=await repo.read(ORG,'tester',execution=row['execution_id'])
    assert reviewed['short_telemetry']['human_review_status']=='accepted'


@pytest.mark.asyncio
async def test_worker_rejects_missing_audio(short_repo,monkeypatch,videos):
    repo,admin=short_repo;row=await create(repo)
    source=AsyncMock(return_value=[]);monkeypatch.setattr('services.media.short_render.sources',source)
    service=MediaService(repo,short_render=True);service.short_adapter=Mock(key='synthetic')
    service.short_stage=Mock();service.short_stage.prepare.return_value=['https://example.test/x']*6
    service.short_adapter.submit=AsyncMock(return_value=str(uuid.uuid4()))
    service.short_adapter.poll=AsyncMock(return_value=('succeeded','https://example.test/output'))
    service.short_adapter.download=AsyncMock(return_value=videos['silent'])
    await service.tick(ORG)
    await admin.execute('UPDATE ben.media_executions SET next_reconcile_at=now() WHERE execution_id=$1',row['execution_id'])
    await service.tick(ORG)
    done=await repo.read(ORG,'tester',execution=row['execution_id'])
    assert done['state']=='failed' and done['storage_key'] is None


@pytest.mark.asyncio
async def test_revoked_source_prevents_paid_submit(short_repo,monkeypatch):
    repo,admin=short_repo;row=await create(repo)
    monkeypatch.setattr('services.media.short_render.sources',AsyncMock(side_effect=HTTPException(404,'unavailable')))
    service=MediaService(repo,short_render=True)
    service.short_adapter=Mock(key='synthetic');service.short_adapter.submit=AsyncMock()
    service.short_stage=Mock()
    await service.tick(ORG)
    done=await repo.read(ORG,'tester',execution=row['execution_id'])
    assert done['state']=='failed' and done['submit_attempts']==0
    service.short_adapter.submit.assert_not_called()
    service.short_stage.prepare.assert_not_called()
