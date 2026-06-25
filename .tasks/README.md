# BEN v2 — Unified Workspace & Task Tracking Manifest

*File Location: `.tasks/README.md`*

## 📜 AI Core Engagement Rules

1. **Single-Task Isolation:** You are strictly authorized to work on **one task at a time** that is explicitly marked as **[IN PROGRESS]**.
2. **Sequential Pulling:** Never advance to a subsequent task, create new files, or modify unlisted components until the active task is marked as **[DONE]** and signed off by the user.
3. **Atomic Commits / Verification:** At the conclusion of every task, update the master status table and the audit log inside this document, summarize the code mutations, and halt execution to await user validation.

---

## 📋 Master Project Task Board

| Task ID | Component / Subsystem | Task Description | Priority | Current Status | Target File / Area |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **001** | Backend / Core | Purge Orphaned Modules & Synthesis Route Stubs | P0 | **[DONE]** | `services/*`, `main.py` |
| **002** | Frontend / UI | Strip Synthesis UI Buttons & Stale Event Handlers | P0 | **[DONE]** | `frontend/src/*` |
| **003** | Documentation | Rewrite Council Context Specs & System Maps | P1 | **[DONE]** | `docs/BEN_SYSTEM_MAP.md` |
| **004** | DB / Persistence | Construct Project, Member, Task & Ledger Models | P1 | **[DONE]** | `database/models.py` |
| **005** | Auth / Security | Harden Routes: Force ENFORCE_AUTH on Projects | P1 | **[DONE]** | `auth/*`, `config.py` |
| **006** | Backend / Service | Build Native Tools Service Layer & REST Endpoints | P0 | **[DONE]** | `services/native_tools_service.py`, `routers/projects.py` |
| **007** | Gateway & Backend | Basalt Public Corp + Copilot, Upskilling & HR Coordination | P0 | **[DONE]** | `services/model_gateway.py`, `routers/public_basalt.py`, `tests/test_*` (42 integration tests) |
| **008** | Agentic Sandbox | Agentic Coding Sandbox & Conversational Preview Pipeline | P0 | **[PENDING]** | `services/copilot_orchestrator.py`, sandbox preview routes |
| **009** | Frontend / PWA | Frontend Adaptive Theme Engine & Mobile Bridges | P1 | **[DONE]** | `frontend/src/theme/*`, `ThemeToggle.jsx`, `mobile/*`, `public/manifest.json` |
| **010** | Auth / Beta | Closed-Beta Passcode Gate & Anonymous Project Creation | P0 | **[DONE]** | `AppGate.jsx`, `auth/beta_gate.py`, `routers/projects.py` |

---

## 🛠️ Task Specifications & Execution Blueprints

### Task 001: Purge Orphaned Backend Modules & Synthesis Stubs

* **🎯 Objective:** Safely delete dead Python modules and `410 Gone` stubs identified in the technical debt audit.
* **📋 Step-by-Step Execution Plan:**
  1. Set Task `001` status to **[IN PROGRESS]** inside this file.
  2. Physically delete these unused files:
     - `services/council_room.py`
     - `services/council_synthesis_prompt.py`
     - `services/council_fast_track.py`
     - `services/adhoc_synthesis_prompt.py`
  3. Clean up `services/council_service.py` by removing dead constants from lines 15-36 (`SYNTHESIS_SYSTEM`, `ExpertResult`, `ExpertOutcome`).
  4. Strip the endpoint `POST /api/threads/{thread_id}/adhoc/synthesize` out of `main.py`.
  5. Remove the stub execution block `run_adhoc_synthesize()` from `services/adhoc_council_service.py`.
* **🧪 Definition of Done:** FastAPI builds cleanly without broken imports, and hitting the old endpoint returns a pure `404 Not Found`.

---

### Task 002: Remove Synthesis UI & Dead Council Components

* **🎯 Objective:** Decouple broken frontend paths, phase timers, and the legacy "Wrap up" button from the main application view.
* **📋 Step-by-Step Execution Plan:**
  1. Set Task `002` status to **[IN PROGRESS]** only after Task 001 is validated.
  2. Open `frontend/src/api/adhoc.js` and erase the `postAdhocSynthesize` network helper function.
  3. Modify `frontend/src/App.jsx` to completely strip out:
     - The import declaration for `postAdhocSynthesize`.
     - The entire local component method `invokeAdhocSynthesize()`.
     - The `runAdhocSynthesize()` error tracking state handlers.
     - The "BEN Synthesize" / "Wrap up" dashboard button layout markup.
  4. Discard `COUNCIL_PHASE_TIMERS` and old checking routines tracking `event.type === 'expert'` streams.
