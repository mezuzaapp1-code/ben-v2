# BEN STATUS — Tenant Mode v2 deployed

**Last updated:** 2026-05-16

**Architecture map:** [`docs/BEN_SYSTEM_MAP.md`](BEN_SYSTEM_MAP.md) — system overview, request/tenant/council lifecycles, ops controls, and build-order rules for future work.

## Summary

`main` @ **`40bd45e`** — Tenant Mode v2 (personal + organization + anonymous) live on Railway.

| Area | Status |
|------|--------|
| Tenant mode v2 | **DEPLOYED** — health `tenant_modes_enabled=true` |
| Anonymous prod smoke | **VERIFIED** |
| Personal/org prod JWT smoke | **PARTIAL** (no token in CI agent) |
| Browser E2E | **Pending** |

Report: `docs/TASK_REPORT_TENANT_MODE_V2_DEPLOY.md`

---

## Prior — Stabilization Checkpoint v1

`main` consolidates recent fix branches (no new features):

| Area | Status |
|------|--------|
| Tenant binding | On `main`; prod unsigned smoke **VERIFIED** |
| Conversation rehydration | On `main`; pytest **VERIFIED** |
| Council lifecycle recovery | Merged `520da66`; pytest **VERIFIED** |
| Clerk org recovery | Merged `28d078d`; pytest **VERIFIED** |
| Sidebar scroll | Merged `72c8ac0` |
| Gemini Strategy (council) | Prod smoke **VERIFIED** |

**Browser E2E:** pending manual pass (see `docs/TASK_REPORT_STABILIZATION_CHECKPOINT_V1.md`).

## Verification

```bash
python -m pytest tests/test_clerk_org_recovery.py tests/test_tenant_binding.py tests/test_conversation_rehydration.py tests/test_council_lifecycle.py tests/test_council_gemini_strategy.py tests/test_council_degraded_honesty.py -q
cd frontend && npm run build
python scripts/stabilization_smoke_v1.py
python scripts/probe_vercel_clerk_bundle.py
```

**35 pytest passed.** Frontend build **PASS**.

## Risks (snapshot)

| ID | Status |
|----|--------|
| R-014 | **PARTIAL** |
| R-015 | **OPEN** |
| R-019 | **OPEN** |
| R-026 | **PARTIAL** |
| R-028 | **PARTIAL** |
| R-031 | **OPEN** |

Report: `docs/TASK_REPORT_STABILIZATION_CHECKPOINT_V1.md`

---

READY FOR CHATGPT REVIEW
