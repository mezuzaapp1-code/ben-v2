"""Owner-scoped, append-only edits. No provider, generation or render dispatch."""
from contextlib import asynccontextmanager
import hashlib
import json
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from services.media.edit_document import validate_edit_document
from services.media.repository import MediaRepository

MAX_REVISIONS = 1000
MAX_DOCUMENTS = 100


def error(status, code, message):
    return HTTPException(status, detail={'code': code, 'message': message})


def unavailable():
    return error(404, 'EDIT_DOCUMENT_UNAVAILABLE', 'Editing document unavailable')


def fingerprint(document, base):
    payload = json.dumps({'base_revision_id': str(base) if base else None, 'document': document},
                         sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def validated(document, key):
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key):
        raise error(422, 'INVALID_EDIT_DOCUMENT', 'Invalid editing request')
    try:
        result = validate_edit_document(document).model_dump(mode='json')
        # PostgreSQL JSONB cannot represent U+0000, although JSON can.
        if any('\0' in item['text'] for item in (*result['body']['cues'], *result['body']['textLayers'])):
            raise ValueError('Unsupported text character')
        return result
    except (ValueError, TypeError, OverflowError):
        raise error(422, 'INVALID_EDIT_DOCUMENT', 'Invalid editing request') from None


def public_revision(row):
    # Never serialize the underlying media row or private storage/provider fields.
    return {key: row[key] for key in ('revision_id', 'parent_revision_id', 'revision_number',
                                      'created_at', 'source_checksum', 'document')}