* **🧪 Definition of Done:** React SPA compiles cleanly, and the old button element is completely removed from the workspace viewport.

---

### Task 003: Align Council Documentation across Reference Manuals

* **🎯 Objective:** Wipe out all mentions of the parallel three-expert architecture from internal markdown files to prevent onboarding friction.
* **📋 Step-by-Step Execution Plan:**
  1. Update `docs/BEN_SYSTEM_MAP.md` sections 2, 4, and 5 to explicitly trace the modern single-model rolling context layout (`mode: "copy_paste"`).
  2. Remove historical remnants and stale timeout specs across `docs/PROJECT_STATE.md`, `docs/ARCHITECTURE_PRINCIPLES.md`, and `docs/TIMING_GOVERNANCE.md`.
* **🧪 Definition of Done:** All architecture files document the deterministic copy-paste schema with zero mentions of parallel evaluation budgets.

---

### Task 004: Establish Project, Member, Task & Ledger Models

* **🎯 Objective:** Deploy the new core database infrastructure required to transition BEN from a simple chat interface to an active project manager tool.
* **📋 Step-by-Step Execution Plan:**
  1. Implement `Project`, `ProjectMember` (supporting internal employees and external vendors), `ProjectTask`, and `FinancialLedger` tables in `database/models.py`.
  2. Configure cascade patterns and foreign keys linking `assigned_to` fields back to the project members.
  3. Execute `alembic revision --autogenerate` followed by `alembic upgrade head` to safely commit schema changes to the PostgreSQL cluster.
* **🧪 Definition of Done:** Database schemas are successfully provisioned on Railway and verify correctly with local reflection tests.

---

### Task 005: Harden Security & Force Auth Restrictions on Projects

* **🎯 Objective:** Ensure the project management workspace runs under strict, server-authoritative token enforcement, completely bypassing the global Shadow Mode.
* **📋 Step-by-Step Execution Plan:**
  1. Isolate all newly established project routes under a separate, secure router group.
  2. Force `ENFORCE_AUTH=true` logic explicitly for any transaction carrying project, budget, or vendor modifications.
  3. Bind incoming queries natively to token extraction variables to eliminate client-side tenant injection exploits.
* **🧪 Definition of Done:** Requests without verified Clerk header assertions are rejected immediately with clean HTTP `401 Unauthorized` codes.

---

### Task 007: Conversational Copilot + Government Intelligence Layer

* **🎯 Objective:** Chat-first operational copilot with Ministry of Labor registry intelligence, tactical quotation hazard mapping, compliance onboarding, daily ops briefing, and inline Action Cards — no split-screen grids.
* **📋 Step-by-Step Execution Plan:**
  1. Register 11 native tools in `services/model_gateway.py` including `fetch_site_intelligence`, `initiate_tactical_quotation`, `onboard_project_member`, `log_daily_operations`.
  2. Implement `services/government_intelligence_service.py` (simulated data.gov.il/MoL registries) and `services/tactical_copilot_tools.py`; persist intel in tenant-scoped project memory matrix (RLS).
  3. Extend `copilot_orchestrator.py` chat triggers (`@intel`, `@tactical`, `@onboard`, `@daily`); emit `mutated_state` NDJSON from `chat_service`.
  4. Add Action Cards: Government Intelligence, Compliance & Insurance, Next-Day Look-Ahead Briefing in `ActionCard.jsx`; maintain mobile chat-first `App.jsx`.
* **🧪 Definition of Done:** Full copilot toolchain with cost engineering tender analysis; **42 active integration tests** pass (`test_copilot_tools`, `test_tactical_intelligence`, `test_attendance`, `test_cost_engineering`, `test_upskilling`, `test_basalt_public`); `npm run build` OK.

**Attendance enhancement (Task 007):** `process_worker_response` in `model_gateway.py` parses flexible hour text vs standard 07:00–17:00 shift; flags `LATE_ARRIVAL` / `EARLY_DEPARTURE` / `PARTIAL_SHIFT`; Daily Attendance Approval Card with orange variance badges and one-tap edit/approve adjusting wage + subsistence.

**Cost Engineering (Task 007):** `analyze_supplier_tender` structures bids into 4-layer matrix, detects anomalies vs historical ledger/tenders; Cost Engineering Bid Tabulation Card with [Accept Bid] / [Counter Offer] updating `financial_ledger` + shopping logs.

