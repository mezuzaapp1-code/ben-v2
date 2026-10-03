"""Default-off planning endpoints; no execution admission or provider dispatch."""
import os
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from services.media.access import media_unavailable, require_pilot
from services.media.production_plan import SavePlan
from services.media.service import MediaService
from services.media import plan_store

router = APIRouter(prefix='/production-plans', tags=['internal-media'])


async def identity(request: Request):
    if os.getenv('BEN_MEDIA_PLAN_ENABLED') != '1':
        raise media_unavailable()
    try:
        return await require_pilot(request)
    except HTTPException as exc:
        if exc.status_code in (401, 403, 404):
            raise media_unavailable() from None
        raise


@router.post('', status_code=201)
async def save_plan(command: SavePlan, owner=Depends(identity),
                    idempotency_key: str = Header(min_length=1, max_length=128)):
    return await plan_store.save(MediaService(), *owner, idempotency_key, command)


@router.get('/{version_id}')
async def get_plan(version_id: UUID, owner=Depends(identity)):
    return await plan_store.read(MediaService().repo, *owner, version_id)
