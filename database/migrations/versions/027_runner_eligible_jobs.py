"""Persisted runner eligibility for automatic new-file ingest.

Adds runner_eligible (default FALSE so existing queued rows stay quarantined),
eligible claim/reap functions, a denylist trigger for two historical file IDs,
and patches every claim/reap path so those IDs cannot be claimed.
Does not enable CLAIM_GLOBAL. Does not change Gate 4A flags.
"""

from alembic import op

from database.models import SCHEMA

revision = "027_runner_eligible_jobs"
down_revision = "026_claim_jobs_for_allowlist"
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
_ELIGIBLE_E = "e.runner_eligible IS TRUE"

_CLAIM_ELIGIBLE = "claim_document_processing_jobs_for_eligible"
_REAP_ELIGIBLE = "reap_expired_document_processing_jobs_for_eligible"
_CLAIM_ELIGIBLE_ARGS = "text, integer, integer"
_REAP_ELIGIBLE_ARGS = "integer, integer, integer"
_TG_FN = "tg_document_processing_jobs_quarantine"
_TG = "trg_document_processing_jobs_quarantine"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.{T}
            ADD COLUMN IF NOT EXISTS runner_eligible boolean
            NOT NULL DEFAULT false
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_doc_processing_jobs_eligible_claim
            ON {SCHEMA}.{T} (available_at, created_at, id)
            WHERE status = 'queued' AND runner_eligible IS TRUE
        """
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_TG_FN}()
        RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.file_id IN (
                '{_HIST_A}'::uuid,
                '{_HIST_B}'::uuid
            ) THEN
                NEW.runner_eligible := false;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS {_TG} ON {SCHEMA}.{T}")
    op.execute(
        f"""
        CREATE TRIGGER {_TG}
            BEFORE INSERT OR UPDATE ON {SCHEMA}.{T}
            FOR EACH ROW
            EXECUTE PROCEDURE {SCHEMA}.{_TG_FN}()
        """
    )

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
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_CLAIM_ELIGIBLE}(
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
        CREATE OR REPLACE FUNCTION {SCHEMA}.{_REAP_ELIGIBLE}(
            p_base_seconds integer DEFAULT 30, p_cap_seconds integer DEFAULT 3600,
            p_limit integer DEFAULT 100)
        RETURNS TABLE(job_id uuid, outcome text)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
        BEGIN
            RETURN QUERY
            WITH expired AS (
                SELECT e.id, e.attempts, e.max_attempts
                  FROM {SCHEMA}.{T} e
                 WHERE e.status = 'running' AND e.lease_expires_at < now()
                   AND {_ELIGIBLE_E}
                   AND {_DENY_E}
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

    # Patch generic FIFO: never claim ineligible or denylisted historical jobs.
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
        CREATE OR REPLACE FUNCTION {SCHEMA}.reap_expired_document_processing_jobs(
            p_base_seconds integer DEFAULT 30, p_cap_seconds integer DEFAULT 3600,
            p_limit integer DEFAULT 100)
        RETURNS TABLE(job_id uuid, outcome text)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
        BEGIN
            RETURN QUERY
            WITH expired AS (
                SELECT e.id, e.attempts, e.max_attempts
                  FROM {SCHEMA}.{T} e
                 WHERE e.status = 'running' AND e.lease_expires_at < now()
                   AND {_ELIGIBLE_E}
                   AND {_DENY_E}
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
                    AND {_ELIGIBLE}
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
        CREATE OR REPLACE FUNCTION {SCHEMA}.reap_expired_document_processing_jobs_for_file(
            p_file_id uuid,
            p_base_seconds integer DEFAULT 30, p_cap_seconds integer DEFAULT 3600)
        RETURNS TABLE(job_id uuid, outcome text)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
        BEGIN
            IF p_file_id IN ('{_HIST_A}'::uuid, '{_HIST_B}'::uuid) THEN
                RETURN;
            END IF;
            RETURN QUERY
            WITH expired AS (
                SELECT e.id, e.attempts, e.max_attempts
                  FROM {SCHEMA}.{T} e
                 WHERE e.file_id = p_file_id
                   AND e.status = 'running' AND e.lease_expires_at < now()
                   AND {_ELIGIBLE_E}
                   AND {_DENY_E}
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
                    AND {_ELIGIBLE}
                    AND {_DENY}
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
        CREATE OR REPLACE FUNCTION {SCHEMA}.reap_expired_document_processing_jobs_for_allowlist(
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
                   AND {_ELIGIBLE_E}
                   AND {_DENY_E}
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

    for fn, args in (
        (_CLAIM_ELIGIBLE, _CLAIM_ELIGIBLE_ARGS),
        (_REAP_ELIGIBLE, _REAP_ELIGIBLE_ARGS),
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
              'GRANT EXECUTE ON FUNCTION {SCHEMA}.{_CLAIM_ELIGIBLE}({_CLAIM_ELIGIBLE_ARGS}) TO %I',
              r.grantee);
            EXECUTE format(
              'GRANT EXECUTE ON FUNCTION {SCHEMA}.{_REAP_ELIGIBLE}({_REAP_ELIGIBLE_ARGS}) TO %I',
              r.grantee);
          END LOOP;
        END $$;
        """
    )

    op.execute(f"REVOKE {SYSTEM_ROLE} FROM CURRENT_USER")


def downgrade() -> None:
    op.execute(f"GRANT {SYSTEM_ROLE} TO CURRENT_USER")
    op.execute(f"DROP TRIGGER IF EXISTS {_TG} ON {SCHEMA}.{T}")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_TG_FN}()")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_REAP_ELIGIBLE}({_REAP_ELIGIBLE_ARGS})")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{_CLAIM_ELIGIBLE}({_CLAIM_ELIGIBLE_ARGS})")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_doc_processing_jobs_eligible_claim")
    op.execute(
        f"ALTER TABLE {SCHEMA}.{T} DROP COLUMN IF EXISTS runner_eligible"
    )
    op.execute(f"REVOKE {SYSTEM_ROLE} FROM CURRENT_USER")
