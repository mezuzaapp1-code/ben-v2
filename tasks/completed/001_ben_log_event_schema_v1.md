# BEN Log Event Schema v1

## Goal

Define a single append-only reasoning-event structure so BEN can record *what happened*, *what was rejected*, and *what remains unresolved* — without workflow engines or governance bureaucracy.

## Completion summary

- New table `ben.ben_log_events` (single store; `cognitive_events` left unused).
- Migration `004_ben_log_events_v1` with RLS `tenant_isolation`.
- ORM `BenLogEvent` + constants `BEN_LOG_EVENT_TYPES`, `BEN_LOG_SOURCES`.
- Event types locked in `tasks/decisions/decision_005_ben_log_event_types_v1.md`.
- Payload convention for `unresolved`, `rejected_paths`, `next_step`, `operational_context`.

## Verification

- [x] `alembic upgrade head` → `004_ben_log_events_v1`
- [x] RLS + CHECK constraints on prod/dev DB
- [x] `compileall` OK
- [x] No runtime coupling (`main.py`, chat, council unchanged in this PR scope)
- [x] Regression: chat routing + tenant binding tests pass

## Next Task

`002_ben_log_capture_v1.md` — now sole focus in `active/`.

---
**Completed:** 2026-05-24  
**Commit:** _(pending — schema files ready)_  
**Verification:** pass (local migration + tests)  
**Notes:** Schema-only; capture blocked until commit/deploy.
