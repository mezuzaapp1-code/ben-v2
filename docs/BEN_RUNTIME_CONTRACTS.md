# BEN Runtime Contracts v1

Formal guarantees for BEN as a **Tier-1 cognitive runtime**. These contracts govern current behavior and define what must remain true before new platform layers (memory graph, agents, integrations) are added.

**Status:** Foundation document — stabilization and governance, not a feature release.

**Related:**

- [`docs/BEN_SYSTEM_MAP.md`](BEN_SYSTEM_MAP.md) — architecture and file index
- [`docs/TIMING_GOVERNANCE.md`](TIMING_GOVERNANCE.md) — timeout tiers and load policy
- [`docs/RISK_REGISTER.md`](RISK_REGISTER.md) — open risks and verification discipline
- [`docs/REPORT_TEMPLATE.md`](REPORT_TEMPLATE.md) — task report PASS/PARTIAL/FAIL semantics

---

## Contract hierarchy

```
Runtime Contracts (this document)  — required guarantees
        ↓
System Map / Timing Governance     — how guarantees are implemented
        ↓
Risk Register + Task Reports       — verification evidence
```

New work must not weaken a contract without an explicit contract revision and verification plan.

---

## 1. Identity Contract

### 1.1 Auth sources

| Source | Meaning | `auth_source` |
|--------|---------|---------------|
| Valid Clerk JWT | Bearer verified via `CLERK_SECRET_KEY` | `clerk_jwt` |
| No / invalid Bearer (default prod) | Anonymous session | `anonymous` |

`ENFORCE_AUTH=false` (default): unsigned traffic is allowed on `/chat`, `/council`, and thread APIs.

`ENFORCE_AUTH=true`: missing or invalid JWT → **401** on cognition routes.

`AUTH_SHADOW_MODE=true` (default): auth outcomes are logged; blocking only when `ENFORCE_AUTH` is also true.

### 1.2 JWT verification expectations

- Token extracted only from `Authorization: Bearer <token>`.
- Verification via Clerk backend SDK; claims used: `sub` → `user_id`, `email`, `org_id` (or nested `o.id`).
- Invalid token with `ENFORCE_AUTH=false` → anonymous context, not 401 (unless enforce on).
- JWT contents are never logged; logs may record `auth_present`, `auth_source`, `tenant_type`, `org_bound`.

### 1.3 Anonymous behavior

- **When:** No Bearer, or invalid Bearer with enforce off.
- **Tenant:** `tenant_type=anonymous`, `tenant_id=BEN_ANONYMOUS_ORG_ID` (server env, UUID).
- **Body `tenant_id`:** Ignored for binding; server scope is always anonymous org.

### 1.4 Signed-in behavior

- **When:** Valid JWT.
- **User identity:** `user_id` from `sub`; never from request body.
- **Default (personal):** No `org_id` in JWT → `tenant_type=personal`, `tenant_id=UUIDv5(user:{sub})`.
- **Organization:** `org_id` present in JWT → `tenant_type=organization`, `tenant_id=org_id`.

### 1.5 Organization behavior

- Clerk organization UUID must be a valid UUID in the token.
- Optional body `tenant_id` must match effective `tenant_id` or be omitted; mismatch → **422**.
- `REQUIRE_ORG_FOR_SIGNED_IN=true` (opt-in): signed-in without org → **403** `clerk_org_required` (structured, recoverable).

### 1.6 No client-trusted `tenant_id`

- **MUST NOT** use `tenant_id` from JSON body, query, or headers as the source of tenant scope.
- Body field is **validate-only** when `auth_source=clerk_jwt`.
- Forged tenant → **422** with clear message; never silent cross-tenant assignment.

### 1.7 Tenant derivation rules (summary)

| Condition | `tenant_type` | Effective `tenant_id` |
|-----------|---------------|------------------------|
| Valid JWT + `org_id` | `organization` | Clerk org UUID |
| Valid JWT, no org, default policy | `personal` | Deterministic UUID from `user:{sub}` |
| Valid JWT, no org, `REQUIRE_ORG_FOR_SIGNED_IN` | — | **403** `clerk_org_required` |
| No valid JWT, enforce off | `anonymous` | `BEN_ANONYMOUS_ORG_ID` |

Implementation: `auth/tenant_binding.py`, `auth/tenant_ids.py`, `auth/tenant_policy.py`.

---

## 2. Tenant Isolation Contract

### 2.1 Personal workspace isolation

