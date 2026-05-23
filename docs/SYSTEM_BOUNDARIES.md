# System Boundaries

**Last updated:** 2026-05-23

What each layer **may** and **may not** do. When in doubt, do not cross boundaries without updating this doc.

---

## Provider layer

**Location:** `services/providers/*`

| May | May not |
|-----|---------|
| HTTP to vendor APIs | Tier routing or fallback chains |
| Parse vendor JSON, return content + tokens | Business rules, tenant policy |
| Optional truncation/TTFB diagnostics | Council expert personas |
| `ProviderSendResult` transport shape | Persist threads or idempotency |

**Consumers:** `model_gateway` only (chat). Council has **separate** inline HTTP (not yet migrated).

---

## Governance layer (runtime ops)

**Location:** `services/ops/load_governance.py`, idempotency, diagnostics, timeouts

| May | May not |
|-----|---------|
| Concurrency caps, overload 503 | Choose which LLM answers chat |
| In-process idempotency keys | Cross-replica consistency (not yet) |
| JSON logs (English) | User-facing provider labels |
| Request lifecycle events | Modify council expert assignments |

---

## Hat layer (persistent governance)

**Status:** **Not implemented**

Future: org policies, role hats, approval workflows. **Must not** be implied by current code.

---

## Council layer

**Location:** `services/council_service.py`, `services/council_room.py`

| May | May not |
|-----|---------|
| Parallel experts + synthesis JSON | Honor chat `provider_id` toolbar |
| Room metadata (`room_id`, status) | Use chat provider adapters (today) |
| Degraded expert honesty | Unbounded total time (25s envelope) |
| Tier-1 transcript persist | User `preferred_language` (v1) |

---

## Persistence layer

**Location:** `database/*`, `services/thread_service.py`, `message_format.py`

| May | May not |
|-----|---------|
| Threads, messages, RLS | Provider HTTP |
| JSON envelopes in `content` | Inject language into stored user text |
| Rehydration decode | Council routing |

**Rule:** Store **raw** user chat text; language instruction only in gateway payload.

---

## Runtime layer (API)

**Location:** `main.py`, `services/chat_service.py`, `services/health_service.py`

| May | May not |
|-----|---------|
| Validate bodies, bind tenant | Vendor API calls (delegate to gateway) |
| Compose chat flow | Council synthesis logic |
| `preferred_language` normalize (en/he) | Auto-detect language chain (v1) |

---

## Frontend layer

**Location:** `frontend/src/*`

| May | May not |
|-----|---------|
| Toolbar, threads UI, provider meta | Backend routing rules |
| Send `provider_id`, (future) `preferred_language` | Impersonate provider brands |

---

## Cross-layer flow (chat)

```text
Frontend → POST /chat → main → chat_service
  → chat_language (wrap) → model_gateway → provider adapter
  → persist raw message via thread_service
```

Council: `Frontend → POST /council → council_service` (separate).
