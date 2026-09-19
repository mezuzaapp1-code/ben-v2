"""Measurement Foundation V1: execution_events and validation_records.

Additive. Does not alter inference_call_records or ledger semantics.
No foreign keys to the product registry or call rows.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database.models import SCHEMA

revision = "032_measurement_foundation_v1"
down_revision = "031_workspace_file_evidence_ir"
branch_labels = None
depends_on = None

EVENTS = "execution_events"
VALIDATIONS = "validation_records"


def _enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_org_isolation ON {SCHEMA}.{table}
        USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        EVENTS,
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("call_id", sa.String(length=64), nullable=True),
        sa.Column("result_id", sa.String(length=64), nullable=True),
        sa.Column("test_id", sa.String(length=128), nullable=True),
        sa.Column("test_version", sa.String(length=64), nullable=True),
        sa.Column("test_run_id", sa.String(length=64), nullable=True),
        sa.Column("question_id", sa.String(length=64), nullable=True),
        sa.Column("section_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at_missing_reason", sa.String(length=128), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("payload_version", sa.String(length=32), nullable=False),
        sa.Column("sequence_in_execution", sa.Integer(), nullable=True),
        sa.Column("frozen_input_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("conditions_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("requested_configuration_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("effective_configuration_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("dispatch_payload_digest", sa.String(length=128), nullable=True),
        sa.Column(
            "fingerprint_algorithm",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'sha256'"),
        ),
        sa.Column(
            "canonicalization_version",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'json-v1'"),
        ),
        sa.Column("content_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ("
            "'execution_started','call_attempted','call_completed',"
            "'first_output_observed','execution_completed','result_recorded',"
            "'observation','telemetry_incomplete','provenance_correction')",
            name="ck_execution_events_event_type",
        ),
        sa.CheckConstraint(
            "origin IN ('operational','controlled_experiment','unknown')",
            name="ck_execution_events_origin",
        ),
        sa.CheckConstraint(
            "(observed_at IS NULL) = (observed_at_missing_reason IS NOT NULL)",
            name="ck_execution_events_observed_at_reason",
        ),
        sa.CheckConstraint(
            "sequence_in_execution IS NULL OR sequence_in_execution >= 0",
            name="ck_execution_events_sequence",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_execution_events_org_execution",
        EVENTS,
        ["org_id", "execution_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_execution_events_org_recorded",
        EVENTS,
        ["org_id", "recorded_at"],
        schema=SCHEMA,
    )
    op.create_index("ix_execution_events_event_type", EVENTS, ["event_type"], schema=SCHEMA)
    op.create_index(
        "ix_execution_events_frozen_input_fp",
        EVENTS,
        ["frozen_input_fingerprint"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_execution_events_test_run_question",
        EVENTS,
        ["test_run_id", "question_id"],
        schema=SCHEMA,
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.tg_execution_events_origin_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(
            hashtext(NEW.org_id::text),
            hashtext(NEW.execution_id)
          );
          IF EXISTS (
            SELECT 1 FROM {SCHEMA}.execution_events e
            WHERE e.org_id = NEW.org_id
              AND e.execution_id = NEW.execution_id
              AND e.event_id <> NEW.event_id
              AND e.origin IS DISTINCT FROM NEW.origin
          ) THEN
            RAISE EXCEPTION 'execution origin conflict'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE CONSTRAINT TRIGGER trg_execution_events_origin_guard
        AFTER INSERT ON {SCHEMA}.{EVENTS}
        DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW
        EXECUTE PROCEDURE {SCHEMA}.tg_execution_events_origin_guard()
        """
    )

    op.create_table(
        VALIDATIONS,
        sa.Column("validation_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("result_id", sa.String(length=64), nullable=True),
        sa.Column("result_integrity_digest", sa.String(length=128), nullable=True),
        sa.Column("validator_type", sa.String(length=64), nullable=False),
        sa.Column("validator_version", sa.String(length=64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_at_missing_reason", sa.String(length=128), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Numeric(18, 8), nullable=True),
        sa.Column("score_meaning", sa.Text(), nullable=True),
        sa.Column("payload_version", sa.String(length=32), nullable=False),
        sa.Column("judge_execution_id", sa.String(length=64), nullable=True),
        sa.Column("test_run_id", sa.String(length=64), nullable=True),
        sa.Column("question_id", sa.String(length=64), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('pass','fail','invalid','pending')",
            name="ck_validation_records_outcome",
        ),
        sa.CheckConstraint(
            "(evaluated_at IS NULL) = (evaluated_at_missing_reason IS NOT NULL)",
            name="ck_validation_records_evaluated_at_reason",
        ),
        sa.CheckConstraint(
            "score IS NULL OR score_meaning IS NOT NULL",
            name="ck_validation_records_score_meaning",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_validation_records_org_execution",
        VALIDATIONS,
        ["org_id", "execution_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_validation_records_result_id",
        VALIDATIONS,
        ["result_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_validation_records_test_run",
        VALIDATIONS,
        ["test_run_id"],
        schema=SCHEMA,
    )

    _enable_tenant_rls(EVENTS)
    _enable_tenant_rls(VALIDATIONS)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {VALIDATIONS}_org_isolation ON {SCHEMA}.{VALIDATIONS}")
    op.execute(f"DROP POLICY IF EXISTS {EVENTS}_org_isolation ON {SCHEMA}.{EVENTS}")
    op.drop_index("ix_validation_records_test_run", table_name=VALIDATIONS, schema=SCHEMA)
    op.drop_index("ix_validation_records_result_id", table_name=VALIDATIONS, schema=SCHEMA)
    op.drop_index("ix_validation_records_org_execution", table_name=VALIDATIONS, schema=SCHEMA)
    op.drop_table(VALIDATIONS, schema=SCHEMA)
    op.execute(f"DROP TRIGGER IF EXISTS trg_execution_events_origin_guard ON {SCHEMA}.{EVENTS}")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.tg_execution_events_origin_guard()")
    op.drop_index("ix_execution_events_test_run_question", table_name=EVENTS, schema=SCHEMA)
    op.drop_index("ix_execution_events_frozen_input_fp", table_name=EVENTS, schema=SCHEMA)
    op.drop_index("ix_execution_events_event_type", table_name=EVENTS, schema=SCHEMA)
    op.drop_index("ix_execution_events_org_recorded", table_name=EVENTS, schema=SCHEMA)
    op.drop_index("ix_execution_events_org_execution", table_name=EVENTS, schema=SCHEMA)
    op.drop_table(EVENTS, schema=SCHEMA)
