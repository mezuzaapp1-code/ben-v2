# Veo implementation gate — no live generation

Verified against official sources on 2026-09-23/24.

## Provider contract

Selected `veo-3.1-fast-generate-preview`, Gemini Developer API (not Vertex).
POST `/v1beta/models/veo-3.1-fast-generate-preview:predictLongRunning`, authenticated
with `x-goog-api-key`. Persist the returned model-scoped operation name; GET it
until `done`, then inspect `error` or `response.generateVideoResponse.generatedSamples`.
Download the file through the authenticated files route before publishing BEN identity.

[Google Veo guide](https://ai.google.dev/gemini-api/docs/veo) documents text/image
inputs; 4/6/8 seconds; 16:9/9:16; 720p, or 1080p/4K at eight seconds. Audio is always
on. Outputs expire after two days and carry SynthID. No Veo cancellation contract
is documented; BEN expiry does not claim provider cancellation or a refund.
The SDK operations module has no cancel method.

The guide contains an inconsistent inlineData image example. The implemented
`bytesBase64Encoded`/`mimeType` encoding and integer duration match Google's
[current SDK converters](https://github.com/googleapis/python-genai/blob/main/google/genai/models.py).
File retrieval follows its [files implementation](https://github.com/googleapis/python-genai/blob/main/google/genai/files.py).
BEN restricts initial downloads to the Google API; approved Google storage redirects
never receive the API key. Unrecognized locations fail closed without regeneration.

## BEN scope and recovery

Only the already-approved `image_to_video` operation is exposed: one owned BEN PNG
first frame, four seconds, 720p, 16:9 or 9:16, native audio, allow_adult.
No new schema/table, scheduler, worker framework or provider-specific public operation.
`BEN_MEDIA_VEO_ENABLED=1` additionally requires the existing internal pilot allowlist.
The image adapters and Direct Chat are unchanged. The existing row/lease/reconciler,
immutable publication, result journal, resource delivery and conversation surface are extended.

The request snapshot freezes the source identity/checksum and normalized parameters.
One committed submitting transition precedes the only POST. Uncertain acceptance
never retries. Restarted workers GET the stored operation. Ready download metadata
survives until journal persistence; ingestion retries reuse bytes or the accepted job.
Fenced PostgreSQL updates prevent stale workers publishing. MP4 H.264/AAC decoding
checks bounded size, dimensions, duration, frame timestamps and complete decode.
Resource success requires BEN storage/checksum, never a provider URL.

## Credential and paid proof gate

Existing `media-v1-proof` environment `GOOGLE_API_KEY` uses the correct Developer API
credential convention. No new key, account, Vertex role or production secret is
required by this implementation. The project still needs active paid billing,
Generative Language API/key restrictions permitting that API, and available Veo quota
in a supported region. Actual project model entitlement is NOT proven by mocked tests.
No credential was accessed and no Veo generation was sent in this gate.

Proposed future proof: exactly one synthetic BEN-owned first frame, no people/customer
content; image-to-video; four seconds, 720p, 16:9, audio ON (native).
[Current pricing](https://ai.google.dev/gemini-api/docs/pricing#veo-3.1): Fast 720p
$0.10/second, so one successful four-second result estimates **$0.40 USD before tax**.
The LRO does not document billed credits/invoice usage. BEN records observed video
seconds and a versioned tariff estimate; actual charge stays unknown. Provider success
can be chargeable even if subsequent BEN ingestion fails. Never regenerate automatically.

[Paid-service terms](https://ai.google.dev/gemini-api/terms) exclude using paid inputs/
outputs to improve Google's products; [abuse monitoring](https://ai.google.dev/gemini-api/docs/usage-policies)
retains prompts/context/output for 55 days and can involve authorized review.
This is not zero retention. Preserve SynthID and provenance. BEN training, fine-tuning
and distillation remain NOT APPROVED. External exposure remains subject to the existing
commercial/legal gate. Human authorization is required before the single paid video proof.

## Deterministic validation

`tests/test_veo_media.py`: exact endpoint/body, authentication, redirects/key boundaries,
malformed operations, rejection, MP4 validation and per-second accounting.
`tests/test_veo_repository.py`: real PostgreSQL with non-bypass tenant RLS; complete route,
source authorization, idempotency, crash/restart, uncertain submit, provider/poll failures,
expiry, journal/ingestion recovery, protected video delivery and conversation history.
CI runs both alongside the unchanged image/Direct Chat regressions, original 11 database
gates and `npm --prefix frontend run build`. No live-proof workflow trigger is added.
