# Continuity Engine v1

## Goal

Answer per thread: where did we stop? what was decided? what remains unresolved? what is the next step?

## Why Now

**P3 — active.** Unlocked after BEN Log capture (P2) writes events on chat/council paths.

## Scope

(when activated)

- Read-only aggregate over `thread_id` + BEN Log events + messages
- `GET /api/threads/{id}/continuity` or extend thread detail
- Rule-based v0; no LLM required initially

## Explicit NON-Goals

- Convergence summaries (P4)
- Cross-thread search
- Auto recommendations via provider calls
- Ledger L2

## Dependencies

- `001_ben_log_event_schema_v1.md` + `002_ben_log_capture_v1.md` complete

## Risks

- Over-fetching messages → slow; bound payload size

## Verification

- Fixture thread with known events returns correct four answers

## Completion Criteria

- [ ] API returns structured continuity block
- [ ] Tests pass; chat/council unchanged

## Next Task

`004_convergence_summaries_v1.md` (add to `queued/` when P3 scoped)
