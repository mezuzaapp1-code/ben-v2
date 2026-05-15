# TASK REPORT

Use this template for every engineering task, Cursor session handoff, and autonomous BEN agent completion report.

---

## 1. Task Name

<!-- Short, specific title (e.g. "Council synthesis production deploy") -->

## 2. Branch

<!-- e.g. feature/council-synthesis-v1 -->

## 3. Goal

<!-- What was requested and what "done" means -->

## 4. Files Changed

<!-- List paths only; no secrets -->

| File | Change type |
|------|-------------|
| | added / modified / deleted |

## 5. Code Changes

<!-- Brief factual summary of behavior or doc changes -->

## 6. Verification Executed

List **exact commands** run in this session. If a check was not run, write **NOT EXECUTED** and do not claim it passed.

### Git / repo (if relevant)

```bash
# Example
git status -sb
git log -1 --oneline
```

### Migration (if relevant)

```bash
# Example
python -m alembic -c database/migrations/alembic.ini upgrade head
python -m alembic -c database/migrations/alembic.ini current
```

### Runtime / API (if relevant)

```bash
# Example local
uvicorn main:app --host 127.0.0.1 --port 8002
# POST /council with test payload
```

### Failure isolation (if relevant)

<!-- e.g. invalid SYNTHESIS_MODEL, forced timeout -->

### Production smoke (if relevant)

```bash
# Example
GET https://<production-host>/docs
POST https://<production-host>/council
```

## 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| | PASS / FAIL / PARTIAL / NOT VERIFIED | |

**Result definitions**

- **PASS** — Executed; criteria met.
- **FAIL** — Executed; criteria not met (include exact error).
- **PARTIAL** — Some criteria met; list what passed and what failed.
- **NOT VERIFIED** — Not executed or blocked (state why).

### VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| | VERIFIED / INFERRED |

## 8. Git Status

<!-- Branch, clean/dirty, commits, push status; untracked files listed -->

## 9. Risks / Warnings

<!-- Operational risks, deploy risks, data risks, missing checks -->

## 10. Recommended Next Step

<!-- One primary action; optional secondary -->

## 11. Ready Status

<!-- e.g. READY TO MERGE | READY WITH WARNINGS | NOT READY | DOCS ONLY -->

---

## Rules (mandatory)

1. **Never claim verification without execution** — If a command was not run, mark **NOT VERIFIED**.
2. **Never hide failures** — Report FAIL and PARTIAL explicitly with exact errors.
3. **Never print secrets** — No API keys, tokens, or full `DATABASE_URL` with credentials.
4. **Separate VERIFIED vs INFERRED** — Production behavior inferred from smoke is VERIFIED only after the smoke runs; Railway dashboard settings are INFERRED until checked.
5. **Include exact errors** — HTTP status, stderr, exception type, and message (redact secrets).
6. **List operational risks explicitly** — Migration rollback, API cost, degraded expert paths, missing health endpoint, etc.

---

READY FOR CHATGPT REVIEW
