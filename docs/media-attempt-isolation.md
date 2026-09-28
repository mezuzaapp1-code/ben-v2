# Bounded immutable MP4 attempts

An internal opt-in publication boundary uses the existing media_executions row,
resource identity, validator and immutable byte store. No schema, provider,
request contract, scheduler, narration operation or UI is introduced. Existing
provider tick dispatch and legacy paths are unchanged.

Each server-generated UUID attempt writes `_media/{org}/{resource}/attempts/{execution}/{attempt}.mp4`.
Completed files are never promoted, overwritten or removed by this boundary.
The database chooses the winner via a row-locked completion update that checks
current identity, owner, version, ingesting state, destination, wall-clock lease
and deadline. Failed updates leave non-authoritative orphan bytes.

Protected delivery retains principal/destination checks before and after I/O,
bounded reads, expected size and SHA256 validation. Attempt keys must exactly
match their org/resource/execution UUID layout. Redirected symlink/junction paths
are rejected. Storage directories must remain application-controlled; this is
not a defense against an attacker concurrently mutating the local filesystem.

After an uncertain commit, read_video_attempt_outcome reads by execution ID and
uses protected delivery if succeeded. A pending result or read failure does not
authorize rendering, deletion or adoption. Orphan discovery/adoption and cleanup
are deliberately absent. Unknown costs are left unknown.

Tests distinguish three failures: partial local write; actual PostgreSQL backend
termination while a storage thread is paused after writing and then continues
after lease expiry/transfer; and injected lost response at the transaction
boundary, separately for real committed and rolled-back transactions. The last
case is response-boundary fault injection, not a TCP proxy dropping COMMIT ACK.
Lease expiry is a targeted administrative timestamp update, not an arbitrary
sleep. Driver close is observed before querying the terminated connection.

Synthetic MP4 fixtures are generated with PyAV. This slice does not prove FFmpeg
compositing, source-stream preservation, narration, large files, load capacity,
or process crash cleanup. Existing provider code does not automatically opt in.
