# Local narration replacement

Internal Python entry point: `services.media.narration.create_narration(service,
org, user, idempotency_key, conversation, video_resource_id=...,
workspace_id=..., music_file_id=..., narration_file_id=...)`.

Requires migration 034, the existing pilot principal allowlist, and an explicitly
enabled `MediaService(local_narration=True)`. The background worker enables it
only when `BEN_MEDIA_LOCAL_NARRATION_ENABLED=1`. Default is disabled. No public
endpoint, timeline or paid provider is introduced.

The video must be a ready BEN MP4 resource owned by the caller. WAVs use existing
Workspace File Library IDs, with org/project scope and uploader identity checked
before and after reading. Only uploaded/ready audio/wav rows are accepted. Paths
are reconstructed within the exact org/workspace/file namespace, and size and
SHA256 are verified. Library WAV support is store-only; composer admission checks
PCM16, 48kHz, mono/stereo, bounded size and duration. MP3 is rejected. These checks
do not establish licensing or spoken-word intelligibility.

The immutable request fingerprint includes input checksums and the fixed mix
profile. Admission rejects narration longer than the exact video time base.
The worker rechecks sources before and after rendering. Its leased local
transitions preserve ownership and check expiry. Final publication uses existing
attempt fencing, immutable storage and protected resource delivery.

FFmpeg preserves H264 packets, timestamps and durations, replaces all original
audio with one AAC stereo 48kHz stream, and uses the specified sidechain graph.
192k is the AAC encoder target bitrate, not a guarantee that every frame is CBR.
Music has gain .35, narration 1.0, threshold .03, ratio 8, attack 10ms, release
150ms. Short audio is padded to video duration. No time stretching. Existing
BEN MP4 dimensions and decode limits still apply (this is not arbitrary footage).

Crash behavior: each rerun uses private scratch files and a new immutable output
attempt. Expired/stale publication is fenced. A committed execution is excluded
from claim. A lost DB response never triggers deletion of a durable attempt.
Uncommitted orphan reuse/garbage collection is outside this slice; rerender may
occur after lease expiry. Local computation is not reported as free cloud usage.

Acceptance uses real synthetic MP4/WAV bytes, FFmpeg and PostgreSQL under a
restricted non-BYPASSRLS role. Migration 034 and Workspace File migration 022
are loaded from their actual Alembic files. The Project fixture supplies its
existing id/org boundary; this is not a full File Library upload API test.
