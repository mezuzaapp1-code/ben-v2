"""Immutable planning versions; separate from paid execution lifecycle."""
from alembic import op

revision = '038_production_plans'
down_revision = '037_short_render'
branch_labels = depends_on = None


def upgrade():
    op.execute('''CREATE TABLE ben.media_plan_versions (
        id uuid PRIMARY KEY, plan_id uuid NOT NULL, version integer NOT NULL CHECK(version>0),
        org_id uuid NOT NULL, created_by varchar(256) NOT NULL,
        conversation_id uuid NOT NULL REFERENCES ben.threads(id) ON DELETE CASCADE,
        parent_version_id uuid UNIQUE REFERENCES ben.media_plan_versions(id),
        idempotency_key varchar(128) NOT NULL,
        request_hash varchar(64) NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
        payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
        asset_snapshot jsonb NOT NULL CHECK(jsonb_typeof(asset_snapshot)='array'),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(plan_id,version), UNIQUE(org_id,created_by,idempotency_key),
        CHECK ((version=1) = (parent_version_id IS NULL)))''')
    op.execute('CREATE INDEX ix_media_plan_owner ON ben.media_plan_versions(org_id,created_by,conversation_id)')
    op.execute('ALTER TABLE ben.media_plan_versions ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE ben.media_plan_versions FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY media_plan_org ON ben.media_plan_versions
        USING(org_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid)
        WITH CHECK(org_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid)""")
    op.execute('''CREATE FUNCTION ben.guard_media_plan_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'Plan versions are immutable'; END IF;
          IF NEW.parent_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM ben.media_plan_versions p WHERE p.id=NEW.parent_version_id
            AND p.plan_id=NEW.plan_id AND p.org_id=NEW.org_id AND p.created_by=NEW.created_by
            AND p.conversation_id=NEW.conversation_id AND NEW.version=p.version+1
          ) THEN RAISE EXCEPTION 'Invalid plan lineage'; END IF;
          RETURN NEW;
        END $$''')
    op.execute('''CREATE TRIGGER guard_media_plan_version BEFORE INSERT OR UPDATE
        ON ben.media_plan_versions FOR EACH ROW EXECUTE FUNCTION ben.guard_media_plan_version()''')


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM ben.media_plan_versions) THEN RAISE EXCEPTION 'Plan history must be retained'; END IF; END $$")
    op.execute('DROP TABLE ben.media_plan_versions')
    op.execute('DROP FUNCTION ben.guard_media_plan_version()')
