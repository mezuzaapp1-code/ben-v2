# BFL internal image proof gate

Implementation follows the official references checked 2026-09-23:

- https://docs.bfl.ai/api-reference/models/generate-or-edit-an-image-with-flux2-%5Bpro%5D
- https://docs.bfl.ai/api-reference/utility/get-result
- https://docs.bfl.ai/api_integration/integration_guidelines
- https://docs.bfl.ai/quick_start/pricing
- https://bfl.ai/legal/flux-api-service-terms

Exact direct endpoint: POST https://api.bfl.ai/v1/flux-2-pro, x-key authentication.
BEN maps its existing 1K / aspect-ratio intent to 1024x1024, 1024x576 or 576x1024.
Request PNG and disable prompt upsampling so the stored prompt is the submitted intent.
Do not send a webhook, batch, input image, or extra user/tenant identifiers.

The existing media_executions row commits submitting before its single POST.
Persist the returned operation ID and exact polling URL privately in that same row.
The existing worker resumes submitted/running rows with bounded GET polling and
fenced updates; there is no new scheduler or schema. Ready output is downloaded
without the API key, from HTTPS delivery.*.bfl.ai only, without redirects and with
byte limits. PNG integrity and immutable publication use the existing storage path.
Temporary URLs never appear in BEN public responses, assets or evaluation records.
The private ready URL survives a download failure; retries download that output
at most three times, never resubmit. Unknown submissions never regenerate.

Model provenance is exact endpoint dispatch, not a fabricated returned model or
snapshot. Capture credits/megapixels when reported, including submission versus
settled stage. One credit is $0.01; invoice charge remains unknown. No token proxy.
The same training/fine-tuning/distillation NOT APPROVED defaults apply. Projectless
conversation evaluation stays on the media record; do not invent workspace event ownership.

## Before any live request

Implementation/mocks do not accept terms, create accounts or purchase credits.
Confirm an authorized BFL account/project, sufficient credit balance and applicable
service terms acceptance. BFL's standard terms include provider use of inputs/outputs
for its own training; BEN's NOT APPROVED flag governs BEN reuse and is not a provider
opt-out. Human terms clearance is required before sending the proof prompt.

Provide a dedicated non-production BFL_API_KEY as an environment secret in the
existing protected media-v1-proof GitHub environment, restricted to astra/ben-media-v1.
Never put it in chat, source, artifacts or ordinary workflow output. Authorize exactly
one paid flux-2-pro text-to-image generation. Published text-to-image pricing starts
at $0.03; capture reported credits rather than asserting a flat invoice cost.

Keep BEN_MEDIA_BFL_ENABLED=0 elsewhere. A future explicitly authorized proof job may
set it to 1 with the existing internal principal gate and a disposable database.
No live BFL workflow is automatically enabled by this implementation.

## Deterministic validation

The branch validation workflow runs tests/test_bfl_media.py and
tests/test_bfl_repository.py alongside unchanged Gemini/Direct Chat regressions,
PostgreSQL gates and the production frontend build. BFL network is MockTransport;
PostgreSQL, non-bypass RLS, route authorization and storage are real test components.

No production migration/deployment, main merge, Veo, Kling or BytePlus work is authorized.
