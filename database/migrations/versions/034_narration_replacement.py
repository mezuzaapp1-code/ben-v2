"""Allow the bounded local composer without weakening provider state checks."""
from alembic import op

revision = "034_narration_replacement"
down_revision = "033_media_executions"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_media_operation", "media_executions", schema="ben")
    op.create_check_constraint("ck_media_operation", "media_executions",
        "operation IN ('image_generation','image_to_video','narration_replacement')", schema="ben")
    op.drop_constraint("ck_media_submitted_ref", "media_executions", schema="ben")
    op.create_check_constraint("ck_media_submitted_ref", "media_executions",
        "state NOT IN ('submitted','running') OR provider_operation_ref IS NOT NULL "
        "OR (operation='narration_replacement' AND state='running')", schema="ben")
    op.create_check_constraint("ck_media_local_identity", "media_executions",
        "operation <> 'narration_replacement' OR (provider='local_composer' "
        "AND model='ffmpeg_stream_copy' AND provider_operation_ref IS NULL "
        "AND state IN ('pending','running','ingesting','succeeded','failed','expired'))", schema="ben")


def downgrade():
    # Intentionally fails if local executions remain; never destroys their history.
    op.drop_constraint("ck_media_local_identity", "media_executions", schema="ben")
    op.drop_constraint("ck_media_submitted_ref", "media_executions", schema="ben")
    op.create_check_constraint("ck_media_submitted_ref", "media_executions",
        "state NOT IN ('submitted','running') OR provider_operation_ref IS NOT NULL", schema="ben")
    op.drop_constraint("ck_media_operation", "media_executions", schema="ben")
    op.create_check_constraint("ck_media_operation", "media_executions",
        "operation IN ('image_generation','image_to_video')", schema="ben")
