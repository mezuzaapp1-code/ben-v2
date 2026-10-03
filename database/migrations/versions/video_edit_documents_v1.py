"""Private video editing history; independent of executions and plan revisions.

Based on main's 036. Reconcile the Alembic parent before merging another branch
that extends 036; do not reuse a migration number reserved by an open PR.
"""
from alembic import op

revision = 'video_edit_documents_v1'
down_revision = '036_chat_photo_sources'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE ben.video_edit_documents (
        document_id uuid PRIMARY KEY,
        org_id uuid NOT NULL,
        created_by varchar(256) NOT NULL CHECK (length(created_by)>0),
        resource_id uuid NOT NULL REFERENCES ben.media_executions(resource_id),
        source_checksum varchar(64) NOT NULL CHECK (source_checksum ~ '^[0-9a-f]{64}$'),
        duration_seconds double precision NOT NULL CHECK (duration_seconds>0 AND duration_seconds<=30),
        head_revision_id uuid NOT NULL,
        head_number integer NOT NULL CHECK (head_number>0),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(document_id,org_id,created_by))''')
    op.execute('''CREATE TABLE ben.video_edit_revisions (
        revision_id uuid PRIMARY KEY,
        document_id uuid NOT NULL,
        org_id uuid NOT NULL,
        created_by varchar(256) NOT NULL,
        revision_number integer NOT NULL CHECK (revision_number>0),
        parent_revision_id uuid,
        idempotency_key varchar(128) NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9_-]{1,128}$'),
        request_fingerprint varchar(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        source_checksum varchar(64) NOT NULL CHECK (source_checksum ~ '^[0-9a-f]{64}$'),
        document jsonb NOT NULL CHECK (jsonb_typeof(document)='object' AND octet_length(document::text)<=1100000),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY(document_id,org_id,created_by)
            REFERENCES ben.video_edit_documents(document_id,org_id,created_by),
        UNIQUE(document_id,revision_id),
        UNIQUE(document_id,revision_id,revision_number),
        UNIQUE(document_id,revision_number),
        UNIQUE(document_id,idempotency_key),
        UNIQUE(document_id,parent_revision_id),
        FOREIGN KEY(document_id,parent_revision_id)
            REFERENCES ben.video_edit_revisions(document_id,revision_id),
        CHECK ((revision_number=1) = (parent_revision_id IS NULL)))''')
    op.execute('''ALTER TABLE ben.video_edit_documents ADD CONSTRAINT fk_video_edit_head
        FOREIGN KEY(document_id,head_revision_id,head_number)
        REFERENCES ben.video_edit_revisions(document_id,revision_id,revision_number)
        DEFERRABLE INITIALLY DEFERRED''')
    op.execute('CREATE INDEX ix_video_edit_owner ON ben.video_edit_documents(org_id,created_by,updated_at,document_id)')
    owner = """org_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND created_by = NULLIF(current_setting('app.current_user_id',true),'')"""
    source = """EXISTS (SELECT 1 FROM ben.media_executions m JOIN ben.threads t
        ON t.id::text=m.conversation_id AND t.org_id=m.org_id
        WHERE m.resource_id=video_edit_documents.resource_id
        AND m.org_id=video_edit_documents.org_id AND m.created_by=video_edit_documents.created_by
        AND m.state='succeeded' AND m.deleted_at IS NULL AND m.mime_type='video/mp4'
        AND m.checksum=video_edit_documents.source_checksum)"""
    for table, condition in (
        ('video_edit_documents', f'{owner} AND {source}'),
        ('video_edit_revisions', f'''{owner} AND EXISTS (SELECT 1 FROM ben.video_edit_documents d
            WHERE d.document_id=video_edit_revisions.document_id
            AND d.org_id=video_edit_revisions.org_id AND d.created_by=video_edit_revisions.created_by)'''),
    ):
        op.execute(f'ALTER TABLE ben.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE ben.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {table}_owner ON ben.{table} USING ({condition}) WITH CHECK ({condition})')
    op.execute('''CREATE FUNCTION ben.guard_video_edit_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$ DECLARE d ben.video_edit_documents; BEGIN
        IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Editing revisions are immutable'; END IF;
        SELECT * INTO d FROM ben.video_edit_documents WHERE document_id=NEW.document_id FOR UPDATE;
        IF NOT FOUND OR NEW.source_checksum IS DISTINCT FROM d.source_checksum
          OR NEW.document->>'document_id' IS DISTINCT FROM d.document_id::text
          OR NEW.document->>'schema_version' IS DISTINCT FROM 'video-edit-v1'
          OR NEW.document->>'kind' IS DISTINCT FROM 'video'
          OR NEW.document->'source'->>'resource_id' IS DISTINCT FROM d.resource_id::text
          OR (NEW.document->'source'->>'duration_seconds')::double precision IS DISTINCT FROM d.duration_seconds
          OR (NEW.revision_number=1 AND (d.head_number<>1 OR d.head_revision_id<>NEW.revision_id))
          OR (NEW.revision_number>1 AND (NEW.parent_revision_id<>d.head_revision_id OR NEW.revision_number<>d.head_number+1))
        THEN RAISE EXCEPTION 'Invalid editing revision'; END IF;
        RETURN NEW; END $$''')
    op.execute('''CREATE TRIGGER guard_video_edit_revision BEFORE INSERT OR UPDATE OR DELETE
        ON ben.video_edit_revisions FOR EACH ROW EXECUTE FUNCTION ben.guard_video_edit_revision()''')
    op.execute('''CREATE FUNCTION ben.guard_video_edit_document() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Editing history must be retained'; END IF;
        IF (NEW.document_id,NEW.org_id,NEW.created_by,NEW.resource_id,NEW.source_checksum,NEW.duration_seconds,NEW.created_at)
            IS DISTINCT FROM
           (OLD.document_id,OLD.org_id,OLD.created_by,OLD.resource_id,OLD.source_checksum,OLD.duration_seconds,OLD.created_at)
          OR NEW.head_number<>OLD.head_number+1
          OR NOT EXISTS (SELECT 1 FROM ben.video_edit_revisions r WHERE r.document_id=OLD.document_id
              AND r.revision_id=NEW.head_revision_id AND r.parent_revision_id=OLD.head_revision_id
              AND r.revision_number=NEW.head_number)
        THEN RAISE EXCEPTION 'Invalid editing head change'; END IF;
        RETURN NEW; END $$''')
    op.execute('''CREATE TRIGGER guard_video_edit_document BEFORE UPDATE OR DELETE
        ON ben.video_edit_documents FOR EACH ROW EXECUTE FUNCTION ben.guard_video_edit_document()''')


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM ben.video_edit_documents)
        THEN RAISE EXCEPTION 'Editing history must be retained'; END IF; END $$""")
    op.execute('ALTER TABLE ben.video_edit_documents DROP CONSTRAINT fk_video_edit_head')
    op.execute('DROP TABLE ben.video_edit_revisions')
    op.execute('DROP TABLE ben.video_edit_documents')
    op.execute('DROP FUNCTION ben.guard_video_edit_revision()')
    op.execute('DROP FUNCTION ben.guard_video_edit_document()')
