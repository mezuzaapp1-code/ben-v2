# Local mobile conversion component

This slice adds a local converter, not an upload endpoint or a deployed import
worker. Existing provider paths retain the legacy 240-frame validator budget.
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

MobileVideoError exposes sanitized code/status/detail for future route mapping.
No HTTP endpoint currently exposes these new errors. Tests exercise real codecs,
HDR synthetic conversion, rotation and audio, corrupt input/output rejection,
extended validator budget, early duration/size rejection, busy and timeout cases.
Metadata and successful decoding do not prove perceptual color or speech quality.

Integration still required: authenticated upload, durable original retention,
claimable import execution, fenced attempt publication, authorized resource
delivery and frontend selection. Do not enable phone upload based on these
component tests alone.
