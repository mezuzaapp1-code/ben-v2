"""Allowlisted claim/reap + job stats for the document-processing runner.

Additive SECURITY DEFINER functions. Does not alter generic FIFO
claim_document_processing_jobs or the file-id-scoped 025 functions.
Empty allowlists claim/reap nothing (fail-closed at SQL).
"""

from alembic import op

from database.models import SCHEMA

revision = "026_claim_jobs_for_allowlist"
down_revision = "025_claim_job_for_file"
branch_labels = None
depends_on = None

T = "document_processing_jobs"
SYSTEM_ROLE = "ben_doc_processor"

_CLAIM = "claim_document_processing_jobs_for_allowlist"
_REAP = "reap_expired_document_processing_jobs_for_allowlist"
_STATS = "document_processing_job_stats"
_CLAIM_ARGS = "text, integer, integer, uuid[], uuid[]"
_REAP_ARGS = "uuid[], uuid[], integer, integer, integer"
_STATS_ARGS = ""


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

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_CLAIM}(
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

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_REAP}(
            p_file_ids uuid[], p_workspace_ids uuid[],
            p_base_seconds integer DEFAULT 30, p_cap_seconds integer DEFAULT 3600,
            p_limit integer DEFAULT 100)
        RETURNS TABLE(job_id uuid, outcome text)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
        BEGIN
            IF (p_file_ids IS NULL OR cardinality(p_file_ids) = 0)
               AND (p_workspace_ids IS NULL OR cardinality(p_workspace_ids) = 0) THEN
                RETURN;
            END IF;
            RETURN QUERY
            WITH expired AS (
                SELECT e.id, e.attempts, e.max_attempts
                  FROM {SCHEMA}.{T} e
                 WHERE e.status = 'running' AND e.lease_expires_at < now()
                   AND (
                        (p_file_ids IS NOT NULL AND cardinality(p_file_ids) > 0
                         AND e.file_id = ANY(p_file_ids))
                        OR
                        (p_workspace_ids IS NOT NULL AND cardinality(p_workspace_ids) > 0
                         AND e.workspace_id = ANY(p_workspace_ids))
                   )
                 ORDER BY e.lease_expires_at
                 FOR UPDATE SKIP LOCKED
                 LIMIT p_limit
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

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_STATS}()
        RETURNS TABLE(
            due_queue_depth bigint,
            oldest_due_queued_age_s double precision,
            running_count bigint,
            failed_count bigint,
            retry_count bigint,
            succeeded_24h bigint
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
            SELECT
                (SELECT count(*) FROM {SCHEMA}.{T}
                  WHERE status = 'queued' AND available_at <= now())::bigint,
                (SELECT EXTRACT(EPOCH FROM (now() - min(available_at)))
                   FROM {SCHEMA}.{T}
                  WHERE status = 'queued' AND available_at <= now()),
                (SELECT count(*) FROM {SCHEMA}.{T} WHERE status = 'running')::bigint,
                (SELECT count(*) FROM {SCHEMA}.{T} WHERE status = 'failed')::bigint,
                (SELECT count(*) FROM {SCHEMA}.{T}
                  WHERE status = 'queued' AND attempts > 0)::bigint,
                (SELECT count(*) FROM {SCHEMA}.{T}
                  WHERE status = 'succeeded' AND updated_at >= now() - interval '24 hours')::bigint;
        $$;
        """
    )

    for fn, args in ((_CLAIM, _CLAIM_ARGS), (_REAP, _REAP_ARGS), (_STATS, _STATS_ARGS)):
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
              'GRANT EXECUTE ON FUNCTION {SCHEMA}.{_CLAIM}({_CLAIM_ARGS}) TO %I',
              r.grantee);
            EXECUTE format(
              'GRANT EXECUTE ON FUNCTION {SCHEMA}.{_REAP}({_REAP_ARGS}) TO %I',
              r.grantee);
            EXECUTE format(
              'GRANT EXECUTE ON FUNCTION {SCHEMA}.{_STATS}() TO %I',
              r.grantee);
          END LOOP;
        END $$;
        """
    )

    op.execute(f"REVOKE {SYSTEM_ROLE} FROM CURRENT_USER")


def downgrade() -> None:
    op.execute(f"GRANT {SYSTEM_ROLE} TO CURRENT_USER")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_STATS}()")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_REAP}({_REAP_ARGS})")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_CLAIM}({_CLAIM_ARGS})")
    op.execute(f"REVOKE {SYSTEM_ROLE} FROM CURRENT_USER")
