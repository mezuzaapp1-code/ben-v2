# Gemini media foundation checkpoint

Approved sequence: Google Gemini 3.1 Flash Image, BFL FLUX.2 Pro, Google Veo
3.1 Fast, fal/Kling O3 Standard. This supersedes the historical BytePlus
credential stop recorded in MEDIA_V1_MIGRATION.md. Commercial clearance is a
gate for affected external-user exposure, not for local/mock development.

## Implemented boundary

`services/media` is separate from the chat provider registry and gateway. It is
not registered in main.py and exposes no HTTP route. The internal adapter sends
exactly `gemini-3.1-flash-image` to Google's Interactions endpoint with header
authentication, no query-string key, no redirects, no automatic retries, and no
cross-provider fallback. It requests one PNG at 1K, with a limited aspect-ratio
allowlist. Image references/editing, tools, search, and provider conversation
state are intentionally outside this first transport proof.

The HTTP body is bounded, compressed responses are rejected, and one returned
image is required. Arbitrary provider error text and credential-bearing HTTP
exceptions are not propagated. Uncertain transport/submission outcomes must be
persisted as `submission_unknown` by the execution owner; this adapter never
resubmits. `store=false` disables optional interaction storage, not Google's
separate abuse monitoring obligations. No provider idempotency guarantee is
claimed.

PNG ingestion verifies the decoded image, enforces byte/pixel bounds, preserves
original bytes/provenance metadata, fsyncs, and publishes through an atomic
non-replacing hard link. Concurrent identical ingestion converges on one file;
different bytes under the same resource identity fail without overwriting.
BEN's existing durable-root and checksum primitives are reused. Hard-link and
durability behavior must also be verified on the eventual deployment volume.
Directory fsync has the same best-effort platform limitation as existing BEN
storage. No WorkspaceFile/document row is created or repurposed.

## Minimal metadata, no new schema

The approved 033 table already has the required JSON and operational fields:

- `request_payload`: `media-v1` request snapshot, normalization version, prompt,
  normalized parameters, destination selectors, empty input references for this
  text-only proof, optional experiment ID, provenance and `media-rights-v1`.
- Existing identity/model/operation, timestamps, attempt/error, cost and resource
  columns remain authoritative. A prompt/config snapshot is internal, not an
  API response. Authorization must precede construction/persistence.
- `usage_dimensions`: output image count separately from allowlisted, genuinely
  provider-reported token counters and modality units. Missing remains null.
- Result observations retain returned model and elapsed provider-call time.
  Snapshot/version is unknown if unreported; actual charge is null when absent.
  Estimated cost/pricing version remain null with `not_priced` until a separate
  pricing component is connected; image count is never converted to fake tokens.
- `media-evaluation-v1` is an observation payload for the existing execution
  event writer: evaluator, rubric, acceptance, bounded labels, cohort and
  correction reference. A cohort tag does not promote this operational execution
  into a controlled experiment without frozen-input/conditions/protocol evidence.
  Its origin must match the original execution. Existing
  event persistence enforces immutable origin. Request handlers must derive it
  from the authorized execution, never from feedback-client ownership claims.

The existing execution-event contract requires a workspace. The event builder
rejects a missing workspace; no synthetic workspace or ownership change is
introduced for projectless conversations. Projectless operational metadata can
remain in media_executions; workspace event emission is optional telemetry.

Training, fine-tuning and distillation default to `not_approved`. No clearance
mutation, training export, training job or distillation path is implemented.
Evaluation/routing research clearance is distinct from output training rights.
Corrections retain restricted provenance. Unknown rights exclude future reuse.

## Official transport references checked 2026-09-22

- https://ai.google.dev/gemini-api/docs/image-generation
- https://ai.google.dev/gemini-api/docs/interactions-overview
- https://ai.google.dev/api/interactions-api
- https://ai.google.dev/gemini-api/terms

The current docs show `steps[].content[]` model_output image blocks and token
usage dimensions. Fixtures exercise that REST envelope; these are synthetic
fixtures, not recorded live provider evidence.

Local result (Windows / Python 3.14.3): 172 passed, including 51 new media
cases and existing Gemini/DeepSeek/chat, measurement-contract, durable-storage,
measurement source-boundary and migration-DDL assertions. The uncommitted local
runner uses a short workspace scratch path, inherited test-directory ACLs and
the actual checkout in place of existing tests' hardcoded `/workspace` ROOT;
assertions and application code are not altered by the runner. The earlier
13-case measurement PostgreSQL suite could not initialize its unchanged
Linux-only launcher (`os.geteuid`); it is not counted as passing. No new database
integration or frontend production-build pass is claimed by this checkpoint.

## Validation and remaining phase work

Focused tests exercise exact dispatch, no fallback/retry, auth failures,
ambiguous transport, model mismatch, malformed/multiple/URL outputs, bounded
response, missing versus zero usage, rights defaults, event-contract compatibility,
disk reload, concurrent ingestion, corruption and cleanup. Local tests use only
httpx.MockTransport. No credentials, generated artifacts or provider output are
committed.

This checkpoint is NOT the complete Gemini vertical slice. Next work is the
authorized media execution service/repository using 033, tenant/destination
authorization, org-scoped idempotency, publication and deletion checks,
authenticated delivery, accounting/pricing, and the minimal existing composer
integration. Execution/evaluation envelope builders are ready but not yet wired
into production persistence. No resource is publicly deliverable merely because
the byte-ingestion helper succeeds.

Before a real provider proof: pass authorization, idempotency, resource reload,
no-fallback and Direct Chat gates. Before external users: applicable commercial
clearance. Before deployment: complete production build and deployment-specific
durability/recovery tests, followed by explicit deployment approval.
