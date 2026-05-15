# BEN TASK REPORT — Council Degraded Expert Honesty v1

**Last updated:** 2026-05-15

## 1. Task Name

Council degraded expert honesty — expose expert outcomes and honest synthesis agreement.

## 2. Branch

`feature/council-degraded-honesty-v1`

## 3. Goal

When experts timeout/fail, council output must not imply full 3/3 agreement. Add `provider`, `model`, `outcome` per expert; honest synthesis prompt + post-processing; minimal UI status labels.

## 4. Files Changed

| File | Change type |
|------|-------------|
| `services/council_service.py` | modified |
| `frontend/src/App.jsx` | modified |
| `frontend/src/App.css` | modified |
| `tests/test_council_degraded_honesty.py` | added |
| `scripts/verify_council_prerelease.py` | modified |
| `docs/RISK_REGISTER.md` | modified |

## 5. Code Changes

- Each council member: `expert`, `provider`, `model`, `outcome` (`ok` \| `degraded` \| `timeout` \| `error`), `response`.
- Synthesis prompt marks unavailable experts; system rules forbid false `2/3` / `3/3`.
- `_honest_agreement_estimate()` corrects LLM agreement when any expert not `ok`.
- Frontend: expert status label + synthesis prefix when any expert failed.

## 6. Before / After

**Before:** Legal timeout → response `Expert unavailable (timeout)` but `model: "claude"` and synthesis could show `agreement_estimate: "2/3"`.

**After:** Legal → `outcome: "timeout"`, `provider: "anthropic"`, actual model; synthesis → `"2/2 available"`; UI → `Unavailable: timeout` + `Based on available expert responses.`

## 7. Verification Executed

```bash
python -m pytest tests/test_council_degraded_honesty.py -v
cd frontend && npm run build
```

## 8. Verification Results

| Check | Result |
|-------|--------|
| Happy path all `outcome=ok` | **PASS** |
| Legal timeout → `outcome=timeout`, not `ok` | **PASS** |
| Invalid Anthropic model → degraded/error | **PASS** |
| Synthesis not `2/3` when legal failed | **PASS** |
| No `HTTPStatusError` in responses | **PASS** |
| Top-level keys unchanged | **PASS** (`cost_usd`, `council`, `question`, `synthesis`, `request_id`) |
| `cost_usd` numeric | **PASS** |
| Prod deploy | **NOT VERIFIED** |

## 9. Runtime matrix (unchanged routing)

| Advisor | Provider | Model |
|---------|----------|-------|
| Legal | anthropic | `ANTHROPIC_MODEL` / default |
| Business | openai | `gpt-4o` |
| Strategy | openai | `gpt-4o-mini` |

## 10. Risks

| ID | Status |
|----|--------|
| **R-021** | **FIXED** (local tests) — synthesis overstatement when experts degrade |

## 11. Ready Status

**READY TO MERGE** (after review) — not merged per request.

---

READY FOR CHATGPT REVIEW
