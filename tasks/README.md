# BEN Task System & Execution Discipline v1

**Mode:** Convergence — operational execution, not architecture expansion.

**North star:** Answer *“Wait… how do we continue from here?”* with continuity and direction, not more AI surface area.

---

## Directory map

| Folder | Purpose | Max load |
|--------|---------|----------|
| `active/` | Current operational focus | **1–3 tasks only** |
| `queued/` | Important, intentionally deferred | Unlimited |
| `completed/` | Immutable finished work + verification + commit | Archive |
| `blocked/` | Explicit dependency blockers | As needed |
| `research/` | Discovery only — not roadmap, not implementation | As needed |
| `decisions/` | Architectural locks — prevents reasoning drift | Living set |

---

## Priority stack (frozen order)

```
P0 Freeze expansion
P1 BEN Log event schema
P2 BEN Log capture
P3 Continuity engine
P4 Convergence summaries
P5 Lightweight council (maintain)
P6 Internal hats (hidden)
P7 Governance layer (after usage validation)
```

**Rule:** Finish schema → verify → capture → verify → continuity → verify → convergence → verify. No parallel architecture tracks.

---

## Execution rules

1. **No parallel architecture tracks** — sequential layers with verification between each.
2. **Do not open** agents, governance UI, workflow engines, orchestration, recursive councils before BEN Log + continuity work in practice.
3. **If everything is active, nothing is active** — cap `active/` at 3.
4. **Research ≠ roadmap** — findings move to `queued/` or `decisions/` explicitly.
5. **Every task must reduce** cognitive scattering, continuity loss, or operational ambiguity.

---

## How to use

1. Copy `TEMPLATE.md` for new work.
2. Place in `active/`, `queued/`, or `research/` — never skip the template.
3. On completion: move to `completed/`, fill verification + commit hash, set **Next Task** on successor.
4. Lock debates in `decisions/` — reference decision IDs in task files.

---

## Current focus

| Status | Tasks |
|--------|-------|
| **Active** | `003_continuity_engine_v1.md` |
| **Completed (P2)** | `002_ben_log_capture_v1.md` |
| **Completed (P1)** | `001_ben_log_event_schema_v1.md` |
| **Queued** | `ledger_l2.md`, `hats_v1.md`, `governance_layer.md`, `workflow_engine.md`, `agents_exploration.md` |
| **Completed** | `ledger_l1_schema.md` |

See `decisions/` for convergence locks.
