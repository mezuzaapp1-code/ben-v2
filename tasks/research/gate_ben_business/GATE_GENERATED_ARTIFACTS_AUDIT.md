# AUDIT — Generated Business Artifacts (FILE OUT)

**Mode:** PLANNING / CODEBASE AUDIT ONLY  
**Date:** 2026-09-13  
**P1:** unchanged (Business draft + Private Supplier Directory + PROCUREMENT conversation)  
**This capability:** **DEFER** (not in P1)  
**Production:** no code, migrations, or deploys

---

## 1. What already exists

### FILE IN (Workspace File Library) — production

| Piece | Path | Role |
|---|---|---|
| `WorkspaceFile` | `database/models.py` | User-uploaded file; extraction/index/initial-read lifecycle |
| Durable bytes | `services/workspace_files/storage.py` | `org_id/workspace_id/file_id/filename` under `projects_root()/_workspace_files/` |
| `write_upload` | same | Stream **uploads** only; fsync + checksum |
| Download | `GET /api/workspaces/{id}/files/{id}/content` | Authz then `FileResponse`; `inline` vs `attachment` |
| Client blob | `frontend/src/api/workspaceFiles.js` `fetchWorkspaceFileBlob` | Authenticated fetch; File Library preview/download |
| Upload UI | `FileLibraryOverlay.jsx`, composer `file_ref` | User attaches files **into** chat |
| `file_ref` | `services/message_format.py` | User-turn parts pointing at **uploaded** `WorkspaceFile` ids |
| CSV/XLSX **read** | `extract.py` | Decode CSV as text; XLSX via stdlib zip/XML (not a writer) |
| PDF **read** | `pypdf` in `requirements.txt` | Extraction only |
| Domain boundary | `domain_boundary.py` | Files ≠ News; uploads never become news |

Download is **not** public: Gate A persistent identity, org + workspace match, 404 if missing. No unsigned URL.

### FILE OUT — does not exist

Searched: XLSX/CSV/PDF **generation**, conversation export, generated attachments, assistant-message file parts, spreadsheet writer libraries, quote/report file exporters.

Findings:

- **No** `openpyxl`, `xlsxwriter`, `reportlab`, `weasyprint`, `jspdf`, SheetJS, ExcelJS in app deps.
- The only XLSX **writer** in-tree is `_minimal_xlsx()` in `scripts/validate_workspace_files_v1.py` — a test fixture to **upload** a fake sheet, not a product generator.
- `StreamingResponse` in `main.py` is **NDJSON chat/council streams**, not file bytes.
- Council `deliverable_artifact` (`synthesis_delivery.py`, `App.jsx`) is a **text block** in the message, not a downloadable file.
- Construction quotation / `analyze_supplier_tender` / Action Cards render **UI + WhatsApp text**, not XLSX/PDF.
- No conversation-export endpoint.
- `Message` is `role` + `content` text; no attachment column for assistant outputs.

**Verdict:** BEN can **read** CSV/XLSX/PDF and **download what the user uploaded**. BEN cannot **generate** a spreadsheet/PDF and return it as a conversation artifact.

---

## 2. A vs B

| | A. WorkspaceFile | B. GeneratedArtifact |
|---|---|---|
| Direction | FILE IN | FILE OUT |
| Source of truth | User bytes | Structured business data (Intent, Quote, directory, …) then serialized |
| Lifecycle | upload → extract → index → retrieve into context | generate → store → authorize download → cite in assistant message |
| Failure modes | parse/OCR/FTS | wrong dataset, over-sharing, stale snapshot |
| Library semantics | “information for BEN to read” (`WorkspaceFile` docstring) | “work product for the human to take away” |

`WorkspaceFile` is **not** a suitable primitive for B:

- Status machine assumes upload/extraction/indexing/initial-read.
- Chat `file_ref` is a **user-turn** ingest path (`user_turn_file_ref_ids`).
- Putting generated XLSX through extract/FTS would index derived data as if it were source documents (contamination).
- `uploaded_by` / `source_chat_id` do not capture generator, schema version, or source object ids.

Do **not** force B into A. Shared **byte storage** is a different question (§4).

---

## 3. Minimum future architecture (do not implement)

```
authorized query over conversation/business data
  → snapshot dataset (JSON rows; provenance of each column)
  → renderer (CSV trivially; XLSX via a dedicated writer; PDF later)
  → durable bytes (authz-scoped key)
  → GeneratedArtifact row
  → assistant message part { type: generated_artifact_ref, id, name }
  → GET .../artifacts/{id}/content  (same auth pattern as files, different object)
```

Smallest useful renderer: **CSV first**, XLSX as the user-visible default once a writer exists. PDF is a later renderer on the same snapshot.

