# Gemini vertical slice — development checkpoint

Status: **environment-blocked; not ready for the internal live proof or release**.
Branch: `astra/ben-media-v1`. This continues the accepted foundation at
`c2b055f086637f99b4f86986b21510dd403a5753`; it does not start FLUX.

## Implemented path

The existing composer has an explicit internal image mode. Its text child,
text handler, streaming contract and provider registry remain unchanged. The
media path uses `/api/media`, exact `gemini-3.1-flash-image`, one 1K PNG, and no
fallback. Image editing/reference inputs remain outside this text-to-image slice.

The server is default-off. `BEN_MEDIA_INTERNAL_ENABLED=1` AND an exact entry in
`BEN_MEDIA_INTERNAL_PRINCIPALS` (JSON array of `org_id`/`user_id` pairs) are required.
Only existing authenticated identities are eligible. These are deployment
controls, not settings an API client can supply. No production setting was changed.
The existing `GOOGLE_API_KEY` is resolved only at the adapter composition root.

Migration 033 remains the only media table/migration. Admission, status, one
preallocated resource identity, accounting and result provenance belong to
`media_executions`. Each transaction sets the tenant RLS context and also filters
by org. Read/delivery additionally require the creating principal. A conversation
is authorized using its existing BEN org ownership, including projectless
conversations; publication locks/rechecks the destination against deletion.
Completed executions listed under that conversation are its media attachments.
No fake chat message, WorkspaceFile, document job or inference record is created.

Admission serializes per org, checks key/fingerprint conflicts and enforces a
20-execution rolling 24-hour internal limit. Repeated keys return the same
execution even at the limit. The client saves intent/key before POST and retains
it after an uncertain HTTP response. Polls are read-only.

A small lifespan loop examines only explicitly configured pilot organizations.
It claims due Gemini rows using `FOR UPDATE SKIP LOCKED`, a ten-minute lease and
version fencing. The sole provider submission is committed as `submitting`
before network I/O. It cannot be resubmitted after restart. A bounded private,
fsynced result journal permits ingestion recovery after a crash. Provider usage
and provenance are recorded before ingestion. Publication requires validated,
durable BEN bytes and a still-authorized destination. Successful publication
removes the redundant private journal when possible.

An uncertain submission without a result journal becomes `submission_unknown`,
never an automatic second generation. Ingestion has three attempts; all active
states have a thirty-minute deadline. Missing destinations and revoked pilot
access fail closed. Disabling the pilot stops new work and access; persisted jobs
are reconsidered only if it is explicitly enabled again. Terminal operational
history is retained. Private orphan bytes after interruption/deletion remain
undeliverable; a retention/cleanup operation is not implemented by this slice.

Delivery uses a BEN resource UUID, authenticated bounded byte reads, size/hash
verification, a destination recheck, `private, no-store` and `nosniff`. Provider
references, storage keys, private prompts and journal contents never appear in
public execution responses. Browser images use authenticated fetch and revoked
blob URLs, not permanent provider URLs.

Accounting preserves observed image count, requested resolution tier, decoded
dimensions and genuinely reported provider usage. A separate dated standard
tariff computes the image component and supported reported usage components.
Incomplete breakdowns have a partial component estimate and a null total; actual
invoice charge remains null. Pricing reference:
https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-flash-image (checked 2026-09-22).
There is no image-to-fake-token conversion.

Request/result rights remain `not_approved` for training/fine-tuning/distillation.
The authenticated evaluation endpoint stores the latest bounded, versioned
observation with evaluator and timestamp in the operational result envelope.
This slice has conversation ownership and does not manufacture workspace IDs to
write `execution_events`. Those events retain their existing telemetry semantics;
their persistence writers and inference accounting infrastructure are unchanged.
No research export, training job or clearance mutation exists.

## Verification and blocked gates

Windows / Python 3.14.3: **190 passed, 10 skipped** across the new service tests,
foundation, existing Gemini/DeepSeek/chat regressions, measurement contracts,
durable storage and migration DDL. The 10 skips are five new real-PostgreSQL
runtime tests and five existing migration database tests. They are NOT passes.
The tests use the existing local short-path/ACL runner without changing assertions.

Frontend checks passed: media client retry identity/transport/status, clean chat,
chat stream ownership, conversation surface and DeepSeek menu. New frontend
files pass ESLint. Python compilation and diff whitespace checks passed.

The 13 older `test_measurement_foundation.py` PostgreSQL cases exercise execution
event/validation-record persistence. This conversation-only media path neither
calls nor changes those writers/tables, so those cases are not a gate for this
change. Contract and ambient-database source-boundary regressions passed. New
media SQL/RLS tests remain a required, separate database gate.

The frontend production command remains exactly:

```
npm --prefix frontend run build
```

The checkout initially lacked dependencies; its unchanged lockfile matched the
existing sibling installation, which was reused through an untracked junction.
Vite then failed before application compilation with esbuild `spawn EPERM`.
The production gate has not been replaced by lint or client unit checks.

The existing disposable Windows PostgreSQL 16.15 launcher failed with restricted
token error 87. Direct server startup reached crash recovery, then PostgreSQL's
startup process failed to signal its checkpoint process (`Operation not permitted`)
and the server shut down. No production database was contacted. Docker has no
available daemon; WSL enumeration returns access denied. No sandbox policy was
changed or bypassed.

## Resume gate

Use an authorized runner supporting PostgreSQL subprocess coordination and
esbuild child-process execution. Run the five new repository tests against a
fresh loopback `media_v1_test_*` database via `MEDIA_TEST_DATABASE_URL`; also run
the existing six migration tests, the focused regressions, and the exact frontend
production build. The repository tests deliberately verify a non-superuser,
non-BYPASSRLS role and cover concurrent admission/claiming, fencing, key conflicts,
tenant isolation, destination deletion, evaluation persistence and admission limits.

After these gates pass, continue the approved internal real-Gemini red-chair
proof through the media HTTP path and verify repeated submission, reload,
authenticated delivery, accounting and provenance. No live generation was
attempted in this checkpoint because the database/build gates are unverified.
Commercial clearance is still required before external exposure. No production
deployment, migration, secret mutation, capacity purchase or FLUX work occurred.
