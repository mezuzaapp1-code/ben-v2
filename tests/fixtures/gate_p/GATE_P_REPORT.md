# GATE P — Provider File Intelligence Benchmark

GATE P STATUS: PARTIAL

Measurement only. Production retrieval, PER_FILE_MAX_CHARS, chunk FTS,
embeddings, and provider adapters were not changed.

## PROVIDER CAPABILITY MATRIX

| Provider | Current BEN integration | Direct PDF | Provider retrieval | Citations | Credentials usable? | Extra setup? | Safe now? |
|---|---|---|---|---|---|---|---|
| OPENAI | OpenAIProvider Chat Completions only (openai); canonical gpt-5.5-instant -> API gpt-4o-mini; gpt-5.5-pro -> API gpt-4o; multimodal parts are ['image/gif', 'image/jpeg', 'image/jpg', 'image/png', 'image/webp']; no Files API, no Responses API, no file_search tool in adapter. | YES at provider API (Responses input_file / Chat Completions type=file). NO in current BEN adapter (pdf_in_ben=False). | YES at provider API (Responses file_search + vector store). NO in current BEN adapter. Requires creating a vector store. | File Search annotations/search_results can be requested via include=. Native input_file does not expose retrieved chunks. | False | Native PDF: isolated Responses call only. File Search: ephemeral vector store + file upload (not present in BEN). | False |
| ANTHROPIC / CLAUDE | AnthropicProvider Messages API (anthropic); canonical claude-sonnet-4.6 / claude-opus-4.8 -> API claude-sonnet-4-6; image blocks only; production ANTHROPIC_CHAT_MAX_TOKENS default 1024; no document blocks, no Files API beta header. | YES at provider API (Messages document source base64/url/file_id, citations.enabled). NO in current BEN adapter. | NO applicable API-level retrieval product. Files API is upload/reuse only. Project RAG exists on claude.ai consumer projects, not the Messages API. CLAUDE_PROVIDER_RETRIEVAL is not an available API mode. | YES for native PDF when citations.enabled=true (page_location + cited_text). | False | Isolated Messages document block; no extra infra. | False |
| GOOGLE / GEMINI | GeminiProvider generateContent (google); canonical gemini-3.5-flash -> API gemini-2.5-flash; inlineData images only; no Files API upload, no File Search store. | YES at provider API (inlineData application/pdf or Files API file_data). NO in current BEN adapter. | YES at provider API (File Search store + fileSearch tool). NO in current BEN adapter. Requires creating a File Search store. | File Search can return page_number on retrieved_context / grounding. Native PDF does not expose retrieved chunks. | False | Native PDF: isolated generateContent inline PDF. File Search: File Search store + import (not present in BEN). | False |
| GROK / XAI | XAIProvider Chat Completions (xai); canonical grok-4.6 -> API grok-4.6; OpenAI-compatible image_url only; search/tools omitted (HTTP 410). No Files API, no Responses attachment_search. | YES at provider API (Responses input_file file_id/file_url; agentic attachment_search on grok-4.6). Chat Completions used by BEN does not accept PDF parts. NO in current BEN adapter. | Implicit attachment_search when files are attached on Responses. Collections/semantic search exist on Files API. Neither is wired in BEN. | attachment_search output can be requested; not exposed by BEN adapter. | False | Isolated Files upload + Responses API. | False |
| BEN local (prefix / FTS) | Gate 3D prefix_fallback PER_FILE_MAX_CHARS=2000; Gate 4A chunk_retrieval_enabled default=False; no embeddings, no vector DB, no provider file tools. | Local pypdf extraction then 2000-char prefix. | Local Postgres chunk FTS exists behind allowlist flag (default OFF). | Prefix path: page numbers not on evidence units. FTS path: chunk page attributes are observable. | True | FTS live Postgres path needs DB + flag+allowlist. Isolated lexical simulation needs no extra setup. | True |

## MODE RESULTS

### BEN_PREFIX_2000

