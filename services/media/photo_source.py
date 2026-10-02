"""Owned photo sources for image-to-video; originals are never modified."""
import asyncio
import hashlib
import io
import uuid
import warnings
from PIL import Image, ImageOps
from fastapi import HTTPException
from sqlalchemy import text
from services.media.contracts import MAX_IMAGE_BYTES
from services.media.image_storage import _resolved, publish_bytes
from services.media.mobile_import import destination
from services.media.access import media_unavailable
from services.workspace_files.storage import files_root, DurableStorageUnavailable


def normalize(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, 'Image must be 20 MiB or smaller')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ('PNG', 'JPEG') or source.width * source.height > 20_000_000 or getattr(source, 'n_frames', 1) != 1:
                    raise ValueError()
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                image = ImageOps.exif_transpose(source).convert('RGBA')
                background = Image.new('RGBA', image.size, 'white')
                background.alpha_composite(image)
                out = io.BytesIO(); background.convert('RGB').save(out, format='PNG')
                result = out.getvalue()
                if len(result) > MAX_IMAGE_BYTES: raise ValueError()
                return result, image.width, image.height
    except (ValueError, OSError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise HTTPException(422, 'Use a valid single-frame JPEG or PNG up to 20 megapixels') from None


def path_for(org, workspace, file_id):
    if not all(isinstance(v, uuid.UUID) for v in (org, workspace, file_id)): raise ValueError('UUID required')
    root = _resolved(files_root()); key = f'{org}/{workspace}/{file_id}/photo.original'
    path = _resolved(root / key)
    if not path.is_relative_to(root) or path != root / key: raise ValueError('Invalid path')
    return key, path


async def read(repo, org, user, workspace, file_id):
    async def record():
        async with repo.transaction(org) as session:
            row = (await session.execute(text('''SELECT f.storage_key,f.checksum,f.byte_size
                FROM ben.workspace_files f JOIN ben.projects p ON p.id=f.workspace_id
                WHERE f.id=:id AND f.workspace_id=:workspace AND f.org_id=:org AND p.org_id=:org
                AND f.uploaded_by=:user AND f.status='uploaded' AND f.media_type IN ('image/jpeg','image/png')'''),
                dict(id=file_id, workspace=workspace, org=org, user=user))).mappings().first()
            if not row: raise media_unavailable()
            return dict(row)
    row = await record(); key, path = path_for(org, workspace, file_id)
    if row['storage_key'] != key: raise media_unavailable()
    def load():
        with path.open('rb') as stream: return stream.read(MAX_IMAGE_BYTES + 1)
    try: data = await asyncio.to_thread(load)
    except OSError: raise media_unavailable() from None
    if len(data) > MAX_IMAGE_BYTES or len(data) != row['byte_size'] or hashlib.sha256(data).hexdigest() != row['checksum']:
        raise HTTPException(422, 'Image integrity mismatch')
    if await record() != row: raise media_unavailable()
    return data


async def upload(repo, org, user, conversation, workspace, key, data):
    await destination(repo, org, conversation, workspace)
    png, width, height = await asyncio.to_thread(normalize, data)
    checksum = hashlib.sha256(data).hexdigest()
    file_id = uuid.uuid5(org, f'photo-v1:{user}:{workspace}:{key}')
    storage_key, path = path_for(org, workspace, file_id)
    mime = 'image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
    try: await asyncio.to_thread(publish_bytes, data, storage_key, path, width, height, mime)
    except (ValueError, DurableStorageUnavailable): raise HTTPException(409, 'Upload could not be verified; use the same original photo') from None
    async with repo.transaction(org) as session:
        await repo.destination(session, org, conversation)
        if await session.scalar(text('SELECT id FROM ben.projects WHERE id=:id AND org_id=:org'), dict(id=workspace,org=org)) is None:
            raise media_unavailable()
        await session.execute(text('''INSERT INTO ben.workspace_files
            (id,org_id,workspace_id,original_filename,display_name,media_type,byte_size,checksum,storage_key,uploaded_by,status)
            VALUES(:id,:org,:workspace,'photo.original','Photo original',:mime,:size,:checksum,:key,:user,'uploaded')
            ON CONFLICT(id) DO NOTHING'''), dict(id=file_id,org=org,workspace=workspace,mime=mime,size=len(data),checksum=checksum,key=storage_key,user=user))
    if await read(repo,org,user,workspace,file_id) != data: raise HTTPException(409,'Upload key conflict')
    return dict(file_id=str(file_id),workspace_id=str(workspace),width=width,height=height)
