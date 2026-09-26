# GATE BEN BUSINESS + CHAT-NATIVE AGENT NETWORK

**Mode:** RESEARCH + ARCHITECTURE + PLANNING ONLY  
**Date:** 2026-09-13  
**Recommendation:** **MODIFY**  
**decision_003:** remains LOCKED  
**Production impact:** none. No code, migrations, flags, FTS, auth/RLS, or deploys.

This note answers: what is the **smallest defensible architecture** that can prove

```
CHAT → INTENT → VERIFIED CAPABILITY → REAL BUSINESS → HUMAN → RESPONSE → CHAT
```

and how BEN should sequence work so that **BEN Business has value before a network exists**.

It does **not** start the next gate. Promotion to `queued/` requires an explicit human decision.

---

## 1. EXECUTIVE RECOMMENDATION

**MODIFY.**

Not GO: the product is not an Agent Web, not a marketplace, not autonomous commerce, and not “pay for an Agent Card.”

Not NO-GO: a real loop is testable, and BEN already has the substrate (Clerk identity, org tenancy, chat threads, workspace files, jobs, construction copilot tools).

**Modified thesis:**

> Sell **BEN Business**: a chat-native workspace that understands demand, qualifies it, and puts a structured request in front of an accountable human. Agent-Ready publication is a **later included capability**, not the SKU.

Until Intent→RFQ→human response works inside BEN, do not say “network.” Call it **chat-native lead/RFQ infrastructure**. A network exists only when repeat **edges** (matches, RFQs, quotes, relationships) form.

**Smallest new infrastructure BEN must build** (the question that gates the work plan):

Eight objects + four services. Nothing else in V1.

Objects: `Business`, `BusinessCapability`, `RoutingPolicy`, `Intent`, `Match`, `Rfq`, `Quote`, `ActionEvent` (+ a thin `AuditEvent`).

Services: intent extraction (frontier JSON schema), deterministic matcher, deterministic router, human inbox + notify.

Everything else (crawler platform, A2A server, public registry, embeddings, offers, autonomy, payments, multiple agent personas, ecommerce) is **deferred or rejected**.

**Exact next gate (revised 2026-09-13):** **P1 — Business draft + Private Supplier Directory + Procurement conversation.**  
Former **B1 (domain verification for inbound network RFQs) is withdrawn.** See `GATE_BEN_BUSINESS_PLAN_REVISION.md`. Not URL crawl. Not Agent Cards. Not matching. Not domain verify.

---

## 2. WHAT CHANGED SINCE THE PREVIOUS MODIFY

Previous discovery (`gate_agent_web`, 2026-09-13) concluded MODIFY around URL→Agent-Ready. That conclusion still holds **for agentization**. This gate **repositions the product**.

| Previous | Now |
|---|---|
| Product ≈ pay to become Agent-Ready | Product ≈ **BEN Business**; Agent-Ready is included later |
| URL understanding on the critical path | **Off the critical path.** Manual verified capabilities are enough for the first loop |
| Open Agent Surface as a near-term pillar | **Compatible later.** Do not delay the first human RFQ |
| “Network” as the frame | **Infrastructure until edges exist.** Do not market network |
| Multi-role agents | **One Business Agent with roles**, human-default |
| Discovery architecture A–E all designed | **Lock card schema only.** Implement well-known/registry after the loop works |
| 50–100 businesses as first experiment | Split: **Technical POC** (3–5 businesses, scripted) → **Product pilot** (10–20, own inquiries + a few BEN intents) → **Network pilot** (50–100) only if product pilot produces TIME TO ACTION and response rate |
| Charge for verification/routing | Charge for **workspace + RFQ desk**. Demand is an upsell once it exists |

Unchanged and still binding:

- URL is not commercial truth.
- Retail agentization is occupied by Google/Shopify/OpenAI.
- Owner verification + deterministic routing are mandatory.
- Stop at RFQ + Quote.
- Open cards are not a moat.
- `decision_003` stays locked.
- Model may reason; business owns policy; human remains accountable; autonomy is per-action and earned.

---

## 3. CURRENT BEN REUSE MAP

| Component | Verdict | Path / fact | Host Business/Intent/RFQ? |
|---|---|---|---|
| Clerk users + JWT | REUSE | `auth/clerk_auth.py` | Identity of people |
| Tenant org / personal | REUSE | `auth/tenant_binding.py` | Business is org-scoped; org ≠ Business |
| Project/workspace | EXTEND | `database/models.py` `Project` | Operating desk + files. **Not** the Business |
| Thread / Message | REUSE | `thread_service.py` | Conversation around Intent. **Not** RFQ SoR |
| Multi-Chat (named) | DEFER | Does not exist as split-pane | Single-thread Intent UX is enough |
| Provider gateway | REUSE | `services/model_gateway.py` | Draft Intent JSON, translate, assist salesperson |
| Council / Add Opinion | REUSE / DEFER | `council_service.py` | Assist only. Not matcher |
| Workspace Files + jobs | EXTEND | `services/workspace_files/*` | Evidence PDFs. Recrawl worker later |
| Evidence IR / source_state | EXTEND | `evidence_ir.py`, `Thread.source_state` | Conversational grounding ≠ capability evidence |
| KnowledgeObject / Relationship | **REJECT as host** | cognitive types only | Do not overload |
| LedgerDecision / Approval | LEARN | human approve pattern | Quote-send later; not V1 SoR |
| ProjectAgent / copilots | DEFER | `decision_003` + `project_agent_service.py` | Not the Business Agent |
| Platform “capability” catalog | **KEEP SEPARATE** | `platform_capabilities.py` | Engine switchboard ≠ business skill |
| ProjectTask | REJECT as RFQ | todo board | Wrong shape |
| Doc job queue | EXTEND | `job_queue.py` | Fetch-pack later |
| Project privileges | EXTEND | `auth/project_privileges.py` | Too thin for publish/RFQ |
| Frontend overlays | EXTEND | `App.jsx`, `NavDrawer.jsx` | Business desk + inbox |
| Public Basalt + IP limit | LEARN pattern | `public_basalt.py` | Copy for later public card |
| Load governance | EXTEND | `load_governance.py` | RFQ quotas later |
| SSRF-safe URL checks | EXTEND | news `feed_url.py` | Fetch-pack only; not B1 |
| WhatsApp | REUSE transport | `WhatsAppLink.jsx` | Adapter, not ledger |
| Stripe | DEFER | `billing/stripe_service.py` | No payments in V1 |
| Inference ledger | EXTEND | `InferenceCallRecordRow` | Cost of intent extraction |
| Hebrew `en`/`he` | REUSE | `chat_language.py` | Intent language |
| Observability | EXTEND | structured logs, no Prometheus yet | TIME TO ACTION events |
| Deployment | REUSE | Vercel + Railway + Postgres | Same stack |
| FTS / retrieval | **DO NOT CHANGE** | Gate 4A flag stays | Matcher uses verified caps, not new FTS |

