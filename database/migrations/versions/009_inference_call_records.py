"""Pass 1: append-only inference_call_records accounting ledger."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database.models import SCHEMA

revision = "009_inference_call_records"
down_revision = "008_news_claims_e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inference_call_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=128), nullable=True),
        sa.Column("workspace_id", sa.String(length=128), nullable=True),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("capability_key", sa.String(length=64), nullable=True),
        sa.Column("pipeline", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("api_model", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("stream", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("usage_status", sa.String(length=16), nullable=False, server_default=sa.text("'missing'")),
        sa.Column("cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("cost_status", sa.String(length=16), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("pricing_version", sa.String(length=64), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("provider_request_id", sa.String(length=128), nullable=True),
        sa.Column("finish_reason", sa.String(length=64), nullable=True),
        sa.Column("error_class", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extras", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "outcome IN ("
            "'success','error','timeout','client_disconnect','stream_interrupted','rejected')",
            name="ck_inference_call_records_outcome",
        ),
        sa.CheckConstraint(
            "usage_status IN ('exact','estimated','missing')",
            name="ck_inference_call_records_usage_status",
        ),
        sa.CheckConstraint(
            "cost_status IN ('priced','unknown','unpriced','zero')",
            name="ck_inference_call_records_cost_status",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inference_call_records_request_id",
        "inference_call_records",
        ["request_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inference_call_records_execution_id",
        "inference_call_records",
        ["execution_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inference_call_records_org_started",
        "inference_call_records",
        ["org_id", "started_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inference_call_records_workspace_started",
        "inference_call_records",
        ["workspace_id", "started_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inference_call_records_provider_model",
        "inference_call_records",
        ["provider", "model"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_inference_call_records_provider_model", table_name="inference_call_records", schema=SCHEMA)
    op.drop_index("ix_inference_call_records_workspace_started", table_name="inference_call_records", schema=SCHEMA)
    op.drop_index("ix_inference_call_records_org_started", table_name="inference_call_records", schema=SCHEMA)
    op.drop_index("ix_inference_call_records_execution_id", table_name="inference_call_records", schema=SCHEMA)
    op.drop_index("ix_inference_call_records_request_id", table_name="inference_call_records", schema=SCHEMA)
    op.drop_table("inference_call_records", schema=SCHEMA)
