# FILE OUT OSS Review — Planning Only

**Status:** RESEARCH COMPLETE. DO NOT IMPLEMENT. DO NOT OPEN A GATE.  
**Date:** 2026-09-13  
**Method:** Official docs + GitHub API/raw source review. No clones, no installs, no execution of unreviewed third-party code, no BEN dependency additions, no migrations, no production changes.  
**P1:** Remains the next product gate. This review found **no architectural blocker** that would replace P1 with FILE OUT.

---

## 0. Verdict in one page

There is **no** mature open-source project BEN should vendor as FILE OUT.

Combine two patterns:

1. **ADAPT** Daita’s artifact object: opaque ID, immutable bytes, provenance, conversation linkage, known-ID recovery, creating an artifact ≠ delivering it, FILE IN ≠ FILE OUT.
2. **LEARN** Frame AgentOS `document_export`: structured content → deterministic renderer → buffer. Do not copy its public URLs, filesystem paths, or auto-inserted Excel `SUM` formulas.

**REJECT** path-based chat delivery (Hermes Deliverable Mode, Hermes Studio download-by-path).  
**REJECT** unrestricted code execution as the generator for business XLSX/CSV.  
**REJECT** public download/preview URLs.

The candidate BEN shape is **confirmed**, not invented:

```text
authorized structured data
  → deterministic renderer (literal XLSX/CSV)
  → write_bytes under a sibling durable prefix
  → GeneratedArtifact metadata in Postgres
  → generated_artifact_ref (ID) on the assistant message
  → authenticated download (reuse WorkspaceFile auth pattern, different object)
```

Later, optional and explicit:

```text
GeneratedArtifact → user action “Save to Knowledge” → WorkspaceFile → extraction/indexing
```

**FILE-OUT 1 is defined below and must not be opened.**

A previously cited `GATE_GENERATED_ARTIFACTS_AUDIT.md` is **not present** on `main` or this checkout. This document is the verified FILE OUT OSS review of record for this repository snapshot.

---

## 1. Verified repositories and docs

Signals (stars, forks, issue counts) are weak. Activity dates are GitHub API as of 2026-09-13.

### Name collisions (do not conflate)

| User label | Verified primary | Also exists | Do not confuse with |
|---|---|---|---|
| Hermes Studio | `EKKOLearnAI/hermes-studio` (formerly `hermes-web-ui`; GitHub redirects) | Desktop/web UI around Nous Hermes Agent. Product now branded Ekko Studio. Site: `https://ekkostudio.xyz`. Latest release `v0.7.21` (2026-09-12). License **BSL 1.1**. | `Instabidsai/agenthermes` (Agent Readiness Levels) — **not** Studio. |
| Hermes Agent / Deliverable Mode | `NousResearch/hermes-agent` MIT. Docs: `https://hermes-agent.nousresearch.com/docs/user-guide/features/deliverable-mode`. Source doc: `website/docs/user-guide/features/deliverable-mode.md`. | Active local/self-hosted agent. Huge star count (~245k) and ~42k open issues: treat stars as **noise**. Production-relevant as a **single-user gateway**, not a multi-tenant SaaS file product. | Hermes Studio (UI only). |
| Daita Agents | `Daita-Corp/daita-agents` MIT (`LICENSE` file is MIT; `pyproject.toml` `license = "MIT"`; version **1.0.1**). Docs: `https://docs.daita-tech.io/` (homepage fetched; several inner guide URLs returned HTTP 403 from this environment). In-repo: `docs/ARTIFACTS.md`, `docs/LOCAL_WORKSPACES.md`, `src/daita/artifacts/`. PyPI still lists a **legacy** `daita-agents` 0.19.0 line; README says 0.x→1.x is a different family with **no migration**. | Active (pushed 2026-09-13). Low stars (~4). Architecture quality is high relative to popularity. | Eval “artifacts” (`.daita/evals/runs/`) — test reports, not FILE OUT. |
| AgentOS | **Three unrelated products.** Review treats the user request as “AgentOS FILE OUT,” so all three are identified; only two have document generation. | See §1.4. | Do not merge Frame, use-agent-os, and Agno. |

**Unverified / not used as evidence**

- Inner Daita hosted docs (`docs.daita-tech.io/guides/create-artifacts` and similar) returned **403** here. Artifact architecture is taken from the GitHub repo (`docs/ARTIFACTS.md` + `src/daita/artifacts/`), which **was** fetched.
- A prior in-thread claim that Daita’s LICENSE file is Apache-2.0 while GitHub metadata says MIT is **stale**. Current `LICENSE` is MIT.
- `GATE_GENERATED_ARTIFACTS_AUDIT.md` is **not** in this repo on `main` or this branch. It cannot be verified from this checkout.