**Parallel system?** No. Same Clerk, API, Postgres, SPA. New schema + routes + UI.

---

## 4. GITHUB FINDINGS

No mature repository implements:

`URL → understand business → owner verify → publish Agent Card → cold discovery → RFQ → human handoff`

as an adoptable product. Pieces exist. Stars were not used as rank.

| Project | What it is | License / risk | Class | BEN fit |
|---|---|---|---|---|
| **a2aproject/A2A** | Agent Card + task lifecycle spec | Open standard | **USE** (schema) | Publish later; do not run A2A server in V1 |
| **A2ARegistry/GlobalA2ARegistry** | Public agent directory + semantic search | Hosted registry | **LEARN** | Do not outsource BEN business identity |
| **awslabs/a2a-agent-registry-on-aws** | Serverless Agent Card registry | AWS-coupled | **LEARN** | Pattern only |
| **firecrawl/firecrawl** | URL→markdown/JSON crawl | **AGPL-3.0** core; hosted SaaS | **ADAPT (hosted API only)** | Do not vendor AGPL into BEN. Bounded fetch first |
| **Crawl4AI** | Playwright crawl, Apache-2.0 | Permissive | **ADAPT** | Fallback if hosted Firecrawl refused; still not V1 |
| **browser-use** | Agent drives a browser | MIT; stealth is cloud | **LEARN** | Hard-site fallback only; not primary ingest |
| **AHTML / AnySiteMCP / wmcp.sh** | Website→MCP tools | Various | **REJECT** | Hallucinated tools + form POST |
| **Instabidsai/agenthermes** | Agent-readiness levels + schema.org | Docs | **LEARN** | Taxonomy of “draft vs ready”; not a runtime |
| **Sill** | Hosted Agent Cards + domain proof | Commercial | **LEARN** | Adjacent competitor for cards, not RFQ desk |
| **jeswr/solid-agent-card** | WebID↔agent binding | Niche | **LEARN** | Accountability pattern |
| **KYA-OS Entity Card** | DID-anchored identity | Early | **LEARN** | Too heavy for V1 |
| **ARD** | Registry entry with representativeQueries | Early spec | **LEARN** | Cold-discovery indexing later |
| **Solace Agent Mesh RFQ** | Event-driven RFQ + A2A + SAP | Vendor | **LEARN** | HITL pause is right; do not import mesh |
| **BetterSpend / OpenS2P / Medusa / Saleor / Vendure** | Full P2P/ecom | Various | **REJECT as platform** | RFQ+Quote are 2 objects, not a commerce suite |
| **Procureflow-AI / VendorBridge-*** | Demo procurement agents | Thin / AI-calls-vendors | **REJECT** | Voice outbound, form-fill, ranking — wrong autonomy |
| **ai-lead-qualification-agent** | Hybrid rules + LLM scoring + HITL email | Typical SaaS demo | **LEARN** | Hybrid scoring idea; do not adopt stack |
| **partner-lead-distribution-engine** | Territory/capacity routing API | Small | **LEARN** | Confirms routing is rules, not embeddings |
| **InboundR** | Email→RFQ objects | Product-shaped | **LEARN** | Object fields useful; Gmail-centric |

**Supply-chain rule:** do not clone, download, or `trust_remote_code` any of the above in this gate. Unreviewed demo RFQ agents are high-risk.

---

## 5. HUGGING FACE FINDINGS

**Do not download or deploy models in this gate.** V1 intent extraction uses the existing frontier gateway with a JSON schema. Specialized models are a **later measured replacement**, not a prerequisite.

