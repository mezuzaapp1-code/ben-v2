# B01: portable editing document

This increment adds a **pure data contract and adapters**, not server saving or new UI. It is based on main `cffba1182a1e12f565e13fe5bf0f7aff43854f7f` (PR 79). PR 82's planning changes were still open when checked; none are imported here. The unpublished rich editor remains a separate working artifact.

## Shape

`video-edit-v1` has document_id, kind=video, source {resource_id, duration_seconds, timebase=seconds}, and body {cues, style, textLayers}. Resource/document IDs are canonical UUID strings; text IDs are bounded ASCII identifiers. Source bytes, URLs, storage keys, owner claims, costs, execution IDs and consent grants do not belong in this payload.

The frontend exports `editorDraftToDocument`, `validateEditDocument` and `documentToEditorDraft`. Restore requires the selected resource ID and probed duration; mismatches fail. Returned structures are detached. Known omitted style properties receive documented defaults; unknown properties fail rather than being silently stripped. Old subtitle drafts without alignment/textLayers are accepted with auto alignment and an empty layer track. Rich text layers, background None, shadow, outline and x/y positions roundtrip without changing text or fractional seconds. x/y are percentages of the video frame; size is the existing editor's cqw unit, outline/spacing use em. No text is interpreted as markup.

Python consumers use `validate_edit_document` or `parse_edit_document` (the public bounded entrypoints), not raw model construction. Parsing rejects duplicate JSON keys. Values must be finite, UTF-8 encodable JSON. Maximum compact UTF-8 payload is 1,000,000 bytes; 300 subtitles; 20 independent layers; 1,000 Unicode code points per text item. The 86,400-second parser ceiling is a defensive budget only. It **does not increase** any upload, model, planner or export limit. The existing 50ms end tolerance is retained; no frames are inferred from average FPS.

Empty independent text layers can be saved while editing; subtitle cues must contain visible non-whitespace text. IDs are unique within each separate track (cue and layer IDs do not address one another). Source duration and resource IDs are syntactically checked claims only. A future service must resolve ownership, integrity checksum, actual duration and supported execution profile against authorized storage. No schema validation confers authorization.

## Usage boundary

Frontend adapter converts an editor draft plus stable source/document metadata. Caller creates the draft document ID once and retains it; never create a new ID on every Save. It does not write localStorage or send HTTP requests. There is no migration of existing local saved drafts in this increment and no live editor call site yet.

Next PR: choose existing authorized storage or a dedicated editing revision store after review; atomic save with base revision, idempotency key and conflict handling. Keep source, editing revisions, planning revisions and paid execution attempts distinct. Immutable revision IDs, actor/timestamps and checksums must be produced by the server. Do not mutate the production-plan schema to smuggle editing layers into it.

## Verification

- `python -m pytest tests/test_edit_document.py -q`
- `EDIT_DOCUMENT_RESULT=edit-document-js.json node frontend/scripts/test-edit-document.mjs`
- `python scripts/check_edit_document_parity.py edit-document-js.json`

Node uses built-in test facilities; no npm install is needed for these tests. Python and JS independently validate the shared Hebrew/English fixture and negative cases; parity compares normalized field values. Tests exercise unknown fields, nonfinite numbers, wrong resources, Unicode, style limits, duplicate IDs, byte budgets and detached roundtrips. Workflow is isolated and invokes no application, database or paid provider.

This does not prove RLS, DB durability, preview/export parity, cross-device resume or production deployment. Those are explicit later gates. It also does not import the unrelated AI planner WIP.
