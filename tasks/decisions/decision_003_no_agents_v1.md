# Decision 003 — No Autonomous Agents v1

**Status:** LOCKED  
**Date:** 2026-05-24

## Decision

No autonomous agent loops, recursive agent graphs, or self-directed tool orchestration in BEN v1 convergence phase.

## Context

Council is a **fixed** parallel expert pipeline + optional synthesis — not agents calling agents. Chat is single-hop to one provider per message.

## Consequences

- `tasks/queued/agents_exploration.md` is research-only.
- `tasks/queued/workflow_engine.md` remains inactive.
- Provider specialization is observational only (`docs/ARCHITECTURE_PRINCIPLES.md` §10).

## Supersedes

Any v1 proposal for “BEN agents” or autonomous expert runtime.

## References

- `docs/ARCHITECTURE_PRINCIPLES.md` — §4 No hidden autonomy, §5 No recursive AI chaos
