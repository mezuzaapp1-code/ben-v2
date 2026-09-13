# Gate D1 — production canary for existing chunk FTS

Isolated canary only. Does **not** enable `BEN_WORKSPACE_CHUNK_RETRIEVAL` globally.

Uses the exact Gate M gold set (`tests/gate_m/gold_questions.py`, 50 questions) and
the same CC0 BEN Gold Supply Agreement PDF generated at runtime.

Measurement talks to production after the existing allowlist is pointed at one
workspace UUID. Cleanup deletes the canary/control files and threads and reverts
the temporary allowlist variables.

Run (requires production credentials in the environment, never committed):

```
python3 scripts/run_gate_d1_prod_canary.py
python3 -m pytest tests/test_gate_d1_canary.py -q
```

Production canary (2026-09-12, SHA `44ef277`): **PASS**, rollout **NOT YET**.
See `gate_d1_report.md` and `canary_result.json`.
