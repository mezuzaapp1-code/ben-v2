"""Authorized immutable originals in the existing file library; queued conversion."""
import asyncio
import hashlib
import tempfile
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text

from services.media.access import media_unavailable, pilot_principals
from services.media.contracts import MAX_VIDEO_BYTES
from services.media.image_storage import _resolved, publish_bytes
from services.media.metadata import request_fingerprint
from services.media.mobile_video import MobileVideoError, _probe, analyze, convert_mobile
from services.workspace_files.storage import files_root


async def destination(repo, org, conversation, workspace):
    async with repo.transaction(org) as s:
        await repo.destination(s, org, conversation)
        found = await s.scalar(text('SELECT id FROM ben.projects WHERE id=:id AND org_id=:org FOR KEY SHARE'),
                               {'id': workspace, 'org': org})
        if found is None:
            raise media_unavailable()


def source_path(org, workspace, file_id):
    if not all(isinstance(v, uuid.UUID) for v in (org, workspace, file_id)):
        raise ValueError('trusted UUIDs required')
    root = _resolved(files_root())
    key = f'{org}/{workspace}/{file_id}/source.mp4'
    path = root / key
    if _resolved(path) != path or not path.is_relative_to(root):
        raise ValueError('invalid source path')
    return key, path


def preflight(data):
    if len(data) > MAX_VIDEO_BYTES:
        raise MobileVideoError('VIDEO_SIZE_EXCEEDED')
    if len(data) < 12 or data[4:8] != b'ftyp':
        raise MobileVideoError()
    with tempfile.TemporaryDirectory(prefix='ben-probe-') as directory:
        scratch = Path(directory)
        path = scratch / 'source.mov'
        path.write_bytes(data)
        return analyze(_probe(path, scratch), _probe(path, scratch, packets=True))


async def source_bytes(repo, org, user, ref):
    workspace, file_id = uuid.UUID(ref['workspace_id']), uuid.UUID(ref['file_id'])
    async def record():
        async with repo.transaction(org) as s:
            row = (await s.execute(text('''SELECT f.storage_key,f.checksum,f.byte_size
                FROM ben.workspace_files f JOIN ben.projects p ON p.id=f.workspace_id
                WHERE f.org_id=:org AND p.org_id=:org AND f.workspace_id=:workspace
                AND f.id=:id AND f.uploaded_by=:user AND f.media_type='video/mp4'
                AND f.status='uploaded' '''),
                dict(org=org, workspace=workspace, id=file_id, user=user))).mappings().first()
            if not row:
                raise media_unavailable()
            return dict(row)
    row = await record()
    key, path = source_path(org, workspace, file_id)
    if row['storage_key'] != key or row['checksum'] != ref['checksum'] or row['byte_size'] != ref['byte_size']:
        raise MobileVideoError()
    def read():
        with path.open('rb') as f:
            return f.read(MAX_VIDEO_BYTES + 1)
    data = await asyncio.to_thread(read)
    if len(data) != row['byte_size'] or hashlib.sha256(data).hexdigest() != row['checksum']:
        raise MobileVideoError()
    if await record() != row:
        raise media_unavailable()
    return data


async def admit(service, org, user, key, conversation, workspace, data):
    if not service.mobile_import or (org, user) not in pilot_principals():
        raise media_unavailable()
    await destination(service.repo, org, conversation, workspace)
    profile = await asyncio.to_thread(preflight, data)
    # Stable upload identity survives a lost admission response; independent of execution.
    file_id = uuid.uuid5(org, f'mobile-v1:{user}:{key}')
    checksum = hashlib.sha256(data).hexdigest()
    storage_key, path = source_path(org, workspace, file_id)
    if path.exists():
        def same():
            with path.open('rb') as f:
                return hashlib.sha256(f.read(MAX_VIDEO_BYTES + 1)).hexdigest() == checksum
        if not await asyncio.to_thread(same):
            raise HTTPException(409, 'Upload key conflict')
    await asyncio.to_thread(publish_bytes, data, storage_key, path, profile.width, profile.height, 'video/mp4')
    async with service.repo.transaction(org) as s:
        await service.repo.destination(s, org, conversation)
        if await s.scalar(text('SELECT id FROM ben.projects WHERE id=:id AND org_id=:org FOR KEY SHARE'),
                          dict(id=workspace, org=org)) is None:
            raise media_unavailable()
        await s.execute(text('''INSERT INTO ben.workspace_files
            (id,org_id,workspace_id,original_filename,display_name,media_type,byte_size,checksum,storage_key,uploaded_by,status)
            VALUES(:id,:org,:workspace,'source.mp4','Mobile video original','video/mp4',:size,:checksum,:key,:user,'uploaded')
            ON CONFLICT(id) DO NOTHING'''), dict(id=file_id, org=org, workspace=workspace,
                size=len(data), checksum=checksum, key=storage_key, user=user))
    ref = dict(file_id=str(file_id), workspace_id=str(workspace), checksum=checksum, byte_size=len(data))
    await source_bytes(service.repo, org, user, ref)
    snapshot = dict(normalization_version='mobile-v1', provider='local_composer', model='ffmpeg_mobile_v1',
        operation='video_import', prompt='', parameters=dict(aspect_ratio=profile.aspect_ratio,
        duration_seconds=profile.duration, validation_profile='mobile-v1'),
        destination=dict(conversation_id=str(conversation), workspace_id=str(workspace)), input_resource_refs=[ref],
        experiment_id=None)
    return await service.repo.create(org, user, key, snapshot, request_fingerprint(snapshot))


async def run_import(service, row):
    row = await service.repo.local_transition(row, 'running')
    if not row:
        return
    try:
        ref = row['request_payload']['input_resource_refs'][0]
        data = await source_bytes(service.repo, row['org_id'], row['created_by'], ref)
        output, _ = await asyncio.to_thread(convert_mobile, data)
        await source_bytes(service.repo, row['org_id'], row['created_by'], ref)
    except (HTTPException, ValueError, OSError) as error:
        await service.repo.change(row, state='failed', error_code=error.code if isinstance(error, MobileVideoError) else 'VIDEO_SOURCE_UNAVAILABLE')
        return
    row = await service.repo.local_transition(row, 'ingesting')
    if row:
        await service.publish_video_attempt(row, output, attempt_id=uuid.uuid4())
