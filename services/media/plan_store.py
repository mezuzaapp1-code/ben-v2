"""Planning persistence only. Does not call an LLM, renderer or paid provider."""
import hashlib
import json
import uuid

from fastapi import HTTPException
from sqlalchemy import text

from services.media import photo_source
from services.media.production_plan import SavePlan
from services.media.short_contract import digest


def public(row):
    return {k: row[k] for k in ('id', 'plan_id', 'version', 'conversation_id',
                               'parent_version_id', 'created_at', 'payload')}


async def read(repo, org, user, version_id):
    async with repo.transaction(org) as session:
        row = (await session.execute(text('''SELECT * FROM ben.media_plan_versions
            WHERE id=:id AND org_id=:org AND created_by=:user'''),
            dict(id=version_id, org=org, user=user))).mappings().first()
        if row is None:
            raise HTTPException(404, 'Plan not found')
        await repo.destination(session, org, row['conversation_id'])
        return public(row)


async def latest(repo, org, user, conversation):
    async with repo.transaction(org) as session:
        await repo.destination(session, org, conversation)
        row = (await session.execute(text('''SELECT * FROM ben.media_plan_versions
            WHERE org_id=:org AND created_by=:user AND conversation_id=:conversation
            ORDER BY created_at DESC,id DESC LIMIT 1'''),
            dict(org=org, user=user, conversation=conversation))).mappings().first()
        return public(row) if row else None


async def resolve_assets(service, org, user, command):
    snapshots = []
    for asset in command.plan.attachments:
        if asset.source_kind == 'chat_photo':
            data = await photo_source.chat_read(service.repo, org, user, command.conversation_id, asset.source_id)
        else:
            row = await service.repo.read(org, user, resource=asset.source_id)
            if (row['conversation_id'] != command.conversation_id or row['state'] != 'succeeded'
                    or row['mime_type'] != 'image/png'):
                raise HTTPException(404, 'Source not found')
            data = await service.resource_bytes(org, user, asset.source_id)
        snapshots.append(dict(attachment_id=str(asset.attachment_id), source_id=str(asset.source_id),
            source_kind=asset.source_kind, checksum=hashlib.sha256(data).hexdigest()))
    return snapshots


async def check_assets(session, org, user, conversation, snapshots):
    for asset in snapshots:
        if asset['source_kind'] == 'chat_photo':
            query = '''SELECT checksum FROM ben.chat_photo_sources
                WHERE id=:id AND org_id=:org AND created_by=:user AND conversation_id=:conversation'''
        else:
            query = '''SELECT checksum FROM ben.media_executions
                WHERE resource_id=:id AND org_id=:org AND created_by=:user AND conversation_id=:conversation
                AND state='succeeded' AND mime_type='image/png' AND deleted_at IS NULL'''
        checksum = await session.scalar(text(query), dict(id=uuid.UUID(asset['source_id']), org=org,
                                                        user=user, conversation=conversation))
        if checksum != asset['checksum']:
            raise HTTPException(409, 'Source changed or unavailable')


async def save(service, org, user, key, command: SavePlan):
    request_hash = digest(command.model_dump(mode='json'))
    # Fast authorized replay; this does not authorize future execution of the plan.
    async with service.repo.transaction(org) as session:
        previous = (await session.execute(text('''SELECT * FROM ben.media_plan_versions
            WHERE org_id=:org AND created_by=:user AND idempotency_key=:key'''),
            dict(org=org, user=user, key=key))).mappings().first()
        if previous:
            await service.repo.destination(session, org, previous['conversation_id'])
            if previous['request_hash'] != request_hash:
                raise HTTPException(409, 'Plan idempotency conflict')
            return public(previous)
        await service.repo.destination(session, org, command.conversation_id)
    assets = await resolve_assets(service, org, user, command)
    async with service.repo.transaction(org) as session:
        await service.repo.destination(session, org, command.conversation_id)
        # Serializes version allocation and competing edits, including new-key retries.
        await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))'),
                              {'scope': 'production-plan:'+str(org)})
        previous = (await session.execute(text('''SELECT * FROM ben.media_plan_versions
            WHERE org_id=:org AND created_by=:user AND idempotency_key=:key'''),
            dict(org=org, user=user, key=key))).mappings().first()
        if previous:
            if previous['request_hash'] != request_hash:
                raise HTTPException(409, 'Plan idempotency conflict')
            return public(previous)
        await check_assets(session, org, user, command.conversation_id, assets)
        plan_id, version = uuid.uuid4(), 1
        if command.parent_version_id:
            parent = (await session.execute(text('''SELECT * FROM ben.media_plan_versions
                WHERE id=:id AND org_id=:org AND created_by=:user AND conversation_id=:conversation'''),
                dict(id=command.parent_version_id, org=org, user=user,
                     conversation=command.conversation_id))).mappings().first()
            if not parent:
                raise HTTPException(404, 'Parent plan not found')
            if await session.scalar(text('SELECT id FROM ben.media_plan_versions WHERE parent_version_id=:id'),
                                    {'id': command.parent_version_id}):
                raise HTTPException(409, 'Plan has a newer version; reload before editing')
            plan_id, version = parent['plan_id'], parent['version']+1
        row = (await session.execute(text('''INSERT INTO ben.media_plan_versions
            (id,plan_id,version,org_id,created_by,conversation_id,parent_version_id,idempotency_key,
             request_hash,payload,asset_snapshot)
            VALUES(:id,:plan,:version,:org,:user,:conversation,:parent,:key,:hash,
                   CAST(:payload AS jsonb),CAST(:assets AS jsonb)) RETURNING *'''),
            dict(id=uuid.uuid4(), plan=plan_id, version=version, org=org, user=user,
                 conversation=command.conversation_id, parent=command.parent_version_id, key=key,
                 hash=request_hash, payload=json.dumps(command.plan.model_dump(mode='json')),
                 assets=json.dumps(assets)))).mappings().one()
        return public(row)
