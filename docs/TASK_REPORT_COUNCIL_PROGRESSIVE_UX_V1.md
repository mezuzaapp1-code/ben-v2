# TASK REPORT — Council Progressive UX v1

## 1. Task Name

Council Progressive UX v1 — alive progress, timeout recovery, Hebrew/long-prompt support.

## 2. Branch

`fix/council-progressive-ux-v1` (not merged)

## 3. Goal

Human-readable council errors, immediate progressive phases, degraded expert clarity, long-prompt hint, RTL-friendly text — without backend/auth/tenant/routing changes.

## 4. Files Changed

| File | Change |
|------|--------|
| `frontend/src/councilProgress.js` | added — phases, long prompt, RTL, error sanitize |
| `frontend/src/CouncilProgressPanel.jsx` | added — step list UI |
| `frontend/src/api/council.js` | timeout/ReadTimeout humanization, degraded labels |
| `frontend/src/App.jsx` | progress panel, RTL bubbles, long hint |
| `frontend/src/App.css` | progress panel, RTL, expert-degraded styles |
| `frontend/scripts/test-council-progress.mjs` | added |

## 5. Behavior

- **Errors:** `ReadTimeout`, `AbortError`, `error: ReadTimeout('')` → *Council took longer than expected. Please retry or shorten the request.*
- **Progress (immediate):** Council started → Legal → Business → Strategy → Synthesizing (staggered while awaiting single `/council` response).
- **Long prompt:** Composer + progress hint when input ≥400 chars or ≥80 words.
- **Hebrew:** `dir="auto"` / RTL class on bubbles; not treated as error.
- **Degraded experts:** `expert-degraded` bubble styling + *Partial* status labels.
- **Recovery:** `finally` clears loading/progress; composer re-enables when input present.

## 6. Verification

```bash
cd frontend && npm run build
node frontend/scripts/test-council-progress.mjs
python -m pytest tests/test_council_lifecycle.py -q
```

Browser matrix (short/long EN, short/long HE, timeout, refresh, retry): **manual** — run on Vercel preview after merge.

## 7. Results

| Check | Result |
|-------|--------|
| `npm run build` | **PASS** |
| `test-council-progress.mjs` | **PASS** |
| `pytest test_council_lifecycle` | **PASS** |
| Browser matrix | **NOT VERIFIED** (agent) |

## 8. Ready status

**READY FOR CHATGPT REVIEW**

---

READY FOR CHATGPT REVIEW
