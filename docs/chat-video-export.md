# Conversation video upload and saved MP4 export

A video can be uploaded into the current conversation without a project. Existing project uploads are unchanged. Originals are immutable and checksum verified; the new chat_video_sources table has owner + org RLS and does not depend on an execution ID. The worker reuses the bounded mobile-v1 conversion and publication checks.

Export MP4 operates on a saved immutable revision. The browser rasterizes its captions and text layers using the same styles and local fonts as preview. The API authorizes the revision before accepting bounded JSON/PNG input, checks its dimensions and timeline, decodes/re-encodes every PNG, and runs FFmpeg under the shared conversion slot. It checks the source checksum, fully decodes the resulting H.264 MP4, preserves the AAC audio, and checks access again before delivery. No AI provider is called. Input raster pixels are client supplied; this is an editable rendition, not a cryptographically attested rendering of the document.

Current export scope: prepared 720p BEN videos, up to 30 seconds, text and subtitle styles/positions/timing. Preview mute and volume do not change exported audio. The download is a derived file; the original and saved revisions are untouched. MP4 bytes are downloaded on demand, not added as another saved library item. Export again from the saved revision if needed.

## Rollout

Run migration chat_video_sources_v1 from video_edit_documents_v1 with the existing migration role. Grant SELECT, INSERT on ben.chat_video_sources to the existing ben_media_runtime role. Verify FORCE ROW LEVEL SECURITY remains on and the role remains NOSUPERUSER/NOBYPASSRLS. Do not broaden feature flags or pilot accounts.

## Verification

CI includes real HEVC projectless admission, idempotent recovery, owner RLS, worker publication, existing project uploads, deterministic export with timed overlay pixel checks and AAC preservation, endpoint bounds and access revocation. Browser acceptance separately checks rasterized Hebrew/English, transparent background, fonts and shadows.
