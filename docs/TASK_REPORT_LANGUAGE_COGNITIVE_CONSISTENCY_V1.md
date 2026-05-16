# TASK REPORT

## 1. Task Name

BEN Language & Cognitive Consistency v1

## 2. Branch

`feature/language-cognitive-consistency-v1`

## 3. Goal

Formalize multilingual cognitive behavior and synthesis consistency: dominant-language detection, synthesis language contract, empty reasoning suppression, localized degradation, RTL/LTR UI — without expanding agents, memory, connectors, tenant/auth architecture, or provider routing.

## 4. Files Changed

| File | Change type |
|------|-------------|
| `services/language_context.py` | added |
| `services/council_service.py` | modified |
| `services/message_format.py` | modified |
| `services/chat_service.py` | modified |
| `frontend/src/languageContext.js` | added |
| `frontend/src/cognitiveLabels.js` | added |
| `frontend/src/App.jsx` | modified |
| `frontend/src/App.css` | modified |
| `frontend/src/api/council.js` | modified |
| `tests/test_language_context.py` | added |
| `tests/test_council_degraded_honesty.py` | modified |
| `frontend/scripts/test-language-context.mjs` | added |
| `docs/BEN_RUNTIME_CONTRACTS.md` | added |
| `docs/RISK_REGISTER.md` | modified |

## 5. Code Changes

- **Dominant language:** Script-count detector (`he`/`en`/`ar`/`mixed`, `rtl`/`ltr`); attached to `/council` and `/chat` responses only (not persisted globally).
- **Synthesis contract:** `synthesis_system_prompt(ctx)` adds mandatory LANGUAGE CONTRACT; experts get respond-in-language clause; chat prepends language instruction.
- **Degraded semantics:** `degraded_expert_message` + localized status labels (en/he/ar).
- **Reasoning UI:** `prune_empty_synthesis_fields` / `isMeaningfulReasoningValue` — no empty optional sections.
- **Frontend:** Shared detection + labels; council progress/timeout copy localized; `dir` on bubbles and progress.

### Dominant language algorithm (summary)

1. Count Hebrew, Arabic, Latin letter runs in user text.
2. If total = 0 → `en`, `ltr`.
3. If runner-up ≥ 35% of leader → `mixed`; direction from RTL vs Latin totals.
4. Else top script wins → `he`/`ar`/`en` with matching direction.

### Before / after examples

| Input | Before | After |
|-------|--------|-------|
| `מה הסיכון המשפטי?` | English synthesis + `Expert unavailable (timeout)` | `dominant_language: he`, Hebrew degraded copy, synthesis prompt Hebrew contract |
| Empty `legal_reasoning` | Could show empty detail block | Key omitted; UI skips section |
| Council progress (HE) | `Council started…` (EN) | `המועצה החלה…` |

### Localization behavior matrix

| Surface | en | he | ar | mixed |
|---------|----|----|-----|-------|
| Degraded expert | Expert unavailable (timeout) | המומחה אינו זמין (פסק זמן) | الخبير غير متاح (انتهاء المهلة) | en labels + synthesis contract to dominant |
| Synthesis footer | structured reasoning layer… | שכבת נימוק מובנית… | طبقة استدلال… | en UI labels; model instructed per contract |
| Council timeout (client) | Council timed out… | פסק הזמן של המועצה… | انتهت مهلة المجلس… | follows `label_locale` |
| RTL bubbles | ltr | rtl | rtl | rtl if HE+AR ≥ Latin |

## 6. Verification Executed

```bash
cd c:\BEN-V2
python -m pytest tests/test_language_context.py tests/test_council_degraded_honesty.py -q
cd frontend
npm run build
node scripts/test-language-context.mjs
```

### Browser (required before FIXED)

**NOT EXECUTED** in this session — operator must verify: Hebrew chat, Hebrew council, English council, refresh persistence, degraded council path, timeout recovery.

## 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| `test_language_context.py` | PASS | 14 tests |
| Hebrew degraded council pytest | PASS | `test_hebrew_council_language_metadata_and_degraded_timeout` |
| `test_council_degraded_honesty.py` (regression) | PASS | 4 tests |
| `npm run build` | PASS | Vite production build |
| `test-language-context.mjs` | PASS | `node frontend/scripts/test-language-context.mjs` |
| Browser matrix | **NOT VERIFIED** | Blocks R-033/R-034/R-035 → FIXED |

## 8. Remaining Gaps

- LLM may still occasionally violate LANGUAGE CONTRACT (monitor in prod).
- `mixed` uses English UI labels; synthesis relies on model interpretation.
- Rehydrated thread messages without stored `dominant_language` infer direction from content only.
- Arabic council not browser-verified.

## 9. Future Multilingual Considerations

- Persist per-thread `dominant_language` on user message envelope for stable refresh.
- Expand label tables via single source code-gen from JSON.
- Agreement_estimate localization (`2/2 זמינים`).
- Browser E2E with Playwright Hebrew/Arabic matrix.

## 10. Risks Updated

- **R-033** PARTIAL — language consistency
- **R-034** PARTIAL — degraded cognition consistency
- **R-035** PARTIAL — RTL rendering stability

---

READY FOR CHATGPT REVIEW
