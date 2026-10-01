"""Bounded local video imports; preserve all existing publication/RLS checks."""
from alembic import op

revision = '035_mobile_video_import'
down_revision = '034_narration_replacement'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_media_operation', 'media_executions', schema='ben')
    op.create_check_constraint('ck_media_operation', 'media_executions',
        "operation IN ('image_generation','image_to_video','narration_replacement','video_import')", schema='ben')
    op.drop_constraint('ck_media_submitted_ref', 'media_executions', schema='ben')
    op.create_check_constraint('ck_media_submitted_ref', 'media_executions',
        "state NOT IN ('submitted','running') OR provider_operation_ref IS NOT NULL "
        "OR (operation IN ('narration_replacement','video_import') AND state='running')", schema='ben')
    op.create_check_constraint('ck_media_import_identity', 'media_executions',
        "operation <> 'video_import' OR (provider='local_composer' AND model='ffmpeg_mobile_v1' "
        "AND provider_operation_ref IS NULL AND state IN ('pending','running','ingesting','succeeded','failed','expired'))", schema='ben')


def downgrade():
    # Fails transactionally if imports remain: never discard their history.
    op.drop_constraint('ck_media_import_identity', 'media_executions', schema='ben')
    op.drop_constraint('ck_media_operation', 'media_executions', schema='ben')
    op.create_check_constraint('ck_media_operation', 'media_executions',
        "operation IN ('image_generation','image_to_video','narration_replacement')", schema='ben')
    op.drop_constraint('ck_media_submitted_ref', 'media_executions', schema='ben')
    op.create_check_constraint('ck_media_submitted_ref', 'media_executions',
        "state NOT IN ('submitted','running') OR provider_operation_ref IS NOT NULL "
        "OR (operation='narration_replacement' AND state='running')", schema='ben')
