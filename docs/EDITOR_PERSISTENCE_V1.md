# Private video editing persistence (B02)

Scope: save/load immutable editing revisions for an already authorized BEN MP4.
No media generation, rendering, provider retry, project creation or UI integration.
The server gate is `BEN_MEDIA_EDIT_DOCUMENTS_ENABLED=1` **and** the existing internal
media principal allowlist. Default is off. Existing routes remain unchanged.

## Storage decision

The verified main checkout has no general creative-document revision store.
`workspace_files` and its evidence/chunk/processing tables represent uploaded
files and extraction; `knowledge_objects` represents knowledge; execution events
and media executions represent work/accounting. None has the head/immutable
revision/concurrency contract. Add `video_edit_documents` and
`video_edit_revisions` separately. Do not put mutable edits in paid executions.

Migration `video_edit_documents_v1` extends main's `036_chat_photo_sources`.
This descriptive revision deliberately does not reserve a numbered migration used
on other open branches. Reconcile its Alembic parent with the actual merge target
before merging; do not deploy competing heads as an accidental migration fork.

Documents inherit source ownership and conversation. A manually created project
is not required. The source ID, checksum, duration, owner and creation timestamp
are immutable. Source availability and ownership are checked on every operation.
Deleted sources, deleted conversations and changed checksums hide all history.
Document ownership is per user **and** organization, not merely per organization.

## API

All routes are under `/api/media/edit-documents`; responses are private/no-store.
Invalid content is redacted. The request stream is capped at 1,000,000 bytes
before JSON parsing (including the save envelope). Unknown/unauthorized documents
return the same `404 EDIT_DOCUMENT_UNAVAILABLE` response. Pilot denial uses the
existing `MEDIA_UNAVAILABLE` envelope.
PostgreSQL JSONB's unsupported U+0000 character is rejected with 422 before
storage. Literal backslash-u text remains valid. Database failures return a
redacted 503, never a false save acknowledgement; clients retain the draft and
the original save key. Transaction lock/statement timeouts are 5/10 seconds.

- `POST /`: the `video-edit-v1` document, with `Idempotency-Key` header. Returns
  revision 1, HTTP 201. The client supplies a UUID document ID so retries keep
  the same identity. Server ownership comes only from authenticated context.
- `GET /{document_id}`: returns the current head revision.
- `POST /{document_id}/revisions`: `{base_revision_id, document}` and a fresh
  `Idempotency-Key`. Returns a new immutable revision, HTTP 201.
- `GET /{document_id}/revisions?limit=20&before=3`: descending history metadata,
  maximum 50/page. `next_before` is exclusive and null at the end. Bodies are not
  repeated in the history listing.
- `GET /{document_id}/revisions/{revision_id}`: loads an immutable version.

Revision responses allowlist only `revision_id`, `parent_revision_id`,
`revision_number`, `created_at`, `source_checksum`, and the validated `document`.
No storage keys, provider identifiers, submitted prompts or credentials escape.

Restore: load the chosen revision and submit its document against the **current**
head using a new save key. This creates a new revision; it never changes history.
No standalone restore button is connected by this increment.

## Source verification and concurrency

Creation uses the existing authorized resource reader: bounded bytes, canonical
path, stored length and SHA-256, followed by owner reauthorization. Video duration
is read from those bytes using the existing PyAV local-video preflight. This
slice retains its 30-second limit. It is not a new ingest/decode pipeline and
does not extend import or export capabilities. The client duration must agree
within 50 ms; the persisted document uses the server duration and is revalidated.

Creation rechecks source ownership/checksum inside the write transaction. Each
save locks the document row and source row; head check, revision insert and head
advance commit together. A competing save gets `409 EDIT_REVISION_CONFLICT`,
preserving the client's draft. Same-key/same-request replay returns the original
revision, including after the head advanced. Same-key/different-request returns
`409 EDIT_IDEMPOTENCY_CONFLICT`. No automatic last-write-wins or paid operation.

Pilot quotas: 100 accessible documents per owner; 1,000 revisions per document.
These cap this initial service; they are not a retention/deletion policy. Replay
still succeeds at quota. A failed transaction must leave both head and history
unchanged. No delete endpoint is exposed.

## Database enforcement and rollout

Reuse the dedicated media pool; its existing runtime guard rejects superusers
and BYPASSRLS roles. Each transaction adds `app.current_user_id` alongside
`app.current_org_id`. Both new tables use ENABLE + FORCE RLS. Policies also
require an available source owned by that principal and its live conversation.
Composite foreign keys keep revision identity/owner/head relationships intact;
triggers reject revision updates/deletes and source substitution/head rollback.

After migration, grant the configured **non-owner runtime role**:

```sql
GRANT SELECT, INSERT ON ben.video_edit_documents, ben.video_edit_revisions TO <media_runtime_role>;
GRANT UPDATE (head_revision_id, head_number, updated_at) ON ben.video_edit_documents TO <media_runtime_role>;
```

Use the actual validated role name, not a literal placeholder. Existing SELECT
permissions on media executions and threads remain prerequisites. Migration does
not alter identities, create login roles, enable flags or deploy automatically.
Downgrade refuses nonempty history; operational rollback disables the flag while
retaining rows. Any future deletion/retention workflow needs explicit design.

## Acceptance and remaining work

`tests/test_edit_persistence.py` uses only a fresh loopback PostgreSQL database
whose name begins `media_v1_test_`, and a nonsuperuser/NOBYPASSRLS role. It covers
real save/load/restore, owner and tenant RLS, racing saves, retry after a committed
response is lost, key conflicts, source substitution/deletion/integrity,
immutable history, quotas, rollback, migration downgrade and sanitized API errors.
It uses synthetic MP4s and forbids execution creation/worker dispatch.

The GitHub workflow additionally runs the pure contract and existing media
regressions, and fails on skips. Authoring the workflow is not a remote CI pass.

Next: B03 editor Save/Load integration, local-draft recovery, conflict UX and My
Work entry. Until that is wired and deployed, the current UI still saves locally.
Human review, publication/remix permission and training consent remain separate;
this service creates none of those grants and submits no training data.

Local validation on 2026-10-03: 107 Python tests passed with zero skips/failures:
26 persistence/API cases, 44 pure document cases, 37 existing media regressions.
PostgreSQL 16.15 ran on loopback in a new disposable cluster; application access
used a nonsuperuser/NOBYPASSRLS role. The cluster was stopped after the run.
The migration graph has one head (`video_edit_documents_v1`) and the new workflow
parses successfully. Remote CI, production migration and deployment were not run.
