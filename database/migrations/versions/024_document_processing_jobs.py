"""Document Intelligence — Gate 3A durable orchestration substrate.

Adds ben.document_processing_jobs: a durable, tenant-isolated job ledger for the
future extraction processor. Purely orchestration state — NO document text/bytes,
NO extraction is executed, NO upload wiring. Additive and inert.

Security model (see docs/threat model in the Gate 3A PR):
- RLS ENABLE + FORCE with org isolation on app.current_org_id for product sessions.
- A dedicated NOLOGIN role `ben_doc_processor` is the *system processing identity*.
  A permissive policy grants it cross-org access; product roles never match it.
- Cross-org claim/reaper/requeue/complete are SECURITY DEFINER functions OWNED BY
  `ben_doc_processor`, returning only job identity/scheduling metadata (no content).
- Tenant integrity of the denormalized (org_id, workspace_id, file_id) triple is
  DB-enforced by a COMPOSITE FK to workspace_files(id, org_id, workspace_id), so a
  job can never reference a file owned by a different org/workspace. The composite
  FK also provides ON DELETE CASCADE when the WorkspaceFile is removed.

Production prerequisite: creating the role requires CREATEROLE/superuser. On a
managed DB where the app role lacks it, an operator pre-creates
`ben_doc_processor` (NOLOGIN) before applying this migration.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from database.models import SCHEMA

revision = "024_doc_processing_jobs"
down_revision = "023_doc_intel_foundation"
branch_labels = None
depends_on = None

T = "document_processing_jobs"
SYSTEM_ROLE = "ben_doc_processor"

_STATUSES = "('queued','running','succeeded','failed','cancelled')"


def upgrade() -> None:
    # --- 0. System processing identity (NOLOGIN). Idempotent. ---
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

    # --- 1. Composite-unique on workspace_files to back the tenant-integrity FK. ---
    # Additive only; does not touch any lifecycle column or data.
    op.create_unique_constraint(
        "uq_workspace_files_id_org_workspace",
        "workspace_files",
        ["id", "org_id", "workspace_id"],
        schema=SCHEMA,
    )

    # --- 2. Durable job ledger. ---
    op.create_table(
        T,
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False, server_default="structured_extraction"),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("extraction_version", sa.Integer(), nullable=False),
        sa.Column("chunking_version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        # Tenant integrity + cascade: (file_id, org_id, workspace_id) must be a real
        # WorkspaceFile ownership tuple. Prevents org A / ws A / file-of-org-B.
        sa.ForeignKeyConstraint(
            ["file_id", "org_id", "workspace_id"],
            [
                f"{SCHEMA}.workspace_files.id",
                f"{SCHEMA}.workspace_files.org_id",
                f"{SCHEMA}.workspace_files.workspace_id",
            ],
            ondelete="CASCADE",
            name="fk_doc_processing_jobs_file_owner",
        ),
        sa.CheckConstraint(f"status IN {_STATUSES}", name="ck_doc_processing_jobs_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_doc_processing_jobs_attempts"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_doc_processing_jobs_max_attempts"),
        sa.CheckConstraint("char_length(job_type) > 0", name="ck_doc_processing_jobs_job_type"),
        # State-machine invariants:
        sa.CheckConstraint(
            "status <> 'running' OR (claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND worker_id IS NOT NULL)",
            name="ck_doc_processing_jobs_running_lease",
        ),
        sa.CheckConstraint(
            "status <> 'queued' OR (claimed_at IS NULL AND lease_expires_at IS NULL "
            "AND worker_id IS NULL)",
            name="ck_doc_processing_jobs_queued_clear",
        ),
        schema=SCHEMA,
    )

    # --- 3. Deduplication: at most one active job per (file, type, versions). ---
    op.create_index(
        "uq_doc_processing_jobs_active",
        T,
        ["file_id", "job_type", "extraction_version", "chunking_version"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    # --- 4. Claim / reaper / tenant / file indexes. ---
    op.create_index(
        "ix_doc_processing_jobs_claim",
        T,
        ["available_at", "created_at", "id"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_doc_processing_jobs_lease",
        T,
        ["lease_expires_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index("ix_doc_processing_jobs_org_workspace", T, ["org_id", "workspace_id"], schema=SCHEMA)
    op.create_index("ix_doc_processing_jobs_file", T, ["file_id"], schema=SCHEMA)

    # --- 5. RLS: FORCE org isolation for product; permissive cross-org for system role. ---
    op.execute(f"ALTER TABLE {SCHEMA}.{T} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{T} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {T}_org_isolation ON {SCHEMA}.{T}
        USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """
    )
    op.execute(
        f"""
        CREATE POLICY {T}_system ON {SCHEMA}.{T}
        FOR ALL TO {SYSTEM_ROLE}
        USING (true) WITH CHECK (true)
        """
    )
    # System role needs table + schema privileges (RLS still constrains it to its policy).
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {SYSTEM_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {SCHEMA}.{T} TO {SYSTEM_ROLE}")

    # --- 6. SECURITY DEFINER system functions, OWNED BY the system role. ---
    # Transient membership lets the migrating role transfer ownership; revoked after.
    op.execute(f"GRANT {SYSTEM_ROLE} TO CURRENT_USER")

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
        CREATE OR REPLACE FUNCTION {SCHEMA}.requeue_document_processing_job(
            p_job_id uuid, p_delay_seconds integer,
            p_error_code text DEFAULT NULL, p_error_detail text DEFAULT NULL)
        RETURNS TABLE(job_id uuid, status text, available_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
            UPDATE {SCHEMA}.{T} j
               SET status = 'queued',
                   available_at = now() + make_interval(secs => GREATEST(p_delay_seconds, 0)),
                   claimed_at = NULL, lease_expires_at = NULL, worker_id = NULL,
                   last_error_code = p_error_code, last_error_detail = p_error_detail,
                   updated_at = now()
             WHERE j.id = p_job_id AND j.status = 'running'
             RETURNING j.id, j.status, j.available_at;
        $$;
        """
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.complete_document_processing_job(
            p_job_id uuid, p_outcome text,
            p_error_code text DEFAULT NULL, p_error_detail text DEFAULT NULL)
        RETURNS TABLE(job_id uuid, status text)
        LANGUAGE sql SECURITY DEFINER SET search_path = {SCHEMA}, pg_temp AS $$
            UPDATE {SCHEMA}.{T} j
               SET status = p_outcome,
                   claimed_at = NULL, lease_expires_at = NULL, worker_id = NULL,
                   last_error_code = CASE WHEN p_outcome = 'failed' THEN p_error_code ELSE NULL END,
                   last_error_detail = CASE WHEN p_outcome = 'failed' THEN p_error_detail ELSE NULL END,
                   updated_at = now()
             WHERE j.id = p_job_id AND j.status = 'running'
               AND p_outcome IN ('succeeded', 'failed', 'cancelled')
             RETURNING j.id, j.status;
        $$;
        """
    )

    for fn, args in (
        ("claim_document_processing_jobs", "text, integer, integer"),
        ("reap_expired_document_processing_jobs", "integer, integer, integer"),
        ("requeue_document_processing_job", "uuid, integer, text, text"),
        ("complete_document_processing_job", "uuid, text, text, text"),
    ):
        op.execute(f"ALTER FUNCTION {SCHEMA}.{fn}({args}) OWNER TO {SYSTEM_ROLE}")
        # Execute only for the system identity — product roles cannot invoke cross-org ops.
        op.execute(f"REVOKE ALL ON FUNCTION {SCHEMA}.{fn}({args}) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {SCHEMA}.{fn}({args}) TO {SYSTEM_ROLE}")

    # Remove latent membership so product/app role never matches the system policy.
    op.execute(f"REVOKE {SYSTEM_ROLE} FROM CURRENT_USER")


def downgrade() -> None:
    # Re-grant membership so the migrating role may drop role-owned functions.
    op.execute(f"GRANT {SYSTEM_ROLE} TO CURRENT_USER")
    for fn, args in (
        ("claim_document_processing_jobs", "text, integer, integer"),
        ("reap_expired_document_processing_jobs", "integer, integer, integer"),
        ("requeue_document_processing_job", "uuid, integer, text, text"),
        ("complete_document_processing_job", "uuid, text, text, text"),
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{fn}({args})")

    op.execute(f"DROP POLICY IF EXISTS {T}_system ON {SCHEMA}.{T}")
    op.execute(f"DROP POLICY IF EXISTS {T}_org_isolation ON {SCHEMA}.{T}")
    op.drop_index("ix_doc_processing_jobs_file", table_name=T, schema=SCHEMA)
    op.drop_index("ix_doc_processing_jobs_org_workspace", table_name=T, schema=SCHEMA)
    op.drop_index("ix_doc_processing_jobs_lease", table_name=T, schema=SCHEMA)
    op.drop_index("ix_doc_processing_jobs_claim", table_name=T, schema=SCHEMA)
    op.drop_index("uq_doc_processing_jobs_active", table_name=T, schema=SCHEMA)
    op.drop_table(T, schema=SCHEMA)
    op.drop_constraint("uq_workspace_files_id_org_workspace", "workspace_files", schema=SCHEMA, type_="unique")

    op.execute(f"REVOKE {SYSTEM_ROLE} FROM CURRENT_USER")
    # Remove any residual privileges/ownership so the role can be dropped cleanly.
    op.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA {SCHEMA} FROM {SYSTEM_ROLE}")
    op.execute(f"DROP OWNED BY {SYSTEM_ROLE}")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{SYSTEM_ROLE}') THEN
                DROP ROLE {SYSTEM_ROLE};
            END IF;
        END $$;
        """
    )
