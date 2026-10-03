# Production Plan v1: planning-only foundation

This change is stacked on managed-short-render PR #80 (migration 037). It adds
migration 038, a Pydantic/OpenAPI contract, default-off save/read endpoints and
PostgreSQL tests. It does not call a language model, TTS, image/video provider or
renderer. It does not implement the planning UI or claim a finished video flow.

## Deliberately bounded contract

Three ordered scenes, 15 seconds ± at most 100ms, 720x1280 portrait. Order is the
array order, not a second potentially inconsistent index. UUID scene IDs are
unique. Duration is calculated from scene lengths minus crossfade overlaps;
cut transitions have no overlap, the last scene has no transition and adjacent
transitions cannot overrun a scene. Narration fit must be measured later, not
silently repaired by loops or freeze frames.

Visuals are discriminated unions: animated_attachment, generated_scene and
controlled_composition. Typed overlays are text and repeated sprites. Attachment
roles are animate_source, character_reference, background and style_reference.
Only existing conversation-owned chat photos or succeeded owned BEN PNG resources
from the same conversation are accepted. No client-provided checksums, URLs,
storage paths, trusted validation flags, prices or provider scripts are accepted.
Sprite cutout quality and visual counts are future render validation requirements;
a valid plan is not proof of visual quality. Music, reuse IDs, quotes and approval
are intentionally not accepted until their respective server checks exist.

## API

Set BEN_MEDIA_PLAN_ENABLED=1 only after migration and runtime-role grants in an
approved environment. Existing pilot authorization also applies. Default is 0.

POST `/api/media/production-plans`, with `Idempotency-Key`, accepts:
`{conversation_id, parent_version_id?: UUID, plan: ProductionPlan}`.
The plan contains original_brief, optional language, attachments and exactly three
scenes. Full typed JSON Schema is exposed in FastAPI OpenAPI, for the next UI step.

Response fields: id (version ID), plan_id, version, conversation_id,
parent_version_id, created_at, payload. GET `/api/media/production-plans/{id}`
returns an owned saved version. No internal asset snapshot or storage key is exposed.

A revision submits the complete new plan and the current parent version ID with
a new idempotency key. One parent can have only one successor; racing edits return
409 rather than silently overwriting or forking. Replay of the same key/body returns
the saved version; a changed request returns 409. Replaying a saved draft does not
authorize execution. Execution must freshly recheck source access and identity.

## Persistence decision

Existing media_executions represents billable attempts, not editable intent. A
separate media_plan_versions table avoids fabricating paid executions for drafts.
Plan/version IDs and version numbers are server-generated. Source checksums are
computed from authorized bytes and rechecked under database locks before insertion.
RLS is forced for org isolation; application queries also enforce creator and
conversation. A database trigger rejects all UPDATEs and mismatched parent lineage.

Grant the dedicated media runtime role SELECT, INSERT on ben.media_plan_versions
(and its already required photo-source read access). Do not grant DELETE/UPDATE.
No grants are applied to an assumed production role by the migration. Conversation
deletion deliberately cascades to plans; immutable means no rewrite, not retention
against user deletion. Downgrade refuses to remove a table containing plan history.

No execution linkage column is added yet: the next execution slice must link each
attempt to an exact version ID and revalidate sources, approvals and budget before
dispatch. There is no implicit conversion to PR #80's five-scene renderer.

## Validation

Pure tests reject malformed timing, dangling references, forged trusted fields,
arbitrary overlay payloads and gated API access before service creation. Real
disposable PostgreSQL tests cover concurrent replay, competing revisions, immutable
history, role privileges, owner/org isolation, source corruption, conversation
deletion, parent lineage and downgrade safety. No paid calls are made.