**Upskilling & Training (Task 007):** `define_tactical_job_requirements` and `simulate_training_day_roi` in `model_gateway.py` with `upskilling_service.py` — statutory vs trainable skill blueprints, certification gap scan, onsite proctor vs offsite ROI with home-base transit; Upskilling Strategy and Onsite Proctor Session Action Cards with one-tap schedule → ledger; tenant-scoped project memory (RLS via `org_id`).

**Basalt Public Corporate (Task 007):** Rate-limited `/api/public/basalt/{jobs,apply,portfolio,content}` driven by `basalt_public_service.py`; US Enterprise EHS copywriting schema (EN default, HE optional); `review_basalt_application` inbox with Basalt Web Application Card ([Approve & Onboard] / [Schedule Training Day]); Daily Attendance & Delay Card with SMS/WhatsApp poll + 65–100 NIS food allowance band; PWA `manifest.json` for mobile deployment.

---

### Task 008: Agentic Coding Sandbox & Conversational Preview Pipeline

* **🎯 Objective:** Stand up an agentic coding sandbox with conversational preview — isolated tool execution, diff preview in chat, and gated deployment pipeline before production merge.
* **📋 Step-by-Step Execution Plan:**
  1. Set Task `008` status to **[IN PROGRESS]** only after Task `007` and `009` are signed off.
  2. Extend `copilot_orchestrator.py` with sandbox session lifecycle (spawn, preview, discard, promote).
  3. Add preview NDJSON event type in `chat_service` for inline code/diff Action Cards.
  4. Wire REST or WebSocket preview endpoints under a sandbox-scoped router with enforced auth.
* **🧪 Definition of Done:** End-to-end sandbox preview from chat trigger through diff render; tenant-scoped; full validation suite (62 tests) green; `npm run build` OK.

---

### Task 009: Frontend Adaptive Theme Engine & Mobile Bridges

* **🎯 Objective:** Light/dark adaptive theme with persistent preference, semantic CSS tokens, and mobile hardware bridges (camera capture, WhatsApp deep links) for field operations.
* **📋 Step-by-Step Execution Plan:**
  1. Implement `ThemeProvider` + `theme.css` CSS variable tokens with `localStorage` sync (`ben-theme` key).
  2. Add `ThemeToggle` Sun/Moon control in chat feed header; flash-prevention script in `index.html`.
  3. Register PWA `manifest.json` with standalone display and theme colors.
  4. Add `CameraCaptureInput` / `CameraCaptureModal` with `getUserMedia` rear-camera fallback.
  5. Wire compliant `https://wa.me/` deep links on counter-offers, attendance, briefings, and training Action Cards.
* **🧪 Definition of Done:** Theme persists across reloads; Action Cards inherit semantic theme; camera + WhatsApp bridges registered via `main.jsx`; `manifest.json` bundled in `dist/`; `npm run build` OK.

---

### Task 010: Closed-Beta Passcode Gate & Anonymous Project Creation

* **🎯 Objective:** Application-level passcode overlay for closed beta cohort; unblock project creation via `BEN_ANONYMOUS_ORG_ID` without per-user Clerk JWT when beta mode is enabled.
* **📋 Step-by-Step Execution Plan:**
  1. Add `AppGate` full-screen overlay; persist `basalt-app-authorized` in `localStorage` after passcode validation.
  2. Wire `VITE_BETA_PASSCODE` (frontend) and `BEN_BETA_PASSCODE` + `BEN_LOCAL_BETA_MODE` (backend); send `X-Basalt-Beta-Passcode` on project API calls.
  3. Replace enforced-only project tenant binding with `build_project_tenant_context_from_request` in `auth/beta_gate.py`.
  4. Remove Clerk "Sign in required" UX from `+ New Project` modal for beta-authorized users.
* **🧪 Definition of Done:** Passcode gate blocks mount until authorized; `POST /api/projects` succeeds with beta header + anonymous org; 66 core tests green; `npm run build` OK.

**Auditor sandboxes (Task 010 expansion):** Two-step `AppGate` (passcode → alias); deterministic `org_id` per alias via UUID v5; isolated project/thread scopes; chat feedback captured to `tasks/feedback/feedback_{alias}_{timestamp}.json` with alias, project name, theme, and message metadata.

**Ngrok mobile CORS (Task 010):** `CORSMiddleware` whitelists dynamic ngrok origins via `allow_origin_regex` (`*.ngrok-free.app`, `*.ngrok.io`, `*.ngrok.app`); `allow_credentials=True` sustains streaming chat and beta session headers over cellular tunnels. **[DONE]**