- Status: MEASURED
- Answer correctness: 27/50
- Decisive-span recall: 21/43
- MRL: 22 (44.0%)
- Unanswerable precision: 7/7
- Early: 19/20
- Middle: 1/6
- Late: 0/13
- Exceptions: 1/5
- Multi-hop: 1/6
- Global: 2/5
- Table: 3/6
- Latency: 0.22 ms mean
- Tokens in/out: n/a / n/a
- Approx cost: n/a
- Failure frontier: {'CONTEXT_LOSS': 11, 'EXCEPTION_MISSED': 4, 'MODEL_REASONING_FAILURE': 1, 'MULTI_HOP': 4, 'GLOBAL_QUESTION': 3}

### BEN_EXISTING_FTS

- Status: MEASURED_ISOLATED
- Answer correctness: 48/50
- Decisive-span recall: 42/43
- MRL: 1 (2.0%)
- Unanswerable precision: 7/7
- Early: 18/20
- Middle: 6/6
- Late: 13/13
- Exceptions: 5/5
- Multi-hop: 5/6
- Global: 5/5
- Table: 6/6
- Latency: 0.98 ms mean
- Tokens in/out: n/a / n/a
- Approx cost: n/a
- Failure frontier: {'CONTEXT_LOSS': 1, 'MODEL_REASONING_FAILURE': 1}

### OPENAI_NATIVE_DOCUMENT

- Status: BLOCKED
- Blocked reason: OPENAI_API_KEY missing
- Answer correctness: BLOCKED
- Decisive-span recall: BLOCKED
- MRL: BLOCKED (n/a%)
- Unanswerable precision: BLOCKED
- Early: BLOCKED
- Middle: BLOCKED
- Late: BLOCKED
- Exceptions: BLOCKED
- Multi-hop: BLOCKED
- Global: BLOCKED
- Table: BLOCKED
- Latency: n/a ms mean
- Tokens in/out: n/a / n/a
- Approx cost: n/a

### OPENAI_FILE_SEARCH

- Status: BLOCKED
- Blocked reason: OPENAI_API_KEY missing; also requires ephemeral vector store not present in BEN
- Answer correctness: BLOCKED
- Decisive-span recall: BLOCKED
- MRL: BLOCKED (n/a%)
- Unanswerable precision: BLOCKED
- Early: BLOCKED
- Middle: BLOCKED
- Late: BLOCKED
- Exceptions: BLOCKED
- Multi-hop: BLOCKED
- Global: BLOCKED
- Table: BLOCKED
- Latency: n/a ms mean
- Tokens in/out: n/a / n/a
- Approx cost: n/a

### CLAUDE_NATIVE_DOCUMENT

- Status: BLOCKED
- Blocked reason: ANTHROPIC_API_KEY missing
- Answer correctness: BLOCKED
- Decisive-span recall: BLOCKED
- MRL: BLOCKED (n/a%)
- Unanswerable precision: BLOCKED
- Early: BLOCKED
- Middle: BLOCKED
- Late: BLOCKED
- Exceptions: BLOCKED
- Multi-hop: BLOCKED
- Global: BLOCKED
- Table: BLOCKED
- Latency: n/a ms mean
- Tokens in/out: n/a / n/a
- Approx cost: n/a

### CLAUDE_PROVIDER_RETRIEVAL

- Status: BLOCKED
- Blocked reason: No applicable Anthropic API retrieval product for this document QA task. Files API is upload/reuse. Project RAG is claude.ai-only.
- Answer correctness: BLOCKED
- Decisive-span recall: BLOCKED
- MRL: BLOCKED (n/a%)
- Unanswerable precision: BLOCKED
- Early: BLOCKED
- Middle: BLOCKED
- Late: BLOCKED
- Exceptions: BLOCKED
- Multi-hop: BLOCKED
- Global: BLOCKED
- Table: BLOCKED
- Latency: n/a ms mean
- Tokens in/out: n/a / n/a
- Approx cost: n/a

### GEMINI_NATIVE_PDF

- Status: BLOCKED
- Blocked reason: GOOGLE_API_KEY / GEMINI_API_KEY missing
- Answer correctness: BLOCKED
- Decisive-span recall: BLOCKED
- MRL: BLOCKED (n/a%)
- Unanswerable precision: BLOCKED
- Early: BLOCKED
- Middle: BLOCKED
- Late: BLOCKED
- Exceptions: BLOCKED
- Multi-hop: BLOCKED
- Global: BLOCKED
- Table: BLOCKED
- Latency: n/a ms mean
- Tokens in/out: n/a / n/a
- Approx cost: n/a

