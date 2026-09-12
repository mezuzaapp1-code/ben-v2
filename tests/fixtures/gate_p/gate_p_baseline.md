# Gate P baseline

- Status: PARTIAL
- Gold: BEN Gold Supply Agreement, 18 pages, 50 questions

## Capability matrix

### OpenAI
- BEN integration: Chat Completions adapter (gpt-5.5-instant / gpt-5.5-pro); multimodal user parts are images only (['image/gif', 'image/jpeg', 'image/jpg', 'image/png', 'image/webp']). No Responses API, no input_file, no file_search in BEN adapters.
- Direct PDF: Provider API: yes via Responses input_file (PDF text+page images on vision models). BEN integration: no.
- Provider retrieval: Provider API: yes, Responses file_search + vector stores. BEN integration: no.
- Citations: file_search annotations / include=file_search_call.results. Native input_file does not expose retrieved chunks.
- Credentials usable: False
- Safe to benchmark now: False
- Blocked: OPENAI_API_KEY not present in this environment

### Anthropic / Claude
- BEN integration: Messages adapter (claude-sonnet-4.6 / claude-opus-4.8); image blocks only. No document/PDF content blocks in BEN adapters.
- Direct PDF: Provider API: yes, Messages document block (base64 / URL / Files API) with optional citations. BEN integration: no.
- Provider retrieval: No hosted File Search product comparable to OpenAI/Gemini. Files API is storage for document blocks, not retrieval. CLAUDE_PROVIDER_RETRIEVAL is not an applicable provider capability.
- Citations: Native PDF citations (page-indexed) when citations.enabled=true on the document block.
- Credentials usable: False
- Safe to benchmark now: False
- Blocked: ANTHROPIC_API_KEY not present in this environment

### Google / Gemini
- BEN integration: generateContent adapter (gemini-3.5-flash); inlineData images only. application/pdf is not a BEN vision media type, so chat never sends PDFs.
- Direct PDF: Provider API: yes, generateContent inlineData/file_data application/pdf (native document vision). BEN integration: no.
- Provider retrieval: Provider API: yes, File Search stores + file_search tool. BEN integration: no.
- Citations: File Search grounding citations. Native PDF does not expose retrieved chunks.
- Credentials usable: False
- Safe to benchmark now: False
- Blocked: GOOGLE_API_KEY not present in this environment

### Grok / xAI
- BEN integration: Chat Completions adapter (grok-4.3 / grok-4.6); images only. xAI document attachments require the Responses API, which BEN does not call.
- Direct PDF: Provider API: yes via Responses input_file (file_id or file_url), which activates attachment_search. BEN Chat Completions path: no.
- Provider retrieval: Implicit attachment_search when files are attached on Responses. Not exposed on BEN's Chat Completions client.
- Citations: Not documented as observable retrieved chunks on Chat Completions.
- Credentials usable: False
- Safe to benchmark now: False
- Blocked: XAI_API_KEY not present in this environment

## Comparison

| Mode | Answer | Recall | MRL | Unans | Early | Middle | Late | Exc | Multi | Global | Latency | Tokens | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BEN_PREFIX_2000 | 27/50 | 21/43 | 22 | 7/7 | 19/20 | 1/6 | 0/17 | 1/5 | 1/6 | 2/5 | local | 0/0 | — |
| BEN_EXISTING_FTS | 48/50 | 42/43 | 1 | 7/7 | 18/20 | 6/6 | 17/17 | 5/5 | 5/6 | 5/5 | local | 0/0 | — |
| OPENAI_NATIVE_DOCUMENT | BLOCKED | — | — | — | — | — | — | — | — | — | — | — | — |
| OPENAI_FILE_SEARCH | BLOCKED | — | — | — | — | — | — | — | — | — | — | — | — |
| CLAUDE_NATIVE_DOCUMENT | BLOCKED | — | — | — | — | — | — | — | — | — | — | — | — |
| CLAUDE_PROVIDER_RETRIEVAL | BLOCKED | — | — | — | — | — | — | — | — | — | — | — | — |
| GEMINI_NATIVE_PDF | BLOCKED | — | — | — | — | — | — | — | — | — | — | — | — |
| GEMINI_FILE_SEARCH | BLOCKED | — | — | — | — | — | — | — | — | — | — | — | — |
| GROK_NATIVE_DOCUMENT | BLOCKED | — | — | — | — | — | — | — | — | — | — | — | — |

- gate_status: PARTIAL
- best_overall: BEN_EXISTING_FTS
- best_retrieval: BEN_EXISTING_FTS
- best_full_document_reasoning: BLOCKED — provider native modes not run
- best_cost_performance: BEN_EXISTING_FTS
- best_exception_detection: BEN_EXISTING_FTS
- document_position_effect: Prefix is a position cliff (early 19/20, middle 1/6, late 0/17). Isolated FTS removes the cliff (early 18/20, middle 6/6, late 17/17) with one early ranking miss.
- important_finding: On this gold file, isolated existing chunk FTS collapsed Gate M position loss: late 0/17 → 17/17, exceptions 1/5 → 5/5, MRL 22 → 1. The remaining FTS miss is M09 ranking (delivery-place query retrieved pages 7 and 11). M33 is model-arithmetic, not retrieval. Provider native PDF/file-search were not run: this Cloud Agent environment has no OpenAI/Anthropic/Google/xAI keys.
- should_gate_d_proceed: YES
- why: Isolated existing chunk FTS recovered late/exception evidence that prefix_2000 lost. Provider native document modes were not measurable here (missing API keys), so they do not replace Gate D. Enable FTS on a canary and re-run Gate M/P. Prefix MRL 22 vs FTS MRL 1 (delta 21).
- smallest_next_step: Canary-enable existing BEN chunk FTS (flag + workspace allowlist). Do not add embeddings.
- strategies: {'BEN_PREFIX_2000': 'FALLBACK', 'BEN_EXISTING_FTS': 'USE', 'OPENAI_NATIVE_DOCUMENT': 'BLOCKED', 'OPENAI_FILE_SEARCH': 'BLOCKED', 'CLAUDE_NATIVE_DOCUMENT': 'BLOCKED', 'CLAUDE_PROVIDER_RETRIEVAL': 'REJECT', 'GEMINI_NATIVE_PDF': 'BLOCKED', 'GEMINI_FILE_SEARCH': 'BLOCKED', 'GROK_NATIVE_DOCUMENT': 'BLOCKED'}
- measured_modes: ['BEN_PREFIX_2000', 'BEN_EXISTING_FTS']
- blocked_modes: ['OPENAI_NATIVE_DOCUMENT', 'OPENAI_FILE_SEARCH', 'CLAUDE_NATIVE_DOCUMENT', 'CLAUDE_PROVIDER_RETRIEVAL', 'GEMINI_NATIVE_PDF', 'GEMINI_FILE_SEARCH', 'GROK_NATIVE_DOCUMENT']
- one_strategy_or_route: Not enough live provider data to choose native-document routing. Among measured modes, existing chunk FTS should be the default retrieval path and prefix_2000 a fallback. Native long-context remains an unmeasured escalation candidate for exception/global questions.
