# Gate M baseline

- Questions: 50
- Answer correctness (extractive from injected context): 27/50
- Decisive-span recall: 21/43
- Citation/page accuracy: not_observable_on_prefix_fallback
- Gold page marker in injected prefix: 26/43
- Unanswerable precision (source does not contain the requested fact): 7/7
- MRL: 22 (44.0%)
- Observed retrieval mode: off
- PER_FILE_MAX_CHARS: 2000
- Extracted chars: 4221
- Prefix chars: 2000

## Failure frontier

- CONTEXT_LOSS: 11
- EXCEPTION_MISSED: 4
- MULTI_HOP: 4
- GLOBAL_QUESTION: 3
- MODEL_REASONING_FAILURE: 1

Answer correctness is extractive recoverability from the injected prefix,
not an LLM grade. Prefix_fallback evidence units do not carry page numbers.
