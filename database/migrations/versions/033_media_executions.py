"""Media V1 operational state and one generated resource per execution.

Additive: no existing ownership or accounting tables are changed. Destination
selectors are not authority: the runtime must authorize them before insertion
and again before publication. No FK to threads: BEN also has SQLite threads,
and deleting a destination must not erase an in-flight paid operation.
Provider references, request payloads and storage keys are backend-only.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database.models import SCHEMA

revision = "033_media_executions"
down_revision = "032_measurement_foundation_v1"
branch_labels = None
depends_on = None
TABLE = "media_executions"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("execution_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", sa.String(256), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True)),
        sa.Column("conversation_id", sa.String(128)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_payload", JSONB(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("provider_operation_ref", sa.Text()),
        sa.Column("provider_state", sa.String(64)),
        sa.Column("provider_output", JSONB()),
        sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submit_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("poll_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ingest_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_polled_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("resource_id", UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(1024)),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("byte_size", sa.BigInteger()),
        sa.Column("checksum", sa.String(64)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("usage_dimensions", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("estimated_cost", sa.Numeric(20, 8)),
        sa.Column("pricing_version", sa.String(128)),
        sa.Column("actual_charge", sa.Numeric(20, 8)),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "idempotency_key", name="uq_media_org_idempotency"),
        sa.UniqueConstraint("resource_id", name="uq_media_resource"),
        sa.CheckConstraint("state IN ('pending','submitting','submitted','running','ingesting',"
                           "'succeeded','submission_unknown','failed','expired')", name="ck_media_state"),
        sa.CheckConstraint("operation IN ('image_generation','image_to_video')", name="ck_media_operation"),
        sa.CheckConstraint("workspace_id IS NOT NULL OR conversation_id IS NOT NULL", name="ck_media_destination"),
        sa.CheckConstraint("conversation_id IS NULL OR length(trim(conversation_id)) > 0", name="ck_media_conversation"),
        sa.CheckConstraint("length(trim(created_by)) > 0 AND length(trim(idempotency_key)) > 0 "
                           "AND length(trim(provider)) > 0 AND length(trim(model)) > 0", name="ck_media_identity"),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_media_fingerprint"),
        sa.CheckConstraint("checksum IS NULL OR checksum ~ '^[0-9a-f]{64}$'", name="ck_media_checksum"),
        sa.CheckConstraint("jsonb_typeof(request_payload) = 'object' AND jsonb_typeof(usage_dimensions) = 'object'",
                           name="ck_media_json_objects"),
        sa.CheckConstraint("version >= 0 AND submit_attempts >= 0 AND poll_attempts >= 0 AND ingest_attempts >= 0",
                           name="ck_media_counters"),
        sa.CheckConstraint("(lease_owner IS NULL) = (lease_expires_at IS NULL)", name="ck_media_lease"),
        sa.CheckConstraint("deadline_at > created_at", name="ck_media_deadline"),
        sa.CheckConstraint("byte_size IS NULL OR byte_size > 0", name="ck_media_byte_size"),
        sa.CheckConstraint("(estimated_cost IS NULL OR (estimated_cost >= 0 AND pricing_version IS NOT NULL)) "
                           "AND (actual_charge IS NULL OR actual_charge >= 0)", name="ck_media_cost"),
        sa.CheckConstraint("state NOT IN ('submitted','running') OR provider_operation_ref IS NOT NULL",
                           name="ck_media_submitted_ref"),
        sa.CheckConstraint("state <> 'succeeded' OR (storage_key IS NOT NULL AND length(storage_key) > 0 "
                           "AND mime_type IS NOT NULL AND byte_size IS NOT NULL AND checksum IS NOT NULL "
                           "AND published_at IS NOT NULL)", name="ck_media_success_output"),
        schema=SCHEMA,
    )
    op.create_index("ix_media_due", TABLE, ["next_reconcile_at"], schema=SCHEMA,
                    postgresql_where=sa.text("state IN ('pending','submitting','submitted','running','ingesting','submission_unknown')"))
    op.create_index("ix_media_org_conversation", TABLE, ["org_id", "conversation_id", "created_at"], schema=SCHEMA)
    op.create_index("ix_media_org_workspace", TABLE, ["org_id", "workspace_id", "created_at"], schema=SCHEMA)
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY media_executions_org_isolation ON {SCHEMA}.{TABLE}
        USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)""")


def downgrade() -> None:
    # Intended for disposable tests / approved rollback only: contains paid-job history.
    op.drop_table(TABLE, schema=SCHEMA)