### 1.1 Hermes Studio — verified

| Field | Value |
|---|---|
| Repo | https://github.com/EKKOLearnAI/hermes-studio |
| Docs / site | README in-repo; homepage https://ekkostudio.xyz ; downloads https://download.ekkolearnai.com/latest |
| Activity | Active (push 2026-09-13; release v0.7.21) |
| Maturity | Production-relevant **local/self-hosted UI**. Not a multi-tenant business-file platform. |
| License | Business Source License 1.1 (commercial use restricted until 2029-05-10, then Apache-2.0) |
| CVE | CVE-2026-67918: path traversal on `/api/hermes/download` `validatePath` in **v0.6.26**. Advisory GHSA-5f28-xj73-vm62. |

### 1.2 Hermes Agent / Deliverable Mode — verified

| Field | Value |
|---|---|
| Repo | https://github.com/NousResearch/hermes-agent |
| Docs | https://hermes-agent.nousresearch.com/docs/user-guide/features/deliverable-mode |
| Activity | Very active (push 2026-09-13) |
| Maturity | Production-relevant **personal agent + messaging gateway**. Experimental as an org-scoped FILE OUT design. |
| License | MIT |
| Related code | `extract_local_files()` in gateway; PRs #1640, #27813; MEDIA path hardening PR #16547 |

### 1.3 Daita Agents — verified

| Field | Value |
|---|---|
| Repo | https://github.com/Daita-Corp/daita-agents |
| Docs | https://docs.daita-tech.io/ plus in-repo `docs/ARTIFACTS.md` |
| Activity | Active (push 2026-09-13; pyproject 1.0.1) |
| Maturity | Early product (beta classifier, 4 stars) with unusually explicit artifact contracts and tests. Not proven multi-tenant SaaS. |
| License | MIT |

### 1.4 AgentOS — three products

| Product | Repo / docs | FILE OUT relevance | License | Activity |
|---|---|---|---|---|
| **Frame AgentOS** + document-export | https://github.com/framerslab/agentos-extensions (`@framers/agentos-ext-document-export`); docs https://docs.agentos.sh/extensions ; types/generators under `registry/curated/productivity/document-export/` | **Yes** — structured export to PDF/DOCX/PPTX/CSV/XLSX | Repo Apache-2.0; package.json of document-export says **MIT** (flag dual-license confusion). 1 star; last push 2026-08-10 | Experimental / low adoption |
| **use-agent-os** | https://github.com/use-agent-os/agent-os Apache-2.0. Skills: `src/agentos/skills/bundled/{xlsx,docx,pptx,html-to-pdf}/` | **Yes** — skill/code-execution file authoring, not an artifact store | Apache-2.0 | Active (push 2026-09-13; ~53 stars, 211 open issues) |
| **Agno AgentOS** | https://github.com/agno-agi/agno ; https://docs.agno.com/agent-os/introduction | **No FILE OUT architecture.** FastAPI runtime for agents (sessions, RBAC, Slack/WhatsApp). Workspace tool is generic FS read/write. | Apache-2.0 (Agno) | Production runtime — **wrong layer** for BEN FILE OUT |

This review scores Frame document-export and use-agent-os separately. Agno is identified so it is not silently reused as “the AgentOS pattern.”

---

## 2. Per-project findings (A–J)

### 2.1 Hermes Studio

**A. Problem / what “artifact” means**  
Studio is a **chat/workspace UI** around Hermes (and other runtimes). “Generated file” means whatever the agent wrote on disk that Studio can preview or download. It is not a first-class durable business object. Uploads are profile-scoped; generated artifacts are downloaded **by resolved path outside the upload directory**.

**B. Architecture**

```text
Hermes/other agent tools
  → file on local/SSH/Docker/Singularity FS
  → Studio path resolver
  → /api/hermes/download (path)
  → inline preview by extension
```

No independent artifact schema. Identity is a filesystem path. MIME is extension-driven. Ownership is profile/session, not org. Persistence is whatever the agent workspace kept. No BEN-like provenance. Cleanup/TTL not a product object.

**C. Chat integration**  
Generated-file **previews** in chat (HTML, PDF, DOCX, PPTX, XLSX, CSV, images, Markdown, source). Download is **path-based**. Chat does not store an opaque artifact ID as source of truth.

Chat stores: **raw path** (and UI preview). Not an artifact ID.

**D. Storage**  
Local (or remote-backend) filesystem. Uploads under `$HERMES_WEB_UI_HOME/upload` with profile subdirs. Generated files are **explicitly not** the upload tree. No org object storage. URLs are local API, not a public CDN — but the API is path-parameterized.