| Model | Org | License | Size / arch | Task | HE/EN | Infra | Class | Why |
|---|---|---|---|---|---|---|---|---|
| **Frontier via BEN gateway** | OpenAI/Anthropic/Google/xAI | API ToS | hosted | intent JSON, clarification | Production `en`/`he` already | existing | **USE (V1)** | Cheapest *sufficient* until gold set exists |
| **BAAI/bge-m3** | BAAI | MIT | ~XLM-R, 1024-d, 8k ctx | embed + sparse + colbert | 100+ langs; HE in MIRACL-class claims, **unverified on BEN gold** | GPU preferred; CPU possible slow | **LEARN** | Intent↔capability retrieval *after* structured match saturates |
| **BAAI/bge-reranker-v2-m3** | BAAI | Apache-2.0 | ~568M cross-encoder | rerank | multilingual | GPU for latency | **LEARN** | Same; do not add vector DB now |
| **intfloat/multilingual-e5-large** | Microsoft | MIT (card) | 0.6B, 512 tok | embed | 100 langs, 512 truncate | GPU/CPU | **LEARN** | Weaker long-doc than M3 |
| **urchade/gliner_multi-v2.1** | Urchade | Apache-2.0 | ~209M | zero-shot NER slots | multilingual; HE quality **unknown** | CPU feasible | **LEARN** | Slot fill candidate; benchmark vs frontier JSON |
| **dicta-il/dictabert-joint** | DICTA | research; **`trust_remote_code=True`** | Hebrew BERT suite | morph + NER | Hebrew-first | CPU/GPU | **REJECT until security review** | Remote code is a supply-chain stop |
| **avichr/heBERT / heBERT_NER** | avichr | MIT (git) | BERT-base | HE NER / sentiment | Hebrew | CPU | **LEARN** | PER/ORG/LOC; not product/qty/unit |
| **spivi87/alephbert-intent-he** | individual | unclear | small | grocery intents | HE chat | CPU | **REJECT** | Wrong taxonomy; synthetic; not commercial-use reviewed |
| Spam/phishing classifiers | various | mixed | — | abuse signals | EN-heavy | CPU | **LEARN** | **Never sole security authority** |

**Cheapest sufficient intelligence (V1):** regex/unit parsers for qty/unit/ISO dates where possible; frontier JSON for residual slots; deterministic match/route. Do not stand up GPU inference for a 5-business POC.

**Hosted vs self-host (later, if benchmarks win):**

- Intent JSON: keep hosted frontier. Cost is per Intent, not per token of the website.
- Embeddings: self-host bge-m3 on CPU is possible for <10k capabilities; GPU (T4-class) if QPS grows. Do not buy GPUs from this paper.
- DictaBERT: only after a security review that removes `trust_remote_code` or pins audited code.

---

## 6. STANDARDS / PROTOCOL FINDINGS

| Standard | Class | Now vs later |
|---|---|---|
| **A2A Agent Card** `/.well-known/agent-card.json` (+ legacy `agent.json`) | **USE** | **Lock field mapping now.** Serve in a later gate |
| **A2A task protocol** | **ADAPT** | After BEN↔BEN RFQ works |
| **MCP** | **LEARN** | ERP/tools later; not public business surface |
| **OpenAPI** | **USE** | BEN RFQ API |
| **schema.org JSON-LD** | **USE** | Emit when publishing |
| **sitemap / robots.txt** | **USE** | Fetch-pack politeness |
| **RFC 8615 well-known** | **USE** | Card + domain-verify file |
| **OAuth 2 / OIDC / Clerk** | **USE** | People identity |
| **WebAuthn / passkeys** | **USE** | Step-up for identity, routing, payment-destination changes. Clerk already supports passkeys |
| **UCP / ACP / AP2 / x402** | **LEARN** | Retail/payments; V1 stops before checkout |
| **agents.txt / agents.json** | **LEARN** | IETF individual drafts + competing `/agents.txt` vs `/.well-known/agents.txt`. **Not a standard.** Harvest if present; do not require |
| **llms.txt** | **ADAPT** | Pointer only |
| **Proprietary BEN protocol** | **REJECT** | |

**Known-domain vs cold discovery:** well-known helps when the URL is known. “Who can supply 200 m² in Netanya?” needs a **registry or search**. Build registry **after** the in-BEN matcher works; keep capability records in a shape that *can* be projected to Agent Card skills.

---

## 7. TECHNOLOGY MAP

| Capability | BEN needs | Existing | Source | Maturity | Class | Why | BEN-specific gap |
|---|---|---|---|---|---|---|---|
| People identity | login, org | Clerk | Current BEN | prod | USE | already wired | Business ≠ org |
| Business ownership | domain + human | DNS TXT / well-known nonce; Search Console pattern | Standard | prod | USE | do not invent | UX + stored proof |
| Step-up auth | high-risk actions | Clerk passkeys, OTP | Clerk | prod | USE | phishing-resistant | Which actions require it |
| Voice/face as primary auth | — | vendors | market | risky | **REJECT as primary** | deepfakes, privacy, storage | Device passkey uses local biometrics without BEN storing faces/voice |
| Website fetch | draft profile | SSRF checks; Firecrawl hosted; Crawl4AI | BEN + GitHub | prod crawl | ADAPT later | AGPL trap | Bounded pack + evidence |
| Page understanding | candidates | schema.org, JSON-LD, Open Graph | Standard | prod | USE | deterministic first | Vertical capability schema |
| File evidence | catalogs | Workspace Files + FTS | Current BEN | measured | EXTEND | do not replace | Provenance on claims |
| Intent overlay | thin JSON | Frontier structured output | Current BEN | prod | USE | | Gold set + no invented prices |
| Slot fill (cheap) | qty/unit/place | GLiNER / regex | HF | research | LEARN later | | Hebrew mixed units |
| Capability match | verified only | SQL + FTS | Current BEN | prod | USE | no vector DB in V1 | Vertical vocab |
| Embeddings | later recall | bge-m3 | HF | mature | LEARN | | BEN gold |
| Commercial routing | deterministic | rules engines (JSON) | many | mature | **NEW small** | LLM must not write `then` | Channel policy |
| RFQ/Quote objects | SoR | do not import Medusa | — | — | **NEW small** | 2 objects | Inbox + notify |
| Notify human | TIME TO ACTION | email + `wa.me` | Current BEN | prod | USE | | WhatsApp Business API later |
| Agent Card publish | later interop | A2A | Standard | v1.0 | USE later | | Projection of verified caps |
| Cold registry | later | A2A registries, ARD | GitHub | early | LEARN | | In-BEN matcher first |
| Abuse | quotas, spam | Basalt limiter; spam models | BEN + HF | mixed | EXTEND | models ≠ authority | RFQ quotas, domain verify |
| Payments | none | Stripe, AP2, ACP | — | — | **DEFER** | | — |
| Autonomy | none | — | — | — | **DEFER** | per-action later | Correction logs now |

