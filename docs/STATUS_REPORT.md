# BEN TASK REPORT — Council Degraded Expert Honesty v1 (merge + production)

**Last updated:** 2026-05-15

## 1. Task Name

Merge and production-verify council degraded expert honesty (`feature/council-degraded-honesty-v1`).

## 2. Branch

`main` @ `e0c056c` (fast-forward merge)

## 3. Goal

Deploy honest expert metadata and synthesis agreement to production; verify API shape, degraded paths (local), and UI (Vercel).

## 4. Files Changed (merge)

| File | Change |
|------|--------|
| `services/council_service.py` | Expert `provider`, `model`, `outcome`; honest synthesis |
| `frontend/src/App.jsx`, `App.css` | Status labels + synthesis disclaimer |
| `tests/test_council_degraded_honesty.py` | Unit tests |
| `scripts/verify_council_honesty_prod.py` | Prod smoke helper |

## 5. Before / After (production API)

**Before:** `council[]` had only `expert`, `model`, `response`; synthesis could show misleading `2/3`.

**After (prod sample, all experts ok):**
```json
{
  "expert": "Legal Advisor",
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "outcome": "ok",
  "response": "..."
}
```
`synthesis.agreement_estimate`: `"3/3 available"`

**After (local mocked Legal timeout):**
- Legal: `outcome: "timeout"`, `provider: "anthropic"`
- `agreement_estimate`: `"2/2 available"`
- UI strings: `Unavailable: timeout`, `Based on available expert responses.`

## 6. Verification Executed

```bash
git fetch origin
git rev-parse e0c056c origin/main
python -m pytest tests/test_council_degraded_honesty.py -v
cd frontend && npm run build
git checkout main && git pull && git merge feature/council-degraded-honesty-v1
git push -v origin HEAD
python scripts/verify_council_honesty_prod.py
python -m pytest tests/test_council_degraded_honesty.py::test_legal_timeout_degraded_honest_synthesis -v
cd frontend && vercel --prod --yes
python scripts/probe_vercel_honesty_ui.py
```

## 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| Branch + `e0c056c` on origin | **PASS** | |
| pytest (3 tests) | **PASS** | |
| Frontend build | **PASS** | |
| Merge to `main` | **PASS** | `04bc370..e0c056c` |
| GET `/health` | **PASS** | 200 |
| GET `/ready` | **PASS** | 200 |
| POST `/council` | **PASS** | 200 |
| `request_id`, `cost_usd`, top-level keys | **PASS** | |
| `provider`, `outcome` on each expert | **PASS** | Prod |
| Local degraded Legal timeout | **PASS** | pytest |
| Local invalid Anthropic model | **PASS** | pytest (suite) |
| Prod forced Legal degraded | **NOT EXECUTED** | Intentionally local-only per task |
| Vercel UI strings in bundle | **PASS** | After `vercel --prod` |
| No HTTPStatusError in responses | **PASS** | Prod + tests |

### VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| `origin/main` = `e0c056c` | **VERIFIED** |
| Prod API new fields | **VERIFIED** |
| Degraded honesty behavior | **VERIFIED** (local pytest) |
| Prod UI labels on live browser | **INFERRED** from bundle probe post-deploy |

## 8. Production smoke detail

```
GET /health -> 200
GET /ready -> 200
POST /council -> 200
experts: [('Legal Advisor', 'ok', 'anthropic'), ('Business Advisor', 'ok', 'openai'), ('Strategy Advisor', 'ok', 'openai')]
agreement_estimate: 3/3 available
```

## 9. Risks

| ID | Status |
|----|--------|
| **R-021** | **FIXED** — pytest + prod API field verification |

## 10. Ready Status

**READY FOR PRODUCTION USE** — backend deployed; frontend redeployed to Vercel for UI labels.

---

READY FOR CHATGPT REVIEW