---

## 🪵 System Audit Log

- **2026-06-06:** Initialized master task tracking directory. Formulated unified manifest and synchronized execution specs to clean the environment for database feature injection.
- **2026-06-06:** Task `001` completed — deleted 4 orphaned service modules, removed synthesis stub/route, pruned legacy council types (`SYNTHESIS_SYSTEM`, `ExpertResult`, `ExpertOutcome`); verified `from main import app` + `404` on removed `/adhoc/synthesize` endpoint.
- **2026-06-06:** Task `002` completed — removed `postAdhocSynthesize`, synthesis handlers, phase timers, expert/synthesis stream events, and Wrap up button; `npm run build` passes.
- **2026-06-06:** Manifest reconciled — unified spec format adopted; tasks `001`/`002` marked **[DONE]** per verified codebase state; awaiting user sign-off before pulling `003`.
- **2026-06-06:** Task `003` completed — rewrote `BEN_SYSTEM_MAP.md`, `PROJECT_STATE.md`, `ARCHITECTURE_PRINCIPLES.md`, `TIMING_GOVERNANCE.md`, and `SYSTEM_BOUNDARIES.md` for copy-paste / rolling context architecture; removed parallel expert and synthesis panel references.
- **2026-06-06:** Task `004` completed — project/member/task/ledger models + migration `005_project_management_v1`.
- **2026-06-06:** Task `005` completed — enforced-auth projects router (`apply_enforced_auth_policy`, `build_enforced_tenant_context_from_request`).
- **2026-06-06:** Task `006` started — native tools service layer and `/api/projects` REST endpoints for members, tasks, and ledger.
- **2026-06-06:** Task `006` completed — `native_tools_service.py` CRUD for members/tasks/ledger; 12 protected REST routes; 10 auth/tenant tests pass.
- **2026-06-06:** Task `007` completed — conversational project copilot: 7-tool gateway registry, project memory matrix (logistics/subsistence/lifecycle), NDJSON `mutated_state` Action Cards in chat feed, chat-first mobile layout; 25 tests pass; `npm run build` OK.
- **2026-06-06:** Task `007` expanded — government intelligence layer: MoL registry simulation, tactical quotation with hazard mapping, compliance onboarding gate, daily ops briefing; 11-tool registry; 33 tests pass; `npm run build` OK.
- **2026-06-06:** Task `007` attendance enhancement — `process_worker_response` shift variance parsing, `daily_attendance_approval` Action Card with orange badges; 41 tests pass; `npm run build` OK.
- **2026-06-06:** Task `007` cost engineering — `analyze_supplier_tender` 4-layer matrix + anomaly detection; procurement bid tabulation Action Card; 46 tests pass; `npm run build` OK.
- **2026-06-06:** Task `007` upskilling expansion — `define_tactical_job_requirements`, `simulate_training_day_roi`, proctor session scheduling; Upskilling Strategy + Onsite Proctor Session Action Cards; 42 Task 007 integration tests pass; `npm run build` OK.
- **2026-06-06:** Task `007` Basalt public corporate rebuild — `/api/public/basalt` jobs/apply/portfolio/content gateway, US EHS copywriting schema, `review_basalt_application` inbox, Basalt Web Application + Daily Attendance & Delay Action Cards; 42 integration tests pass; `npm run build` OK.
- **2026-06-06:** Task `009` completed — Light/Dark theme (`ThemeContext`, `theme.css`, `ThemeToggle`), localStorage sync, PWA `manifest.json`; mobile hardware bridges (camera capture modal, WhatsApp deep links); `npm run build` OK.
- **2026-06-06:** Task `008` stub authored — Agentic Coding Sandbox & Conversational Preview Pipeline registered as **[PENDING]**; deployment gate unblocked pending user sign-off.
- **2026-06-06:** Pre-flight cleanup — reconciled Task 007 test index to 42; fixed `ProjectCreateBody.location_base` auth regression; removed dead ESLint symbols in `App.jsx`; 62/62 validation tests green.
- **2026-06-06:** Task `010` completed — `AppGate` passcode overlay (`basalt-app-authorized`), beta project routes via `BEN_ANONYMOUS_ORG_ID`, removed Clerk sign-in blocker from New Project modal; 66 core tests green; `npm run build` OK.
- **2026-06-06:** Task `010` ngrok CORS — dynamic `allow_origin_regex` for `*.ngrok-free.app` / `*.ngrok.io` / `*.ngrok.app`; `allow_credentials=True`; backend integrity verified.