**E. Generators / types**  
Studio does **not** generate XLSX/PDF. It previews/downloads them. Generation is the agent (Hermes skills / `execute_code`).

**F. Security — do not copy**  
CVE-2026-67918: download-by-path traversal. Path APIs are the wrong trust boundary for a multi-tenant product. HTML/source previews of agent-written files are XSS/script risks. BSL blocks commercial embedding.

**G. Sandbox**  
N/A for Studio itself. Generation requires the agent runtime (often shell/Python).

**H. Lifecycle**  
Reopen/preview/download while the file still exists on that backend. No first-class regenerate/convert/share/CRM attach. Temporary vs durable is “still on disk.”

**I. FILE IN vs FILE OUT**  
Partial: uploads vs generated downloads use different directories. Same preview/download machinery. Not a semantic split (no provenance, no “do not index generated bytes”).

**J. Maturity**  
Active UI product, popular, BSL, real CVE on the exact FILE OUT surface BEN would be tempted to copy.

**Recommendation: REJECT** as architecture. LEARN only the UX idea “file card appears in the thread.”

---

### 2.2 Hermes Agent / Deliverable Mode

**A. Problem / what “artifact” means**  
A file the agent wrote and mentioned as an **absolute or `~/` path** with a supported extension, so the **messaging gateway** can upload it as a native Slack/Discord/Telegram/WhatsApp/Signal attachment. Also `kanban_complete(artifacts=[...paths...])`. Later optional `[[artifact:/abs/path|title]]` hint exists in the Hermes ecosystem; the official deliverable-mode doc still describes **path scrape**.

**B. Architecture**

```text
execute_code / skill (xlsx, docx, pdf, pptx, matplotlib, …)
  → write /tmp/report.xlsx (or ./out/)
  → assistant text contains /tmp/report.xlsx
  → gateway extract_local_files() / extract_media()
  → strip path from visible text
  → platform upload (image inline vs document)
```

No durable artifact object. File id = path. MIME ≈ extension table. Ownership = local user. Conversation linkage = the chat message the gateway posts. Persistence = local disk + whatever the chat platform stored. No TTL object. No versioning.

**C. Chat integration**  
Visible chat: native attachment. Internal SoR: **raw path in assistant text** (then stripped). Kanban: path list in tool metadata. Not an artifact ID.

Chat stores: **1. raw path** (gateway SoR). After delivery, the **platform** has a copy; Hermes does not keep a BEN-like ID.

**D. Storage**  
Local venv/sandbox disk (`/tmp`, `./out/`, workspace). Messaging platforms then hold their own copy. Uploaded user files and generated files are not a two-table model.

**E. Generators**  
Broad: images, video, audio, PDF, DOCX, XLSX, CSV, PPTX, HTML, archives, geo, …  
XLSX: **openpyxl** skill (`skills/productivity/xlsx/SKILL.md`); JSON spec → `xlsx_create.py`; optional LibreOffice recalc.  
PDF: **reportlab** + pypdf + pdfplumber (`skills/productivity/pdf/SKILL.md`); also latex-pdf-report skill.  
DOCX / PPTX: dedicated skills (python-docx / python-pptx family).  
This is a **skill + code execution** factory, not a trusted renderer.

**F. Security — do not copy**  
Path scrape of model text is prompt-injection → file exfiltration. PR #16547 had to constrain `MEDIA:` to cache/allowlisted roots. Bare-path delivery is still the documented happy path. Single-user assumption. HTML as file upload. `.apk`/archives in the extension list.

**G. Sandbox**  
**Requires** shell/Python/`execute_code`, often LibreOffice. Not acceptable as BEN V1 generator.

**H. Lifecycle**  
Same-thread: if the file still exists. Edit/regenerate = agent writes another path. Share = the messaging platform. No CRM object. Distinguishes “mentioned path” from “platform attachment,” not temporary vs work product.

**I. FILE IN vs FILE OUT**  
Not clean. Workspace files, skills, and generated `/tmp` files share a disk mental model.

**J. Maturity**  
Very active agent. Deliverable Mode is a **UX pattern for consumer messengers**, not a multi-tenant artifact service.

**Recommendation: LEARN** (file appears in thread; extension routing). **REJECT** path-as-SoR, code execution, and gateway scrape.

---

### 2.3 Daita Agents

**A. Problem / what “artifact” means**  
A **committed internal object**: immutable bytes + manifest (`ArtifactRef`) **before** any optional local delivery. Several kinds:

| Kind | Tool / renderer | Meaning |
|---|---|---|
| Exact tabular export | `data_export_tabular` | Validated SQL → complete CSV or XLSX; authorship `exact_source_data` |
| Model-authored table | `artifact_create_tabular` | Bounded findings as CSV/XLSX/HTML; requires evidence tool-call IDs |
| Narrative | `artifact_create_document` | Markdown/plain text |
| Result snapshot | `artifact_snapshot_result` | Exact JSON copy of one validated tool result |
| Text edit | `artifact_edit_text` then `artifact_save_local` | Replacement bytes as artifact; workspace unchanged until approved save |

**B. Architecture**

```text
validated query or authenticated tool result
  → ExactCsvRenderer / ExactXlsxRenderer / document renderer
  → ArtifactDraft (bytes + filename + media_type + sensitivity + provenance)
  → AgentHomeArtifactStore commit (agent_home/artifacts, staging, sha256)
  → ArtifactRef id `artifact-<32 hex>`
  → later-turn artifact_list (current conversation) / artifact_read (known ID)
  → optional artifact_convert (Daita XLSX Data snapshot → CSV)
  → optional approval-gated artifact_save_local
```

Schema (from `src/daita/artifacts/models.py`):

- `ArtifactRef`: `artifact_id`, `run_id`, `conversation_id`, `call_id`, `capability_id`, `filename`, `media_type`, `byte_size`, `sha256`, `sensitivity`, `provenance`, `created_at`
- `ArtifactProvenance`: `authorship`, `evidence_call_ids`, `derived_from_artifact_id`, resource bindings, optional SQL fingerprint, columns, row_count
- Bounds: 64 MiB/artifact, 8 artifacts/run, 10k/agent, etc.
- Store: immutable payloads under one agent-home layout; commit timeout; cancellation-before-publish gate

**C. Chat integration**  
Terminal/agent loop: tool results carry **artifact IDs**. `artifact_list` is **current-conversation only**. `artifact_read` is known-ID, agent-owned, bounded preview. **No public inventory.** Clearing a conversation invalidates internal IDs; copies already delivered to user directories remain.

Chat/tool layer stores: **3. artifact ID** + structured ref. Local save may later report a **verified path** to the human — that path is a delivery receipt, not the SoR.

**D. Storage**  
Agent-home filesystem (`artifacts/` + staging), not DB blobs for bytes. SQLite holds agent identity, transcripts, jobs. Workspace files are a **read-first Files domain** that **never owns a writer**. Generated artifacts are a separate store. Public URLs: none. Recovery: `Agent.read_artifact` / `save_artifact` / `daita artifacts save` by known ID.

**E. Generators**  
CSV, XLSX, HTML tables, MD/TXT, JSON snapshots. **No PDF/DOCX/PPTX suite** in the artifact renderer.  
XLSX: runtime dep **XlsxWriter** (`_load_xlsxwriter`) plus **post-write verification** (`verify_exact_xlsx`) that inspects the zip/XML, prohibits hyperlinks/drawings/oleObjects/external relationships, and treats workbooks as **literal-only**. CSV cells get spreadsheet-formula protection (`_FORMULA_DANGEROUS` for `= + - @`). HTML escapes model-authored values and forbids external content. openpyxl is a **dev extra** (read/test), not the writer.

**F. Security — copy the *ideas*, not the single-tenant layout**  
Strong: formula protection, literal XLSX, no public inventory, known-ID access, sensitivity inheritance, absolute workspace paths never placed in model requests or artifact provenance (`LOCAL_WORKSPACES.md`).  
Do not copy: single-agent-home on one machine as if it were org tenancy; reporting verified local paths to clients in a SaaS.

**G. Sandbox**  
Tabular FILE OUT is **trusted renderers**, not `execute_code`. DuckDB for `file_query` is an isolated read worker (FILE IN analytics), not the XLSX writer. Text edits are bounded replacements, not a shell.

**H. Lifecycle**  
Same conversation: list/read. Convert: only verified XLSX Data snapshot → CSV, no source rerun. Local save is **explicit**. Create ≠ delivered. Conversation clear drops IDs, not already-saved copies. Distinguishes **internal artifact** vs **delivered work product**.

**I. FILE IN vs FILE OUT**  
**Cleanest of the set.** Catalog/sources and workspace reads are untrusted input. Writers go through artifact commit + optional approved delivery. Generated artifacts are not auto-cataloged as sources.

**J. Maturity**  
Young, well-specified, tested, MIT. Not a hosted multi-tenant proof. Best **semantic** fit for BEN.

**Recommendation: ADAPT** (object, IDs, provenance, renderer safety, FILE IN/OUT split). Do not vendor the TUI/agent loop.

---

### 2.4 Frame AgentOS — `@framers/agentos-ext-document-export`

