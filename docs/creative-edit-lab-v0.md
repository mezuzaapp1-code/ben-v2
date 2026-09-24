# Creative Edit Lab V0 — Gate 1 checkpoint

Scope: manual deterministic image experiment, Gates 1–3 authorized. This
checkpoint implements **Gate 1 only**. A sofa input has not been supplied; Gate 2
visual quality cannot be established, so Gates 2–3 have not been implemented.
No provider call, database migration, creative revision system or edit operation
is introduced here.

## Internal access

The existing media pilot must authorize the principal. Set
`BEN_CREATIVE_LAB_ENABLED=1` in an approved local/test environment only. No
production environment or secret changes are part of this work.

The internal MediaComposer panel receives the active workspace and uses the
existing authenticated Workspace Files upload endpoint. The Lab reads that
original through existing workspace authorization, checks stored size/checksum,
and rechecks source availability after processing. Original ownership and bytes
are unchanged. Preview responses use `private, no-store` and contain no paths.
The new route is `/api/media/creative-lab/canonical` with workspace/file UUIDs.

Canonical previews are derived responses, not published BEN media resources.
No execution row is manufactured for image generation. Integration of a local
edit into the media lifecycle belongs to subsequent authorized gate work after
the visual experiment can proceed; this checkpoint does not claim it exists.

## Canonical contract

- Static opaque RGB PNG/JPEG only; reject alpha, palette/gray/CMYK, animation,
  invalid ICC, corrupt data, oversized input and unsupported transfer metadata.
- Original limit 20 MiB; decoded limit 20 million pixels; derivative limit 64 MiB.
- Apply EXIF orientation exactly once, then ICC-to-sRGB conversion where a valid
  profile exists. Untagged input is explicitly reported as assumed sRGB.
- Save clean RGB PNG with an sRGB chunk and no inherited EXIF/text/profile data.
- Pixel hash covers versioned RGB8 interpretation, width/height and decoded bytes.
- Reopen output PNG and verify identical decoded pixels. Report byte and pixel
  hashes independently. Compression identity is not a preservation claim.
- Timing is wall-clock canonicalization latency. Local compute cost is unknown,
  not zero. Provider calls and generation cost are zero for this implementation.
- Browser display of the original is not the canonicalization validator.

## Tests and boundaries

`python -m pytest tests/test_creative_lab.py -q` tests actual codecs, EXIF,
profiles, deterministic reload, bounds and fail-closed HTTP behavior.
HTTP authorization tests use explicit service doubles; they do not claim new
PostgreSQL/RLS coverage or an authenticated browser end-to-end proof.

On this Windows sandbox Python 3.14's private temporary-directory ACL blocks
pytest's default temp path. The existing external `work/run_media_tests.py`
runner retains inherited scratch ACLs without changing assertions. The normal
GitHub-hosted validation job uses the unmodified pytest command.

The GitHub validation workflow also admits the isolated Lab branch for build
and regressions. Paid proof and recovery jobs remain restricted to the original
media branch and explicit dispatch inputs; a Lab branch push cannot run them.

Next required input: the actual sofa PNG/JPEG with the two target cushions.
No Gate 2/3 PASS, outside-support metric or recolor artifact is claimed yet.
