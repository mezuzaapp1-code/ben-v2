# Decision 002 — BEN Log vs Ledger

**Status:** LOCKED  
**Date:** 2026-05-24

## Decision

| Layer | Purpose | Audience |
|-------|---------|----------|
| **BEN Log** | Operational reasoning continuity — prompts, outcomes, rejections, next steps | Engineers day-to-day |
| **Ledger** | Human governance **proof** — explicit approve/reject/act on policy subjects | Operators / governance |

They are complementary, not interchangeable. BEN Log is **P1–P4**; Ledger L2 is **queued** until continuity is proven in practice.

## Context

Ledger L1 (schema + RLS) is live (`58f68f1`) with zero runtime coupling. Ledger L2 API design exists but must not compete with BEN Log for focus.

## Consequences

- Do not route chat/council through ledger.
- Do not use ledger rows as continuity reconstruction source in v1.
- Capture layer writes BEN Log events, not `ledger_*` tables.

## References

- `tasks/queued/ledger_l2.md`
- `database/models.py` — `cognitive_events`, `ledger_*`
