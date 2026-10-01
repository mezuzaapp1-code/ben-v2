# Local mobile conversion component

This slice adds a default-off upload endpoint and local import worker. Existing provider paths retain the legacy 240-frame validator budget.
The opt-in mobile-v1 validation profile accepts up to 30 seconds and 902 decoded
video frames, with the existing byte, stream, dimension and decode-time checks.

Inputs: local MP4/MOV bytes, H.264/HEVC, up to 64 MiB, 30 seconds and 3840x2160
pixel area. Explicitly supported color profiles include BT.709, untagged 8-bit
SDR (assumed BT.709), BT.470BG/SMPTE170M SDR, and tagged 10-bit BT.2020 PQ/HLG.
Dolby Vision and ambiguous 10-bit color are rejected. Unknown profiles fail
closed. The original must be retained by the future authorized upload caller.

Output: H.264/AAC, BT.709, 1280x720 or 720x1280 canvas. Aspect ratio is preserved
with padding, not cropping. FFmpeg autorotate is the sole rotation mechanism.
Output uses a supported cadence no higher than 30 fps; 60 fps inputs lose frames.
HDR tone mapping is lossy and requires human review on real HDR samples.

Concurrency is one conversion per Python process, not globally across replicas.
Decoder/encoder threads are limited to two, filters to one; conversion timeout
is 120 seconds, each probe 15 seconds, validator decode budget 30 seconds.
These controls are not OS CPU or memory quotas. All scratch files are private
to a temporary directory; only that directory is cleaned up. No DB publication,
source deletion, paid provider or network media retrieval occurs here.

MobileVideoError exposes sanitized code/status/detail through POST /api/media/video-imports.
The authenticated endpoint accepts a raw bounded body, with conversation_id, workspace_id
and idempotency_key query parameters; no arbitrary source path or URL is accepted. Tests exercise real codecs,
HDR synthetic conversion, rotation and audio, corrupt input/output rejection,
extended validator budget, early duration/size rejection, busy and timeout cases.
Metadata and successful decoding do not prove perceptual color or speech quality.

Activation requires migration 035_mobile_video_import after 034, the existing
non-bypass media role with SELECT on projects/workspace_files and INSERT on
workspace_files, a durable BEN_PROJECTS_DATA_DIR, and
BEN_MEDIA_MOBILE_IMPORT_ENABLED=1 on API and worker. The existing exact-principal
pilot allowlist still applies. Default is off; enabling/revoking the worker flag
requires restart. Already published authorized media remain readable when off.
No production activation is implied by CI passing.

Originals live in workspace_files with independent stable file IDs, server SHA256
and immutable byte publication; execution snapshots retain source ID and checksum.
Client SHA256 is only a browser retry guard, never server authorization/evidence.
A lost response retries the same key and bytes. A different payload conflicts.
A crash may leave orphan bytes; this slice does not delete completed originals or
attempts. A new worker may re-render after a failed commit; no orphan adoption.

The UI exposes Upload video only through capabilities, requires a workspace,
then uses existing conversation polling and authenticated resource delivery.
The player displays the entire frame (contain), without cropping, and downloads
the whole bounded derivative before playback (no adaptive streaming).
Narration derived from an imported video inherits the mobile validation budget.
