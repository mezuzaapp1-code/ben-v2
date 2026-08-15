"""Additive file-id-scoped claim/reap for document_processing_jobs.

Operators need to drain one authorized WorkspaceFile without claiming the
generic queue (a historical queued PDF sits ahead of canaries). This revision
adds two SECURITY DEFINER functions that reuse the Gate 3A lease/attempt
semantics and touch ONLY rows for the requested file_id.

Does NOT replace or alter claim_document_processing_jobs /
reap_expired_document_processing_jobs. Generic drain stays unchanged.
"""

from alembic import op

from database.models import SCHEMA

revision = "025_claim_job_for_file"
down_revision = "024_doc_processing_jobs"
branch_labels = None
depends_on = None

T = "document_processing_jobs"
SYSTEM_ROLE = "ben_doc_processor"

_CLAIM_FOR_FILE = "claim_document_processing_job_for_file"
_REAP_FOR_FILE = "reap_expired_document_processing_jobs_for_file"
_CLAIM_ARGS = "text, integer, uuid"
_REAP_ARGS = "uuid, integer, integer"


def upgrade() -> None:
    # 024 creates this role; keep the create idempotent so the additive
    # functions can be applied on a DB that already has the 024 tables.
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

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_CLAIM_FOR_FILE}(
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
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_REAP_FOR_FILE}(
            p_file_id uuid,
            p_base_seconds integer DEFAULT 30, p_cap_seconds integer DEFAULT 3600)
        RETURNS TABLE(job_id uuid, outcome text)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
        BEGIN
            RETURN QUERY
            WITH expired AS (
                SELECT e.id, e.attempts, e.max_attempts
                  FROM {SCHEMA}.{T} e
                 WHERE e.file_id = p_file_id
                   AND e.status = 'running' AND e.lease_expires_at < now()
                 ORDER BY e.lease_expires_at
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
            ), upd AS (
                UPDATE {SCHEMA}.{T} j
                   SET status = CASE WHEN e.attempts >= j.max_attempts THEN 'failed' ELSE 'queued' END,
                       available_at = CASE WHEN e.attempts >= j.max_attempts THEN j.available_at
                            ELSE now() + make_interval(secs => LEAST(
                                 p_cap_seconds,
                                 (p_base_seconds * power(2, GREATEST(e.attempts - 1, 0)))::int)) END,
                       claimed_at = NULL,
                       lease_expires_at = NULL,
                       worker_id = NULL,
                       last_error_code = CASE WHEN e.attempts >= j.max_attempts
                            THEN 'max_attempts_exceeded' ELSE 'lease_expired' END,
                       last_error_detail = 'reaped expired lease',
                       updated_at = now()
                  FROM expired e
                 WHERE j.id = e.id
                 RETURNING j.id, j.status
            )
            SELECT upd.id, upd.status FROM upd;
        END;
        $$;
        """
    )

    for fn, args in ((_CLAIM_FOR_FILE, _CLAIM_ARGS), (_REAP_FOR_FILE, _REAP_ARGS)):
        op.execute(f"ALTER FUNCTION {SCHEMA}.{fn}({args}) OWNER TO {SYSTEM_ROLE}")
        op.execute(f"REVOKE ALL ON FUNCTION {SCHEMA}.{fn}({args}) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {SCHEMA}.{fn}({args}) TO {SYSTEM_ROLE}")
        # Same DATABASE_URL role that already invokes the generic claim/reap.
        op.execute(f"GRANT EXECUTE ON FUNCTION {SCHEMA}.{fn}({args}) TO CURRENT_USER")

    # Copy any extra EXECUTE grants already present on the generic claim.
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
              'GRANT EXECUTE ON FUNCTION {SCHEMA}.{_CLAIM_FOR_FILE}({_CLAIM_ARGS}) TO %I',
              r.grantee);
            EXECUTE format(
              'GRANT EXECUTE ON FUNCTION {SCHEMA}.{_REAP_FOR_FILE}({_REAP_ARGS}) TO %I',
              r.grantee);
          END LOOP;
        END $$;
        """
    )

    op.execute(f"REVOKE {SYSTEM_ROLE} FROM CURRENT_USER")


def downgrade() -> None:
    op.execute(f"GRANT {SYSTEM_ROLE} TO CURRENT_USER")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_REAP_FOR_FILE}({_REAP_ARGS})")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_CLAIM_FOR_FILE}({_CLAIM_ARGS})")
    op.execute(f"REVOKE {SYSTEM_ROLE} FROM CURRENT_USER")
