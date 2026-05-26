# Ledger L1 Schema (org RLS)

## Goal

Deploy schema-only ledger substrate with zero runtime coupling.

## Why Now

Governance proof layer foundation — decoupled from chat/council.

## Scope

- `003_ledger_v1` migration
- `LedgerDecision`, `LedgerApproval`, `LedgerAction` ORM
- RLS `tenant_isolation` on all three tables

## Explicit NON-Goals

- API, services, gates, UI

## Dependencies

- `002_ko_synthesis_jsonb` migration chain

## Risks

- Downgrade unsafe with data; org_id consistency not FK-enforced across child rows

## Verification

- `alembic upgrade head` → `003_ledger_v1`
- Prod: tables + RLS + CHECK constraints; `/api/ledger/*` 404
- `/health` `58f68f1`, `database=ok`

## Completion Criteria

- [x] All criteria met

## Next Task

`tasks/queued/ledger_l2.md` (deferred per decision_002)

---
**Completed:** 2026-05-24  
**Commit:** `58f68f1` — schema: add ledger v1 tables with org RLS  
**Verification:** pass (prod migration + smoke)  
**Notes:** L2 WIP exists uncommitted on disk — park before BEN Log P1.
