"""Conversation-scoped immutable phone video storage with owner RLS."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import uuid
from fastapi import HTTPException
from sqlalchemy import text
from services.media.access import media_unavailable
from services.media.contracts import MAX_VIDEO_BYTES
from services.media.image_storage import _resolved, publish_bytes
from services.workspace_files.storage import files_root, DurableStorageUnavailable

@asynccontextmanager
async def transaction(repo, org, user, conversation):
    async with repo.transaction(org) as session:
        await session.execute(text("SELECT set_config('app.current_user_id',:user,true)"), {'user': user})
        await repo.destination(session, org, conversation)
        yield session

def path_for(org, conversation, file_id):
    if not all(isinstance(v,uuid.UUID) for v in (org,conversation,file_id)):
        raise ValueError('UUID required')
    root=_resolved(files_root())
    key=f'_chat_video/{org}/{conversation}/{file_id}/source.mp4'
    path=_resolved(root/key)
    if path!=root/key or not path.is_relative_to(root): raise ValueError('Invalid source path')
    return key,path

async def read(repo,org,user,ref):
    conversation,file_id=uuid.UUID(ref['conversation_id']),uuid.UUID(ref['file_id'])
    async def record():
        async with transaction(repo,org,user,conversation) as session:
            row=(await session.execute(text("""SELECT storage_key,checksum,byte_size FROM ben.chat_video_sources
                WHERE id=:id AND org_id=:org AND created_by=:user AND conversation_id=:conversation"""),
                dict(id=file_id,org=org,user=user,conversation=conversation))).mappings().first()
            if not row: raise media_unavailable()
            return dict(row)
    row=await record()
    key,path=path_for(org,conversation,file_id)
    if row['storage_key']!=key or row['checksum']!=ref['checksum'] or row['byte_size']!=ref['byte_size']:
        raise media_unavailable()
    def load():
        with path.open('rb') as f: return f.read(MAX_VIDEO_BYTES+1)
    try: data=await asyncio.to_thread(load)
    except OSError: raise media_unavailable() from None
    if len(data)!=row['byte_size'] or hashlib.sha256(data).hexdigest()!=row['checksum']:
        raise media_unavailable()
    if await record()!=row: raise media_unavailable()
    return data

async def store(repo,org,user,conversation,key,data,profile):
    file_id=uuid.uuid5(org,f'chat-video-v1:{user}:{conversation}:{key}')
    storage_key,path=path_for(org,conversation,file_id)
    async with transaction(repo,org,user,conversation): pass
    try:
        await asyncio.to_thread(publish_bytes,data,storage_key,path,profile.width,profile.height,'video/mp4')
    except (ValueError,DurableStorageUnavailable):
        raise HTTPException(409,'Upload key conflict') from None
    ref=dict(file_id=str(file_id),conversation_id=str(conversation),checksum=hashlib.sha256(data).hexdigest(),byte_size=len(data))
    async with transaction(repo,org,user,conversation) as session:
        await session.execute(text("""INSERT INTO ben.chat_video_sources
            (id,org_id,created_by,conversation_id,storage_key,checksum,byte_size)
            VALUES(:id,:org,:user,:conversation,:key,:checksum,:size) ON CONFLICT(id) DO NOTHING"""),
            dict(id=file_id,org=org,user=user,conversation=conversation,key=storage_key,checksum=ref['checksum'],size=len(data)))
    if await read(repo,org,user,ref)!=data: raise HTTPException(409,'Upload key conflict')
    return ref