**A. Problem / what “artifact” means**  
A generated export file from structured `DocumentContent`. Tool result is `DocumentExportOutput`: `filePath`, `downloadUrl`, `previewUrl`, `format`, `sizeBytes`, `filename`. Session object = file in `{workspace}/exports/`.

**B. Architecture**

```text
document_export({ format, content: DocumentContent, options })
  → CsvGenerator | XlsxGenerator | PdfGenerator | DocxGenerator | SlidesGenerator
  → Buffer
  → ExportFileManager.save → {timestamp}-{slug}.{ext} under workspace/exports/
  → downloadUrl = publicBaseUrl or http://localhost:3777/exports/{filename}
  → previewUrl = …/preview
```

Identity is **filename in exports dir**, not an opaque ID. `resolveManagedPath` rejects `..` and separators (better than Hermes Studio CVE, still filename-as-capability). No provenance, org, or conversation FK in the export types.

**C. Chat integration**  
Tool result envelope with **path + public URL + preview URL**. Chat likely stores whatever the agent copies from that JSON (often URL or path).

Chat stores: **2. public URL** and **1. raw path**. Not an artifact ID.

**D. Storage**  
Local `exports/` under agent workspace. Separated from generic workspace files by convention (`exports/`), not by a different security domain. `publicBaseUrl` is an explicit public-link feature.

**E. Generators**  
CSV (`csv-stringify`), XLSX (**ExcelJS**), PDF (**PDFKit**), DOCX (**docx** npm), PPTX (**pptxgenjs**). One `DocumentContent` shape (title, sections, tables, charts, images, lists).  
XLSX pattern: section tables → worksheets, header styling, numeric detection, **auto `SUM` formula row**, freeze header. That SUM behavior is a BEN anti-pattern for supplier/customer exports.

**F. Security — do not copy**  
Public download/preview URLs. Returning `filePath` to the model/client. Auto formulas in XLSX (CSV injection / unexpected computation). ImageSpec accepts remote `url` (SSRF/content risk). HTML widgets live in a **sibling** extension (`widget-generator`) — do not import that preview model. Low adoption; package license disagrees with repo license.

**G. Sandbox**  
**Trusted TypeScript renderers.** No shell required for the happy path. This is the right *generator class* for BEN, in Python, without public URLs.

**H. Lifecycle**  
List/delete files in `exports/`. Preview endpoint. No regenerate-as-new-ID, no CRM attach, no “save to knowledge.” Temporary vs durable = files left in exports/.

**I. FILE IN vs FILE OUT**  
Weak separation: same workspace disk, `exports/` subdirectory.

**J. Maturity**  
Experimental extension (v0.1.0, 1 star, last push Aug 2026). Useful as a **renderer sketch**, not a platform.

**Recommendation: LEARN** renderer split + structured input. **REJECT** URLs, paths-in-tool-output, and formula rows.

---

### 2.5 use-agent-os AgentOS

**A. Problem / what “artifact” means**  
Skills tell the model to write a file with `write_file` / `execute_code`, then `publish_artifact` **if that tool exists**. An artifact is a **workspace file the skill published**, not a separate business type. Skills also say: if no file-authoring tools, do not paste the file into chat.

**B. Architecture**

```text
skill (xlsx/docx/pptx/html-to-pdf)
  → python script or openpyxl/python-docx/python-pptx/WeasyPrint/reportlab
  → path in workspace
  → optional publish_artifact
```

`publish_artifact` is referenced from skill Markdown; this review **did not** retrieve a stable Python tool schema (search was polluted by GitHub Actions artifacts). Treat chat linkage as **path-centric** unless a later source shows an ID.

**C. Chat integration**  
Path + optional publish tool. Skills forbid dumping full source as the deliverable.

Chat stores: **1. raw path** (primary). ID unconfirmed.

**D. Storage**  
Agent workspace / state dir. Not a second object class vs uploads.

**E. Generators**  
XLSX **openpyxl** (inspect/edit/create; formulas if cell starts with `=`).  
DOCX **python-docx**. PPTX **python-pptx** (optional PptxGenJS + LibreOffice visual QA).  
PDF: WeasyPrint HTML path (`document-extras`) or **reportlab** via `pdf-toolkit` skill. Native deps (Pango/Cairo) for WeasyPrint.

**F. Security — do not copy**  
Unrestricted file authoring + shell. Unzip/repack OOXML. HTML/CSS → PDF (script/external resource risk depending on fetcher). `.xlsm`/macros explicitly out of scope — good — but the skill still writes formulas.

**G. Sandbox**  
**Requires** Python execution and often shell (`soffice`, unzip). Conflicts with BEN “deterministic renderer” and with `decision_003` (no autonomous agent loops).

