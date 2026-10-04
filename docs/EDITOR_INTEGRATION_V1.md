# Private saved video editing (B03)

BEN's existing conversation video opens the full editor: subtitles, free text,
font/color/background/shadow controls, free positioning, sound preview, Undo/Redo
and a collapsible panel for desktop and mobile. With
`BEN_MEDIA_EDIT_DOCUMENTS_ENABLED=1` and pilot access, Save writes an immutable
revision to the authenticated account. With the flag off, explicitly labelled
browser-local saving remains available.

The + menu and conversation media expose Edit video. My saved work discovers
private edits across conversations through GET `/api/media/edit-documents`.
Optional `resource_id` filters the same owner-scoped collection. Responses expose
only document/resource IDs, version number and update time. The collection is
bounded by the existing 100-document owner quota; source/conversation RLS and
source checksum checks still apply. There is no browser-only document index.

Opening a video loads its latest saved document. Version history can load older
content into the draft; Save restores it as a new immutable version. The source
video remains unchanged. SRT download includes subtitles only; this slice does
not render overlays into an MP4. Saved editing retains B02's 30-second limit.

## Recovery and conflicts

- The exact save body and idempotency key are stored under account + resource
  before POST. No POST occurs if recovery storage cannot be written.
- A timeout/503 leaves the draft and key intact. Reload shows the pending draft;
  only explicit Retry save resubmits its identical request. Editing is locked
  until that uncertainty is resolved. No generation/provider call is involved.
- Controls and keyboard Undo are locked while a save is in flight. No early
  "saved" acknowledgement is shown.
- A rejected conflicting save keeps the current draft. Load latest explicitly
  loads the server head and preserves current changes (including caption typing)
  in Undo. Saving old content never silently overwrites another revision.
- Scope/resource keys separate accounts and videos. Authorization remains server
  enforced on every request. Authentication-header refresh does not recreate the
  editing session or erase an open draft.

## Validation and rollout

`test-edit-session.mjs` covers lost response/reload, exact replay, storage failure,
account scoping, conflicts and source mismatch. `test-saved-editor-ui.mjs` drives
the actual React editor through save/reopen/restore, blocked concurrent controls,
conflict recovery and reload with an uncertain save. Existing text editor and
media UI regressions remain in CI. Real PostgreSQL tests cover list privacy and
source checksum changes, in addition to B02's immutable history and concurrency.

Apply `video_edit_documents_v1` and explicit runtime grants from
EDITOR_PERSISTENCE_V1.md before enabling the pilot flag. This code change alone
does not apply the production migration or enable access. Roll back operationally
by disabling the flag, retaining saved records.

Before opening user trials, separately close the remaining video-upload project
dependency, verify real production save/reopen under the pilot account, and finish
the preview-compatible MP4 export slice. Research launch experiments are paused.


## Main-menu media entry

The main navigation now exposes **Media editor** and **My saved work** (with Hebrew labels when the UI locale is Hebrew). Both open an account-scoped responsive Media studio drawer, independent of project selection. The composer shortcut opens the same drawer.

The saved tab reads the authenticated edit-document collection. The editor tab lists only completed MP4 resources in the current conversation. Selecting a card opens the existing saved video editor; closing it refreshes saved versions. Loading failures are retryable and unavailable media can be dismissed. The drawer cannot close behind an active editor, so the editor retains control of unsaved-change and in-flight-save protection.

This is navigation into existing editing capabilities, not an all-media catalog: generated images remain in their conversations, and MP4 export with edits is not added. No migration, provider submission, or feature-flag broadening is needed.

Validation: `node scripts/test-media-library.mjs` exercises the real React components, authenticated reads, empty/error/retry states, video selection, editor Escape confirmation, failed media recovery, and account remount isolation.
