# Kling accepted-operation recovery (2026-09-24)

Scope: existing accepted operation only. No generation POST, production change,
new execution identity, or replacement lifecycle row is authorized by recovery.

## Verified evidence

Original proof: run 36001150264, BEN execution
`9a8a062c-9d30-41cb-bd3e-8f791f4360fd`. Its first poll failed with
`media_provider_fetch_failed`. That run did not retain the poll HTTP status/body.
The test fixture subsequently dropped the BEN schema and the runner destroyed
its disposable PostgreSQL service. The retained proof had only a boolean saying
the provider reference was persisted, not a recovery snapshot.

GET-only investigation run 36003817676, commit
`84dd3017a1f6c54cf1d8dbb817ab1cd07591fddc`, recovered the original result:

- `GET /fal-ai/kling-video/requests/{request_id}/status`: HTTP 200,
  object fields `status`, `request_id`, `response_url`, `metrics`; COMPLETED.
- `GET /fal-ai/kling-video/requests/{request_id}`: HTTP 200, `video` object.
- One unauthenticated CDN download: HTTP 200, 881210 bytes.
- MP4: 1280x720, 16:9, 3.0416666667 seconds, no audio.
- SHA256: `48eb832bb9d3115df6f998054aca62d376fb0931a2d53725a2d071a02b369171`.
- API-reported inference time: 80.8310000896 seconds. Actual charge $0.252
  comes from the human's dashboard observation, not the result API.

The output was not expired at recovery. Dashboard "Output not available" did
not describe queue-result/CDN availability. JSON and video are retained encrypted
in the private recovery artifact; no provider URL is a BEN resource reference.

## Root cause boundaries

The original first-poll HTTP response cannot be reconstructed from retained
logs. No specific HTTP code or adapter-contract mismatch is claimed. Completed
operation paths are verified correct by real HTTP 200 responses. The existing
adapter rejects GET statuses other than 200; old proof instrumentation captured
only >=400 responses. The fix records all queue response codes and allowlisted
body shapes, including 2xx/3xx, without retaining arbitrary messages.

Confirmed recovery failure: destructive test teardown lacked a protected export
of the operational row/source/conversation. Recovering provider bytes cannot
restore those missing facts. Offline ingestion validates the recovered bytes
through BEN's existing MP4/immutable-storage functions and the original reserved
resource ID, but does not publish a resource or claim the original execution
succeeded. The encrypted bundle explicitly records this limitation.

Official contracts consulted:
- https://fal.ai/docs/documentation/model-apis/inference/queue
- https://raw.githubusercontent.com/fal-ai/fal/main/projects/fal_client/src/fal_client/client.py
- https://fal.ai/docs/documentation/model-apis/media-expiration

## Proof recovery safeguards

Only proof/test/workflow code changes. Each durable lifecycle transition and
acceptance receipt checkpoints the original media rows, thread identities,
synthetic input/output bytes, private provider reference and sanitized diagnostics.
Snapshots use AES-GCM with a per-file random salt/nonce and HKDF key derived from
the existing protected FAL_KEY. The credential itself is never serialized.
Artifacts are retained for 30 days and uploaded with `if: always()`.

Restore requires the same credential, an EMPTY loopback disposable test database
named `media_v1_test_*`, and the matching schema. `restore()` refuses ambiguous
pending work without a validated acceptance receipt, preserves IDs, clears stale
leases, and permits only completed source rows. The deterministic recovery test
drops/recreates the schema, restores solely from ciphertext, and resumes with a
transport that rejects every POST. It checks immutable bytes, delivery and
idempotency after restoring both a committed operation and the acceptance/DB gap.

For a future accepted-operation recovery, use a protected environment, recreate
the original test schema, invoke `tests.kling_recovery_snapshot.restore` with
its encrypted archive and an empty BEN byte-store root, then reconcile using a
GET-only transport. Never rerun the original paid proof entrypoint. Preserve
access to the encryption credential for the artifact lifetime; rotation would
otherwise prevent decryption. No production secret is needed.

Limits: upload after normal failure/teardown is covered. Abrupt runner loss
before artifact upload can still lose the local snapshot. This is a proof-harness
recovery mechanism, not a new durable production scheduler or backup system.
Training reuse remains not approved. No architecture change is required.
