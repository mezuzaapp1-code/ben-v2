# Gate I1 — isolated automated improvement-loop prototype

Research + isolated prototype only.

- Does **not** change production retrieval flags
- Does **not** merge or deploy
- Does **not** touch auth / RLS / tenant isolation
- Candidate expansion is fail-closed: `BEN_FTS_LEXICAL_EXPAND` default **off**

Run:

```
python3 scripts/run_gate_i1.py
python3 -m pytest tests/test_gate_i1_improvement_loop.py tests/test_query_expansion.py tests/test_document_intelligence_gate4a.py -q
```

First measured failure is seeded from the committed Gate P `BEN_EXISTING_FTS` residual. Classification and candidate generation use **failure class + evidence**, not question ID.
