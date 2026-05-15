# BEN STATUS REPORT

**Last updated:** 2026-05-15

## Current Phase

Post-hardening cleanup and governance prep. **Hardening v1 verified in production.** Next architecture layer: **Resource + Timing Governance**.

## Current Active Branch

`main` (operational docs updates on `main`)

## Current Active Task

Post-hardening cleanup — Railway health-check instructions, variable audit, R-003 classification, risk register refresh.

## Recently Completed Tasks

| Task | Outcome |
|------|---------|
| Hardening Sprint v1 | Merged; prod smoke passed |
| `/health` and `/ready` | **Live** on `https://ben-v2-production.up.railway.app` |
| Council + synthesis | Prod **200**, `request_id`, no raw provider errors |

## Blocked Tasks

None.

## Open Risks

`docs/RISK_REGISTER.md` — R-002 (Railway CLI audit), R-003 (untracked files), R-008 (log formatter), **R-009** (timing/load governance).

## Production Status

| Item | Status |
|------|--------|
| `GET /health` | **200** — liveness (`healthy` when DB up) |
| `GET /ready` | **200** — readiness (`migration_head`, env, DB) |
| `POST /council` | **200** — hardened responses |

## Railway — manual health check (do not skip)

1. Open [Railway Dashboard](https://railway.app/dashboard) → project **ben-v2** (or your project name).
2. Select the **production web service** (FastAPI / `uvicorn`).
3. Go to **Settings** → **Deploy** (or **Healthcheck** section per current UI).
4. Set **Healthcheck Path** to `/health` (path only, not full URL).
   - Railway expects HTTP **200** from `https://<service-host>/health` during deploy rollouts.
   - Default timeout ~300s; increase via **Healthcheck Timeout** or variable `RAILWAY_HEALTHCHECK_TIMEOUT_SEC` if startup is slow.
5. **Optional readiness semantics:** use `/ready` instead if you want deploy gate on DB + migration + required env (returns **503** until ready).
6. Save; trigger a redeploy or wait for next deploy to confirm zero-downtime handoff.
7. Allow hostname `healthcheck.railway.app` if your app filters hosts (see Railway docs).

**Recommendation:** `/health` for liveness during deploy; keep `/ready` for manual/CI readiness checks.

## Railway — variable audit (manual)

CLI not authenticated in dev session (`railway login` required). Verify in **Variables** tab:

| Variable | Production status (this session) |
|----------|----------------------------------|
| `OPENAI_API_KEY` | **INFERRED PRESENT** (OpenAI experts work in prod) |
| `ANTHROPIC_API_KEY` | **INFERRED PRESENT** (Legal prose in prod smoke) |
| `ANTHROPIC_MODEL` | **INFERRED PRESENT** (`claude-sonnet-4-6` behavior; confirm value in UI) |
| `DATABASE_URL` | **INFERRED PRESENT** (`/ready` migration_head + synthesis persist) |

Local `.env` (dev machine): all four **PRESENT** (`ANTHROPIC_MODEL=claude-sonnet-4-6`).

## Deployment Readiness

**READY** for Resource + Timing Governance layer after R-002 dashboard confirmation and R-003 file hygiene.

## Recommended Next Step

1. Set Railway healthcheck path to `/health` (steps above).
2. Confirm four variables in Railway UI; close R-002.
3. Commit `scripts/`; add `_council_test.json` to `.gitignore`.
4. Start **Resource + Timing Governance** (R-009).

READY FOR CHATGPT REVIEW
