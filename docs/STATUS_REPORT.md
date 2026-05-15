# BEN TASK REPORT — Council Reasoning Preservation v1

**Last updated:** 2026-05-15

## 1. Task Name

Preserve differentiated legal / operational / strategic reasoning in council synthesis (optional structured fields).

## 2. Branch

`feature/reasoning-preservation-v1` (not merged)

## 3. Before / after synthesis (conceptual)

**Before:** JSON `{ recommendation, consensus_points, main_disagreement, agreement_estimate }` tended to collapse nuance into one blurb.

**After:** Same core keys **plus** optional: `shared_recommendation`, `disagreement_points`, `legal_reasoning`, `operational_reasoning`, `strategic_reasoning`, `infrastructure_reasoning`, `minority_or_unique_views`. Omitted or absent when empty. `recommendation` always present for backward compatibility.

## 4. Runtime matrix

Unchanged: Legal → Anthropic, Business → OpenAI, Strategy → Gemini, Synthesis → OpenAI.

## 5. Verification

```bash
python -m pytest tests/test_reasoning_preservation.py tests/test_council_gemini_strategy.py tests/test_council_degraded_honesty.py -v
cd frontend && npm run build
```

## 6. PASS / FAIL

| Check | Result |
|-------|--------|
| Extended synthesis fields (mocked) | **PASS** |
| Degraded expert honesty (`2/2 available`) | **PASS** |
| Backward compat minimal JSON | **PASS** |
| Existing council tests | **PASS** |
| Frontend build | **PASS** |
| Production | **NOT VERIFIED** |

## 7. VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| Parser + honest agreement_post-process | **VERIFIED** (code + pytest) |
| LLM follows preservation prompt in prod | **INFERRED** |

## 8. Risks

| ID | Status |
|----|--------|
| R-022 | **PARTIAL** |
| R-024 | **OPEN** — compression risk until prod validated |

## 9. Readiness

**READY FOR REVIEW** — merge + prod spot-check on nuanced prompts.

---

READY FOR CHATGPT REVIEW