class EditRepository:
    def __init__(self, media_repository=None):
        self.media = media_repository or MediaRepository()

    async def list_documents(self, org, user, *, resource=None):
        # Owner quota bounds the complete collection at 100. Never return bodies,
        # storage keys or another user's work; RLS also checks live source access.
        async with self.transaction(org, user) as session:
            rows = (await session.execute(text('''SELECT d.document_id,d.resource_id,
                d.head_number,d.updated_at FROM ben.video_edit_documents d
                JOIN ben.media_executions m ON m.resource_id=d.resource_id
                AND m.org_id=d.org_id AND m.created_by=d.created_by
                WHERE d.org_id=:org AND d.created_by=:user
                AND m.checksum=d.source_checksum AND m.state='succeeded'
                AND m.deleted_at IS NULL
                AND (CAST(:resource AS uuid) IS NULL OR d.resource_id=CAST(:resource AS uuid))
                ORDER BY d.updated_at DESC,d.document_id LIMIT 100'''),
                {'org': org, 'user': user, 'resource': resource})).mappings().all()
            return {'documents': [dict(row) for row in rows]}

    @asynccontextmanager
    async def transaction(self, org, user):
        async with self.media.transaction(org) as session:
            await session.execute(text("SELECT set_config('app.current_user_id', :user, true)"), {'user': user})
            await session.execute(text("SET LOCAL lock_timeout='5s'"))
            await session.execute(text("SET LOCAL statement_timeout='10s'"))
            yield session

    async def source(self, session, org, user, resource):
        row = (await session.execute(text('''SELECT * FROM ben.media_executions
            WHERE resource_id=:resource AND org_id=:org AND created_by=:user
            AND state='succeeded' AND deleted_at IS NULL AND mime_type='video/mp4' FOR SHARE'''),
            {'resource': uuid.UUID(str(resource)), 'org': org, 'user': user})).mappings().first()
        if not row:
            raise unavailable()
        try:
            await self.media.destination(session, org, row['conversation_id'])
        except HTTPException:
            raise unavailable() from None
        return row

    async def head(self, session, org, user, document_id, *, lock=False):
        row = (await session.execute(text('''SELECT * FROM ben.video_edit_documents
            WHERE document_id=:id AND org_id=:org AND created_by=:user''' + (' FOR UPDATE' if lock else '')),
            {'id': document_id, 'org': org, 'user': user})).mappings().first()
        if not row:
            raise unavailable()
        source = await self.source(session, org, user, row['resource_id'])
        if source['checksum'] != row['source_checksum']:
            raise unavailable()
        return row

    async def replay(self, session, document_id, key, digest):
        old = (await session.execute(text('''SELECT * FROM ben.video_edit_revisions
            WHERE document_id=:id AND idempotency_key=:key'''), {'id': document_id, 'key': key})).mappings().first()
        if old and old['request_fingerprint'] != digest:
            raise error(409, 'EDIT_IDEMPOTENCY_CONFLICT', 'This save key was already used for a different request')
        return public_revision(old) if old else None

    async def insert(self, session, org, user, document, checksum, key, digest, revision, number, base):
        row = (await session.execute(text('''INSERT INTO ben.video_edit_revisions
            (revision_id,document_id,org_id,created_by,revision_number,parent_revision_id,
             idempotency_key,request_fingerprint,source_checksum,document)
            VALUES (:revision,:id,:org,:user,:number,:base,:key,:digest,:checksum,CAST(:document AS jsonb)) RETURNING *'''),
            {'revision': revision, 'id': uuid.UUID(document['document_id']), 'org': org, 'user': user,
             'number': number, 'base': base, 'key': key, 'digest': digest, 'checksum': checksum,
             'document': json.dumps(document, ensure_ascii=False, allow_nan=False)})).mappings().one()
        return public_revision(row)

    async def create(self, org, user, key, document, *, checksum, duration):
        # checksum/duration are supplied ONLY by the server's verified-byte reader.
        document = validated(document, key)
        if abs(document['source']['duration_seconds'] - duration) > .05 or not 0 < duration <= 30:
            raise error(422, 'EDIT_SOURCE_MISMATCH', 'Editing timeline does not match the source video')
        digest = fingerprint(document, None)
        document['source']['duration_seconds'] = duration
        document = validated(document, key)
        doc_id, rev_id = uuid.UUID(document['document_id']), uuid.uuid4()
        try:
            async with self.transaction(org, user) as session:
                # Bounded owner admission also serializes first-save retries.
                await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))'),
                                      {'scope': f'video-edits:{org}:{user}'})
                source = await self.source(session, org, user, document['source']['resource_id'])
                if source['checksum'] != checksum:
                    raise error(409, 'EDIT_SOURCE_CHANGED', 'Source changed during verification')
                old = await self.replay(session, doc_id, key, digest)
                if old:
                    return old
                count = await session.scalar(text('SELECT count(*) FROM ben.video_edit_documents WHERE org_id=:org AND created_by=:user'),
                                             {'org': org, 'user': user})
                if count >= MAX_DOCUMENTS:
                    raise error(429, 'EDIT_DOCUMENT_LIMIT', 'Editing document limit reached')
                await session.execute(text('''INSERT INTO ben.video_edit_documents
                    (document_id,org_id,created_by,resource_id,source_checksum,duration_seconds,head_revision_id,head_number)
                    VALUES (:id,:org,:user,:resource,:checksum,:duration,:revision,1)'''),
                    {'id': doc_id, 'org': org, 'user': user, 'resource': source['resource_id'],
                     'checksum': checksum, 'duration': duration, 'revision': rev_id})
                return await self.insert(session, org, user, document, checksum, key, digest, rev_id, 1, None)
        except IntegrityError:
            raise error(409, 'EDIT_DOCUMENT_CONFLICT', 'Use a new editing document ID or load your existing document') from None

    async def save(self, org, user, document_id, base, key, document):
        document = validated(document, key)
        if document['document_id'] != str(document_id):
            raise error(422, 'INVALID_EDIT_DOCUMENT', 'Document ID does not match')
        digest = fingerprint(document, base)
        async with self.transaction(org, user) as session:
            head = await self.head(session, org, user, document_id, lock=True)
            if (document['source']['resource_id'] != str(head['resource_id'])
                    or abs(document['source']['duration_seconds'] - head['duration_seconds']) > .05):
                raise error(422, 'EDIT_SOURCE_MISMATCH', 'Editing source cannot change')
            document['source']['duration_seconds'] = head['duration_seconds']
            document = validated(document, key)
            # Replay BEFORE checking head: a lost response must survive later edits.
            old = await self.replay(session, document_id, key, digest)
            if old:
                return old
            if base != head['head_revision_id']:
                raise error(409, 'EDIT_REVISION_CONFLICT', 'A newer version exists; keep your draft and reload')
            if head['head_number'] >= MAX_REVISIONS:
                raise error(429, 'EDIT_REVISION_LIMIT', 'Editing revision limit reached')
            rev_id, number = uuid.uuid4(), head['head_number'] + 1
            result = await self.insert(session, org, user, document, head['source_checksum'], key, digest, rev_id, number, base)
            await session.execute(text('''UPDATE ben.video_edit_documents SET head_revision_id=:revision,
                head_number=:number, updated_at=clock_timestamp() WHERE document_id=:id'''),
                {'revision': rev_id, 'number': number, 'id': document_id})
            return result

    async def read(self, org, user, document_id, revision=None):
        async with self.transaction(org, user) as session:
            head = await self.head(session, org, user, document_id)
            row = (await session.execute(text('''SELECT * FROM ben.video_edit_revisions
                WHERE document_id=:id AND revision_id=:revision'''),
                {'id': document_id, 'revision': revision or head['head_revision_id']})).mappings().first()
            if not row:
                raise unavailable()
            return public_revision(row)

    async def history(self, org, user, document_id, *, before=None, limit=20):
        if not 1 <= limit <= 50 or (before is not None and before < 1):
            raise error(422, 'INVALID_EDIT_CURSOR', 'Invalid history cursor')
        async with self.transaction(org, user) as session:
            head = await self.head(session, org, user, document_id)
            rows = (await session.execute(text('''SELECT revision_id,parent_revision_id,revision_number,created_at
                FROM ben.video_edit_revisions WHERE document_id=:id AND revision_number<:before
                ORDER BY revision_number DESC LIMIT :limit'''),
                {'id': document_id, 'before': before or head['head_number'] + 1, 'limit': limit+1})).mappings().all()
            page = [dict(r) for r in rows[:limit]]
            return {'revisions': page, 'next_before': page[-1]['revision_number'] if len(rows)>limit else None}