### GEMINI_FILE_SEARCH

- Status: BLOCKED
- Blocked reason: GOOGLE_API_KEY missing; also requires a File Search store not present in BEN
- Answer correctness: BLOCKED
- Decisive-span recall: BLOCKED
- MRL: BLOCKED (n/a%)
- Unanswerable precision: BLOCKED
- Early: BLOCKED
- Middle: BLOCKED
- Late: BLOCKED
- Exceptions: BLOCKED
- Multi-hop: BLOCKED
- Global: BLOCKED
- Table: BLOCKED
- Latency: n/a ms mean
- Tokens in/out: n/a / n/a
- Approx cost: n/a

### GROK_NATIVE_DOCUMENT

- Status: BLOCKED
- Blocked reason: XAI_API_KEY missing
- Answer correctness: BLOCKED
- Decisive-span recall: BLOCKED
- MRL: BLOCKED (n/a%)
- Unanswerable precision: BLOCKED
- Early: BLOCKED
- Middle: BLOCKED
- Late: BLOCKED
- Exceptions: BLOCKED
- Multi-hop: BLOCKED
- Global: BLOCKED
- Table: BLOCKED
- Latency: n/a ms mean
- Tokens in/out: n/a / n/a
- Approx cost: n/a

## COMPARISON TABLE

| Mode | Answer Correctness | Decisive Recall | MRL | Unanswerable | Early | Middle | Late | Exceptions | Multi-hop | Global | Latency | Tokens | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BEN_PREFIX_2000 | 27/50 | 21/43 | 22 (44.0%) | 7/7 | 19/20 | 1/6 | 0/13 | 1/5 | 1/6 | 2/5 | 0.22 | n/a | n/a |
| BEN_EXISTING_FTS | 48/50 | 42/43 | 1 (2.0%) | 7/7 | 18/20 | 6/6 | 13/13 | 5/5 | 5/6 | 5/5 | 0.98 | n/a | n/a |
| OPENAI_NATIVE_DOCUMENT | BLOCKED | BLOCKED | BLOCKED (n/a%) | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | n/a | n/a | n/a |
| OPENAI_FILE_SEARCH | BLOCKED | BLOCKED | BLOCKED (n/a%) | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | n/a | n/a | n/a |
| CLAUDE_NATIVE_DOCUMENT | BLOCKED | BLOCKED | BLOCKED (n/a%) | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | n/a | n/a | n/a |
| CLAUDE_PROVIDER_RETRIEVAL | BLOCKED | BLOCKED | BLOCKED (n/a%) | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | n/a | n/a | n/a |
| GEMINI_NATIVE_PDF | BLOCKED | BLOCKED | BLOCKED (n/a%) | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | n/a | n/a | n/a |
| GEMINI_FILE_SEARCH | BLOCKED | BLOCKED | BLOCKED (n/a%) | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | n/a | n/a | n/a |
| GROK_NATIVE_DOCUMENT | BLOCKED | BLOCKED | BLOCKED (n/a%) | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | n/a | n/a | n/a |

## VERDICT

**BEST OVERALL:** BEN_EXISTING_FTS

**BEST RETRIEVAL:** BEN_EXISTING_FTS

**BEST FULL DOCUMENT REASONING:** BLOCKED — no provider native-document run in this environment

**BEST COST PERFORMANCE:** BEN_EXISTING_FTS (local, $0 provider spend)

**BEST EXCEPTION DETECTION:** BEN_EXISTING_FTS

**DOCUMENT POSITION EFFECT:** Prefix EARLY=19/20 MIDDLE=1/6 LATE=0/13. Isolated FTS EARLY=18/20 MIDDLE=6/6 LATE=13/13. Provider native-document position effect is unmeasured without API keys.