**H. Lifecycle**  
Workspace file remains; publish is session delivery. Edit-in-place skills exist (not V1 for BEN). Share via channels (Telegram/Slack/Discord) is a product feature of this AgentOS, not BEN V1.

**I. FILE IN vs FILE OUT**  
Not clean. Same workspace.

**J. Maturity**  
Active Apache-2.0 agent OS. FILE OUT is a **skill pack**, not an artifact platform. 211 open issues; still early.

**Recommendation: LEARN** Python library list and “don’t paste bytes in chat.” **REJECT** as architecture (code execution, path SoR).

---

### 2.6 Agno AgentOS (identified, not a FILE OUT template)

FastAPI runtime: sessions, RBAC, Slack/Telegram/WhatsApp, knowledge, tracing. `Workspace(".")` is generic FS tools. **No** GeneratedArtifact equivalent in the reviewed introduction. **REJECT** as FILE OUT design. Do not pull Agno “Knowledge” (FILE IN) into generated exports.

---

## 3. Comparison matrix

Ratings are BEN-relative (multi-tenant SaaS, FILE IN already exists, no public URLs, no ACE). Stars are not used as quality.

| Project | Artifact object | Chat integration | Storage separation | XLSX | PDF/DOCX | Security model | Sandbox | Persistence | Preview/download UX | Production maturity | License | BEN fit | Rec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Hermes Studio | Weak (path) | Preview cards; path download | Partial (upload dir vs generated path) | Preview only | Preview only | Path API; CVE-2026-67918 | Agent-side ACE | Local profile FS | Strong local preview | Active UI; BSL; CVE | BSL 1.1 | Poor | **REJECT** |
| Hermes Deliverable Mode | Weak (path list) | Native messenger attach after path scrape | No | openpyxl + ACE | reportlab/pypdf + skills | Path scrape / MEDIA; later allowlist | **Required ACE** | Disk + chat platform | Excellent messenger UX | Active agent; not multi-tenant | MIT | UX only | **LEARN** |
| Daita Agents | **Strong** (`ArtifactRef`) | ID + conversation-scoped list; known-ID read | **Strong** | XlsxWriter + verify; formula-safe | No suite | Known-ID; no public inventory; sensitivity | Trusted renderer | Immutable agent-home store | Preview via `artifact_read`; download = explicit save | Young; well-specified | MIT | **Best semantics** | **ADAPT** |
| Frame document-export | Medium (file metadata) | Path + **public URL** | Weak (`exports/`) | ExcelJS; **auto SUM** | PDFKit / docx / pptxgenjs | Public URLs; path return | Trusted renderer | Workspace exports dir | Built-in preview URL | Experimental | Apache-2.0 / MIT mismatch | Renderer sketch | **LEARN** |
| use-agent-os | Weak (workspace file) | Path; optional publish | Weak | openpyxl + ACE | WeasyPrint / reportlab / python-docx | Skill/FS trust | **Required ACE** | Workspace | Channel attach | Early OS | Apache-2.0 | Libraries only | **LEARN** |
| Agno AgentOS | N/A | N/A | N/A | N/A | N/A | Runtime RBAC (wrong layer) | Agent tools | DB sessions | N/A | Runtime product | Apache-2.0 | None for FILE OUT | **REJECT** |

**USE:** none.

---

## 4. Security findings (BEN must not copy)

1. **Download or chat SoR by filesystem path** — Hermes Studio CVE-2026-67918; Hermes `extract_local_files`; Frame `filePath`. BEN already rejects client `storage_key` on `file_ref` (`test_user_turn_file_ref_ids_ignore_client_storage_key`). Keep that discipline for outputs.
2. **Public `downloadUrl` / `previewUrl`** — Frame `publicBaseUrl`. Violates BEN principles 5–6.
3. **Model-authored formulas in XLSX/CSV** — Frame auto-`SUM`; Hermes/use-agent-os openpyxl formulas. Daita’s `= + - @` prefix protection is the pattern to copy. CSV injection is a real Excel threat.
4. **HTML/script preview of generated files** — Studio inline HTML preview. Untrusted HTML in an authenticated app is XSS.
5. **Bare-path scrape of assistant text** — prompt injection can name `/etc/passwd`-class files if validation is weak (Hermes had to patch MEDIA roots).
6. **Mixing uploads and generated bytes in one table/index** — would pull FILE OUT into FTS/extraction. BEN FILE IN must stay ingest-only.
7. **Remote image URLs inside generators** — Frame `ImageSpec.url`.
8. **Commercial license trap** — Hermes Studio BSL 1.1.
9. **Arbitrary code execution as the writer** — conflicts with `decision_003` and with deterministic business artifacts.

