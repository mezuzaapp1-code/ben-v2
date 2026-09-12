# Gate P gold file / provider benchmark

Uses the **exact** Gate M gold set (do not edit `tests/gate_m/gold_questions.py`).

- Document: original CC0 BEN Gold Supply Agreement (18 pages), generated at runtime
- Questions: `tests/fixtures/gate_m/gold_questions.json` (50)

This fixture measures:

1. Committed Gate M `BEN_PREFIX_2000` baseline (not rerun)
2. Isolated `BEN_EXISTING_FTS` against a throwaway workspace allowlist
3. Provider native document / file-search modes **only when** the corresponding
   API key is already present (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
   `GOOGLE_API_KEY`, `XAI_API_KEY`)

It does **not** enable production FTS, change `PER_FILE_MAX_CHARS`, or modify
BEN provider adapters.

Run:

```
python3 scripts/run_gate_p_benchmark.py
python3 -m pytest tests/test_gate_p_provider_benchmark.py tests/test_gate_m_gold_benchmark.py -q
```
