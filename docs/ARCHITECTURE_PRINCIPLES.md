# Architecture Principles

**Last updated:** 2026-05-23

These principles govern all BEN-V2 work. Codebase language is **English only** (code, comments, logs, tests, filenames).

---

## 1. Reliability before intelligence

Ship bounded paths, durability, and honest degradation before adding smarter orchestration. Partial council success beats hung requests or silent data loss.

---

## 2. Provider-first transparency

Users work **with** GPT, Claude, or Gemini inside BEN—not a hidden blend. UI and API must show **provider + model**. BEN adds workspace, continuity, and structure; it does not pretend to be the model.

---

## 3. Bounded execution

Every user-facing path has timeouts and caps (`docs/TIMING_GOVERNANCE.md`). No unbounded waits, unbounded tokens, or unbounded retries.

---

## 4. No hidden autonomy

No silent agent loops, recursive self-calls, or autonomous tool use without explicit product design and human-visible intent.

---

## 5. No recursive AI chaos

Council is a **fixed** parallel expert pipeline plus optional synthesis—not agents calling agents in unbounded graphs. Chat is **single-hop** to one selected provider per message.

---

## 6. Human approval gates

Destructive actions, governance changes, production auth enforcement, and schema migrations require explicit operator decision—not implicit deploy side effects.

---

## 7. Separation of concerns

| Concern | Owner |
|---------|--------|
| HTTP to OpenAI/Anthropic/Google | Provider adapters |
| Tier routing, circuit breaker, cost | Model gateway |
| Thread/message persist | Thread service |
| Expert parallel + synthesis | Council service |
| Auth/tenant | Auth + `main` handlers |
| Language instruction (v1) | `chat_language` + raw persist in `chat_service` |

Do not mix orchestration into adapters or provider HTTP into council without a deliberate boundary change.

---

## 8. Measure before automate

Instrument and document before queues, autoscaling rules, or adaptive timeouts (`docs/INSTRUMENTATION_PLAN.md`).

---

## 9. English operations, localized responses

Logs and diagnostics stay English. Chat **responses** may be Hebrew, English, or other languages via `preferred_language`—not via translating logs.
