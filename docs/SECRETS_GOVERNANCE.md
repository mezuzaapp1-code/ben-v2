# BEN Secrets Governance

How BEN-V2 stores, accesses, rotates, and logs secrets. **Documentation v1** — complements `SECURITY_BASELINE.md`.

**Status:** Foundation v1 — documentation only.

---

## Principles

1. **Secrets live in environment only** — Railway (API), Vercel (frontend public keys only), local `.env` (never committed).
2. **Least exposure** — each secret used in the smallest scope (server vs browser).
3. **No secret echo** — HTTP responses, structured logs, and error messages must not contain secret material.
4. **Rotate on compromise** — revoke and replace; no “wait until next deploy” for confirmed leaks.

---

## Environment variable classification

| Class | Examples | Required at startup | Logged if missing? |
|-------|----------|---------------------|--------------------|
| **Critical** | `DATABASE_URL`, `OPENAI_API_KEY` | Yes — fail fast (`validate_startup`) | Warning only (not values) |
| **Provider optional** | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `SYNTHESIS_MODEL` | No | Warning (`subsystem=startup`) |
| **Auth** | `CLERK_SECRET_KEY` | Yes when `ENFORCE_AUTH=true` (future) | Warning |
| **Deploy metadata** | `RAILWAY_GIT_COMMIT_SHA`, `GIT_COMMIT` | No | Never log values |
| **Internal automation** | `BEN_INTERNAL_API_KEY` (future) | No | Never log values |

**Never** print env values in scripts, smoke tests, or task reports. Reference names only.

---

## Provider and billing secrets

| Secret | Location | Consumer |
|--------|----------|----------|
| `OPENAI_API_KEY` | Server env | `council_service`, `model_gateway` |
| `ANTHROPIC_API_KEY` | Server env | `council_service` (legal expert) |
| `GOOGLE_API_KEY` | Server env | `model_gateway` (optional) |
| `CLERK_SECRET_KEY` | Server env | `auth/clerk_auth.py` |
| Stripe / Clerk webhook secrets | Server env | `billing/` (when routes enabled) |

Browser clients must **not** receive provider API keys. Frontend uses Clerk publishable key only (Vercel env).

---

## Logging redaction (current)

`services/ops/json_log_formatter.py` strips structured fields and string values when:

- Key matches: `api_key`, `authorization`, `password`, `secret`, `token`, `database_url`
- String starts with: `sk-`, `sk_ant`, `Bearer `

**Gaps to close in implementation phase:**

- Redact `DATABASE_URL` host/user if ever attached to log `extra` under alternate keys.
- Avoid logging full exception messages from `httpx` that may include URL query params with keys.

---

## Repository hygiene

| Rule | Rationale |
|------|-----------|
| `.env` in `.gitignore` | Local dev only |
| No `_council_test.json` with real tokens in git | R-003 |
| No smoke output in committed files | Prevents accidental paste of responses with secrets |
| Use `scripts/` verifiers with redacted stdout | Documented in task reports |

---

## Rotation playbook

| Event | Action |
|-------|--------|
| Key leaked in chat/log/repo | Revoke key in provider dashboard immediately; rotate Railway env; redeploy |
| Employee offboarding | Rotate shared provider keys if access existed |
| Routine rotation | Update Railway → deploy → verify `/health` + authenticated `/council` smoke |

Record rotation date in ops notes (future: `docs/DECISIONS.md`).

---

## Verification

| Check | How |
|-------|-----|
| No secrets in `git log -p` for docs commits | Manual review |
| Prod smoke output redacted | `scripts/prod_smoke_json_logging.py` prints structure only |
| JSON log sample has no `sk-` | Railway log tail after deploy |

---

## Related documents

- `SECURITY_BASELINE.md` — auth and route policy
- `docs/RISK_REGISTER.md` — R-013–R-015

---

READY FOR CHATGPT REVIEW