### Smallest genuinely new infrastructure

1. `Business` + ownership proof + accountable contacts  
2. `BusinessCapability` with provenance (never publish `INFERRED`)  
3. `RoutingPolicy` evaluator (default HUMAN/DECLINE)  
4. `Intent` overlay on a Thread/Message  
5. `Match` record  
6. `Rfq` + `Quote`  
7. `ActionEvent` timeline (TIME TO ACTION)  
8. `AuditEvent` (who/what/which business/which auth)  
9. Intent-extract prompt+schema on existing gateway  
10. Deterministic matcher + router  
11. Business Action Inbox UI + notify adapters  

**Not new:** crawl platform, A2A server, vector DB, commerce platform, MCP, payments, multi-agent runtime, buyer/seller accounts.

---

## 8. WHAT ALREADY EXISTS

Chat, council, org tenancy, projects, files, evidence IR, jobs, construction quotation/tender tools, WhatsApp deep links, public rate-limited API pattern, Hebrew/English responses, inference cost ledger, architecture that **forbids hidden autonomy**.

---

## 9. WHAT BEN ACTUALLY NEEDS TO BUILD

The eight objects, four services, inbox UI, domain-verify flow, TIME TO ACTION events. Later: fetch-pack, card projection, registry read API.

---

## 10. WHAT BEN SHOULD NOT BUILD

- Proprietary agent protocol  
- Website-to-MCP auto tools  
- Crawler inside the repo (AGPL Firecrawl)  
- Ecommerce/procurement suite  
- Vector database for V1 matching  
- Specialized HF models without a gold benchmark  
- Multiple persistent Sales/CS/Procurement agent records  
- Feed, likes, followers  
- Buyer vs seller account types  
- BEN-held payments, KYC/AML/PCI  
- Autonomous prices, stock, contracts, payment-destination changes  
- Public Agent Surface **before** the internal loop works  
- Lifting `decision_003`  
- Changing FTS cohorts, retrieval, auth/RLS, production flags  

---

## 11. MINIMUM TARGET ARCHITECTURE

```
User (any Clerk identity)
  → existing Chat thread
  → Intent extractor (frontier JSON; no invented commercials)
  → Intent object (raw text retained)
  → Matcher (OWNER_VERIFIED capabilities only)
  → Router (deterministic policy; default HUMAN)
  → Rfq
  → notify accountable human
  → human writes Quote (structured)  [chat/email/WhatsApp = adapters]
  → customer thread receives Quote projection
```

Business side without inbound BEN demand:

```
Business owner
  → Business + domain proof + contacts
  → manual capabilities + routing
  → Action Inbox (even if first RFQs are from their own pasted inquiries)
```

One **Business Agent** = the BEN-operated pipeline above. Not a swarm. Roles (sales/service/procurement) are **permissioned views** on the same inbox.

---

## 12. MINIMUM DATA MODEL

Challenge every object. V1 only.

| Object | Why | SoR owner | Mutators | Lifecycle | Version | Provenance | Reuse? |
|---|---|---|---|---|---|---|---|
| **Business** | Accountable org node | BEN row; owner=Clerk org | owner | draft→verified→suspended | updated_at | verification_level | NEW. Not Project |
| **BusinessCapability** | Match key | Business | owner | candidate→verified→retired | version int | PUBLIC_OBSERVED / INFERRED / OWNER_VERIFIED / LIVE_CONNECTED | NEW. Not platform catalog |
| **RoutingPolicy** | Commercial truth | Business | owner + step-up | published snapshot | version; immutable snapshot on each route | owner | NEW |
| **Intent** | Thin overlay | Customer tenant | system from chat; user edits clarifications | open→matched→closed | — | confidence; raw_text | NEW. Thread is parent |
| **Match** | Audit why this business | system | system | proposed→accepted→rejected | — | rule ids | NEW |
| **Rfq** | Qualified request | interaction | system create; human not “price” | sent→acked→quoted→declined | — | — | NEW |
| **Quote** | Only commercial offer | Business human | human (or later approved draft) | draft→sent→expired | version | no inferred prices | NEW |
| **ActionEvent** | TIME TO ACTION | system | append-only | — | — | — | NEW |
| **AuditEvent** | WHO/WHAT/WHICH AUTH | system | append-only | — | — | — | NEW |
| AgentManifest | projection | derived | system | — | — | verified caps only | **DEFER serve**; map fields now |
| ProductOrServiceRef | SKU catalog | — | — | — | — | — | **DEFER** (name on capability is enough) |
| Offer | promotions | — | — | — | — | — | **DEFER** |
| BusinessRelationship | distributor graph | — | — | — | — | — | **DEFER** until a manufacturer needs ROUTE_PARTNER |
| InteractionEndpoint | A2A URL | — | — | — | — | — | **DEFER** (inbox is the endpoint) |
| Evidence | claim→URL/quote | capability | system/owner | — | hash | — | EXTEND file evidence **when crawl exists**; V1 owner statement is evidence |

**V1 relationship substitute:** routing action HUMAN or DIRECT_SALES to a named contact. No global manufacturer→contractor ontology.

---

## 13. SECURITY / IDENTITY / FRAUD ARCHITECTURE

Insert into gates, not as a finale.

