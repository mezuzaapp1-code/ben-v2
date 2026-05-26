# Ledger L2 Minimal API

## Goal

Authenticated append-only governance proof API on L1 schema (decisions, approvals, actions).

## Why Now

**Deferred.** L1 substrate live in prod (`58f68f1`). Operational priority is BEN Log + continuity (P1–P4). Ledger answers *human governance proof*, not *how do we continue engineering work*.

## Scope

(when activated)

- `auth/ledger_auth.py`, `services/ledger_service.py`, `services/ledger_allowlist.py`
- Six routes under `/api/ledger/*`
- `tests/test_ledger_l2.py`

## Explicit NON-Goals

- Gates on chat/council
- Workflow engine
- Provider writes
- UI

## Dependencies

- L1 schema at head (`003_ledger_v1`)
- BEN Log + continuity validated in practice (decision_002)
- Park or branch uncommitted L2 WIP before resuming

## Risks

- Implicit authority drift if ledger coupled to routing
- Competes with convergence focus

## Verification

- Design spec already written (conversation 2026-05-24)
- Full test matrix + prod smoke when implemented

## Completion Criteria

- Per Ledger L2 design spec; org-JWT only; `/api/ledger` 404 until shipped

## Next Task

`governance_layer.md` (P7) — only after usage validation