- Each Clerk `sub` maps to one stable personal `tenant_id` (UUID v5 namespace).
- Threads and messages for personal tenant A **MUST NOT** be readable or writable under personal tenant B’s scope.
- Personal and anonymous scopes **MUST NOT** share storage unless explicitly migrated (not implemented).

### 2.2 Organization workspace isolation

- Org scope is the Clerk `org_id` UUID.
- All thread/message operations **MUST** use JWT-derived org, not client-supplied org.

### 2.3 Anonymous isolation

- All unsigned traffic shares `BEN_ANONYMOUS_ORG_ID` by design (shared anonymous workspace).
- Product risk accepted until per-session anonymous IDs or enforce-auth-only mode.

### 2.4 Forged tenant rejection

- Signed request with body `tenant_id` ≠ bound scope → **422**.
- Pytest and manual signed-forge prod tests are required before marking R-014 **FIXED**.

### 2.5 Future RLS expectations

- Per-request: `set_config('app.current_org_id', tenant_uuid)` before DB access.
- Application queries **MUST** filter by `org_id` column matching effective `tenant_id`.
- Database RLS policies **SHOULD** align with application `tenant_id` (defense in depth); contract requires app-layer enforcement today.

---

## 3. Request Lifecycle Contract

### 3.1 Every request must end visibly

- HTTP handler **MUST** return a terminal status (2xx, 4xx, 5xx) within bounded time.
- Frontend **MUST** clear loading state in `finally` (or equivalent) for chat and council.
- User **MUST** always see: success content, structured error, or recoverable banner — never indefinite spinner.

### 3.2 Bounded execution

| Route class | Hard ceiling (current) |
|-------------|-------------------------|
| `/health`, `/ready` | 5s (`HEALTH_ROUTE_TIMEOUT_S`) |
| `/chat` (provider) | 12s HTTP client tier |
| `POST /council` | 25s outer (`COUNCIL_TOTAL_TIMEOUT_S`) |
| Client council | 35s abort (`COUNCIL_CLIENT_TIMEOUT_MS`) |

No code path may block the event loop indefinitely on provider I/O without a timeout.

### 3.3 Timeout semantics

- **Server council timeout:** Partial expert results may return; synthesis may be null; HTTP **200** preferred over hang.
- **Client abort:** User sees humanized timeout message; UI recovers; retry allowed.
- **Expert-level timeout (12s):** Expert `outcome=timeout`; counted in agreement honesty rules.

### 3.4 Recoverable failures

- **403** `clerk_org_required`: recoverable when org policy on; banner + org switcher.
- **422** validation: user can fix session/body and retry.
- **401** (when enforce on): user can sign in and retry.
- Council/chat errors use `council_error` / `api_error` bubbles — not raw stack traces.

### 3.5 `request_id` expectations

- Traced routes: `/chat`, `/council`, `/health`, `/ready`, `/api/threads`, `/api/threads/{id}`.
- Accept `X-Request-ID` or generate UUID; attach to JSON responses where `attach_request_id` is used.
- Logs **SHOULD** include `request_id` for correlation (structured JSON).

### 3.6 No permanently blocked UI state

- After any council or chat completion, failure, or client abort: `loading=false`, progress UI cleared, composer enabled when input non-empty.
- **MUST NOT** leave Send/Council disabled forever after a single failure.

---

## 4. Council Runtime Contract

### 4.1 Expert flow (current)

| Stage | Provider | Role |
|-------|----------|------|
| Legal Advisor | Anthropic | Legal analysis |
| Business Advisor | OpenAI | Operational/business |
| Strategy Advisor | Google Gemini | Strategic |
| Synthesis | OpenAI | Structured JSON merge |

Experts run **in parallel** within the 25s envelope; synthesis after experts complete or timeout individually.

### 4.2 Degraded expert semantics

- `outcome`: `ok` | `timeout` | `degraded` | `error`.
- Non-ok experts **MUST** appear in response with visible degraded/partial labeling in UI.
- Expert text may be placeholder: `Expert unavailable ({category})`.

### 4.3 Agreement honesty

- Synthesis **MUST NOT** claim full panel agreement (e.g. `3/3`) when any expert is non-ok.
- `agreement_estimate` uses available experts only (e.g. `2/2 available`).
- Prompt and post-processing (`_honest_agreement_estimate`) enforce this; regressions are contract violations.

### 4.4 Partial availability behavior

- HTTP **200** with partial council is valid and expected.
- Synthesis may be `null` if synthesis times out or fails; experts still shown.
- UI prefix when experts failed: “Based on available expert responses.”

### 4.5 Timeout behavior

