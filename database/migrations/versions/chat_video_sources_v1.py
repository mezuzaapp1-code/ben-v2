"""Immutable conversation-owned video originals, independent of projects."""
from alembic import op
revision = 'chat_video_sources_v1'
down_revision = 'video_edit_documents_v1'
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""CREATE TABLE ben.chat_video_sources (
        id uuid PRIMARY KEY, org_id uuid NOT NULL,
        conversation_id uuid NOT NULL REFERENCES ben.threads(id),
        created_by varchar(256) NOT NULL CHECK (length(created_by)>0),
        storage_key text NOT NULL UNIQUE,
        checksum varchar(64) NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'),
        byte_size integer NOT NULL CHECK(byte_size>0 AND byte_size<=67108864),
        created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute('CREATE INDEX ix_chat_video_owner ON ben.chat_video_sources(org_id,created_by,conversation_id)')
    op.execute('ALTER TABLE ben.chat_video_sources ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE ben.chat_video_sources FORCE ROW LEVEL SECURITY')
    condition = """org_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND created_by=NULLIF(current_setting('app.current_user_id',true),'')
        AND EXISTS (SELECT 1 FROM ben.threads t WHERE t.id=conversation_id AND t.org_id=chat_video_sources.org_id)"""
    op.execute(f'CREATE POLICY chat_video_owner ON ben.chat_video_sources USING ({condition}) WITH CHECK ({condition})')
    op.execute("""CREATE FUNCTION ben.guard_chat_video_source() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Video originals are immutable'; END $$""")
    op.execute('CREATE TRIGGER guard_chat_video_source BEFORE UPDATE OR DELETE ON ben.chat_video_sources FOR EACH ROW EXECUTE FUNCTION ben.guard_chat_video_source()')

def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM ben.chat_video_sources) THEN RAISE EXCEPTION 'Video originals must be retained'; END IF; END $$")
    op.execute('DROP TABLE ben.chat_video_sources')
    op.execute('DROP FUNCTION ben.guard_chat_video_source()')
