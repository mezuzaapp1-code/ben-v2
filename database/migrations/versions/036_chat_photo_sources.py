"""Conversation-owned photo originals, independent of projects."""
from alembic import op
revision = '036_chat_photo_sources'
down_revision = '035_mobile_video_import'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('''CREATE TABLE ben.chat_photo_sources (
        id uuid PRIMARY KEY, org_id uuid NOT NULL,
        conversation_id uuid NOT NULL REFERENCES ben.threads(id) ON DELETE CASCADE,
        created_by varchar(256) NOT NULL, storage_key text NOT NULL,
        checksum varchar(64) NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
        byte_size integer NOT NULL CHECK (byte_size > 0 AND byte_size <= 20971520),
        created_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('CREATE INDEX ix_chat_photos_owner ON ben.chat_photo_sources(org_id,conversation_id,created_by)')
    op.execute('ALTER TABLE ben.chat_photo_sources ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE ben.chat_photo_sources FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY chat_photos_org ON ben.chat_photo_sources
        USING (org_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid)""")

def downgrade():
    # Never silently delete users' uploaded originals or ownership records.
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM ben.chat_photo_sources) THEN RAISE EXCEPTION 'Photo sources must be retained'; END IF; END $$")
    op.execute('DROP TABLE ben.chat_photo_sources')
