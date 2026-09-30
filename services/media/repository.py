"""Durable, tenant-bound operations on approved migration 033 only."""
from contextlib import asynccontextmanager
import json
import uuid

from fastapi import HTTPException
from sqlalchemy import text
from services.media.contracts import GEMINI_IMAGE_MODEL, BFL_IMAGE_MODEL, VEO_VIDEO_MODEL, KLING_VIDEO_MODEL

ACTIVE = ("pending", "submitting", "submitted", "running", "ingesting", "submission_unknown")


class MediaRepository:
    def __init__(self, session_factory=None):
        if session_factory is None:
            from services.media.connection import get_media_session
            session_factory = get_media_session
        self.sessions = session_factory

    @asynccontextmanager
    async def transaction(self, org):
        async with self.sessions() as session:
            async with session.begin():
                bypass = await session.scalar(text(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"))
                if bypass is not False:
                    raise HTTPException(503, "Media database role unavailable")
                await session.execute(text("SELECT set_config('app.current_org_id', :org, true)"),
                                      {"org": str(org)})
                yield session

    async def destination(self, session, org, conversation):
        # Match BEN conversation ownership; lock against deletion until commit.
        found = await session.scalar(text("SELECT id FROM ben.threads WHERE id=:id AND org_id=:org FOR KEY SHARE"),
                                     {"id": uuid.UUID(str(conversation)), "org": org})
        if found is None:
            raise HTTPException(404, "Conversation not found")

    async def create(self, org, user, key, snapshot, fingerprint):
        async with self.transaction(org) as s:
            await self.destination(s, org, snapshot["destination"]["conversation_id"])
            # Serialize org admission for both duplicate keys and bounded spend.
            await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:org, 0))"), {"org": str(org)})
            old = (await s.execute(text("SELECT * FROM ben.media_executions WHERE org_id=:org AND idempotency_key=:key"),
                                   {"org": org, "key": key})).mappings().first()
            if old:
                if old["created_by"] != user or old["request_fingerprint"] != fingerprint:
                    raise HTTPException(409, "Idempotency key conflict")
                return dict(old)
            count = await s.scalar(text("SELECT count(*) FROM ben.media_executions WHERE org_id=:org AND created_at > now()-interval '24 hours'"), {"org": org})
            if count >= 20:
                raise HTTPException(429, "Internal media daily limit reached")
            row = (await s.execute(text("""INSERT INTO ben.media_executions
                (execution_id,org_id,created_by,conversation_id,idempotency_key,request_fingerprint,
                 request_payload,provider,model,operation,deadline_at,resource_id)
                VALUES (:id,:org,:user,:conversation,:key,:fingerprint,CAST(:payload AS jsonb),
                        :provider,:model,:operation,now()+interval '30 minutes',:resource)
                RETURNING *"""), {"id": uuid.uuid4(), "org": org, "user": user,
                "conversation": snapshot["destination"]["conversation_id"], "key": key,
                "fingerprint": fingerprint, "payload": json.dumps(snapshot), "model": snapshot["model"], "provider": snapshot["provider"],
                "operation": snapshot["operation"], "resource": uuid.uuid4()})).mappings().one()
            return dict(row)

    async def read(self, org, user, *, execution=None, resource=None, conversation=None):
        async with self.transaction(org) as s:
            params = {"org": org, "user": user}
            clause = "org_id=:org AND created_by=:user AND deleted_at IS NULL"
            for col, value in (("execution_id", execution), ("resource_id", resource), ("conversation_id", conversation)):
                if value is not None:
                    clause += f" AND {col}=:{col}"
                    params[col] = value
            rows = (await s.execute(text(f"SELECT * FROM ben.media_executions WHERE {clause} ORDER BY created_at DESC LIMIT 50"), params)).mappings().all()
            if conversation:
                await self.destination(s, org, conversation)
                return [dict(r) for r in rows]
            if not rows:
                raise HTTPException(404, "Media not found")
            await self.destination(s, org, rows[0]["conversation_id"])
            return dict(rows[0])

    async def claim(self, org, owner, *, local=False, mobile=False):
        async with self.transaction(org) as s:
            row = (await s.execute(text("""SELECT * FROM ben.media_executions
                WHERE org_id=:org AND state IN ('pending','submitting','submitted','running','ingesting','submission_unknown')
                AND (((provider='google' AND model=:model OR provider='bfl' AND model=:bfl_model) AND operation='image_generation')
                     OR (provider='google' AND model=:veo_model AND operation='image_to_video')
                     OR (provider='fal' AND model=:kling_model AND operation='image_to_video')
                     OR (:local AND provider='local_composer' AND model='ffmpeg_stream_copy' AND operation='narration_replacement')
                     OR (:mobile AND provider='local_composer' AND model='ffmpeg_mobile_v1' AND operation='video_import'))
                AND next_reconcile_at <= now() AND (lease_expires_at IS NULL OR lease_expires_at < now())
                ORDER BY next_reconcile_at FOR UPDATE SKIP LOCKED LIMIT 1"""),
                {"mobile": mobile, "local": local, "org": org, "model": GEMINI_IMAGE_MODEL, "bfl_model": BFL_IMAGE_MODEL, "veo_model": VEO_VIDEO_MODEL, "kling_model": KLING_VIDEO_MODEL})).mappings().first()
            if not row:
                return None
            row = (await s.execute(text("""UPDATE ben.media_executions SET lease_owner=:owner,
                lease_expires_at=now()+CASE WHEN operation='video_import' THEN interval '5 minutes' WHEN provider='bfl' OR operation IN ('image_to_video','narration_replacement') THEN interval '2 minutes' ELSE interval '10 minutes' END,
                version=version+1, updated_at=now()
                WHERE execution_id=:id AND org_id=:org RETURNING *"""),
                {"owner": owner, "id": row["execution_id"], "org": org})).mappings().one()
            return dict(row)

    async def change(self, row, **values):
        allowed = {"state", "submit_attempts", "ingest_attempts", "error_code", "provider_operation_ref",
                   "provider_output", "usage_dimensions", "estimated_cost", "pricing_version", "actual_charge",
                   "storage_key", "mime_type", "byte_size", "checksum", "published_at", "next_reconcile_at",
                   "provider_state", "poll_attempts", "last_polled_at"}
        if set(values) - allowed:
            raise ValueError("invalid media mutation")
        assignments, params = [], {"id": row["execution_id"], "org": row["org_id"],
                                   "owner": row["lease_owner"], "version": row["version"]}
        for key, value in values.items():
            if key in ("provider_output", "usage_dimensions"):
                value = json.dumps(value)
                assignments.append(f"{key}=CAST(:{key} AS jsonb)")
            else:
                assignments.append(f"{key}=:{key}")
            params[key] = value
        async with self.transaction(row["org_id"]) as s:
            if values.get("state") == "succeeded":
                await self.destination(s, row["org_id"], row["conversation_id"])
            result = (await s.execute(text(f"""UPDATE ben.media_executions SET {','.join(assignments)},
                version=version+1,updated_at=now(),lease_owner=NULL,lease_expires_at=NULL
                WHERE execution_id=:id AND org_id=:org AND lease_owner=:owner AND version=:version
                RETURNING *"""), params)).mappings().first()
            return dict(result) if result else None

    async def complete_attempt(self, row, stored):
        """Choose already durable bytes. No filesystem I/O under the row lock.

Only the new internal attempt path uses this stricter publication gate. Existing
provider lifecycle methods remain unchanged. A lost commit reply is not retried.
        """
        from services.media.attempt_storage import resolve_attempt_key
        async with self.transaction(row["org_id"]) as s:
            current = (await s.execute(text("""SELECT * FROM ben.media_executions
                WHERE org_id=:org AND execution_id=:id FOR UPDATE"""),
                {"org": row["org_id"], "id": row["execution_id"]})).mappings().first()
            if (not current or current["state"] != "ingesting" or current["deleted_at"] is not None
                    or current["version"] != row["version"]
                    or current["lease_owner"] != row["lease_owner"] or not current["lease_owner"]
                    or any(current[k] != row[k] for k in ("resource_id", "created_by", "request_fingerprint"))):
                return None
            if current["operation"] not in ("image_to_video", "narration_replacement", "video_import") or stored.mime_type != "video/mp4":
                raise ValueError("MP4 attempt requires video execution")
            resolve_attempt_key(current["org_id"], current["resource_id"],
                                current["execution_id"], stored.storage_key)
            await self.destination(s, current["org_id"], current["conversation_id"])
            # Check wall clock AFTER acquiring row and destination locks.
            result = (await s.execute(text("""UPDATE ben.media_executions SET
                state='succeeded',version=version+1,storage_key=:key,mime_type=:mime,
                byte_size=:size,checksum=:checksum,published_at=clock_timestamp(),
                updated_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL,
                ingest_attempts=ingest_attempts+1,error_code=NULL
                WHERE execution_id=:id AND org_id=:org AND version=:version
                AND lease_owner=:owner AND state='ingesting'
                AND lease_expires_at > clock_timestamp() AND deadline_at > clock_timestamp()
                RETURNING *"""), {"id": current["execution_id"], "org": current["org_id"],
                "version": row["version"], "owner": row["lease_owner"], "key": stored.storage_key,
                "mime": stored.mime_type, "size": stored.byte_size, "checksum": stored.checksum})).mappings().first()
            return dict(result) if result else None

    async def local_transition(self, row, state):
        """Keep ownership through local work; expired attempts cannot advance."""
        if state not in ("running", "ingesting"):
            raise ValueError("invalid local transition")
        async with self.transaction(row["org_id"]) as s:
            result = (await s.execute(text("""UPDATE ben.media_executions
                SET state=:state, version=version+1, updated_at=clock_timestamp()
                WHERE org_id=:org AND execution_id=:id AND version=:version
                AND lease_owner=:owner AND lease_expires_at > clock_timestamp()
                AND deadline_at > clock_timestamp() AND deleted_at IS NULL
                AND provider='local_composer' AND ((operation='narration_replacement'
                AND model='ffmpeg_stream_copy') OR (operation='video_import' AND model='ffmpeg_mobile_v1')) AND state IN ('pending','running','ingesting')
                RETURNING *"""), {"state": state, "org": row["org_id"], "id": row["execution_id"],
                "version": row["version"], "owner": row["lease_owner"]})).mappings().first()
            return dict(result) if result else None

    async def mark_submitting(self, row):
        # Keep lease through the sole external submission; commit BEFORE network I/O.
        async with self.transaction(row["org_id"]) as s:
            await self.destination(s, row["org_id"], row["conversation_id"])
            result = (await s.execute(text("""UPDATE ben.media_executions SET state='submitting',
                submit_attempts=submit_attempts+1,version=version+1,updated_at=now()
                WHERE execution_id=:id AND org_id=:org AND lease_owner=:owner AND version=:version
                  AND state='pending' AND submit_attempts=0 RETURNING *"""),
                {"id": row["execution_id"], "org": row["org_id"], "owner": row["lease_owner"],
                 "version": row["version"]})).mappings().first()
            return dict(result) if result else None

    async def record_result(self, row, result, observation):
        """Keep returned usage/provenance even when later byte publication fails."""
        async with self.transaction(row["org_id"]) as s:
            result_row = (await s.execute(text("""UPDATE ben.media_executions SET state='ingesting',
                provider_operation_ref=:ref,provider_output=CAST(:observation AS jsonb),
                usage_dimensions=CAST(:usage AS jsonb),version=version+1,updated_at=now()
                WHERE execution_id=:id AND org_id=:org AND lease_owner=:owner AND version=:version
                RETURNING *"""), {"ref": result.operation_ref, "observation": json.dumps(observation),
                    "usage": json.dumps(result.usage), "id": row["execution_id"], "org": row["org_id"],
                    "owner": row["lease_owner"], "version": row["version"]})).mappings().first()
            return dict(result_row) if result_row else None

    async def record_poll(self, row, *, status, output, usage):
        """Persist private reconciliation evidence before downloading; keep fencing lease."""
        async with self.transaction(row["org_id"]) as s:
            result = (await s.execute(text("""UPDATE ben.media_executions SET state='running',
                provider_state=:status,provider_output=CAST(:output AS jsonb),usage_dimensions=CAST(:usage AS jsonb),
                poll_attempts=poll_attempts+1,last_polled_at=now(),version=version+1,updated_at=now(),
                lease_expires_at=now()+interval '2 minutes'
                WHERE execution_id=:id AND org_id=:org AND lease_owner=:owner AND version=:version
                RETURNING *"""), {"status": status, "output": json.dumps(output), "usage": json.dumps(usage),
                    "id": row["execution_id"], "org": row["org_id"], "owner": row["lease_owner"], "version": row["version"]})).mappings().first()
            return dict(result) if result else None

    async def evaluate(self, org, user, execution, observation):
        async with self.transaction(org) as s:
            row = (await s.execute(text("""SELECT * FROM ben.media_executions WHERE execution_id=:id
                AND org_id=:org AND created_by=:user AND deleted_at IS NULL FOR UPDATE"""),
                {"id": execution, "org": org, "user": user})).mappings().first()
            if not row or row["state"] != "succeeded":
                raise HTTPException(404, "Media not found")
            await self.destination(s, org, row["conversation_id"])
            await s.execute(text("""UPDATE ben.media_executions SET provider_output=jsonb_set(
                COALESCE(provider_output,'{}'::jsonb),'{evaluation}',CAST(:evaluation AS jsonb)),
                version=version+1,updated_at=now() WHERE execution_id=:id AND org_id=:org"""),
                {"evaluation": json.dumps(observation), "id": execution, "org": org})