**IMPORTANT FINDING:** Gate M's 2000-character prefix is a position filter, not a comprehension filter: EARLY 19/20, MIDDLE 1/6, LATE 0/13. Isolated Gate 4A lexical FTS flips that to LATE 13/13 and EXCEPTION 5/5 without embeddings, at $0 provider cost. Residual FTS misses are lexical paraphrase (M09, recovered by prefix) and extractive multi-hop arithmetic (M33, evidence present). Native provider PDF/File Search APIs exist, but this Cloud Agent environment does not inject OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, or XAI_API_KEY, so those modes are BLOCKED rather than estimated.

**BEN ARCHITECTURAL IMPLICATION:** Do not wait for one retrieval strategy. Measured evidence supports routing: local FTS as the default for this single-file lexical/exception workload; prefix as a cheap early-page complement when FTS hits the wrong pages; native full-document context as later escalation once provider keys and PDF adapters are available. Not implemented in this gate.

**SHOULD GATE D STILL PROCEED:** YES

**WHY:** Provider native-document quality is still unknown in this environment, while isolated FTS already attacks the measured Gate M failure (late/exception context loss) using code BEN already has behind a fail-closed flag. Gate D should remain the local retrieval gate; it should not be replaced by an unmeasured provider PDF adapter.

**SMALLEST NEXT STEP:** Inject existing Railway provider keys into the BEN Document Benchmark environment, re-run Gate P live modes C/E/G only (native document, no File Search stores), then decide whether Gate D FTS allowlisting is sufficient or whether a later adapter gate should add native PDF escalation.

**RESIDUALS:**

```json
{
  "fts_failures": [
    {
      "question_id": "M09",
      "category": "LEXICAL_VARIATION",
      "position": "EARLY",
      "failure_category": "CONTEXT_LOSS",
      "selected_pages": [
        7,
        11
      ],
      "decisive_in_injected": false
    },
    {
      "question_id": "M33",
      "category": "MULTI_HOP",
      "position": "EARLY",
      "failure_category": "MODEL_REASONING_FAILURE",
      "selected_pages": [
        4,
        3,
        17,
        1
      ],
      "decisive_in_injected": true
    }
  ],
  "prefix_late_or_span_failures": [
    "M06",
    "M07",
    "M14",
    "M21",
    "M23",
    "M24",
    "M25",
    "M26",
    "M30",
    "M31",
    "M32",
    "M34",
    "M35",
    "M36",
    "M38",
    "M39",
    "M41"
  ],
  "notes": [
    "M09 is a lexical-paraphrase miss: Gate 4A tokens from 'Where must the goods be delivered?' do not overlap the page-2 locator 'named delivery place is 12 HaMelacha Street'. Prefix recovered it because it is early.",
    "M33 retrieves quantity and price pages, but extractive scoring requires the computed product 425000, which is not a source span. That is not a retrieval miss."
  ]
}
```

**CLASSIFICATIONS:**

```json
{
  "BEN_PREFIX_2000": "FALLBACK",
  "BEN_EXISTING_FTS": "USE",
  "OPENAI_NATIVE_DOCUMENT": "BLOCKED",
  "OPENAI_FILE_SEARCH": "BLOCKED",
  "CLAUDE_NATIVE_DOCUMENT": "BLOCKED",
  "CLAUDE_PROVIDER_RETRIEVAL": "BLOCKED",
  "GEMINI_NATIVE_PDF": "BLOCKED",
  "GEMINI_FILE_SEARCH": "BLOCKED",
  "GROK_NATIVE_DOCUMENT": "BLOCKED"
}
```

## FILES CREATED/MODIFIED

- `/opt/cursor/artifacts/GATE_P_REPORT.md`
- `/workspace/tasks/research/gate_p/GATE_P_REPORT.md`
- `/workspace/tests/fixtures/gate_p/GATE_P_REPORT.md`
- `tests/gate_p/`
- `scripts/run_gate_p_benchmark.py`
- `tests/test_gate_p_provider_benchmark.py`
- `tasks/research/gate_p/GATE_P_REPORT.md`

## TESTS

pytest tests/test_gate_p_provider_benchmark.py tests/test_gate_m_gold_benchmark.py — 12 passed; python3 scripts/run_gate_p_benchmark.py — PARTIAL (provider keys absent)

STOP.

