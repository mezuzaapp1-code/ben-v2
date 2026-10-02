# Managed Short Rendering v1

Implementation for review, default off. No production migration, deployment,
paid render, real S3 staging or visual Hebrew qualification is implied by CI.

## Contract

One fixed route: five existing, owned, succeeded BEN PNG resources, seven seconds
each; one authorized workspace WAV (PCM16, 48kHz, mono/stereo, 34.9–35s, at most
7,000,000 bytes); optional ordered, non-overlapping caption cues. Output is
35s portrait 720x1280, H.264/AAC, 30fps. Images use contain, not cropping.
This does not generate images, speech, translation or music. Chat photo file IDs
are not accepted as image resource IDs in this slice.

POST `/api/media/short-render-quotes` accepts ScenePlan:

```json
{
  "conversation_id": "<owned-conversation-uuid>",
  "request_version": "short-render-v1",
  "profile_id": "five-scenes-35s-portrait-v1",
  "image_resource_ids": ["<uuid-1>", "<uuid-2>", "<uuid-3>", "<uuid-4>", "<uuid-5>"],
  "narration_workspace_id": "<authorized-workspace-uuid>",
  "narration_file_id": "<wav-file-uuid>",
  "captions": [{"start_ms": 0, "end_ms": 7000, "text": "שלום BEN 2026!"}]
}
```

Returns a signed ten-minute `quote_id`, estimate, reservation, pricing version and
expiry. POST `/api/media/short-renders` with `Idempotency-Key`, and body
`{"plan": <same ScenePlan>, "quote_id": <signed quote>}` returns the existing
ExecutionResponse. Poll existing `/api/media/executions/{id}` and retrieve content
through the existing authenticated resource endpoint. No new UI in this PR.
No client URLs, RenderScript, storage paths or monetary values are accepted.

## State and budget

Migration 037 extends media_executions, retaining its existing RLS and role grants.
Quote consumption, org budget reservation and execution creation are one locked
transaction. Same idempotency key replays the same execution, including after
quote expiry; a changed plan/owner conflicts. A quote cannot fund a second job.

The worker stages while pending, fences its lease/deadline and writes submitting
before the single POST. Uncertain POST results become submission_unknown. Crashes
after submission never trigger another POST, even without a provider render ID.
Known IDs use bounded GET reconciliation, at most 100 polls and 20 minutes overall.
There is no callback trust or undocumented provider idempotency assumption.

Reservations count for 24 hours. Submitted outcomes that did not succeed remain
reserved beyond that window, including failed/expired/unknown outcomes; they need
operator reconciliation, not automatic release. The configurable reservation
must be a verified conservative provider charge bound before enabling paid work.
The code enforces BEN admission limits, not a provider-side spending cap.

## Private staging and rollout prerequisites

Both flags default to 0:
`BEN_MEDIA_SHORT_RENDER_ENABLED` permits admission/new submissions;
`BEN_MEDIA_SHORT_RENDER_RECONCILE_ENABLED` permits worker processing.
Turning admission off stops new submissions while reconciliation can remain on.
Existing BEN pilot principal and durable media database settings are also required.

Required settings: CREATOMATE_API_KEY, BEN_SHORT_QUOTE_SECRET (32+ chars),
BEN_SHORT_STAGE_BUCKET, BEN_SHORT_ESTIMATE_USD, BEN_SHORT_RESERVATION_USD,
BEN_SHORT_DAILY_BUDGET_USD, BEN_SHORT_PRICING_VERSION. No prices are supplied as
production defaults; test dollar figures are synthetic.

Staging uses AWS S3 with all four Block Public Access controls enabled, SSE AES256,
execution-scoped keys under ben-short/, and a required enabled prefix lifecycle
rule with Expiration Days=1. Role credentials need bucket public-access/lifecycle
reads and object put/get for that prefix. Use short-lived credentials with enough
remaining lifetime for the 30-minute URL window. Disable bucket versioning or
configure noncurrent-version expiration separately before rollout.

Signed GET URLs last 30 minutes and are never persisted or logged. URL expiry
does not erase objects: this first slice relies on the one-day lifecycle cleanup
(S3 deletion is asynchronous), including partial uploads after crashes. Originals
stay in BEN untouched. No claim of immediate staging deletion is made.

Output download accepts only the exact HTTPS Creatomate CDN render UUID MP4 path,
with no credentials, redirects, query or fragment. An unexpected CDN URL fails
closed and must be qualified before expanding the allowlist.

## Verification and evidence

ffprobe plus full PyAV decode verifies container, H.264/AAC, dimensions, duration,
frame bounds, monotonic video timestamps, continuous audio and decoded audio end
within 100ms. Input checksums/permissions are rechecked before publication.
Immutable attempt bytes become authoritative only through the existing fenced
publication transaction. A valid MP4 is a technical gate, not product approval.

Private short_telemetry records profile, stage/queue/execution/download/validation
timing, technical check and output SHA256. Monetary estimate/pricing/reservation
remain typed execution columns. Actual reported USD remains null, cost_status
estimated_only; no cost is fabricated from callbacks. Human acceptance uses the
existing evaluation endpoint and updates the linked private review status.

Hebrew is preserved in logical Unicode order; there is no blind reversal.
Font availability, mixed Hebrew/Latin/numbers/punctuation, clipping, visual quality
and listening approval require a separately authorized live provider smoke test.
Successful mock CI alone does not open the pilot.

CI uses a real disposable PostgreSQL database, tenant-restricted role, synthetic
FFmpeg artifacts and mocked provider/S3 clients. It covers budget races, quote
replay, RLS, lease fencing, uncertain submissions, revoked input access, malformed
and silent output, full decode and private telemetry, plus the existing media
regressions and frontend checks. It makes no paid provider requests.
