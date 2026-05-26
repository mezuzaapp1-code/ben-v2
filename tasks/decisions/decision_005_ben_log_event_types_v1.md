# Decision 005 — BEN Log Event Types v1

**Status:** LOCKED  
**Date:** 2026-05-24  
**Schema:** `ben.ben_log_events` (`004_ben_log_events_v1`)

## Decision

BEN Log uses **one append-only table** (`ben_log_events`). Legacy `cognitive_events` remains unused in v1 to avoid dual stores.

Event types are **reasoning continuity primitives**, not workflow states:

| `event_type` | Use |
|--------------|-----|
| `prompt` | Input observed (user/operator) |
| `response` | Model output observed |
| `decision` | Decision recorded |
| `rejection` | Path/rule/option rejected |
| `unresolved` | Open item flagged |
| `next_step` | Recommended continuation |
| `context` | Operational context snapshot |
| `note` | Freeform human note |

**Source:** `chat` | `council` | `human` | `system`

## Payload v1 (JSONB, optional)

Recommended keys for P2 capture (not DB-enforced):

```json
{
  "unresolved": true,
  "rejected_paths": ["option-a", "provider-scoring"],
  "next_step": "Run migration verify then capture PR",
  "operational_context": { "phase": "P1", "commit": "..." }
}
```

**Hot columns:** `summary`, `provider`, `model`, `actor_id` — for scan without parsing payload.

## Boundaries

- NOT approval verdicts, workflow steps, Hat ownership, or ledger rows.
- Payload size capped at capture layer (64 KB); schema does not enforce.

## References

- `tasks/active/001_ben_log_event_schema_v1.md`
- `tasks/active/002_ben_log_capture_v1.md`
