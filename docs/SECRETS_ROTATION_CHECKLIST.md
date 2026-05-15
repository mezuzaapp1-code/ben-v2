# BEN Secrets Rotation Checklist

Operational checklist for rotating credentials without leaking values in logs, commits, or task reports. **Reference variable names only — never paste live secrets.**

See also: `docs/SECRETS_GOVERNANCE.md`, `docs/SECURITY_BASELINE.md`.

---

## Before you start

- [ ] Confirm whether the old key is **compromised** (leak in chat, log, git, screenshot) or **routine** rotation.
- [ ] Use a private channel; do not rotate in shared screen recordings.
- [ ] Have Railway dashboard (and Vercel if frontend keys change) access ready.
- [ ] Plan a **5-minute** prod smoke after deploy: `GET /health`, `GET /ready`, `POST /council` (valid Clerk not required yet).

---

## OpenAI

| Step | Action |
|------|--------|
| 1 | Create new key in [OpenAI API keys](https://platform.openai.com/api-keys). |
| 2 | Railway → project → **Variables** → update `OPENAI_API_KEY` (do not copy into tickets). |
| 3 | Revoke old key in OpenAI dashboard after prod smoke **PASS**. |
| 4 | Local: update `.env` only on your machine; never commit. |

**Used by:** `council_service`, `model_gateway`, synthesis.

---

## Anthropic

| Step | Action |
|------|--------|
| 1 | Create new key in Anthropic console. |
| 2 | Railway → update `ANTHROPIC_API_KEY`. |
| 3 | Confirm `ANTHROPIC_MODEL` still valid (e.g. `claude-sonnet-4-6`). |
| 4 | Revoke old key after smoke **PASS**. |

**Used by:** Legal expert path in `council_service`.

---

## Google (if enabled)

| Step | Action |
|------|--------|
| 1 | Rotate in Google Cloud / AI Studio per your project policy. |
| 2 | Railway → update `GOOGLE_API_KEY`. |
| 3 | Smoke `/chat` path if Google tier is used. |

**Used by:** `model_gateway` (optional provider).

---

## Clerk (auth — when `ENFORCE_AUTH` is enabled)

| Step | Action |
|------|--------|
| 1 | Clerk Dashboard → API Keys → rotate **secret** key. |
| 2 | Railway → update `CLERK_SECRET_KEY`. |
| 3 | Vercel → update publishable key only if Clerk instructs frontend rotation. |
| 4 | Verify Bearer login on staging before revoking old secret. |

**Not required for current prod** (auth not enforced on `/council` yet).

---

## Stripe (billing)

| Step | Action |
|------|--------|
| 1 | Stripe Dashboard → Developers → API keys → roll secret key. |
| 2 | Railway → update `STRIPE_SECRET_KEY`. |
| 3 | If webhook secret rotated: update `STRIPE_WEBHOOK_SECRET` and re-register endpoint if needed. |
| 4 | Confirm `STRIPE_PRICE_ID_PRO` unchanged unless product/price changed. |
| 5 | Test checkout in **test mode** before live. |

**Used by:** `billing/stripe_service.py`, `billing/webhook_handler.py`.

---

## Database (`DATABASE_URL`)

| Step | Action |
|------|--------|
| 1 | Prefer Railway **internal** URL rotation via Railway Postgres plugin (reset password / connection string). |
| 2 | Update `DATABASE_URL` in Railway variables. |
| 3 | Redeploy; verify `GET /ready` → `migration_head` correct. |
| 4 | Never log full connection string; `ben.ops` redacts `database_url` keys. |

---

## Railway variable update (general)

1. Project → **Variables** → edit name (value hidden).
2. Save → trigger redeploy (or wait for auto-deploy from `main`).
3. Poll `GET /health` → `version` matches expected commit.
4. Run `scripts/prod_smoke_timeout_v1.py` or manual smoke (no secret output).

---

## Emergency leak response

| Priority | Action |
|----------|--------|
| **Immediate** | Revoke compromised key at provider **first**. |
| **Within 15 min** | Rotate Railway vars; redeploy. |
| **Within 1 h** | Search git history (`git log -p`) for accidental commit; if found, treat as incident, rotate all related keys. |
| **Never** | Paste keys in Slack, Cursor chat, or GitHub issues. |
| **Logs** | Check Railway log sample for `sk-`, `Bearer`, `postgresql://` — report presence, not values. |

---

## Rules (always)

- **Never** commit `.env` or `_council_test.json` with real tokens.
- **Never** print secrets in `ben.ops` logs, smoke scripts, or task reports.
- **Never** commit API responses that contain provider error bodies with embedded keys.
- Use `.env.example` for **placeholder** patterns only (`sk_test_...`).

---

READY FOR CHATGPT REVIEW
