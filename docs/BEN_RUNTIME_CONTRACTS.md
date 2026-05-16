# BEN Runtime Contracts

Operational guarantees for BEN cognitive runtime behavior. This document is normative for `/chat` and `/council` unless superseded by an explicit ADR.

---

## 6. Language & Cognitive Consistency (v1)

### 6.1 Dominant language detection

- **Scope:** Per request only (`dominant_language`, `text_direction` on `/chat` and `/council` JSON). No global language profiles are persisted.
- **Algorithm:** Deterministic script counting (Hebrew `\u0590–\u05FF`, Arabic `\u0600–\u06FF`, Latin letters). Empty or non-script input → `en` + `ltr`.
- **Classes:** `en`, `he`, `ar`, `mixed`, `unknown` (unknown/mixed label locale falls back to English UI strings; synthesis prompt still instructs model to follow dominant/mixed user language).
- **Mixed input:** When a secondary script ≥ 35% of the leading script count, classify as `mixed`; direction follows RTL scripts if Hebrew+Arabic ≥ Latin.
- **Implementation:** `services/language_context.py`, mirrored in `frontend/src/languageContext.js`.

### 6.2 Synthesis language guarantees

- Synthesis system prompt includes a mandatory **LANGUAGE CONTRACT** requiring all JSON string values in the user's dominant language.
- Expert system prompts append `Respond only in {language}.` for non-English dominants.
- Display text for consensus/disagreement/footer uses localized labels via `build_synthesis_display_text(..., lang_ctx)`.
- Chat prepends `[Respond only in {language}]` to the user message for non-English dominants (no provider routing change).

### 6.3 Structured reasoning rendering

- Optional synthesis keys (`legal_reasoning`, `operational_reasoning`, `strategic_reasoning`, `infrastructure_reasoning`, `minority_or_unique_views`, `disagreement_points`, `shared_recommendation`) are **omitted** when empty, null, or placeholder (`null`, `none`, `n/a`).
- UI must not render empty `<details>` sections or orphan headers (`isMeaningfulReasoningValue` / `prune_empty_synthesis_fields`).

### 6.4 Multilingual degradation guarantees

- Degraded expert messages use deterministic localized templates, e.g. Hebrew: `המומחה אינו זמין (פסק זמן)`.
- Expert status chips and council progress/timeout copy follow `label_locale` (en/he/ar).
- Council outer timeout still returns partial expert rows; metadata includes `dominant_language` / `text_direction`.

### 6.5 RTL rendering expectations

- `text_direction: rtl` for Hebrew/Arabic dominants; `ltr` for English.
- Message bubbles and council progress use `dir` + `bubble-text--rtl` / `bubble-text--ltr`.
- Mixed-direction content uses `unicode-bidi: plaintext` on RTL bubbles to reduce layout breaks.

### 6.6 Verification gates

| Gate | Automated | Browser |
|------|-----------|---------|
| Language detection | `pytest tests/test_language_context.py` | NOT VERIFIED |
| Hebrew council metadata + degraded copy | `pytest tests/test_council_degraded_honesty.py` | NOT VERIFIED |
| Frontend label parity | `node frontend/scripts/test-language-context.mjs` | NOT VERIFIED |
| Refresh rehydration RTL | thread rehydration tests (partial) | NOT VERIFIED |

**Do not mark language/RTL risks FIXED until manual browser matrix passes** (Hebrew chat, Hebrew council, English council, refresh, degraded path, timeout recovery).

---

READY FOR CHATGPT REVIEW