| Layer | Budget | On exceed |
|-------|--------|-----------|
| Per expert | 12s | Degraded expert row |
| Synthesis | 10s | `synthesis: null` |
| Council total | 25s | Partial payload returned |
| Client | 35s | Abort; humanized message; UI recover |

### 4.6 Future streaming expectations (non-binding)

- **Not implemented in v1.** Contract reserves: SSE/chunked expert tokens, progressive render per expert, server-push phase events.
- Until streaming ships: progressive UI is **time-based simulation** only; must remain honest (not imply live expert tokens).

---

## 5. Persistence Contract

### 5.1 Thread durability

- `POST /chat` with optional `thread_id` creates or continues a thread under effective `tenant_id`.
- Thread rows stored in `ben.threads`; messages in `ben.messages`.
- Thread title derived from first message snippet (truncated).

### 5.2 Refresh rehydration expectations

- `GET /api/threads` lists threads for bound tenant (newest first, limit 50).
- `GET /api/threads/{id}` returns messages in chronological order.
- Frontend **SHOULD** restore `activeThreadId` from `localStorage` and re-fetch detail on load.
- On org-required errors during hydrate: **MUST NOT** wipe local draft without user action.

### 5.3 Message persistence guarantees

- Chat: user + assistant rows persisted synchronously in chat handler path.
- Council: transcript persisted **asynchronously** after HTTP response (background task).
- Council JSON envelopes preserve expert metadata for rehydration display.

### 5.4 Background persistence behavior

- Council HTTP response **MUST NOT** wait for KO write or full transcript persist.
- Persist failures **MUST** log WARNING; **MUST NOT** change already-sent HTTP status.
- User may see council in UI before DB transcript completes (eventual consistency).

### 5.5 Eventual consistency expectations

- Immediately after council: UI shows in-memory transcript; DB may lag seconds.
- `GET /api/threads` immediately after council may not list new thread until persist completes.
- Refresh after short delay **SHOULD** show persisted council messages.

### 5.6 Dual store (known)

- Council synthesis may also write `knowledge_objects` (KO) in parallel to thread messages (R-027).
- Contract: both are tenant-scoped; unification is future work.

---

## 6. Language Contract

### 6.1 Dominant language detection

- **Current:** No server-side language classifier; providers receive user text as-is.
- **Contract:** User message language is preserved in prompts; providers should respond in the same language when capable.

### 6.2 Synthesis language expectations

- Synthesis system prompt **SHOULD** instruct: respond in the dominant language of the user question.
- Mixed-language input: synthesis **SHOULD** prefer the majority language of the question text.

### 6.3 RTL / LTR behavior

- Frontend **MUST** render Hebrew/Arabic scripts with `dir="auto"` or explicit RTL on message bubbles.
- RTL text **MUST NOT** be treated as an error or validation failure.
- Layout **SHOULD** remain usable (sidebar + composer) with RTL user input.

### 6.4 Multilingual continuity

- Thread may contain multiple languages across messages; each turn handled independently.
- No automatic translation unless a future feature explicitly adds it.

### 6.5 No mixed-language synthesis unless intentional

- Synthesis **SHOULD** be internally consistent in one primary language per response.
- Code-switching in synthesis only when mirroring intentional multilingual user questions.

---

## 7. Failure Contract

### 7.1 Human-readable failures

- API errors for users **MUST** use plain language messages.
- Forbidden in user-visible UI: raw `{"detail":...}` JSON, `ReadTimeout('')`, `error: ...` traces, stack strings.

### 7.2 No raw JSON / UI traces

- `parseBenErrorResponse` / `humanizeCouncilFetchError` / `sanitizeCouncilErrorMessage` are the canonical frontend sanitization paths.
- Structured API errors (e.g. `clerk_org_required`) map to banner or bubble text, not JSON paste.

### 7.3 Recoverable states

- Every failure state **MUST** allow: edit input, retry send/council, switch thread, sign out, or select org (when applicable).

### 7.4 Retry semantics

- Retry is always user-initiated (button click); no automatic unbounded retry loops.
- Failed council/chat **MUST NOT** duplicate user message on retry unless user sends again.

### 7.5 Partial cognition semantics

- Partial council success is **success with degraded content**, not a hard failure.
- Chat provider failure → error bubble; council partial → expert rows + optional synthesis.

---

## 8. Operational Contract

### 8.1 Timeout budgets

- Single source of constants: `services/ops/timeouts.py`.
- Tiers: FAST 5s, PRO 12s, DELIBERATE 25s — see [`TIMING_GOVERNANCE.md`](TIMING_GOVERNANCE.md).

