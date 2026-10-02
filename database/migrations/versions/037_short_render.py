"""Bounded managed rendering on existing media ownership and RLS."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '037_short_render'
down_revision = '036_chat_photo_sources'
branch_labels = depends_on = None


def upgrade():
    op.add_column('media_executions', sa.Column('short_quote_id', UUID()), schema='ben')
    op.add_column('media_executions', sa.Column('reserved_cost', sa.Numeric(20,8)), schema='ben')
    op.add_column('media_executions', sa.Column('short_telemetry', JSONB()), schema='ben')
    op.create_unique_constraint('uq_short_quote', 'media_executions', ['org_id','short_quote_id'], schema='ben')
    op.drop_constraint('ck_media_operation', 'media_executions', schema='ben')
    op.create_check_constraint('ck_media_operation', 'media_executions',
        "operation IN ('image_generation','image_to_video','narration_replacement','video_import','short_render')", schema='ben')
    op.create_check_constraint('ck_short_identity', 'media_executions',
        "operation <> 'short_render' OR (provider='creatomate' AND model='fixed_5_scene_v1' "
        "AND short_quote_id IS NOT NULL AND reserved_cost IS NOT NULL AND estimated_cost IS NOT NULL AND reserved_cost > 0 AND estimated_cost >= 0 "
        "AND reserved_cost >= estimated_cost AND short_telemetry IS NOT NULL "
        "AND jsonb_typeof(short_telemetry)='object' AND submit_attempts <= 1)", schema='ben')


def downgrade():
    # Never erase reservations or reconciliation history.
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM ben.media_executions WHERE operation='short_render') "
               "THEN RAISE EXCEPTION 'short render history must be retained'; END IF; END $$")
    op.drop_constraint('ck_short_identity', 'media_executions', schema='ben')
    op.drop_constraint('uq_short_quote', 'media_executions', schema='ben')
    op.drop_constraint('ck_media_operation', 'media_executions', schema='ben')
    op.create_check_constraint('ck_media_operation', 'media_executions',
        "operation IN ('image_generation','image_to_video','narration_replacement','video_import')", schema='ben')
    for name in ('short_quote_id','reserved_cost','short_telemetry'):
        op.drop_column('media_executions', name, schema='ben')
