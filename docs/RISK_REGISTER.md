# BEN Risk Register

**Last register review:** 2026-05-16 (Language & cognitive consistency v1 — automated only)

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
| R-014 | Client-supplied `tenant_id` without auth binding | **High** | **PARTIAL** | 2026-05-15 | 2026-05-16 | **CHANGED** — tenant mode v2: `tenant_id` from JWT/personal UUID only; forged body **VERIFIED** pytest (org + personal); signed prod forge **NOT RUN** | Prod signed forge test; then **FIXED** | No | **Yes** (until signed prod check) |
| R-015 | No rate limiting on expensive routes | Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-108 Phase 4 | No | No |
| R-018 | Accidental shell artifact files in repo root | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Manual delete locally | No | No |
| R-019 | Auth shadow without production log baseline | Low | OPEN | 2026-05-15 | 2026-05-15 | **CHANGED** — `tenant_bind` logs add `auth_present`/`org_bound`/`auth_source` (no JWT); prod log sample still **NOT VERIFIED** | `railway logs` + `tenant bound for POST` | No | No |
| R-022 | Multi-provider council response divergence | Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | **CHANGED** — synthesis v1 adds domain reasoning sections; differentiation **PARTIALLY VERIFIED** (pytest); prod open | Monitor LLM compliance + council UX | No | No |
| R-023 | Gemini Strategy Advisor operational variability | Low–Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | **CHANGED** — prod Strategy `google`/`gemini-2.5-flash`/`ok` **VERIFIED**; `gemini-1.5-flash` **FAIL** (retired) | Pin `GEMINI_MODEL=gemini-2.5-flash` on Railway | No | No |
| R-024 | Council synthesis compresses distinct expert reasoning | Medium | OPEN | 2026-05-15 | 2026-05-15 | **NEW** — optional `legal_reasoning`/`strategic_reasoning`/etc.; pytest **NOT PROD VERIFIED** until merge | Merge `feature/reasoning-preservation-v1` + prod spot-check | No | No |
| R-025 | Legal Advisor (Anthropic) timeout variability under heavier prompts | Medium | OPEN | 2026-05-15 | 2026-05-15 | **NEW** — prod short prompt 0/5 Legal timeout; ~3.4k char prompt 1/2 Legal `timeout`; `claude-sonnet-4-6` **VERIFIED** ok when fast enough | Tail logs (`provider_anthropic` duration); optional Haiku eval; prompt bounding; **not FIXED** | No | No |
| R-026 | Conversation continuity / refresh rehydration | Medium | **PARTIAL** | 2026-05-16 | 2026-05-16 | **CHANGED** — anonymous refresh Playwright **VERIFIED** (bubbles persist); personal/org refresh **NOT VERIFIED** | Manual B+C refresh; then **FIXED** | No | No |
| R-027 | Council transcript persistence incomplete vs KO | Low–Medium | **PARTIAL** | 2026-05-16 | 2026-05-16 | **NEW** — council rows in `messages` with expert metadata; `knowledge_objects` synthesis still parallel; draft thread links via list heuristic | Document dual-store; optional unify later | No | No |
| R-028 | Council submit can hang or block UI | Medium | **PARTIAL** | 2026-05-16 | 2026-05-16 | **CHANGED** — anonymous council Playwright completes, UI recovers; signed/long-prompt matrix **NOT VERIFIED** | Manual D + fail-path | No | No |
| R-031 | Clerk org context UX failure (signed-in, no org in JWT) | Medium | **PARTIAL** | 2026-05-16 | 2026-05-16 | **CHANGED** — anonymous: no org banner **VERIFIED**; personal no-org sign-in **NOT VERIFIED** | Manual B; then **FIXED** | No | No |
| R-032 | Personal vs organization tenant mode ambiguity | Medium | **OPEN** | 2026-05-16 | 2026-05-16 | **NEW** — personal uses deterministic UUID v5 (`user:{sub}`); org uses Clerk `org_id`; plan-based `REQUIRE_ORG` not wired to billing; cross-mode data migration undefined | Document operator playbook; browser matrix post-merge | No | No |
| R-033 | Multilingual synthesis / agreement language drift | Medium | **PARTIAL** | 2026-05-16 | 2026-05-16 | **NEW** — dominant-language detection + synthesis LANGUAGE CONTRACT; pytest **VERIFIED**; prod LLM compliance + browser Hebrew council **NOT VERIFIED** | Manual Hebrew/English council matrix; spot-check synthesis language | No | No |
| R-034 | Degraded cognition copy inconsistent across locales | Low–Medium | **PARTIAL** | 2026-05-16 | 2026-05-16 | **NEW** — localized degraded expert + status labels (en/he/ar); Hebrew timeout pytest **VERIFIED**; full degraded council browser **NOT VERIFIED** | Browser degraded path Hebrew + English | No | No |
| R-035 | RTL rendering stability (refresh / mixed content) | Low–Medium | **PARTIAL** | 2026-05-16 | 2026-05-16 | **NEW** — `dir` + `bubble-text--rtl` on bubbles/progress; automated JS smoke **VERIFIED**; refresh persistence RTL **NOT VERIFIED** | Manual refresh after Hebrew council; mixed LTR URLs in RTL bubble | No | No |

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
