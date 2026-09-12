# Gate M gold file

Original synthetic supply agreement authored for BEN retrieval measurement.

- License: CC0 1.0 (public domain dedication). Not a real contract.
- Document is generated at runtime by `tests/gate_m/pdf_builder.py` from
  `tests/gate_m/gold_document.py` (18 pages).
- Labels: `gold_questions.json` (50 questions).

This fixture measures the **current default** chat file path: Gate 3D
`prefix_fallback` via `load_ready_files_context` with chunk FTS left OFF.

Run:

```
python3 scripts/run_gate_m_benchmark.py
python3 -m pytest tests/test_gate_m_gold_benchmark.py -q
```
