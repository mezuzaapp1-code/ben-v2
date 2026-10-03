"""Real PostgreSQL and owned local bytes; planning never submits media jobs."""
import asyncio
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text

from services.media import plan_store, photo_source
from services.media.service import MediaService
from services.media.production_plan import SavePlan
from tests.test_media_repository import repository, ORG, OTHER, THREAD
from tests.test_media_migration import migration_sql
from tests.test_production_plan import draft
from tests.test_photo_source import photo


@pytest_asyncio.fixture
async def setup(repository,monkeypatch,tmp_path):
    repo,admin=repository
    for name in ('036_chat_photo_sources.py','038_production_plans.py'):
        await admin.execute(migration_sql(filename=name))
    async with repo.transaction(ORG) as s:role=await s.scalar(text('SELECT current_user'))
    await admin.execute(f'GRANT SELECT,INSERT ON ben.chat_photo_sources,ben.media_plan_versions TO {role}')
    monkeypatch.setenv('BEN_PROJECTS_DATA_DIR',str(tmp_path))
    service=MediaService(repo)
    return service,admin,role


def command(parent=None):
    return SavePlan(conversation_id=THREAD,parent_version_id=parent,plan=draft())


@pytest.mark.asyncio
async def test_atomic_replay_revision_race_and_immutability(setup):
    service,admin,role=setup;cmd=command()
    rows=await asyncio.gather(*(plan_store.save(service,ORG,'tester','one',cmd) for _ in range(5)))
    assert len({r['id'] for r in rows})==1
    first=rows[0]
    c2=command(first['id'])
    results=await asyncio.gather(*(plan_store.save(service,ORG,'tester',key,c2) for key in ('a','b')),return_exceptions=True)
    assert sum(isinstance(r,dict) for r in results)==1
    assert sum(isinstance(r,HTTPException) and r.status_code==409 for r in results)==1
    second=next(r for r in results if isinstance(r,dict))
    assert second['version']==2 and second['plan_id']==first['plan_id']
    assert (await plan_store.read(service.repo,ORG,'tester',first['id']))['payload']==first['payload']
    with pytest.raises(HTTPException):await plan_store.save(service,ORG,'tester','one',command())
    with pytest.raises(Exception):
        await admin.execute("UPDATE ben.media_plan_versions SET payload='{}' WHERE id=$1",first['id'])
    async with service.repo.transaction(ORG) as s:
        with pytest.raises(Exception):await s.execute(text('DELETE FROM ben.media_plan_versions'))
    assert await admin.fetchval('SELECT count(*) FROM ben.media_executions')==0
    with pytest.raises(Exception):await admin.execute(migration_sql('downgrade','038_production_plans.py'))


@pytest.mark.asyncio
async def test_owned_sources_checksums_revocation_and_rls(setup):
    service,admin,role=setup
    source=await photo_source.chat_upload(service.repo,ORG,'tester',THREAD,'photo',photo())
    fid=uuid.UUID(source['file_id']);aid=uuid.uuid4();value=draft()
    value['attachments']=[dict(attachment_id=aid,source_id=fid,source_kind='chat_photo',role='animate_source')]
    value['scenes'][0]['visual']=dict(kind='animated_attachment',attachment_id=aid,motion_prompt='move gently')
    cmd=SavePlan(conversation_id=THREAD,plan=value)
    result=await plan_store.save(service,ORG,'tester','valid',cmd)
    assert (await plan_store.latest(service.repo,ORG,'tester',THREAD))['id']==result['id']
    assert await plan_store.latest(service.repo,ORG,'outsider',THREAD) is None
    assert 'asset_snapshot' not in result and 'storage_key' not in str(result)
    for org,user in ((OTHER,'tester'),(ORG,'outsider')):
        with pytest.raises(HTTPException):await plan_store.read(service.repo,org,user,result['id'])
        with pytest.raises(HTTPException):await plan_store.save(service,org,user,'other',cmd)
    async with service.repo.transaction(OTHER) as s:
        assert await s.scalar(text('SELECT count(*) FROM ben.media_plan_versions'))==0
    _,path=photo_source.chat_path(ORG,THREAD,fid);path.write_bytes(b'corrupted')
    with pytest.raises(HTTPException):await plan_store.save(service,ORG,'tester','corrupt',cmd)
    assert await admin.fetchval('SELECT count(*) FROM ben.media_plan_versions')==1
    await admin.execute('DELETE FROM ben.threads WHERE id=$1',THREAD)
    with pytest.raises(HTTPException):await plan_store.read(service.repo,ORG,'tester',result['id'])


@pytest.mark.asyncio
async def test_database_rejects_forged_lineage_and_empty_downgrade(setup):
    service,admin,role=setup
    await admin.execute(migration_sql('downgrade','038_production_plans.py'))
    await admin.execute(migration_sql(filename='038_production_plans.py'))
    await admin.execute(f'GRANT SELECT,INSERT ON ben.media_plan_versions TO {role}')
    row=await plan_store.save(service,ORG,'tester','first',command())
    with pytest.raises(Exception):
        await admin.execute('''INSERT INTO ben.media_plan_versions
            (id,plan_id,version,org_id,created_by,conversation_id,parent_version_id,idempotency_key,request_hash,payload,asset_snapshot)
            VALUES($1,$2,2,$3,'outsider',$4,$5,'forged',$6,'{}','[]')''',
            uuid.uuid4(),row['plan_id'],ORG,THREAD,row['id'],'a'*64)
