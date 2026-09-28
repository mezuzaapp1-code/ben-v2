# Kling O3 Standard via fal — internal implementation gate

Reviewed 2026-09-24, starting from `6a76de526c9af2248b602e92df9d9de32ddfcd30`.
No live generation, account purchase, terms acceptance or production change is
authorized by this implementation. Default-off `BEN_MEDIA_KLING_ENABLED=1`
requires the existing internal pilot gate/principal allowlist too.

## Verified provider contract

- Image-to-video: `fal-ai/kling-video/o3/standard/image-to-video`.
- Text-to-video: `fal-ai/kling-video/o3/standard/text-to-video`.
- This increment enables **I2V only**, reusing BEN's existing `image_to_video`
  operation. T2V was verified but does not introduce another BEN operation.
- Both schemas expose string durations `3`–`15`, default `5`, and optional native
  audio (OpenAPI default false). T2V exposes 16:9, 9:16 and 1:1. I2V exposes
  start/end images, but **no aspect-ratio or resolution argument**. Prompt limit
  2,500 characters; I2V source limit 50 MiB, minimum 300px per side, ratio 0.4–2.5.
  Output is one `video` file object; byte size and MIME may be absent.
- BEN intentionally narrows this to one PNG start frame, 20 MiB/20M pixels,
  2,000-character prompt, three seconds, audio off, 16:9 or 9:16. Source must match
  requested ratio. Output must pass existing 64 MiB H.264/AAC MP4 validation,
  decoded three-second duration and 1280×720 / 720×1280 checks. The endpoint
  schema does not guarantee exact pixel dimensions: 720p is BEN's proof acceptance
  target, to be confirmed in an authorized live proof, not a fabricated API field.

Sources: [I2V API](https://fal.ai/models/fal-ai/kling-video/o3/standard/image-to-video/api),
[I2V OpenAPI](https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=fal-ai/kling-video/o3/standard/image-to-video),
[T2V OpenAPI](https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=fal-ai/kling-video/o3/standard/text-to-video).
The I2V HTML title incorrectly says Pro; its endpoint, schema title and page
header identify Standard. BEN pins the canonical Standard endpoint.

## Transport, retention and recovery

Server credential: `FAL_KEY`, an **API-scope** key; HTTP `Authorization: Key …`.
Submit once to `https://queue.fal.run/{model}`. Persist the acceptance reference
and validated status URL privately. Queue states are IN_QUEUE, IN_PROGRESS,
COMPLETED; completion can still contain an error. Fetch result JSON separately,
then download its fal.media URL without credentials or redirects. Never expose
provider URLs/IDs in resource responses or conversation history.

The official SDK normalizes request URLs to `fal-ai/kling-video/requests/{id}`;
endpoint OpenAPI also documents the full endpoint prefix. Both exact forms are
validated against the persisted request ID; arbitrary hosts/paths are rejected.
BEN explicitly sets `X-Fal-No-Retry: 1` to disable fal's default queue retries.
The 1,800-second request timeout is time-to-start, not a cancellation guarantee.
Optional webhooks use signed notifications (Ed25519) and can be redelivered;
BEN uses its existing polling instead. Cancel is best-effort once running;
BEN expiry never claims provider cancellation or a refund.

JSON storage defaults to 30 days; BEN requests `X-Fal-Store-IO: 0` using the
documented queue-compatible header. Generated CDN objects have configurable
retention, potentially forever if unset; BEN requests 3,600 seconds via
`X-Fal-Object-Lifecycle-Preference`. This is a provider preference, not proof of
upstream deletion or private access. Files may be publicly accessible until
expiration. BEN ingests promptly into its own protected store.

Sources: [authentication](https://fal.ai/docs/documentation/setting-up/authentication),
[queue](https://fal.ai/docs/documentation/model-apis/inference/queue),
[official Python client](https://github.com/fal-ai/fal/blob/main/projects/fal_client/src/fal_client/client.py),
[headers](https://fal.ai/docs/documentation/model-apis/common-parameters),
[retention](https://fal.ai/docs/documentation/model-apis/media-expiration),
[webhooks](https://fal.ai/docs/documentation/model-apis/inference/webhooks).

## BEN ownership and validation

No migration or new infrastructure. Existing media rows, leases, request
fingerprints, tenant authorization, journal, immutable publication, checksums,
protected resource route and conversation listing remain authoritative. Only
exact routing/allowlists, the adapter, tariff/provenance and the existing media
composer's video selection are extended. Google/BFL adapter files and Direct
Chat are unchanged. Training, fine-tuning and distillation remain not approved.

Tests exercise committed submitting state before transport, concurrent duplicate
admission/claims, restart-only polling, failed/uncertain acceptance without
resubmission, status/result failures, deadline/poll exhaustion, ingestion/journal
recovery, immutable output, tenant RLS, protected delivery, history and accounting.
CI adds the Kling unit/PostgreSQL suites to the existing validation job. It adds
no live job, credential access or paid dispatch flag.

## Next human gate

For a future proof, supply non-production `FAL_KEY` (API scope, account with model
access and credits) in protected GitHub environment `media-v1-proof`, restricted
to `astra/ben-media-v1`. Explicitly authorize **one** synthetic I2V generation.
Proposed input: BEN-owned 1280×720 geometric PNG, gentle motion, three seconds,
16:9, audio off, expected 720p. Endpoint-specific price is $0.084/second without
audio, $0.112 with audio: **$0.252 before tax** for this proof. This is a tariff
estimate, never an invoice claim. Queue inference time is not billed video time;
actual charge/credits remain unknown unless reported by the provider.
[Endpoint pricing](https://fal.ai/models/fal-ai/kling-video/o3/standard/image-to-video)
takes precedence over the conflicting older generic Kling landing-page example.

Commercial use is labeled on the endpoint. fal's
[API supplement](https://fal.ai/legal/api-services) permits end-user integration
and restricts training/development use of client content, but excludes models
designated Pending Enterprise Ready; content goes to the third-party provider.
Before external/customer use, the human owner must confirm this model's contractual
coverage, upstream handling/retention and BEN end-user terms. Do not infer zero
retention from no-training language. [fal terms](https://fal.ai/legal/terms-of-service)
also restrict training competing models with third-party output. The direct
[Kling paid agreement](https://kling.ai/document-api/protocols/paidServiceProtocol)
is not assumed to be the agreement governing the fal channel. No training or
distillation clearance is granted by this review. Local synthetic mocks require
no new commercial acceptance; a paid proof remains a separate human gate.