### 8.2 Bounded concurrency

- Council: max 3 parallel expert calls per request.
- **Future:** per-tenant concurrency caps, queues (R-010, R-011) — not required for v1 contract compliance but blocked for scale.

### 8.3 Future load governance

- Rate limiting on `/chat` and `/council` (R-015) **REQUIRED** before public scale.
- No unbounded fan-out jobs in request path.

### 8.4 Observability expectations

- Structured JSON logs via `BenOpsJsonFormatter`.
- Subsystem tags: `auth`, `council`, `health`, `chat`, etc.
- Provider outcomes logged with category: `timeout`, `config_error`, `provider_unavailable`, etc.

### 8.5 Structured logs

- **MUST NOT** log JWTs, API keys, or full `DATABASE_URL` with credentials.
- Tenant bind logs: `tenant_type`, `auth_source`, `org_bound`, `auth_present`.

### 8.6 Risk register discipline

- New risks get IDs in `docs/RISK_REGISTER.md`.
- **FIXED** only after verification named in register.
- **PARTIAL** when pytest/smoke pass but browser or prod gaps remain.

---

## 9. Verification Gates

### 9.1 Browser verification requirements

Before marking **FIXED** for UX-facing risks (R-026, R-028, R-031):

| Scenario | Required |
|----------|----------|
| Signed-out chat + council + refresh | Pass |
| Signed-in personal (no org) | Pass |
| Signed-in organization | Pass |
| Council timeout recovery | Pass |
| No raw JSON in bubbles | Pass |
| Hebrew/RTL layout | Pass |

Automated Playwright may supplement; **MUST NOT** replace signed-in Clerk flows without credentials.

### 9.2 Deploy verification requirements

After merge to `main`:

1. Railway `/health` shows expected `version` and `tenant_modes_enabled=true`.
2. Vercel loads JS bundle with Clerk publishable key.
3. `scripts/prod_smoke_tenant_mode_v2.py` (or successor) passes unsigned paths.
4. Signed JWT smoke when tokens available.

### 9.3 PASS / PARTIAL / FAIL semantics

| Label | Meaning |
|-------|---------|
| **PASS** | Executed; criteria met |
| **PARTIAL** | Some criteria met; gaps documented |
| **FAIL** | Executed; criteria not met |
| **NOT VERIFIED** | Not run; no PASS claim |

### 9.4 No merge without verification

- Feature/fix branches **SHOULD** include: pytest for touched paths, `npm run build` for frontend, task report with evidence table.
- Contract-weakening changes **REQUIRE** contract doc update in same PR.
- Stabilization merges **SHOULD NOT** mark risks **FIXED** without browser matrix sign-off.

---

## 10. Future Layers (blocked until contracts stable)

The following are **explicitly blocked** until Runtime Contracts v1 are verified stable on production and open UX risks (R-026, R-028, R-031, R-014) are closed or accepted:

| Layer | Block reason |
|-------|----------------|
| **Memory graph** | Requires stable tenant identity + persistence contract |
| **Agents** | Requires memory + bounded execution + failure contract |
| **Integrations / connectors** | Requires auth + tenant isolation + rate limits |
| **Workflow execution** | Requires agents + observability + load governance |
| **Autonomous orchestration** | Requires all above + human-in-the-loop failure contract |

Unblocking process: contract amendment → implementation → verification gate → risk register update.

---

## 11. Runtime Principles

1. **Reliability before intelligence** — A silent hang or wrong tenant is worse than a weaker model answer.
2. **One stabilized layer at a time** — No parallel platform expansions during stabilization sprints.
3. **Finish → verify → merge → deploy → smoke test → continue** — No “done” without evidence.
4. **Bounded execution** — Every path has a ceiling (see timeouts).
5. **Bounded cost** — Per-request provider calls are finite; council is capped at 4 LLM calls + synthesis per request today.
6. **Bounded failure radius** — DB persist failure does not revoke council HTTP 200; one expert failure does not kill the others.

---

## Contract compliance checklist (for PRs)

- [ ] Tenant scope derived only from JWT/policy (no new body-trusted fields)
- [ ] User-visible errors humanized
- [ ] Loading/progress cleared in `finally`
- [ ] Timeouts use `services/ops/timeouts.py` constants (no ad-hoc infinite waits)
- [ ] Task report with PASS/PARTIAL/FAIL table
- [ ] Risk register updated if new operational risk introduced
- [ ] No blocked future layer merged without contract review

---

*Last updated: 2026-05-16 — Runtime Contracts v1 foundation.*
