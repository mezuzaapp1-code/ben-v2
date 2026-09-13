# GATE P2 — Native Document Provider Benchmark

GATE P2 STATUS: BLOCKED

RESEARCH / MEASUREMENT ONLY. Production adapters, retrieval, FTS,
PER_FILE_MAX_CHARS, and gold labels were not changed.
Provider File Search / vector stores were not used.

SECURE ENVIRONMENT:
OPENAI_API_KEY: KEY_PRESENT=false
ANTHROPIC_API_KEY: KEY_PRESENT=false
GOOGLE_API_KEY: KEY_PRESENT=false
GEMINI_API_KEY: KEY_PRESENT=false
XAI_API_KEY: KEY_PRESENT=false
RAILWAY_TOKEN: KEY_PRESENT=true
cursor_injected_secret_names: MISTRAL_API_KEY, BEN_DOC_PROCESSING_CRON_SECRET, BEN_GATE_A_CANARY_BEARER, RAILWAY_TOKEN
railway_can_inject_provider_keys: False
railway_me_error: Not Authorized
railway_project_token_error: Project Token not found
safe_injection_possible: False
action: STOP
reason: Provider keys are not present in this process, Cursor did not inject them, and Railway cannot supply them (me=Not Authorized; project_token=Project Token not found). No secrets were requested in chat. File Search was not used.

OPENAI:
model: not_run
correct: BLOCKED
unanswerable: BLOCKED
early: BLOCKED
middle: BLOCKED
late: BLOCKED
exceptions: BLOCKED
multi-hop: BLOCKED
global: BLOCKED
tables: BLOCKED
citations: BLOCKED
latency: BLOCKED
tokens: BLOCKED
cost: BLOCKED
DOCUMENT_AVAILABLE_TO_MODEL: NOT_RUN
FINAL ANSWER QUALITY: NOT_RUN
blocked_reason: OPENAI_API_KEY KEY_PRESENT=false

CLAUDE:
model: not_run
correct: BLOCKED
unanswerable: BLOCKED
early: BLOCKED
middle: BLOCKED
late: BLOCKED
exceptions: BLOCKED
multi-hop: BLOCKED
global: BLOCKED
tables: BLOCKED
citations: BLOCKED
latency: BLOCKED
tokens: BLOCKED
cost: BLOCKED
DOCUMENT_AVAILABLE_TO_MODEL: NOT_RUN
FINAL ANSWER QUALITY: NOT_RUN
blocked_reason: ANTHROPIC_API_KEY KEY_PRESENT=false

GEMINI:
model: not_run
correct: BLOCKED
unanswerable: BLOCKED
early: BLOCKED
middle: BLOCKED
late: BLOCKED
exceptions: BLOCKED
multi-hop: BLOCKED
global: BLOCKED
tables: BLOCKED
citations: BLOCKED
latency: BLOCKED
tokens: BLOCKED
cost: BLOCKED
DOCUMENT_AVAILABLE_TO_MODEL: NOT_RUN
FINAL ANSWER QUALITY: NOT_RUN
blocked_reason: GOOGLE_API_KEY/GEMINI_API_KEY KEY_PRESENT=false

GROK:
model: not_run
correct: BLOCKED
unanswerable: BLOCKED
early: BLOCKED
middle: BLOCKED
late: BLOCKED
exceptions: BLOCKED
multi-hop: BLOCKED
global: BLOCKED
tables: BLOCKED
citations: BLOCKED
latency: BLOCKED
tokens: BLOCKED
cost: BLOCKED
DOCUMENT_AVAILABLE_TO_MODEL: NOT_RUN
FINAL ANSWER QUALITY: NOT_RUN
blocked_reason: XAI_API_KEY KEY_PRESENT=false; Responses/file path not entered

COMPARISON:

| Mode | Correct | Unanswerable | Early | Middle | Late | Exceptions | Multi-hop | Global | Tables |
|---|---|---|---|---|---|---|---|---|---|
| BEN prefix | 27/50 | 7/7 | 19/20 | 1/6 | 0/13 | 1/5 | 1/6 | 2/5 | 3/6 |
| BEN FTS | 48/50 | 7/7 | 18/20 | 6/6 | 13/13 | 5/5 | 5/6 | 5/5 | 6/6 |
| OpenAI native | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |
| Claude native | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |
| Gemini native | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |
| Grok native | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |

BEST CHEAP RETRIEVAL: BEN_EXISTING_FTS (committed Gate P)
BEST FULL-DOCUMENT REASONING: BLOCKED — native PDF modes not run
BEST EXCEPTION HANDLING: BEN_EXISTING_FTS 5/5 among measured modes
BEST MULTI-HOP: BEN_EXISTING_FTS 5/6 among measured modes
BEST GLOBAL DOCUMENT UNDERSTANDING: BEN_EXISTING_FTS 5/5 among measured modes
BEST TABLE UNDERSTANDING: BEN_EXISTING_FTS 6/6 among measured modes
BEST COST/PERFORMANCE: BEN_EXISTING_FTS ($0 provider spend)
DOES ANY NATIVE PROVIDER MATERIALLY BEAT BEN FTS? NOT_MEASURED
M09: Native PDF not run. Committed: prefix recovered M09; isolated FTS missed it (lexical paraphrase).
M33: Native PDF not run. Committed FTS retrieved quantity+price pages; remaining miss is computation, not retrieval.
BEN ROUTING IMPLICATION: Unchanged from Gate P until native PDF is measured: FTS remains the cheap measured retrieval path; native document stays a possible later escalation, not a replacement.
RECOMMENDATION: Do not implement provider PDF support from this gate. Add OPENAI_API_KEY, ANTHROPIC_API_KEY, and GOOGLE_API_KEY to the BEN Document Benchmark Cursor environment via the dashboard secret injector, then re-run Gate P2. Do not paste key values into chat.

STOP.

Do not implement provider PDF support.
Do not change production.