---

## 5. Libraries worth considering *when* a future gate opens

Do **not** add these now. BEN today: `pypdf` for **read**; XLSX **parse** via stdlib zip/XML; no writers in `requirements.txt`.

| Format | Prefer for BEN V1 FILE OUT | Notes |
|---|---|---|
| **CSV** | Python stdlib `csv` | Formula-prefix sanitization (Daita-style) before write. UTF-8. |
| **XLSX** | **XlsxWriter** *or* **openpyxl** write, plus a verify pass | Daita: XlsxWriter + zip/XML verify, literal cells only. Hermes/use-agent-os: openpyxl (formulas/LibreOffice — not V1). Frame ExcelJS is Node; do not add a Node writer to FastAPI. BEN already has a tiny `_minimal_xlsx()` test helper — not a product writer. |
| **PDF** | Later: **reportlab** (structured) | Hermes pdf skill. WeasyPrint (HTML) needs native libs and HTML trust — worse V1. PDFKit is Node. Not in FILE-OUT 1. |
| **DOCX** | Later: **python-docx** | Frame’s `docx` npm is the TS analogue. |
| **PPTX** | Later: **python-pptx** | Optional PptxGenJS is Node. Not V1. |

Prefer **literal cells** over Excel formulas for customer/supplier/quote tables.

---

## 6. USE / ADAPT / LEARN / REJECT

| Project | Decision |
|---|---|
| Hermes Studio | **REJECT** |
| Hermes Deliverable Mode | **LEARN** (thread attachment UX only) |
| Daita Agents | **ADAPT** (artifact object, IDs, provenance, renderer safety, create≠deliver, FILE IN≠FILE OUT) |
| Frame document-export | **LEARN** (structured content → buffer). Reject URLs/paths/SUM |
| use-agent-os | **LEARN** (Python libs). Reject ACE |
| Agno AgentOS | **REJECT** (wrong layer) |
| Combined | **Do not USE any one repo.** ADAPT Daita semantics + LEARN Frame renderer shape, implemented in BEN’s Python/Postgres/durable-root world |

---

## 7. Existing-solutions-first answers

1. **One project to largely ADAPT?** Daita’s **artifact semantics**, not its TUI, SQLite agent-home, or DuckDB. No repo should be vendored.
2. **Combine patterns?** Yes: Daita object model + Frame deterministic generators + BEN existing `write_bytes` / JWT download / `file_ref`-style IDs.
3. **Explicitly not copy?** Path APIs, public URLs, ACE writers, auto-index generated files, path scrape, HTML preview, Excel formula rows, Studio BSL code, Agno Knowledge-as-export.
4. **Libraries:** CSV stdlib; XLSX XlsxWriter or openpyxl **literal**; PDF reportlab later; DOCX python-docx later; PPTX python-pptx later.
5. **FILE OUT without ACE?** **Yes.** Daita and Frame both generate tabular/docs from structured data without a shell. That matches BEN sales/procurement/management spreadsheets.
6. **Preview in V1?** **No.** Authenticated download is enough. Preview is XSS/MIME surface area. Hermes Studio preview is the cautionary UX.
7. **Immutable snapshots in V1?** **Yes.** Daita commits immutable payloads. Regeneration = new ID + provenance `derived_from_artifact_id` later, not mutate-in-place.
8. **Postgres metadata + durable bytes?** **Yes.** Matches BEN `WorkspaceFile` (DB row + `_workspace_files/` bytes). Use a **sibling prefix** (e.g. `_generated_artifacts/`) so a storage_key leak cannot be confused with FILE IN. Do not put XLSX in Postgres blobs.
9. **Convert to WorkspaceFile only via explicit “Save to Knowledge”?** **Yes.** Daita’s create≠`artifact_save_local` is the same idea. Auto-promotion would violate FILE IN ≠ FILE OUT and would start FTS on generated bytes.

---

## 8. Recommended BEN architecture (evaluate, do not implement)

### Why the candidate shape holds

Daita shows the object/ID/provenance/lifecycle. Frame shows structured data → buffer. BEN already has org-scoped durable write, JWT download, and chat parts that store **IDs not paths** (`file_ref.file_id`). Council `deliverable_artifact` today is **text**, not a file — do not overload it.

### Smallest shape

```text
authorized structured rows (already visible to this org/thread)
  → deterministic renderer (CSV or literal XLSX)
  → durable write under sibling prefix, org_id/workspace_id/artifact_id/filename
  → GeneratedArtifact row (Postgres): id, org, workspace, thread, created_by,
      media_type, byte_size, sha256, source_kind, provenance JSON,
      NOT extracted, NOT FTS
  → assistant message part generated_artifact_ref { artifact_id, name, media_type }
  → GET authenticated content (Gate A / JWT / org match), FileResponse, no public URL
```

