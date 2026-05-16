# BEN Risk Register

**Last register review:** 2026-05-16 (Council lifecycle fix — branch, not merged)

**RISK_REGISTER.md changed:** YES

---

## ACTIVE

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-002 | Railway variables not CLI-verified | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Manual Railway dashboard audit | No | No |
| R-010 | No runtime load isolation yet | Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | UNCHANGED | Per-tenant concurrency / queues | No | No |
| R-011 | No queue infrastructure yet | Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-107 | No | No |
| R-012 | Runtime latency instrumentation | Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | UNCHANGED | Prod JSON log sample | No | No |
| R-013 | Unauthenticated `/chat` and `/council` | **High** | **PARTIAL** | 2026-05-15 | 2026-05-15 | **CHANGED** — tenant binding on `main`; `ENFORCE_AUTH=false`; unsigned prod `/chat`+`/council` **VERIFIED** 200 | Enable enforce + Bearer-only when ready | No | **Yes** (until enforce) |
| R-014 | Client-supplied `tenant_id` without auth binding | **High** | **PARTIAL** | 2026-05-15 | 2026-05-15 | **CHANGED** — pytest **VERIFIED**; prod unsigned + health flags **VERIFIED**; **signed JWT + forged body tenant prod test NOT RUN** (no token in agent env) | Manual: Clerk session + POST mismatch → expect 422; then **FIXED** | No | **Yes** (until signed prod check) |
| R-015 | No rate limiting on expensive routes | Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-108 Phase 4 | No | No |
| R-018 | Accidental shell artifact files in repo root | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Manual delete locally | No | No |
| R-019 | Auth shadow without production log baseline | Low | OPEN | 2026-05-15 | 2026-05-15 | **CHANGED** — `tenant_bind` logs add `auth_present`/`org_bound`/`auth_source` (no JWT); prod log sample still **NOT VERIFIED** | `railway logs` + `tenant bound for POST` | No | No |
| R-022 | Multi-provider council response divergence | Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | **CHANGED** — synthesis v1 adds domain reasoning sections; differentiation **PARTIALLY VERIFIED** (pytest); prod open | Monitor LLM compliance + council UX | No | No |
| R-023 | Gemini Strategy Advisor operational variability | Low–Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | **CHANGED** — prod Strategy `google`/`gemini-2.5-flash`/`ok` **VERIFIED**; `gemini-1.5-flash` **FAIL** (retired) | Pin `GEMINI_MODEL=gemini-2.5-flash` on Railway | No | No |
| R-024 | Council synthesis compresses distinct expert reasoning | Medium | OPEN | 2026-05-15 | 2026-05-15 | **NEW** — optional `legal_reasoning`/`strategic_reasoning`/etc.; pytest **NOT PROD VERIFIED** until merge | Merge `feature/reasoning-preservation-v1` + prod spot-check | No | No |
| R-025 | Legal Advisor (Anthropic) timeout variability under heavier prompts | Medium | OPEN | 2026-05-15 | 2026-05-15 | **NEW** — prod short prompt 0/5 Legal timeout; ~3.4k char prompt 1/2 Legal `timeout`; `claude-sonnet-4-6` **VERIFIED** ok when fast enough | Tail logs (`provider_anthropic` duration); optional Haiku eval; prompt bounding; **not FIXED** | No | No |
| R-026 | Conversation continuity / refresh rehydration | Medium | **PARTIAL** | 2026-05-16 | 2026-05-16 | **NEW** — `GET /api/threads`, `thread_id` on chat/council, frontend localStorage + load; pytest **VERIFIED**; browser refresh E2E **NOT VERIFIED** | Manual refresh test; then consider **FIXED** | No | No |
| R-027 | Council transcript persistence incomplete vs KO | Low–Medium | **PARTIAL** | 2026-05-16 | 2026-05-16 | **NEW** — council rows in `messages` with expert metadata; `knowledge_objects` synthesis still parallel; draft thread links via list heuristic | Document dual-store; optional unify later | No | No |
| R-028 | Council submit can hang or block UI | Medium | **PARTIAL** | 2026-05-16 | 2026-05-16 | **NEW** — background persist; client 35s abort; progress UI; humanized errors; pytest **VERIFIED**; browser **NOT VERIFIED** | Manual council submit + refresh after fail | No | No |

---

## ACCEPTED / DEFERRED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-004 | No formal PR for council-synthesis merge | Low | ACCEPTED | 2026-05-15 | 2026-05-15 | UNCHANGED | PRs going forward | No | No |
| R-006 | No Engineering OS automation yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-104 | No | No |
| R-007 | No Dynamic Provider Config yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-106 | No | No |
| R-016 | CORS wildcard `https://*.vercel.app` | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-108 Phase 5 | No | No |

---

## FIXED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Resolved | Notes |
|----|----------------|----------|--------|------------|--------------|----------|-------|
| R-001 | No `/health` in production | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod `/health` 200. |
| R-005 | `/health` healthy path not integration-tested | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod `/ready` 200. |
| R-008 | Structured logs without JSON formatter | Low | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | `BenOpsJsonFormatter` | — |
| R-009 | Timing & Load Governance (docs only) | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Docs + runtime timeouts | — |
| R-003 | Untracked scripts / test JSON clutter | Low | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Hygiene merge | — |
| R-017 | Council worst-case may exceed 25s DELIBERATE | Low–Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Outer 25s cap on prod | — |
| R-020 | Frontend deploy without Clerk publishable key | Low | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Vercel env + bundle `pk_*` + sign-in UI **VERIFIED**; signed-in Bearer header E2E optional | — |
| R-021 | Council synthesis may overstate agreement when experts degrade | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Merged `e0c056c`; prod API `provider`/`outcome` **VERIFIED**; degraded path pytest **VERIFIED**; Vercel UI redeployed | — |

---

READY FOR CHATGPT REVIEW