| Control | When mandatory |
|---|---|
| User identity (Clerk) | B1 |
| Business ownership (DNS TXT or `/.well-known/ben-verify.txt` nonce) | B1 before verified |
| Accountable human contact (reachable) | B1 |
| Tenant isolation (existing RLS; **do not change** — new tables org-scoped like today) | B1 schema |
| RBAC: owner vs inbox agent vs viewer | B1 |
| AuditEvent | B1 |
| Rate limits on RFQ create | B5 |
| Spam/junk heuristics (rules first) | B5 |
| Domain-verified businesses only in matcher | B4 |
| Step-up (passkey/OTP) | identity change, routing policy change, payment-destination (future), permission escalation — **B1 for policy publish** |
| External-agent auth | when public surface ships (late) |
| Secrets | existing SECRETS_GOVERNANCE; no new secret classes in B1 |
| Prompt injection | site text = untrusted evidence when crawl ships |
| Agents never | identity, permission, credential, payment-destination changes |

**Voice/face as BEN-stored biometrics: REJECT as primary.** Deepfake and privacy cost dominate. Passkeys may use **device** Face ID/Touch ID locally; BEN stores a public key, not a face.

**Fraud expectation:** fake businesses, fake RFQs, impersonation. Domain verify + quotas + default HUMAN already remove the worst autonomous damage.

---

## 14. HUMAN-FIRST AGENT ARCHITECTURE

V1 agent = **pipeline + inbox**, not an autonomous commercial actor.

Allowed: understand, classify, extract, ask missing slots, qualify, identify business/contact, draft RFQ, notify, assist salesperson, project Quote into chat.

Forbidden unless a future per-action gate: invent prices/discounts/stock/delivery, accept contracts, change payment details, commit funds, place orders.

Default commercial behavior: **HUMAN**.

One Business Agent. “Create a sales agent” → configure inbox + capabilities + contacts + policy. Do not spawn a second runtime.

---

## 15. AUTONOMY-EARNING MODEL

Preserve now, implement later:

`OBSERVE → RECOMMEND → HUMAN APPROVES → LIMITED AUTONOMY → EXPANDED`

Log: recommendation vs human action, routing accuracy, quote correction, exceptions, fraud flags, financial exposure (zero in V1), repeatable workflows.

Grant **per action** (FAQ answer, route, draft quote, send quote), never per agent.

V1 only OBSERVE + RECOMMEND (draft RFQ text). Send quote = human.

---

## 16. CHAT → INTENT ARCHITECTURE

Natural language remains source. Intent is a thin overlay.

Example: “I need 200 m² polycarbonate 10mm delivered to Netanya next week.”

| Field | Source | Invent? |
|---|---|---|
| raw_text | message | never drop |
| source_thread / source_message | FK | — |
| language | existing detector | — |
| need_type | model enum + unknown | no |
| product_service_text | model/span | keep raw |
| specs_text | span | keep raw |
| quantity, unit | regex first, model second | no if missing → clarify |
| location_text | span | no geocode invention |
| required_by | date parse | no |
| budget | **only if user said it** | never |
| constraints | spans | no |
| clarifications[] | if required slots empty | — |
| confidence | model | — |

Deterministic: units, numbers, ISO dates. Model: category and messy Hebrew/English mix. Missing commercials stay missing.

---

## 17. INTENT ↔ CAPABILITY ARCHITECTURE

Match **only** `OWNER_VERIFIED` capabilities.

V1 scorer: structured overlap (need_type, geo if policy has geo, keywords on capability label + aliases) + existing FTS **read-only** against capability text. **No new embeddings.**

False capability > miss. If unclear, clarify or HUMAN.

Record `Match` with reason codes (not a vibe score only).

---

## 18. COMMERCIAL ROUTING

Evaluator: first matching owner rule; default HUMAN or DECLINE.

Actions V1: `HUMAN | DECLINE | DIRECT_SALES` (named contact). `ROUTE_PARTNER | FANOUT_RFQ` wait for BusinessRelationship (later).

LLM fills Intent. LLM does not write policy. Policy publish requires step-up.

---

## 19. RFQ / QUOTE ARCHITECTURE

`Intent → Match → Rfq → Human → Quote → customer Chat`

Quote fields: issuer Business, rfq_id, lines, validity, delivery terms or `unspecified`, actor_id, timestamps.

Transports: BEN inbox first; email/`wa.me` notify. API/A2A later.

Chat is not the ledger.

---

## 20. BUSINESS OFFERS ARCHITECTURE

**DEFER.** Relevance-gated Offer↔Intent is correct, but it is an advertising surface and will become spam. After the RFQ loop has fill-rate data.

---

## 21. OPEN / EXTERNAL AGENT COMPATIBILITY

**Compatible now / implement later.**

Now: capability ids, descriptions, evidence, auth “inbox”, no BEN-only skill ontology that cannot map to A2A `AgentSkill`.

Later: well-known card, BEN-hosted fallback, public registry GET.

External agents must **not** delay the first BEN-user→real-business experiment.

Public read of verified capabilities can be free; infrastructure stays paid. Still unproven — do not build the open door until abuse controls from B5 exist.

---

## 22. TRUE MVP

One happy path:

Owner verifies Business + 1–N capabilities + HUMAN contact.  
User says need in Chat.  
Intent extracted (clarifications if needed).  
Match to verified capability.  
RFQ in inbox + notify.  
Human sends structured Quote.  
User sees Quote in Chat.

Metrics: TIME TO ACTION, match quality, capability precision, RFQ response rate, quote rate, human correction rate, false capability rate.

If this fails, do not build the larger network.

---

## 23. TECHNICAL POC

3–5 **invited** businesses (can include Basalt + 2 friendly suppliers). Scripted intents. No public signup. No crawl. Manual capabilities. Success = one real human answers inside a measured TIME TO ACTION.

---

## 24. PRODUCT PILOT

10–20 businesses. Standalone BEN Business value: inbox for **their own** inquiries (paste/email later) + Multi-Chat + files. Plus a handful of BEN-user intents. Willingness-to-pay signal: they use the inbox weekly without network density.