Authorization: inherit from the **business data + conversation** used to build the rows (principle 4). Same org isolation as WorkspaceFile. Never emit `storage_key` or absolute paths to the client (principle 7).

### FILE IN vs FILE OUT (preserve)

| | WorkspaceFile (IN) | GeneratedArtifact (OUT) |
|---|---|---|
| Source | User upload | Renderer from structured data |
| Prefix | `_workspace_files/` | sibling, not the same tree semantically |
| Chat part | `file_ref` (user turn) | `generated_artifact_ref` (assistant turn) |
| Extraction / FTS | Yes | **No** unless explicit Save to Knowledge |
| Mutability | Library object with processing states | Immutable snapshot |

### Mapping to BEN use cases (future)

| Use case | V1 FILE-OUT 1 | Later |
|---|---|---|
| Sales follow-up XLSX in chat | Yes | Email / WhatsApp |
| Procurement quote comparison XLSX | Yes | Attach to RFQ/Quote |
| Weekly sales PDF | **No** (not V1) | PDF renderer gate |
| Save to workspace knowledge | No auto | Explicit action |
| Send | No | Separate send gate |

---

## 9. What BEN should explicitly not build

- Public URLs, signed-URL-as-default, unauthenticated preview
- Path-parameter download APIs
- `execute_code` / sandbox / LibreOffice as the XLSX path
- PDF/DOCX/PPTX suite in the first FILE OUT gate
- Conversion engine (except maybe CSV↔XLSX later, like Daita’s narrow convert)
- Auto FTS / chunking / File Library indexing of generated bytes
- Artifact knowledge base
- Autonomous send (email/WhatsApp/CRM writeback)
- Overloading `WorkspaceFile` or `file_ref` for outputs
- Overloading council `deliverable_artifact` text as a file
- Preview HTML/iframe for V1
- Vendor Hermes Studio (BSL) or Frame URLs
- Opening FILE OUT because it is attractive

---

## 10. Should the GeneratedArtifact audit change?

**This checkout:** no `GATE_GENERATED_ARTIFACTS_AUDIT.md` on `main` or the Gate P branch. That prior audit is **unverified here**.

**If that audit recommended** NEW `GeneratedArtifact` + sibling durable prefix + ID refs + DEFER + do not auto-index: **this OSS review agrees and strengthens it**:

- Prefer Daita-like **literal XLSX** and formula sanitization over Frame ExcelJS SUM / openpyxl formulas
- Prefer **authenticated download without preview** in V1
- Treat **create vs Save to Knowledge** as two steps
- Still **DEFER**; still **do not put FILE OUT in P1**

No schema, migration, or product-gate change now.

---

## 11. First future FILE-OUT gate (defined, not opened)

**FILE-OUT 1**

Structured rows → XLSX or CSV → durable artifact → assistant thread `generated_artifact_ref` → authenticated download.

**In:** deterministic renderer, org isolation, immutable snapshot, provenance fields (who, thread, source query/dataset id, time).

**Out:** public URL, email, WhatsApp, CRM writeback, PDF suite, conversion engine, unrestricted code execution, File Library indexing, artifact KB, autonomous sending, in-app preview.

**Do not queue this gate automatically.** Research does not enter `queued/` without an explicit task.

---

## 12. Exact recommended next action

**Keep P1 as the next product gate.**  
This review does not expose a concrete architectural blocker in FILE IN, auth, or durable storage that would force FILE OUT ahead of P1.

**Do not implement GeneratedArtifact. Do not add dependencies. Do not create migrations. Do not deploy. Do not open FILE-OUT 1.**

When FILE OUT is eventually scheduled, start from §8 and Daita `ArtifactRef` / `docs/ARTIFACTS.md`, not from Hermes path delivery or Frame public URLs.

---

## Appendix A — BEN current FILE IN (context only)

- `WorkspaceFile` + `services/workspace_files/storage.py` (`_workspace_files/`, org/workspace/file_id, `write_upload`, path containment)
- Download: `GET /api/workspaces/{workspace_id}/files/{file_id}/content` (`routers/workspace_files.py`)
- Chat: `file_ref` with `file_id` only (`services/message_format.py`)
- No XLSX/PDF writers in app requirements (`pypdf` is extract)
- Council `deliverable_artifact` is a string in session state, not a file

## Appendix B — Review constraints honored

- No implementation, migrations, P1 changes, deploys, or dependency adds
- No clone/install/execute of Hermes, Daita, Frame, or AgentOS codebases
- Source read via GitHub raw/API and official docs only
