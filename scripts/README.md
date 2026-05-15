# BEN verification scripts

Local and production smoke helpers. **Do not embed API keys** — rely on environment (`.env` locally, Railway in prod).

## Prerequisites

```powershell
Set-Location c:\BEN-V2
.\.venv\Scripts\pip install -r requirements.txt
# Local API: .env with DATABASE_URL, OPENAI_API_KEY, ANTHROPIC_API_KEY
```

## Scripts

| Script | Purpose | Usage |
|--------|---------|--------|
| `verify_json_logging_v1.py` | Parse `ben.ops` JSON from stderr log file | `python scripts/verify_json_logging_v1.py http://127.0.0.1:8002 C:\path\ben_ops.jsonl` |
| `verify_timeout_alignment_v1.py` | Local health/ready/council + wall-clock | `python scripts/verify_timeout_alignment_v1.py http://127.0.0.1:8003` |
| `prod_smoke_json_logging.py` | Production council/health smoke (no secrets in output) | `python scripts/prod_smoke_json_logging.py` |
| `prod_smoke_timeout_v1.py` | Production smoke + timing | `python scripts/prod_smoke_timeout_v1.py` |
| `verify_council_prerelease.py` | Council + synthesis with **HTTP mocks**; needs Postgres | `$env:DATABASE_URL='...'; python scripts/verify_council_prerelease.py` |
| `run_council_merge_checks.ps1` | Docker Postgres + alembic + prerelease verifier | `.\scripts\run_council_merge_checks.ps1` |

## Local test payloads

Use `_council_test.json` or `council_test_*.json` in repo root — **gitignored**. Copy shape from `.env.example` tenant UUID only; no secrets.

## Production URL

Default: `https://ben-v2-production.up.railway.app` (see script `BASE` constants).

---

READY FOR CHATGPT REVIEW