The dataset snapshot must be stored (or reproducibly derived) so the file is an **export of authorized rows**, not a free-form model hallucination written to disk.

---

## 4. Reuse storage for bytes?

**Yes, physically; no, as the domain row.**

Reuse:

- Durable root, org/workspace path isolation, checksum, fsync, `FileResponse` after authz, no public URL.
- A sibling prefix is cleaner than mixing keys: e.g. `_generated_artifacts/{org}/{business_or_workspace}/{artifact_id}/…` so File Library listing never accidentally includes FILE OUT.

Do not reuse:

- `write_upload` (UploadFile-only)
- `WorkspaceFile` insert
- extraction/indexing pipeline
- File Library list/search as the user-facing catalog of generated work

Need a `write_bytes` (or equivalent) **beside** `write_upload`, not an upload fake.

Workspace is today’s storage axis (`workspace_id`). P1 `Business` may later own artifacts; until then, org + workspace/business id must both be on the artifact row so auth cannot drift.

---

## 5. Optional later attach/send (not designed now)

Once a `GeneratedArtifact` exists with org authz:

- **Save to workspace:** optional copy or *link* into File Library **as a derived FILE IN** only if the user explicitly wants BEN to *read* it later. Default: stay FILE OUT only.
- **Attach to customer/supplier/RFQ/Quote:** nullable FKs on the artifact row; do not invent those FKs in P1.
- **Email / WhatsApp:** send **bytes after the same authz check**; current `wa.me` cannot attach files. WhatsApp Business API / email MIME are separate deferred gates.

Do not design those integrations in this audit.

---

## 6. Security

Inherit authorization from the **conversation + business data** used:

- Same org as the thread; never cross-org.
- No public/unsigned URL by default (copy File Library: JWT + tenant bind + 404).
- Generator may only serialize rows the requesting principal could already read (private suppliers of *this* business; quotes of *this* RFQ).
- Snapshot at generation time so a later permission shrink cannot be bypassed by an old guessable URL — still require current auth; if access revoked, download 404s even if bytes remain.
- Model must not dump arbitrary SQL/files into the artifact; renderer consumes a **server-built** dataset, not LLM-authored bytes.

---

## 7. P1 interaction — no change

P1 stays: Business draft + Private Supplier Directory + PROCUREMENT conversation.

P1 does **not** block FILE OUT if it:

- keeps directory and future Quote/RFQ as **structured objects** (P2), not only chat prose
- stamps `org_id` and `business_id` on the Procurement thread (already in P1 plan)
- does **not** store supplier lists *only* as uploaded WorkspaceFiles without a `PrivateSupplier` row (P1 already requires owner-accepted directory rows)

P1 would **complicate** FILE OUT if it later stuffed comparison tables into chat text only, or uploaded generated sheets as WorkspaceFiles to “have a file.” Do not do that in P1 or P2.

**P1 requires no schema or product change for artifacts.**

---

## 8. Recommendation

| Layer | Verdict |
|---|---|
| GeneratedArtifact **capability** (now) | **DEFER** |
| Domain object (when gated) | **NEW** |
| Durable byte storage | **EXTEND** (`write_bytes` + sibling prefix) |
| Authn/download pattern | **REUSE** (Gate A + org/workspace FileResponse) |
| WorkspaceFile / File Library | **Do not reuse** as the object |
| Chat `file_ref` | **EXTEND later** with a distinct `generated_artifact_ref` |
| XLSX/PDF libraries | **NEW** dependency when that gate starts; CSV can be stdlib |
| Email/WhatsApp send, save-to-library | **DEFER** |

Overall: **DEFER** the feature; when built, **NEW object + EXTEND storage + REUSE auth**.

---

## Answers

**A. What exists today?** FILE IN library: durable org-scoped bytes, authenticated download/preview, user `file_ref` in chat, CSV/XLSX/PDF **parsers**. Text “deliverable” in council. No FILE OUT generator.

**B. What is missing?** Dataset snapshot, renderer (XLSX/CSV/PDF writers), `GeneratedArtifact` row, assistant attachment part, download route for generated ids, `write_bytes`, UI chip/download in the thread.

**C. Can current storage be reused?** Yes for **bytes and authz mechanics**. No for the **WorkspaceFile** semantic/pipeline.

**D. Should GeneratedArtifact be a separate domain object?** **Yes.**

**E. Does P1 require any change now?** **No.** Do not add artifact generation to P1.

**F. Smallest future FILE-OUT gate?** After P2 (structured quotes in a Procurement thread): one authorized dataset (e.g. quote comparison rows) → server-built CSV or XLSX → `GeneratedArtifact` + thread ref + authenticated download. No email, WhatsApp attach, MD, or File Library indexing.

STOP.

No implementation. No migrations. No production changes. Do not start this gate.
