"""Durable file_initial_read jobs, isolated from extraction drain claims.

Extraction claim/reap paths keep recovering extraction jobs only. Initial Read
uses dedicated claim functions so a crash after pending is reaped and retried.
Does not execute the LLM. Does not change Gate 4A flags.
"""

from alembic import op

from database.models import SCHEMA

revision = "030_file_initial_read_jobs"
down_revision = "029_doc_upload_intel_v1"
branch_labels = None
depends_on = None

T = "document_processing_jobs"
SYSTEM_ROLE = "ben_doc_processor"

_HIST_A = "43cef794-1fff-40ae-bd3c-47d9fc121518"
_HIST_B = "0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4"
_DENY = (
    f"c.file_id <> '{_HIST_A}'::uuid AND c.file_id <> '{_HIST_B}'::uuid"
)
_DENY_E = (
    f"e.file_id <> '{_HIST_A}'::uuid AND e.file_id <> '{_HIST_B}'::uuid"
)
_ELIGIBLE = "c.runner_eligible IS TRUE"
_EXTRACTION = (
    "c.job_type IN ('file_extraction', 'structured_extraction')"
)
_IR = "c.job_type = 'file_initial_read'"

_CLAIM_IR = "claim_file_initial_read_jobs"
_CLAIM_IR_FILE = "claim_file_initial_read_job_for_file"
_SYNC_FAILED = "sync_failed_file_initial_reads"
_CLAIM_IR_ARGS = "text, integer, integer"
_CLAIM_IR_FILE_ARGS = "text, integer, uuid"
_SYNC_ARGS = ""


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{SYSTEM_ROLE}') THEN
                CREATE ROLE {SYSTEM_ROLE} NOLOGIN;
            END IF;
        END $$;
        """
    )
    op.execute(f"GRANT {SYSTEM_ROLE} TO CURRENT_USER")

    # Extraction drains must never claim Initial Read jobs (LLM is a separate drain).
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.claim_document_processing_jobs(
            p_worker_id text, p_lease_seconds integer, p_limit integer)
        RETURNS TABLE(job_id uuid, org_id uuid, workspace_id uuid, file_id uuid,
                      job_type text, attempts integer, extraction_version integer,
                      chunking_version integer, lease_expires_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
            UPDATE {SCHEMA}.{T} AS j
               SET status = 'running',
                   attempts = j.attempts + 1,
                   claimed_at = now(),
                   lease_expires_at = now() + make_interval(secs => p_lease_seconds),
                   worker_id = p_worker_id,
                   updated_at = now()
             WHERE j.id IN (
                 SELECT c.id FROM {SCHEMA}.{T} c
                  WHERE c.status = 'queued' AND c.available_at <= now()
                    AND {_ELIGIBLE}
                    AND {_DENY}
                    AND {_EXTRACTION}
                  ORDER BY c.available_at, c.created_at, c.id
                  FOR UPDATE SKIP LOCKED
                  LIMIT p_limit
             )
             RETURNING j.id, j.org_id, j.workspace_id, j.file_id, j.job_type,
                       j.attempts, j.extraction_version, j.chunking_version, j.lease_expires_at;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.claim_document_processing_jobs_for_eligible(
            p_worker_id text, p_lease_seconds integer, p_limit integer)
        RETURNS TABLE(job_id uuid, org_id uuid, workspace_id uuid, file_id uuid,
                      job_type text, attempts integer, extraction_version integer,
                      chunking_version integer, lease_expires_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
            UPDATE {SCHEMA}.{T} AS j
               SET status = 'running',
                   attempts = j.attempts + 1,
                   claimed_at = now(),
                   lease_expires_at = now() + make_interval(secs => p_lease_seconds),
                   worker_id = p_worker_id,
                   updated_at = now()
             WHERE j.id IN (
                 SELECT c.id FROM {SCHEMA}.{T} c
                  WHERE c.status = 'queued' AND c.available_at <= now()
                    AND {_ELIGIBLE}
                    AND {_DENY}
                    AND {_EXTRACTION}
                  ORDER BY c.available_at, c.created_at, c.id
                  FOR UPDATE SKIP LOCKED
                  LIMIT p_limit
             )
             RETURNING j.id, j.org_id, j.workspace_id, j.file_id, j.job_type,
                       j.attempts, j.extraction_version, j.chunking_version, j.lease_expires_at;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.claim_document_processing_job_for_file(
            p_worker_id text, p_lease_seconds integer, p_file_id uuid)
        RETURNS TABLE(job_id uuid, org_id uuid, workspace_id uuid, file_id uuid,
                      job_type text, attempts integer, extraction_version integer,
                      chunking_version integer, lease_expires_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
            UPDATE {SCHEMA}.{T} AS j
               SET status = 'running',
                   attempts = j.attempts + 1,
                   claimed_at = now(),
                   lease_expires_at = now() + make_interval(secs => p_lease_seconds),
                   worker_id = p_worker_id,
                   updated_at = now()
             WHERE j.id IN (
                 SELECT c.id FROM {SCHEMA}.{T} c
                  WHERE c.file_id = p_file_id
                    AND c.status = 'queued' AND c.available_at <= now()
                    AND {_DENY}
                    AND {_EXTRACTION}
                  ORDER BY c.available_at, c.created_at, c.id
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
             )
             RETURNING j.id, j.org_id, j.workspace_id, j.file_id, j.job_type,
                       j.attempts, j.extraction_version, j.chunking_version, j.lease_expires_at;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.claim_document_processing_jobs_for_allowlist(
            p_worker_id text, p_lease_seconds integer, p_limit integer,
            p_file_ids uuid[], p_workspace_ids uuid[])
        RETURNS TABLE(job_id uuid, org_id uuid, workspace_id uuid, file_id uuid,
                      job_type text, attempts integer, extraction_version integer,
                      chunking_version integer, lease_expires_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
            UPDATE {SCHEMA}.{T} AS j
               SET status = 'running',
                   attempts = j.attempts + 1,
                   claimed_at = now(),
                   lease_expires_at = now() + make_interval(secs => p_lease_seconds),
                   worker_id = p_worker_id,
                   updated_at = now()
             WHERE j.id IN (
                 SELECT c.id FROM {SCHEMA}.{T} c
                  WHERE c.status = 'queued' AND c.available_at <= now()
                    AND {_DENY}
                    AND {_EXTRACTION}
                    AND (
                        (p_file_ids IS NOT NULL AND cardinality(p_file_ids) > 0
                         AND c.file_id = ANY(p_file_ids))
                        OR
                        (p_workspace_ids IS NOT NULL AND cardinality(p_workspace_ids) > 0
                         AND c.workspace_id = ANY(p_workspace_ids))
                    )
                  ORDER BY c.available_at, c.created_at, c.id
                  FOR UPDATE SKIP LOCKED
                  LIMIT p_limit
             )
             RETURNING j.id, j.org_id, j.workspace_id, j.file_id, j.job_type,
                       j.attempts, j.extraction_version, j.chunking_version, j.lease_expires_at;
        $$;
        """
    )

    # Chat-originated files may not be runner_eligible. Recovery must still
    # claim their Initial Read jobs after a crash; do not filter on eligible.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_CLAIM_IR}(
            p_worker_id text, p_lease_seconds integer, p_limit integer)
        RETURNS TABLE(job_id uuid, org_id uuid, workspace_id uuid, file_id uuid,
                      job_type text, attempts integer, extraction_version integer,
                      chunking_version integer, lease_expires_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
            UPDATE {SCHEMA}.{T} AS j
               SET status = 'running',
                   attempts = j.attempts + 1,
                   claimed_at = now(),
                   lease_expires_at = now() + make_interval(secs => p_lease_seconds),
                   worker_id = p_worker_id,
                   updated_at = now()
             WHERE j.id IN (
                 SELECT c.id FROM {SCHEMA}.{T} c
                  WHERE c.status = 'queued' AND c.available_at <= now()
                    AND {_IR}
                    AND {_DENY}
                  ORDER BY c.available_at, c.created_at, c.id
                  FOR UPDATE SKIP LOCKED
                  LIMIT p_limit
             )
             RETURNING j.id, j.org_id, j.workspace_id, j.file_id, j.job_type,
                       j.attempts, j.extraction_version, j.chunking_version, j.lease_expires_at;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_CLAIM_IR_FILE}(
            p_worker_id text, p_lease_seconds integer, p_file_id uuid)
        RETURNS TABLE(job_id uuid, org_id uuid, workspace_id uuid, file_id uuid,
                      job_type text, attempts integer, extraction_version integer,
                      chunking_version integer, lease_expires_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
            UPDATE {SCHEMA}.{T} AS j
               SET status = 'running',
                   attempts = j.attempts + 1,
                   claimed_at = now(),
                   lease_expires_at = now() + make_interval(secs => p_lease_seconds),
                   worker_id = p_worker_id,
                   updated_at = now()
             WHERE j.id IN (
                 SELECT c.id FROM {SCHEMA}.{T} c
                  WHERE c.file_id = p_file_id
                    AND c.status = 'queued' AND c.available_at <= now()
                    AND {_IR}
                    AND {_DENY}
                  ORDER BY c.available_at, c.created_at, c.id
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
             )
             RETURNING j.id, j.org_id, j.workspace_id, j.file_id, j.job_type,
                       j.attempts, j.extraction_version, j.chunking_version, j.lease_expires_at;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_SYNC_FAILED}()
        RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
        DECLARE n integer;
        BEGIN
            UPDATE {SCHEMA}.workspace_files wf
               SET initial_read_status = 'failed',
                   initial_read_at = now(),
                   updated_at = now()
             WHERE wf.initial_read_status IN ('none', 'pending')
               AND wf.status = 'ready'
               AND EXISTS (
                    SELECT 1 FROM {SCHEMA}.{T} j
                     WHERE j.file_id = wf.id
                       AND j.org_id = wf.org_id
                       AND j.workspace_id = wf.workspace_id
                       AND j.job_type = 'file_initial_read'
                       AND j.status = 'failed'
               )
               AND NOT EXISTS (
                    SELECT 1 FROM {SCHEMA}.{T} j
                     WHERE j.file_id = wf.id
                       AND j.org_id = wf.org_id
                       AND j.workspace_id = wf.workspace_id
                       AND j.job_type = 'file_initial_read'
                       AND j.status IN ('queued', 'running')
               );
            GET DIAGNOSTICS n = ROW_COUNT;
            RETURN n;
        END;
        $$;
        """
    )

    for fn, args in (
        (_CLAIM_IR, _CLAIM_IR_ARGS),
        (_CLAIM_IR_FILE, _CLAIM_IR_FILE_ARGS),
        (_SYNC_FAILED, _SYNC_ARGS),
    ):
        op.execute(f"ALTER FUNCTION {SCHEMA}.{fn}({args}) OWNER TO {SYSTEM_ROLE}")
        op.execute(f"REVOKE ALL ON FUNCTION {SCHEMA}.{fn}({args}) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {SCHEMA}.{fn}({args}) TO {SYSTEM_ROLE}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {SCHEMA}.{fn}({args}) TO CURRENT_USER")

    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
          FOR r IN
            SELECT DISTINCT grantee
              FROM information_schema.routine_privileges
             WHERE routine_schema = '{SCHEMA}'
               AND routine_name = 'claim_document_processing_jobs'
               AND privilege_type = 'EXECUTE'
               AND grantee <> 'PUBLIC'
          LOOP
            EXECUTE format(
              'GRANT EXECUTE ON FUNCTION {SCHEMA}.{_CLAIM_IR}({_CLAIM_IR_ARGS}) TO %I',
              r.grantee);
            EXECUTE format(
              'GRANT EXECUTE ON FUNCTION {SCHEMA}.{_CLAIM_IR_FILE}({_CLAIM_IR_FILE_ARGS}) TO %I',
              r.grantee);
            EXECUTE format(
              'GRANT EXECUTE ON FUNCTION {SCHEMA}.{_SYNC_FAILED}() TO %I',
              r.grantee);
          END LOOP;
        END $$;
        """
    )
    op.execute(f"REVOKE {SYSTEM_ROLE} FROM CURRENT_USER")


def downgrade() -> None:
    op.execute(f"GRANT {SYSTEM_ROLE} TO CURRENT_USER")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_SYNC_FAILED}()")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_CLAIM_IR_FILE}({_CLAIM_IR_FILE_ARGS})")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_CLAIM_IR}({_CLAIM_IR_ARGS})")
    op.execute(f"REVOKE {SYSTEM_ROLE} FROM CURRENT_USER")
