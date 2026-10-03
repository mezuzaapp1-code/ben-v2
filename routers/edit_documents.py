"""Default-off private editing persistence. No render or generation endpoint."""
import json
import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from services.media.access import media_unavailable, require_pilot
from services.media.edit_document import MAX_DOCUMENT_BYTES, parse_edit_document
from services.media.edit_repository import error
from services.media.edit_service import EditService

router = APIRouter(prefix='/edit-documents', tags=['editing-documents'])


async def edit_identity(request: Request):
    if os.getenv('BEN_MEDIA_EDIT_DOCUMENTS_ENABLED') != '1':
        raise media_unavailable()
    try:
        return await require_pilot(request)
    except HTTPException as exc:
        if exc.status_code in (401, 403, 404):
            raise media_unavailable() from None
        raise


def edit_service(identity=Depends(edit_identity)):
    # Gate must run before opening a database session or accessing an asset.
    return EditService()


async def request_document(request, *, save=False):
    key = request.headers.get('Idempotency-Key', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key):
        raise error(422, 'INVALID_EDIT_DOCUMENT', 'A valid Idempotency-Key is required')
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise error(415, 'EDIT_JSON_REQUIRED', 'Send an application/json editing document')
    data = bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>MAX_DOCUMENT_BYTES:
            raise error(413, 'EDIT_DOCUMENT_TOO_LARGE', 'Editing request exceeds the size limit')
        data.extend(chunk)
    try:
        if not save:
            return key, None, parse_edit_document(bytes(data)).model_dump(mode='json')
        def unique(pairs):
            result = {}
            for k, v in pairs:
                if k in result:
                    raise ValueError('duplicate field')
                result[k] = v
            return result
        body = json.loads(bytes(data), object_pairs_hook=unique)
        if not isinstance(body, dict) or set(body) != {'base_revision_id', 'document'}:
            raise ValueError('invalid save envelope')
        if not isinstance(body['base_revision_id'], str):
            raise ValueError('invalid base')
        base = uuid.UUID(body['base_revision_id'])
        # Reuse the complete pure validator, including byte/depth bounds.
        document = parse_edit_document(json.dumps(body['document'], ensure_ascii=False)).model_dump(mode='json')
        return key, base, document
    except (ValueError, TypeError, OverflowError, RecursionError):
        # Never echo private payloads, validation input, SQL or source paths.
        raise error(422, 'INVALID_EDIT_DOCUMENT', 'Invalid editing request') from None


def private(response):
    response.headers['Cache-Control'] = 'private, no-store'


async def persisted(operation):
    try:
        return await operation
    except SQLAlchemyError:
        # Uncertain commit: client keeps its draft AND same idempotency key.
        # Never expose SQL parameters, connection strings or user content.
        raise error(503, 'EDIT_STORAGE_UNAVAILABLE', 'Save could not be confirmed; keep your draft and retry with the same save key') from None


@router.post('', status_code=201)
async def create(request: Request, response: Response, identity=Depends(edit_identity), service=Depends(edit_service)):
    private(response)
    key, _, document = await request_document(request)
    return await persisted(service.create(*identity, key, document))


@router.post('/{document_id}/revisions', status_code=201)
async def save(document_id: uuid.UUID, request: Request, response: Response,
               identity=Depends(edit_identity), service=Depends(edit_service)):
    private(response)
    key, base, document = await request_document(request, save=True)
    return await persisted(service.repo.save(*identity, document_id, base, key, document))


@router.get('')
async def documents(response: Response, resource_id: uuid.UUID | None = None,
                    identity=Depends(edit_identity), service=Depends(edit_service)):
    private(response)
    return await persisted(service.repo.list_documents(*identity, resource=resource_id))


@router.get('/{document_id}')
async def load(document_id: uuid.UUID, response: Response, identity=Depends(edit_identity), service=Depends(edit_service)):
    private(response)
    return await persisted(service.repo.read(*identity, document_id))


@router.get('/{document_id}/revisions')
async def history(document_id: uuid.UUID, response: Response, before: int | None = Query(default=None, ge=1),
                  limit: int = Query(default=20, ge=1, le=50),
                  identity=Depends(edit_identity), service=Depends(edit_service)):
    private(response)
    return await persisted(service.repo.history(*identity, document_id, before=before, limit=limit))


@router.get('/{document_id}/revisions/{revision_id}')
async def revision(document_id: uuid.UUID, revision_id: uuid.UUID, response: Response,
                   identity=Depends(edit_identity), service=Depends(edit_service)):
    private(response)
    return await persisted(service.repo.read(*identity, document_id, revision_id))