---

## 25. NETWORK PILOT

Only if product pilot passes. 50–100 in **one** cluster (construction materials IL is still the best first vertical because BEN already has quotation/tender/vendor tools and HE/EN — but it carries channel-conflict risk; treat that as a kill switch, not a surprise). 30–50 real intents. Then, and only then, optional card publication.

---

## 26. TIME-TO-ACTION MEASUREMENT

`ActionEvent` append-only, UTC, interaction_id:

`intent_expressed` (message created)  
`intent_structured`  
`qualification_completed` (clarifications done or skipped)  
`match_found` / `match_none`  
`business_notified`  
`business_acknowledged` (inbox open or explicit ack)  
`human_opened`  
`human_responded`  
`quote_returned`

Product value is **not** model latency. Dash: p50/p90 between `intent_expressed` and `human_responded` / `quote_returned`.

Reuse structured logs + new event table. Prometheus still optional.

---

## 27. BUSINESS MODEL

| Layer | What they get | When it is real |
|---|---|---|
| BEN Core / Personal | Multi-Chat | exists |
| BEN Business | workspace, files, inbox, qualification, Agent-Ready *later included* | after B1–B6 |
| BEN Network | qualified inbound demand | only after network pilot |

**Challenge “Agent-Ready included”:** good packaging, bad if it delays the inbox. Include it as a **checkbox that ships late**, not as onboarding step 1.

Pricing: subscription for Business software is the honest SKU. Lead fees create spam. Commissions need GMV you will not have. Usage on inference is already metered internally — optional overage, not the story.

Businesses pay for outcomes (faster qualified handling, less junk) and tools. Not JSON.

---

## 28. NETWORK EFFECT ANALYSIS

Flywheel (users generate demand; businesses generate capability; BEN connects) is **conditional**.

Breaks if: no verified caps, false caps, ignored RFQs, demand happens in ChatGPT, channel conflict, open scrape of cards.

**Definitions:** NODE=Business/user; EDGE=Match/RFQ/Quote/relationship; INTENT=object; CAPABILITY=verified claim; INTERACTION=RFQ/Quote; RELATIONSHIP=repeat or `authorized_by`.

Directory: cards, zero interactions.  
Network: repeat edges and fill-rate memory.

Do not use “network” in product copy until network pilot shows repeat buyer intents and business-side response.

---

## 29. DEFENSIBILITY

| Asset | Compounds? | Moat? |
|---|---|---|
| Open Agent Card | no | no |
| Crawl | no | no |
| Generic LLM | no | no |
| Owner-verified caps + aliases | yes | weak-moderate |
| Routing policies | yes | **strongest unique** |
| Human correction history | yes | moderate |
| Fill-rate / TIME TO ACTION | yes | moderate |
| Vertical density | yes | classic |
| Business Memory | yes | after data exists |
| Integrations | yes | slow |
| Reliability/trust | yes | process, copyable |

---

## 30. KILL CRITERIA

Stop or modify if:

- owners will not verify capabilities  
- false capability rate ≥ 10% published  
- users distrust matches  
- businesses ignore RFQs (response < 30% in 2 business days)  
- TIME TO ACTION not better than email/WhatsApp they already use  
- RFQs are mostly junk  
- nobody will pay for the inbox  
- channel-conflict incident caused by BEN  
- human workload increases  
- abuse/security cost dominates  
- incumbents (Google/Shopify/ChatGPT) make the B2B desk irrelevant  

Do not protect the idea.

---

## 31. ORDERED GATE PLAN

Candidate sequence **rejected as-is**. URL understanding moved **after** the commercial loop. Offers, open surface, autonomy pushed back. Observability pulled **into B1**.

### GATE B0 — this document

PURPOSE: research + architecture + plan.  
HYPOTHESIS: smallest loop is chat→human quote without crawl/cards.  
USER VALUE: none yet.  
ARCHITECTURAL CHANGE: none.  
REUSED: none.  
EXTERNAL: standards/GitHub/HF survey.  
NEW: this note.  
DATA: none.  
DEPS: none.  
SECURITY: none.  
OBS: none.  
TESTS: none.  
SUCCESS: human accepts MODIFY + B1.  
FAILURE: treated as a build backlog.  
ROLLBACK: n/a.  
PASS: this file + no production change.  
MUST NOT START: any implementation, migrations, queued tasks.

### GATE B1 — Business identity, ownership, accountable human — **NEXT**

PURPOSE: a real business can exist in BEN with a reachable human.  
HYPOTHESIS: Clerk org + domain proof + named contact is enough to receive an RFQ later.  
USER VALUE: “this is my business; I am the accountable person.” First **business** foothold, not yet RFQ.  
ARCHITECTURAL CHANGE: new org-scoped tables `Business`, `BusinessContact`, `AuditEvent`; domain-verify flow; no RLS redesign (same `org_id` pattern).  
REUSED: Clerk, tenant binding, frontend overlay pattern, privileges pattern.  
EXTERNAL: DNS TXT / well-known nonce; Clerk passkeys for policy later (enable if already available, do not build WebAuthn).  
NEW: Business APIs + minimal UI.  
DATA: Business (draft/verified), contacts, verification_level, AuditEvent.  
DEPS: B0 pass.  
SECURITY: ownership proof before `verified`; tenant isolation; audit; no agent can change identity.  
OBS: `business_created`, `domain_verified`.  
TESTS: cannot mark verified without proof; cannot attach another org’s domain; no secret leakage in verify nonce logs.  
SUCCESS: 3 invited owners complete verify + contact.  
FAILURE: owners cannot prove domain; impersonation possible.  
ROLLBACK: feature-flag UI off; tables unused. **Do not change production auth.**  
PASS: verified Business + contact + tests.  
MUST NOT START: crawl, matcher, public cards, FTS changes.

**Complexity: SMALL**

### GATE B2 — Manual capabilities + routing policy (default HUMAN)

PURPOSE: verified capabilities exist without a website.  
HYPOTHESIS: owners can name 3–10 capabilities more accurately than a crawler.  
USER VALUE: “we do X, we do not do Y.”  
CHANGE: `BusinessCapability`, `RoutingPolicy` snapshot; provenance enum; publish ≠ inferred.  
REUSED: none of platform capability catalog.  
EXTERNAL: none.  
NEW: capability editor; policy JSON evaluator stub with HUMAN/DECLINE/DIRECT_SALES.  
DATA: capabilities, policy versions.  
DEPS: B1.  
SECURITY: policy publish = step-up if passkeys on; else reauth. Inferred never publishable.  
OBS: `capability_verified`, `policy_published`.  
TESTS: inferred cannot match; default HUMAN; evaluator unit tests.  
SUCCESS: precision 100% on owner-entered set (by definition); owners finish in <30 min.  
FAILURE: owners refuse to enumerate capabilities.  
ROLLBACK: flag off.  
PASS: ≥1 OWNER_VERIFIED capability + HUMAN contact rule.  
MUST NOT START: matching live demand.

**Complexity: SMALL**

### GATE B3 — Chat → Intent overlay

PURPOSE: utterance becomes a thin Intent.  
HYPOTHESIS: frontier JSON + regex units beats a new HF model on mixed HE/EN trade chat.  
USER VALUE: BEN asks only missing slots, does not invent budget/price.  
CHANGE: `Intent` row; extract via `model_gateway` structured output; **no FTS change**.  
REUSED: Thread, Message, chat_language, gateway, inference ledger.  
EXTERNAL: none of HF.  
NEW: schema, clarifier messages.  
DATA: Intent.  
DEPS: none on B2 technically; product-wise after B2 so Intent has somewhere to go.  
SECURITY: prompt treats user text as data; no tool that sends RFQ yet.  
OBS: `intent_expressed`, `intent_structured`, `qualification_completed`; extraction cost.  
TESTS: gold 20 utterances (HE, EN, mixed) — no invented budget; qty/unit; Netanya example.  
SUCCESS: slot-F1 vs gold ≥ owner-judged usable; zero invented commercials.  
FAILURE: systematic invention of price/stock.  
ROLLBACK: extractor off; chat unchanged.  
PASS: gold suite + flag default off until B5.  
MUST NOT START: specialized model download.

**Complexity: MEDIUM**

### GATE B4 — Intent ↔ verified capability match

PURPOSE: pick businesses without embeddings.  
HYPOTHESIS: SQL/FTS-on-capability-text is enough at <100 caps.  
USER VALUE: “these verified businesses can take this.”  
CHANGE: `Match`; read-only use of existing FTS **only if** capability text is stored in a new table, **not** by changing file FTS cohorts.  
REUSED: optional `plainto_tsquery` patterns from chunk retriever — copy logic, do not flip production flags.  
EXTERNAL: none.  
NEW: matcher service.  
DATA: Match.  
DEPS: B2, B3.  
SECURITY: unpublished/inferred caps excluded.  
OBS: `match_found` / `match_none`.  
TESTS: false-capability fixtures; M09-style paraphrase later.  
SUCCESS: precision ≥ 90% on 20 scripted intents; false cap = 0.  
FAILURE: need embeddings immediately (then LEARN bge-m3 in a **measurement** gate, not silently).  
ROLLBACK: matcher off.  
PASS: tests + precision bar.  
MUST NOT START: vector DB.

**Complexity: SMALL–MEDIUM**

### GATE B5 — RFQ to real human (TRUE LOOP left half)

PURPOSE: qualified request reaches a person.  
HYPOTHESIS: notify + inbox beats waiting for A2A.  
USER VALUE: first **qualified lead**.  
CHANGE: `Rfq`, Action Inbox, email/`wa.me` notify, quotas.  
REUSED: WhatsAppLink, email if any, Basalt rate-limit pattern, ActionCard patterns.  
EXTERNAL: none.  
NEW: inbox UI.  
DATA: Rfq, ActionEvent.  
DEPS: B1–B4.  
SECURITY: RFQ quotas; verified businesses only; audit actor.  
OBS: `business_notified`, `human_opened`.  
TESTS: cannot RFQ unverified; quota; tenant isolation.  
SUCCESS: POC humans open RFQ (ack) in < 4 hours p50.  
FAILURE: ignored inbox.  
ROLLBACK: notify off.  
PASS: ≥3 real RFQs acknowledged.  
MUST NOT START: payments, public registry.

**Complexity: MEDIUM**

### GATE B6 — Quote back to customer chat (TRUE LOOP right half)

PURPOSE: structured Quote is SoR; chat shows projection.  
HYPOTHESIS: humans will fill 5 fields if inbox is simple.  
USER VALUE: first **business response** in BEN.  
CHANGE: `Quote`; human compose; project into thread.  
REUSED: Message insert path.  
EXTERNAL: none.  
NEW: quote form.  
DATA: Quote.  
DEPS: B5.  
SECURITY: only business members mint Quote; no model auto-send.  
OBS: `human_responded`, `quote_returned`.  
TESTS: chat text ≠ Quote SoR; expiry.  
SUCCESS: quote rate ≥ 50% of acked RFQs in POC.  
FAILURE: humans reply only on WhatsApp and refuse structure — then add **capture adapter** (paste/parse) before giving up.  
ROLLBACK: quote UI off.  
PASS: one end-to-end timed loop.  
MUST NOT START: autonomy, offers, A2A.

**Complexity: MEDIUM**

### GATE B7 — URL → Draft Business Understanding (accelerator)

PURPOSE: faster onboarding, not truth.  
HYPOTHESIS: bounded fetch + schema.org produces useful **candidates**.  
USER VALUE: owner edits instead of typing from scratch.  
CHANGE: fetch-pack job; candidates `INFERRED`/`PUBLIC_OBSERVED`.  
REUSED: SSRF-safe URL, DocumentProcessingJob pattern, Workspace Files for PDFs.  
EXTERNAL: **hosted** Firecrawl API optional; else httpx+readability; Crawl4AI only if license review. **No AGPL in-tree.** Browser-use not primary.  
NEW: draft UI diff.  
DATA: evidence URLs/hashes on candidates.  
DEPS: B2 (so owner can accept/reject).  
SECURITY: untrusted HTML; size caps; robots; no private IP.  
OBS: fetch cost, candidate accept rate.  
TESTS: inferred cannot publish; SSRF suite.  
SUCCESS: owner accept ≥ 50% of candidates as starting point.  
FAILURE: garbage candidates; then keep manual B2.  
ROLLBACK: crawl off.  
PASS: draft-only path.  
MUST NOT START: live commercial from crawl.

**Complexity: MEDIUM**

### GATE B8 — Business Action Inbox polish / dashboard

PURPOSE: TIME TO ACTION visible to the business.  
Can **merge into B5/B6** if UI is already the inbox. Standalone only if inbox is unusable.  
**Complexity: SMALL** if merged; else SMALL.

### GATE B9 — Offers ↔ Intent

**DEFER.**  
**Complexity: MEDIUM** (spam risk). Do not start before network pilot.

### GATE B10 — Open Agent Surface / external discovery

PURPOSE: Scenario C without BEN seat.  
HYPOTHESIS: projecting verified caps to A2A card is cheap **after** B6.  
CHANGE: well-known or BEN-hosted card; optional public GET search.  
REUSED: Basalt public pattern.  
EXTERNAL: A2A spec.  
DEPS: B2, B5 abuse controls.  
SECURITY: public GET cache; no inferred; RFQ still authenticated.  
PASS: external script fetches card without Clerk.  
MUST NOT START: before B6 pass.  
**Complexity: MEDIUM**

### GATE B11 — Measured learning from human actions

PURPOSE: correction logs for future per-action autonomy.  
DEPS: B6. OBSERVE/RECOMMEND only.  
**Complexity: SMALL**

### GATE B12 — Limited autonomy experiments

**DEFER.** Requires B11 metrics bars, new explicit decision, still not lifting `decision_003` globally — a **narrow per-action** decision record instead.  
**Complexity: LARGE** (product+legal). Do not start.

---

## 32. DEPENDENCIES BETWEEN GATES

```
B0
 └─ B1
     └─ B2
         ├─ B3 (can start research/gold in parallel with B1)
         │    └─ B4 (needs B2+B3)
         │         └─ B5
         │              └─ B6  ← first complete loop
         │                   ├─ B8 (if not merged)
         │                   ├─ B11
         │                   └─ B10 (after abuse controls)
         └─ B7 (after B2; not on loop critical path)
B9 after network evidence
B12 after B11 + new decision
```

---

## 33. WHICH GATES CAN RUN IN PARALLEL

**Research parallel (no schema):** HF intent gold-set design; A2A field mapping; Firecrawl **contract** (no integration); vertical capability vocab for construction IL.

**Develop parallel:** B3 gold utterances while B1 UI is built — **if** they do not land competing Intent schemas. One owner of `Intent` JSON.

**Must stay sequential:** B1→B2→B4→B5→B6. B7 never blocks B5. B10 never before B6. No parallel “agent runtime” vs “inbox.”

---

## 34. ROUGH COMPLEXITY

| Gate | Size |
|---|---|
| B0 | SMALL (done) |
| B1 | SMALL |
| B2 | SMALL |
| B3 | MEDIUM |
| B4 | SMALL–MEDIUM |
| B5 | MEDIUM |
| B6 | MEDIUM |
| B7 | MEDIUM |
| B8 | SMALL |
| B9 | MEDIUM (deferred) |
| B10 | MEDIUM (deferred) |
| B11 | SMALL |
| B12 | LARGE (deferred) |

---

## 35. FIRST REAL USER VALUE

**Already exists:** Multi-Chat.  
**First new user value:** B3 clarifications + B6 quote in thread.  
Users do not need a network to chat.

---

## 36. FIRST REAL BUSINESS VALUE

**B1–B2 + inbox (B5)** even with **manually pasted** inquiries: qualification + routing to the right human. This is the **cold-start SKU**.  
**First qualified lead from a BEN user:** B5.  
**First real business response in BEN:** B6.  
**First willingness to pay:** product pilot using inbox weekly (not card publish).  
**First network behavior:** repeat Intent→Quote edges in network pilot.

---

## 37. GATE THAT PROVES OR DISPROVES THE NETWORK THESIS

**B6 in a product/network pilot, not B10.**

B6 POC with 3 friends proves **plumbing**.  
Network thesis needs: multiple businesses, independent users, repeat matches, TIME TO ACTION better than status quo, businesses answering. If B6 works and nobody repeats, it is a **helpdesk**, which can still be BEN Business — then **drop the network claim**.

---

## 38. EXACT RECOMMENDED NEXT GATE ONLY

**GATE B1 — Business identity, ownership verification, and accountable human contact.**

Do not implement B1 in this gate. Do not migrate. Do not change production. Do not lift `decision_003`. Do not download models. Do not start B2–B12 automatically.

STOP.

THE MODEL MAY REASON.  
THE BUSINESS OWNS COMMERCIAL POLICY.  
THE HUMAN REMAINS ACCOUNTABLE.  
AUTONOMY MUST BE EARNED.
