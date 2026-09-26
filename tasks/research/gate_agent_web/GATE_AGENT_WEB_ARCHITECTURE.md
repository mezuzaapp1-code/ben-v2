# GATE AGENT WEB — Architecture Discovery

**Status:** RESEARCH ONLY  
**Date:** 2026-09-13  
**Recommendation:** **MODIFY**  
**Production impact:** none. No adapters, retrieval, FTS, flags, migrations, or deploys.

This note evaluates whether BEN can productize:

> Give BEN a website URL → BEN makes the business Agent-Ready → agents (BEN and third-party) discover and interact with it → the business pays BEN.

It is written against the live BEN v2 tree (provider-first workspace, document intelligence Gates 1–4A, construction copilot tools, locked `decision_003` against autonomous agents) and against the 2026 open-protocol landscape (A2A, MCP, UCP, ACP, AP2, schema.org).

It does **not** validate the idea because the story is compelling. The four questions that matter:

| Question | Verdict |
|---|---|
| Technically possible? | **Partially.** URL → observed profile is credible. URL → truthful commercial agent is not automatic. |
| Commercially valuable? | **Only in a narrow B2B wedge.** Retail “agent-ready” is already being given away by platforms that own demand. |
| Defensible? | **Not via an open format.** Only via verified policy, density, freshness, and interaction history. |
| Scalable as stated? | **No.** “1,000 random URLs” produces a directory, not a network. |

---

## 0. What BEN is today (constraint, not a canvas)

BEN v2 is a **tenanted multi-model operations workspace**: Clerk identity, org-scoped projects, chat/council over named frontier models, workspace-file ingest → extract → chunk FTS, a construction/EHS copilot (quotations, invoices, supplier tenders), and a public Basalt corporate API.

It is **not** an agent network. Relevant facts:

- `decision_003_no_agents_v1.md` is **LOCKED**: no autonomous loops, no recursive agent graphs.
- Architecture principles forbid hidden autonomy and recursive AI chaos.
- “Capability” in production means an internal engine/integration catalog, not a public business skill.
- “Vendor” means a project member type and invoice matching, not a marketplace role.
- There is no Agent Card, no `/.well-known/agent-card.json`, no RFQ object, no capability registry, no website crawler for arbitrary businesses.
- SSRF-safe fetch exists for **news RSS only**.
- WhatsApp is `wa.me` deep links, not WhatsApp Business API.
- Stripe Checkout exists as a thin Pro helper, not a merchant-of-record product.
- Gate P/P2 showed prefix retrieval is a position filter; isolated FTS recovered late evidence; native provider PDF was **BLOCKED** on credentials. Document understanding is measured and incomplete, not magical.

Anything that looks like an Agent Web is a **new product line**, not a toggle on current chat.

---

## 1. Executive conclusion

The hypothesized transformation is real as a **category**, and already contested.

The human web (pages, navigation, forms, interpretation) is being overlaid by an agent web (capabilities, policies, actions, evidence, permissions). Google, Shopify, OpenAI/Stripe, and Microsoft are standardizing **retail** discovery-to-checkout: product feeds, `/.well-known/ucp`, A2A Agent Cards, MCP tool bindings, ACP, AP2. Shopify merchants get much of this for free because the platform owns the catalog and wants to keep the merchant of record.

BEN should **not** invent a proprietary agent protocol. BEN should **not** try to be “UCP for everyone with a URL.” That race is lost on retail SKUs.

BEN *might* have a product if and only if it restricts the thesis:

1. **Agentization of non-store businesses** — manufacturers, importers, distributors, contractors — whose websites describe capabilities and channel policy, not a cart.
2. **Owner-verified commercial routing** — deterministic policy the model is forbidden to invent.
3. **Evidence-grounded claims** with provenance (`PUBLIC_OBSERVED` / `INFERRED` / `OWNER_VERIFIED` / `LIVE_CONNECTED`).
4. **Open publication** of a standard Agent Card + schema.org, plus a **BEN capability registry** so unknown agents can find nodes without already knowing the domain.
5. **Stop the transaction at RFQ + Quote** until a dense vertical actually produces economic value.

The slogan “businesses pay to become Agent-Ready; agents discover them freely” is **strategically attractive and empirically weak**. Businesses pay for **demand** (leads, RFQs, jobs won), not for a JSON file. Platforms that own demand (Google, ChatGPT, Shopify) can give the format away. BEN does not own that demand. Selling agentization without a path to qualified demand is selling SEO without owning search.

**GO / MODIFY / NO-GO:** **MODIFY.**

- **GO** would over-claim: URL→Agent-Ready is not a scalable, defensible, paid product as stated.
- **NO-GO** would ignore a real gap: B2B commercial routing is not what UCP/ACP solve.
- **MODIFY** means: kill the generic “any website” launch; run a 90-day dense-vertical experiment; keep formats open; charge for verification, routing, and demand handling — not for emitting a card.

This note does **not** unlock implementation. `decision_003` remains locked. Promotion to `queued/` requires an explicit human decision.

---

## 2. Is URL → Agent Ready technically credible?

**For a preview profile: yes. For a commercially exposed agent: no, not from the URL alone.**

Technically credible from a URL, using existing methods:

- Domain identity, language, NAP (name/address/phone) when present
- `Organization` / `LocalBusiness` JSON-LD
- Sitemap-derived page inventory
- Product/service *mentions* (not live catalog)
- Documented service areas, certifications, “about” claims
- Public PDFs (datasheets, catalogs) via BEN’s existing extract/FTS path
- Contact channels that are already published

Not technically credible from a URL, no matter how good the crawler:

- Whether the business wants this kind of demand
- Distributor vs direct vs decline rules
- Real price, inventory, lead time, MOQ
- Who is authorized to quote
- Whether the website is stale, a brochure, or a lie
- Legal identity (company number, VAT, licensed contractor)
- Channel conflict (routing around an exclusive distributor)

Gate M/P is the internal warning label. Even with a gold 18-page PDF and known labels, prefix retrieval missed late evidence; FTS missed a paraphrase (M09); computation (M33) is not retrieval. A marketing website is worse: JavaScript, PDFs, Hebrew/English mix, distributor locators, “contact us” as the only action.

**Scalable product claim** (“give BEN your URL and you are Agent-Ready”) is therefore **false** unless Agent-Ready is defined as “draft observed profile pending owner confirmation.” That definition can be a product. Calling the draft “published and discoverable to real demand” is negligent.

---

## 3. What BEN would create from a URL

A **draft Business Node**, not an agent.

Pipeline (research design only):

1. **Fetch pack** (SSRF-safe, robots.txt-respecting, size-capped): homepage, sitemap, `robots.txt`, `/.well-known/agent-card.json`, `/.well-known/ucp`, `llms.txt`, JSON-LD, Open Graph, a bounded set of linked pages (about, products, contact, dealers).
2. **Structured harvest** (deterministic first): schema.org, Open Graph, sitemaps, merchant/product feeds if present, existing Agent Cards / UCP profiles / OpenAPI.
3. **Document harvest**: PDFs and catalogs through the existing workspace-file pipeline (extract, chunks, evidence IR). Do not claim native provider PDF until Gate P2 is unblocked.
4. **Inferred layer** (LLM, labeled `INFERRED`): capability candidates, languages, likely roles (manufacturer vs contractor), likely service geography.
5. **Evidence graph**: every claim points at URL, selector/quote, fetch timestamp, content hash.
6. **Owner review packet**: diffable draft vs blanks the owner must fill.

Outputs of a URL job:

| Object | From URL? | Publishable? |
|---|---|---|
| `BusinessProfile` draft | Mostly observed | Only after owner confirm of identity |
| `Capability[]` candidates | Mixed observed/inferred | Only `OWNER_VERIFIED` |
| `ProductOrServiceRef[]` | Mentions only | As `PUBLIC_OBSERVED`, never as live catalog |
| `CommercialPolicy` | Almost never | Never from inference |
| `AgentManifest` | Only if site already has one | Mirror, do not overwrite |
| `Evidence[]` | Yes | Public subset after review |
| `InteractionEndpoint` | No | Owner-defined |

**Do not** auto-generate MCP tools that POST forms or “call the website.” Website-to-MCP scrapers (AnySiteMCP, wmcp.sh, AHTML extractors) turn human UI into hallucinated tools. That is a security and liability trap, not Agent-Ready.

---

## 4. What requires owner confirmation

Minimum before exposing the business to **real demand** (not before showing a private preview):

1. **Legal identity** — legal name, country, and that the claimant controls the domain (DNS TXT or well-known file with a BEN nonce).
2. **Human responsible party** — name, role, reachable channel (email/phone/WhatsApp) that actually answers.
3. **Capability allowlist** — explicit yes/no on each inferred capability. Unpublished = not a capability.
4. **Demand eligibility** — who they will talk to (trade only, geo, order size, licensed buyers).
5. **Routing policy** — at least: `direct | route_to_partner | human_review | decline` with conditions.
6. **Public vs private** — what may appear on the Agent Card vs authenticated extended card.
7. **Freshness SLA** — owner accepts that BEN will re-crawl and that stale `PUBLIC_OBSERVED` claims will be marked stale, not silently treated as true.
8. **Non-impersonation** — attestation that they are not publishing as another firm.

Not required for V1 publish (keep in CONNECTED later): ERP, live price, live stock, payment, exclusive catalog.

**Preview (FREE/TRIAL)** may show inferred capabilities privately to the owner. **Published Agent-Ready** must not.

---

## 5. Proposed Business Node (minimal)

Keep the model small. Roles are **contextual**, not account types.

```
BusinessNode
  identity: { legal_name, trading_name, country, domains[], languages[] }
  profile:  { summary, urls[], contacts_public[] }
  capabilities[]: { id, label, input_types, output_types, evidence_ids[], status }
  product_service_refs[]: { name, code?, evidence_ids[], status }   # references, not SKUs
  commercial_policy: { rules[] }   # owner-authored, deterministic
  relationships[]: { other_node_id, type, status }  # type is free-form + a small vocab
  agent_manifest: { a2a_card_url, schema_org_url, ben_surface_url }
  interaction_endpoints[]: { kind, url, auth, permitted_intents[] }
  evidence[]: { claim_id, source_url, quote, fetched_at, hash }
  provenance: { PUBLIC_OBSERVED | INFERRED | OWNER_VERIFIED | LIVE_CONNECTED }
```

**Relationship types** (vocab, not a global ontology): `distributes`, `supplies`, `installs`, `authorizes`, `refers`. The same node can be buyer on one interaction and seller on another.

**Claim rule (non-negotiable):**

- Never present inferred **price, inventory, availability, delivery promise, or commercial terms** as verified or live.
- UI and Agent Card must carry the provenance enum on every commercial field.
- `INFERRED` never leaves the owner preview.

This is smaller than an ERP, larger than a directory listing, and deliberately **not** a storefront.

---

## 6. Proposed Agent Surface

The Agent Surface is the **published projection** of a Business Node. It is not the Node, and it is not BEN chat.

### Public (no auth)

- A2A Agent Card (skills = `OWNER_VERIFIED` capabilities only)
- schema.org `Organization` / `LocalBusiness` JSON-LD
- Provenance-tagged capability list
- Public evidence URLs
- How to start an interaction (human form and/or authenticated A2A skill)
- Rate-limit and robots/llms policy

### Authenticated (agent or user identity)

- Extended Agent Card (private skills, partner-only capabilities)
- Submit `Intent` / `RFQ`
- Read quote status for *this* interaction
- Distributor routing *results* that the owner allowed this caller to see

### Permissioned by the business

- Named partner agents
- Volume/geo-qualified callers
- Human takeover channel

### Never exposed

- Inferred capabilities
- Internal cost, margin, true stock
- Other customers’ RFQs
- Owner private notes / Business Memory
- Raw crawl dumps, emails, WhatsApp threads
- Credentials, OAuth tokens
- Anything the commercial policy marks `decline` or `internal`

### Hosting

Publish **three mirrors of the same public card**, because discovery is not one mechanism:

| Location | Role |
|---|---|
| `https://{business-domain}/.well-known/agent-card.json` | Standards-native. Requires domain control (DNS or file). Also serve legacy `agent.json`. |
| `https://agent.ben.ai/b/{node_id}` | BEN-hosted surface if the business cannot or will not host files. Canonical fallback. |
| BEN Capability Registry API | How strangers search by skill/geo/evidence without knowing the domain. |

Do **not** make `agent.ben.ai` the only copy. That recreates a closed directory and fights the interoperability goal.

Rate limiting: public GETs are cheap and cacheable (`ETag`, `Cache-Control`). Mutation endpoints (RFQ) are identity-bound, quota’d per caller, and abuse-scored. Scraping the public card is expected and allowed; scraping private evidence or blasting RFQs is not.

---

## 7. Discovery architecture

The “Agent Wi-Fi” analogy is **partially wrong**, and the wrong part is the part that sounds like a product.

Wi-Fi: a radio **broadcasts** an SSID. Passers-by see networks they did not previously know.

The web: `/.well-known/agent-card.json` is **not a broadcast**. It is a well-known *path* on a domain you already have. A2A’s own spec says the client “knows or programmatically discovers the domain.” Programmatic discovery of unknown domains is **search / registry**, which A2A explicitly does **not** standardize.

So:

| Approach | What it solves | What it does not |
|---|---|---|
| **A. business `/.well-known/...`** | Interop when the URL is already known | Cold discovery |
| **B. BEN-hosted surface** | Businesses that cannot host files; stable URL | Still need an index |
| **C. BEN Capability Registry** | Skill/geo/query for strangers | Becomes a directory unless queries are capability-structured |
| **D. Search-indexable manifests** | Google/Bing/ChatGPT may ingest JSON-LD + cards | BEN does not control those indexes |
| **E. Combination** | Required | Operationally heavier |

**Recommended: E**, with honest layering:

1. **Identity discovery** = domain + well-known card (A2A) + schema.org.
2. **Capability discovery** = registry query (`capability`, `geo`, `provenance=OWNER_VERIFIED`, `languages`).
3. **Demand discovery** = BEN (and later other agents) holding **Intents** and matching them. This is where a network can start.

Without (2) or a major search engine doing (2) for you, external agents **cannot** “just encounter” a BEN-agentized business. Pretending otherwise is brochureware.

---

## 8. How an external non-BEN agent discovers a BEN-agentized business

Mandatory scenario C, spelled out.

Assume manufacturer `acme-poly.co.il` is Agent-Ready on BEN.

**Path 1 — the agent already has the domain** (most interoperable, least magical):

1. GET `https://acme-poly.co.il/.well-known/agent-card.json` (and `agent.json`).
2. Read public skills, auth schemes, A2A `url`.
3. If the business cannot host: the card’s `url` points at `https://agent.ben.ai/a2a/acme-poly` and the card is also at `https://agent.ben.ai/b/{id}/agent-card.json`.
4. Caller authenticates per the card (OAuth2/Bearer as declared). No BEN user account is required for **public read**. Submitting an RFQ may require *some* caller identity (DID, OAuth client, signed Agent Card), not a BEN subscription.

**Path 2 — the agent does not have the domain** (the actual product problem):

1. Caller queries a discovery surface that is not BEN-proprietary if possible: web search for schema.org + Agent Card; an A2A curated registry if one exists in-market; or `GET https://discover.ben.ai/v1/capabilities?q=polycarbonate+sheet&geo=IL&provenance=OWNER_VERIFIED`.
2. Registry returns **cards**, not leads: name, skill ids, evidence links, well-known URLs, auth.
3. Caller then follows Path 1.

**Path 3 — BEN is invisible to them:** if BEN only lists businesses inside a logged-in app, the external agent never finds them. That would falsify the open-surface thesis. Therefore the registry read API must be public, rate-limited, and return standard Agent Cards.

BEN may still **charge the business** for being listed, verified, and kept fresh. That is analogous to Google Business Profile (business pays/optimizes for presence; users search free) — with the important difference that Google already has query demand.

---

## 9. Standards / protocols — USE / ADAPT / LEARN / REJECT

Do not invent a BEN protocol where a standard is sufficient.

| Technology | Class | Maturity / adoption | BEN fit | Notes |
|---|---|---|---|---|
| **A2A Agent Cards** `/.well-known/agent-card.json` | **USE** | Linux Foundation; v1.0 (2026); well-known + registry + private config | Public surface + skills | Also serve legacy `agent.json`. Use extended authenticated cards for private skills. |
| **A2A JSON-RPC / HTTP** | **ADAPT** | Same; task lifecycle useful for RFQ | Interaction transport for agent-ready peers | Canonical objects stay BEN-internal; A2A is a projection. |
| **MCP** | **LEARN → selective USE** | High adoption as *tool* protocol | Connect BEN *to* ERP/CRM later; not the public business surface | MCP is agent-to-tool, not stranger-to-business. Do not wrap a brochure site as MCP tools. |
| **OpenAPI** | **USE** | Mature | Human/dev API for registry + RFQ | Complement, not replacement, for Agent Cards. |
| **schema.org + JSON-LD** | **USE** | Ubiquitous | Indexable identity, NAP, `makesOffer` only when verified | Harvest inbound; emit outbound. |
| **sitemap.xml / robots.txt** | **USE** | Mature | Crawl budget and politeness | Honor robots; do not treat sitemap as a catalog. |
| **llms.txt** | **ADAPT** | Convention, not a standard | Optional pointer to the Agent Card | Do not treat as capabilities. |
| **RFC 8615 well-known** | **USE** | Mature | Card, UCP, OAuth metadata, BEN domain-verify file | |
| **UCP `/.well-known/ucp`** | **LEARN** (retail) / **REJECT** as BEN core | Google+Shopify; 2026; expanding lodging/food | Emit later if a node is actually a store | Solves cart/checkout, not distributor routing. |
| **ACP (OpenAI/Stripe)** | **LEARN** | Apache-2.0; Instant Checkout deprioritized Mar 2026 toward feeds | Ignore for V1 transactions | Spec ≠ surface. OpenAI pulled native checkout flexibility. |
| **AP2 (Google payments)** | **LEARN** | Mandates as VCs; 60+ launch partners | Relevant only if BEN ever handles money | Out of V1 boundary. |
| **Google/Microsoft merchant feeds** | **ADAPT** | Production retail | Import if a business already has a feed | Not a substitute for B2B capabilities. |
| **Website-to-MCP (AnySiteMCP, wmcp, AHTML extractor)** | **REJECT** for publish | Early OSS | Useful as a *research crawler* at most | Hallucinated tools + form-POST = abuse. |
| **NLWeb (Microsoft)** | **LEARN** | Site `/ask` + MCP | Conversational Q&A over schema.org | Not commercial routing. |
| **Firecrawl / Crawl4AI** | **ADAPT** | Production crawl/extract; Firecrawl AGPL-3.0 | Fetch pack implementation candidate | License: AGPL may force self-host care or use hosted API. Prefer bounded custom fetch first. |
| **Semantic web / SPARQL global graph** | **REJECT** as product | Academically mature, product-weak | Provenance yes; global ontology no | |
| **OAuth 2.0 / OIDC** | **USE** | Mature | Caller auth, domain-linked identity, Clerk already in BEN | |
| **DNS TXT / well-known nonce** | **USE** | Mature (Google Search Console pattern) | Domain control = anti-impersonation | |
| **Business directories (Google Business, Duns, etc.)** | **ADAPT** | Mature | Cross-check identity; do not become one | |
| **Proprietary “BEN protocol”** | **REJECT** | — | Fights distribution | Format open; engine paid. |

---

## 10. Intent ↔ Capability architecture

Do not reduce this to keyword search. Also do not pretend a free-form LLM match is a network layer.

```
utterance
  → Intent object (typed, versioned, provenance)
  → Capability query (structured)
  → candidate Business Nodes (verified capabilities only)
  → Commercial Routing (deterministic)
  → Interaction (RFQ / question / human)
  → Result object
```

**Intent** (minimal):

- `need_type`: `supply | install | design | logistics | service | unknown`
- `item`: normalized product/service refs + raw text
- `quantity` + unit
- `geo` + timing
- `buyer_context`: `consumer | contractor | distributor | unknown` (contextual, not an account type)
- `constraints`: licensed, indoor/outdoor, standards
- `clarifications_needed[]`
- `evidence` of interpretation (which phrases mapped)

Matching is **capability-first**:

- Candidate must have an `OWNER_VERIFIED` capability that accepts the Intent’s input types.
- Evidence must exist for that capability (page, catalog PDF, owner statement).
- Score: capability overlap, geo policy, relationship graph (authorized distributor), freshness, historical fill rate — **not** embedding similarity alone.
- If clarification is cheaper than a false match, ask. False capability is worse than a missed match (see §23).

Example: *“I need a contractor to install a polycarbonate roof, including material, next week.”*

Required capabilities (possibly split across nodes): `install.polycarbonate_roof`, `supply.polycarbonate_sheet`, `schedule.window <= 7d`. One node may do both; often a contractor + a distributor. Routing may assemble a **chain**, not a single listing.

---

## 11. Commercial Routing architecture

**The model never authors policy.** It may *propose* a draft policy from website language (`INFERRED`, owner preview only). Execution is a deterministic evaluator.

```
rule:
  when: { capability, quantity_op, geo, buyer_class, time, flags }
  then: { action, target }
  action: DIRECT_SALES | ROUTE_PARTNER | FANOUT_RFQ | HUMAN | DECLINE
  target: node_id | group_id | owner_inbox
```

Evaluator properties:

- First matching rule wins, or explicit priority — pick one and test it.
- Default rule is `HUMAN` or `DECLINE`, never `DIRECT_SALES`.
- Every routing decision is an audit record: intent id, matched rule id, targets, timestamp.
- LLM may fill Intent slots; LLM may **not** override `then`.

This is the actual BEN product in the polycarbonate story. UCP/ACP have no equivalent of “small order → distributor, project → plant, odd spec → human.”

---

## 12. Manufacturer / distributor example

Customer needs 200 m² polycarbonate.

1. Intent: `supply.polycarbonate_sheet`, qty=200 m², geo=Israel, buyer=contractor.
2. Manufacturer node `Acme` has capability `supply.polycarbonate_sheet` OWNER_VERIFIED, policy:
   - `qty < 50 m²` → `ROUTE_PARTNER` nearest `authorizes` distributor with `OWNER_VERIFIED` or `BEN member`
   - `qty >= 50 m²` → `FANOUT_RFQ` to authorized distributors **and** `DIRECT_SALES` optional
   - `flag=special_spec` → `HUMAN`
   - else `DECLINE`
3. 200 m² matches project band → RFQ objects to distributor set + manufacturer sales inbox.
4. Website-discovered “dealers” that are not owner-verified are **not** routing targets. They may appear as `PUBLIC_OBSERVED` suggestions to the owner (“confirm these?”).
5. External non-member distributors can receive RFQ over email/WhatsApp **transport**; the RFQ object still lives in BEN.
6. Quotes return as `Quote` objects. User (contractor) compares. No payment.

Channel conflict is a **business-risk** feature, not a bug: the owner must be able to forbid BEN from showing plant-direct pricing to the public card. That is policy, not model judgment.

---

## 13. Canonical Interaction / RFQ / Quote

One object model, many transports.

```
Intent → MatchSet → RequestForQuote → Quote → UserApproval → OrderIntent → Confirmation
```

| Object | Who may create | Commitment? |
|---|---|---|
| `Intent` | Any identified user/agent | No |
| `MatchSet` | BEN matcher | No |
| `RFQ` | Buyer identity, after policy allows | Request only |
| `Quote` | Seller identity / authorized agent | **Commercial offer** — structured fields only |
| `OrderIntent` | Buyer, explicit approval | Intent to buy, still not payment |
| `Confirmation` | Seller | Acceptance of OrderIntent |

**Quote MUST contain:** issuer node, RFQ id, lines (qty, spec, unit, price or `price_on_request`), validity window, Inco/delivery terms or `unspecified`, provenance of each commercial field, human-readable PDF/text **derived from** the structure (not the reverse).

**AI may** draft messages, translate, extract a quote from an email into the object (with `INFERRED` until seller confirms).

**AI may not** be the sole record of a price or a yes.

Transports (`BEN↔BEN`, A2A, API, WhatsApp, email, web link) are **adapters**. Source of truth is the object store. This matches existing Action Cards (`wa.me` is already a transport, not a ledger).

---

## 14. Transaction boundary

| Stop | Regulation / ops | V1? |
|---|---|---|
| **A. Qualified lead** | Low. Spam/abuse still real. | Acceptable experiment, weak ROI proof |
| **B. RFQ + Quote** | Contract-ish but familiar B2B; no PCI | **Recommended V1 stop** |
| C. Order confirmation | Stronger liability, tax on “who sold” | Later |
| D. Merchant payment link | PCI on merchant; BEN is messenger | Optional later, not BEN-held funds |
| E. BEN-controlled payment | KYC/AML, PCI, refunds, chargebacks, tax, disputes | **Out.** Prove value first |

Preference in the brief is correct. OpenAI/Stripe’s Instant Checkout pullback (2026-03) is a caution: even incumbents found in-chat checkout less flexible than sending the buyer to merchant checkout. BEN has no PSP advantage.

Israel/EU construction RFQs also collide with licensed-contractor rules, invoicing law, and (for payments) payment-service regulation. None of that is needed to test whether routing works.

---

## 15. Open vs closed network

| | Closed BEN network | Open surface + paid agentization |
|---|---|---|
| Growth | Slow, invite-only density can work in one vertical | Faster distribution **if** cards are actually fetched |
| Demand | BEN must recruit buyers | Stranger agents *might* bring demand — unproven |
| Defensibility | Data lock-in | Format is copyable; engine might not be |
| Monetization | Take-rate or subscription inside the walls | Subscription for verification/routing; no tax on discovery |
| Spam | Easier to police | Public RFQ endpoints attract abuse |
| Security | Smaller blast radius | Impersonation + scraping + prompt injection via sites |
| Platform risk | BEN is the platform | Google/OpenAI may index and bypass BEN |
| Copying | Competitors rebuild both engine and graph | Competitors reuse cards; must rebuild policy/memory |

**Hypothesis “format open, engine paid” is sound only if the engine produces something the format cannot already get from Shopify/Google.** For stores, it does not. For policy-heavy B2B, it might.

Closed-only would contradict “maximum interoperability” and make Scenario C impossible. Open-only without a registry makes Scenario C impossible in the other direction (no broadcast).

**Design:** open cards + public capability search + paid verification/routing/demand tools. Accept that Google can copy the card. Do not accept that Google will author the manufacturer’s distributor policy.

---

## 16. Subscription / business model analysis

Proposed tiers mapped to reality:

| Tier | Honest job | Risk |
|---|---|---|
| FREE/TRIAL preview | Show the owner how wrong the website is | If preview is good enough, they screenshot and leave |
| AGENT READY | Verified card, registry listing, recrawl, evidence | They will not pay if no RFQs arrive |
| BUSINESS AGENT PRO | Routing, RFQ desk, WhatsApp/email, analytics, memory | This is the first tier with plausible ROI |
| CONNECTED | ERP/POS live facts | High integration cost; true differentiation |

Compare to alternatives:

- **Lead fees / commissions:** businesses understand them; they also hate them; they create spam incentives. Worse fit for “open discovery.”
- **Closed marketplace take-rate:** needs GMV. BEN has none.
- **Give away cards, charge ads:** Google’s model. BEN lacks the query box.

**Do not assume the proposed model is correct.** The historically working analog is **Google Business Profile + a vertical CRM** (Houzz, BuildingConnected, Thomasnet): presence may be cheap/free; software that **wins work** is paid.

If AGENT READY (publish only) does not produce measurable RFQs in 90 days, the paid SKU is PRO, and FREE preview is a sales tool — not a network.

---

## 17. Why would a business pay?

The pitch “your website is for humans; BEN makes you usable by AI agents” is **category-correct and ROI-vague**.

What a manufacturer might actually pay for, in order of plausibility:

1. **Qualified RFQs that match policy** (not consumer tire-kickers)
2. **Not breaking the distributor channel**
3. **A desk that answers in Hebrew/English after hours** without inventing prices
4. **Analytics:** which intents, which geographies, which declines
5. **Freshness:** catalog PDF changed, card updated
6. **External-agent visibility** — only if those agents exist and send work
7. **“Verified representation”** as a status symbol — weak, like unused ISO badges

Measurable ROI: **won jobs / hours saved on junk inquiries / reduced channel conflict incidents.** If the 90-day test cannot move those, there is no subscription.

Willingness-to-pay threat: Wix/Shopify/Google will check the “AI-ready” box for stores. Construction SMEs already pay for Ucan, Salesforce, WhatsApp. BEN must sit on the **RFQ path**, not next to the website builder.

---

## 18. First 1,000 businesses — density, not logos

1,000 random Israeli/global websites ≈ a bad scrape of the Yellow Pages. No edges.

Construction-materials ecosystem (aligned with existing BEN copilot + the polycarbonate example):

| Role | Count (indicative) | Why |
|---|---|---|
| Sheet/profile manufacturers | 8–15 | Policy-rich, few nodes, high leverage |
| Importers | 10–20 | Bridge standards/stock |
| Authorized distributors | 80–150 | Geo coverage |
| Trade stores / yards | 100–200 | Last-mile supply |
| Roofing / opening contractors | 400–600 | Demand **and** supply |
| Fabricators / CNC | 50–80 | Special process |
| Logistics / crane / access | 30–50 | Completes install intents |
| Designers / inspectors | 20–40 | Clarification and compliance |

This is a **graph**: manufacturer→distributor→contractor→homeowner. Random restaurants plus random lawyers plus one polycarbonate plant is not.

Recruitment order: **manufacturers first** (policy), then **their real distributors** (owner-verified relationships), then contractors who already buy from those distributors. Do not open signup to “any URL.”

Geography: one country (IL) or one metro, bilingual HE/EN, because the copilot and the example already live there.

---

## 19. Network effect — and where it dies

Claimed flywheel:

more businesses → more capabilities → more intents solved → more users → more demand → more business value → more paid Agent-Ready nodes.

Break points:

1. **Cold start:** no verified capabilities → intents fail → users leave. Directory decay.
2. **Demand ownership:** if intents happen in ChatGPT/Google, businesses ask why they pay BEN.
3. **False capabilities:** one bad install match destroys contractor trust.
4. **Unresponsive nodes:** Agent-Ready but nobody answers RFQs = worse than being absent.
5. **Open scrape:** a rival copies cards and undercuts on take-rate.
6. **Channel conflict:** manufacturer kills the listing after one bypassed distributor.
7. **Model cost:** recrawl + extraction of 1,000 JS sites is a cost center until PRO revenue exists.

When is it a network rather than a directory?

| Primitive | Directory | Network |
|---|---|---|
| NODE | listing | business/user/agent with verified caps |
| EDGE | “related links” | Intent→Capability match, RFQ, quote, `authorizes`, repeat fill |
| INTENT | search string | durable object |
| CAPABILITY | tags | verified, evidenced, policy-bound |
| INTERACTION | click-to-call | structured RFQ/quote over any transport |
| TRANSACTION | out of band | OrderIntent (later) |

**Threshold (hypothesis, not fact):** a network exists when a new Intent can be filled using **edges created by previous interactions**, not only website text — e.g. “this distributor actually quoted 200 m² in 14 hours, twice.” That is Business Memory, and it is BEN-specific.

1,000 Agent-Ready websites with zero RFQs are a directory. 80 nodes with 200 filled RFQs are a small network.

---

## 20. Is this a new network layer?

**Mostly no; locally maybe.**

The open agent web layer is being built by A2A + UCP + ACP + schema.org + search engines. BEN would be a **participant and a vertical operator**, not the inventor of the layer.

What could be a BEN-shaped layer: **policy-aware B2B capability routing with evidence**, sitting *on top of* open cards. That is closer to a **clearinghouse** than to TCP/IP.

Calling it “the agent web” oversells. Calling it “a verified B2B capability network for one trade” is testable.

---

## 21. Defensibility

If a competitor fetches every public Agent Surface, they get names, skills, endpoints. That is **not** a moat.

| Asset | Compounds? | Moat? |
|---|---|---|
| Open card format | No (public good) | No |
| Agentization quality | Some, until Google harvests sites itself | Weak |
| Capability normalization in one vertical | Yes, with expert + data | Maybe |
| Owner verification + domain control | Yes, operational | Table stakes |
| Freshness | Yes, costly | Weak alone |
| **Commercial routing policies** | Yes, private | **Strongest unique asset** |
| Business Memory (who actually quotes) | Yes | Strong if density exists |
| ERP integrations | Yes, high switching | Classic SaaS lock-in, slow |
| Transaction history | Yes | Strong; empty at t=0 |
| Network density in one trade | Yes | Classic; lost if random 1,000 |
| Reliability / evidence | Yes | Trust brand, copyable process |
| Distribution (WhatsApp desk, copilot) | Yes if used daily | Workflow lock-in |

Do not call “we understood the website” a moat. Firecrawl + GPT understands websites. Call **owner-policy + fill-rate graph + not routing around distributors** a compounding asset *if* the 90-day test creates those edges.

---

## 22. Security / trust

Threats that can kill the thesis (see also §23):

- **Impersonation:** publish a card as a competitor. Mitigation: domain verification, signed cards (JWS), legal identity check. A2A is already moving this way.
- **Malicious websites / prompt injection:** crawl content in the model context. Mitigation: treat site text as untrusted evidence; never as instructions; capability allowlist is owner-gated.
- **Spam RFQs:** public endpoints. Mitigation: caller auth, quotas, proof-of-work/reputation, owner policy `buyer_class`.
- **Privacy leakage:** inferred employees, unpublished prices in PDFs. Mitigation: publish allowlist; PDF evidence redaction in owner review.
- **Stale data presented as live:** provenance enum + recrawl timestamps on the card.
- **Agent scraping:** allowed on public cards; ToS + rate limits on registry search; do not put secrets on the public surface.

Trust levels for a node (align with emerging A2A identity levels, do not invent a new stack):

1. Self-asserted URL paste — **preview only**
2. Domain-verified — may publish public card
3. Organization-verified (registrar/VAT/manual) — may receive live demand
4. Live-connected systems — may assert price/stock

---

## 23. Adversarial failure review

| Failure | Sev | Likely | Mitigation | Threatens thesis? |
|---|---|---|---|---|
| Stale website data | High | High | Provenance + recrawl + owner SLA | Yes, if published as truth |
| Wrong extraction | High | High | Owner allowlist; evidence quotes | Yes |
| Hallucinated capability | Critical | High without gates | Never publish `INFERRED` | **Yes — core** |
| Duplicates | Med | High | Domain as primary key; VAT merge | No |
| Fake / impersonated businesses | Critical | Med | Domain + org verify | **Yes** |
| Unresponsive businesses | High | High | SLA, demote from registry | Yes (trust) |
| Spam | High | High if open RFQ | Auth + quota + policy | Yes |
| Prompt injection via site | High | Med | Untrusted evidence sandbox | Yes |
| Malicious sites (malware, SSRF) | High | Med | Existing SSRF-safe fetch, allowlist schemes | Ops |
| Privacy leakage | High | Med | Publish allowlist | Legal |
| Bad distributor routing | Critical | Med | Deterministic policy; unverified dealers excluded | **Yes — unique value** |
| Supplier bypass / channel conflict | High | Med | Owner rules; private prices | Commercial death |
| Attribution fights | Med | High | Object ids, audit log | Med |
| External-agent abuse | High | Unknown | Auth, rate limit | Open-surface thesis |
| Model cost | Med | High | Deterministic harvest first; LLM for inference only | Unit economics |
| Cold start | Critical | High | Density-first recruitment | **Yes** |
| No willingness to pay | Critical | **High** | Charge PRO for RFQ desk, not for JSON | **Yes — core** |
| Incumbents give cards away | High | **Already happening in retail** | Avoid retail SKUs | Kills generic thesis |

---

## 24. 90-day experiment

**Hypothesis:** In one dense construction-materials cluster, owner-verified capabilities + routing produce qualified RFQs **without** payment infrastructure and **without** a new protocol.

Scope:

- 50–100 **invited** businesses (not open URL signup): ~10 supply-side principals, ~30 distributors/stores, ~40–60 contractors.
- Agentize with URL draft + **mandatory owner session** (30–60 min) to confirm capabilities and write 3–10 routing rules.
- Publish A2A cards (BEN-hosted if needed) + schema.org.
- Create 30–50 **real** buyer intents (internal team + friendly contractors), not synthetic trivia.
- Transports: BEN UI + email/WhatsApp. No A2A strangers required in week 1; run **one** scripted external-agent discovery test (Path 1 + Path 2) for Scenario C.
- No payments. Stop at Quote.

Explicit thresholds (fail closed):

| Metric | Fail if | Pass if |
|---|---|---|
| Owner confirm rate | < 40% of recruited | ≥ 70% |
| False capability rate (published) | ≥ 10% | < 3% |
| Intent coverage (can match ≥1 verified node) | < 40% | ≥ 70% |
| Capability precision (human judge) | < 70% | ≥ 90% |
| Business response to RFQ ≤ 2 business days | < 30% | ≥ 60% |
| Quote rate on responded RFQs | < 30% | ≥ 50% |
| Time to useful response (median) | > 5 days | ≤ 1 day |
| Repeat buyer intent (week 8–12) | 0 | ≥ 5 unique |
| External agent can fetch card w/o BEN login | No | Yes |
| Any live price published from inference | Any | Zero |

Kill criteria: channel-conflict incident caused by BEN; or owners churn because RFQs are junk.

This experiment does **not** require lifting `decision_003`. Matching and routing are deterministic services with human-visible RFQ objects — not autonomous agent loops.

---

## 25. End-to-end walkthroughs (target design, not implemented)

### A. Homeowner — “install a polycarbonate roof next week”

1. User (any BEN identity; not a “buyer account”) states need.
2. BEN emits Intent: install + supply, geo from profile/ask, timing ≤7d, buyer_context=consumer.
3. Clarification if needed: existing structure, city, who supplies material.
4. Capability query: `install.polycarbonate_roof` and optionally `supply.polycarbonate_sheet`.
5. Candidates: contractors with verified install capability in geo, whose policy accepts consumers and that schedule.
6. Routing: contractor policy may `FANOUT` to their preferred distributor for materials, or quote installed-complete.
7. RFQ to 1–3 contractors (owner cap). Not a public blast.
8. Quotes as objects; homeowner approves one OrderIntent **later**; V1 stops at quote.
9. If no contractor policy accepts consumers, return `DECLINE` with reason `trade_only` — that is success of routing, not a product failure.

### B. Same human, now contractor — “200 m² polycarbonate”

1. Same identity; `buyer_context=contractor` on this Intent.
2. Capability `supply.polycarbonate_sheet`, qty=200.
3. Manufacturer policy routes to authorized distributors + optional plant.
4. RFQs travel WhatsApp/email to non-BEN distributors; objects stay in BEN.
5. Contractor compares Quotes. They may later be the **seller** on a homeowner Intent. No account switch.

### C. External company agent — “polycarbonate supplier” (no BEN subscription)

1. Agent searches public registry or web index, **or** is given `acme-poly.co.il`.
2. GET well-known Agent Card (business domain or `agent.ben.ai` fallback).
3. Reads skill `supply.polycarbonate_sheet`, auth, A2A endpoint.
4. Sends A2A task or HTTP RFQ with its own caller credentials.
5. BEN enforces Acme’s policy (qty, geo, trade). May `DECLINE` or route to distributors.
6. Response is a structured Quote or a decline — not a chatty hallucination.
7. **No BEN seat required** for that caller. Acme paid BEN to host/verify/route.

If step 1 has no public index and the agent does not know the domain, discovery **fails**. That is why the registry read API is mandatory for the thesis. Shipping only an in-app directory falsifies Scenario C.

---

## 26. Biggest technical risk

**Publishing inferred capabilities as if they were true.**

Everything else (crawling, A2A, WhatsApp) is engineering. False skills create real-world dispatch errors (wrong contractor on a roof, unauthorized distributor, invented MOQ). Gate M/P already showed retrieval/paraphrase failure on a clean PDF. Websites are worse.

Mitigation is product, not model: owner allowlist, provenance on every field, default deny.

---

## 27. Biggest business risk

**Nobody pays for Agent-Ready presence, and BEN does not own demand.**

Retail agentization is being bundled into Shopify/Google for free. B2B owners pay for jobs. If the experiment cannot produce RFQs that owners value, the subscription is a manifesto.

Secondary: channel conflict with distributors — politically fatal in the exact vertical that is otherwise the best fit.

---

## 28. What competitors / standards already solve vs what BEN must uniquely build

**Already solved (do not rebuild):**

- Agent self-description and domain discovery: A2A Agent Cards
- Tool access to systems BEN owns: MCP
- Retail catalog + checkout: UCP, ACP, merchant feeds
- Indexable identity: schema.org, Google Business Profile
- Crawl/extract: Firecrawl-class tools, JSON-LD, sitemaps
- User identity: Clerk / OAuth
- Document ingest for PDFs the business uploads: BEN workspace files + FTS

**BEN must uniquely build (if this product exists):**

1. **Vertical capability schema** for trade (not generic “skills: shopping”)
2. **Owner verification + commercial policy editor + deterministic router**
3. **Distributor relationship graph** with verification states
4. **Intent / RFQ / Quote objects** with transport adapters
5. **Evidence/provenance** on every public claim (extends Gate evidence IR, does not replace it)
6. **Public capability registry** that emits standard cards
7. **Abuse/quota** on open RFQ
8. **Dense recruitment playbook** — this is operations, not code, and it is the actual network

**Must not uniquely build:** a BEN-only agent protocol; in-chat payments; website-to-MCP auto tools; buyer vs seller account types; a global ontology of all commerce.

---

## 29. GO / MODIFY / NO-GO

**MODIFY.**

| Axis | As proposed (“any URL, pay to be Agent-Ready, open agent web”) | Modified |
|---|---|---|
| Technical | Over-claimed | URL → draft only; publish after owner+policy |
| Commercial | Weak vs incumbents | Charge for RFQ desk + routing in one trade |
| Defensible | Open format is not a moat | Policy graph + fill history |
| Scalable | Random 1,000 fails | Density-first 50–100 experiment |

**Immediate non-actions (this gate):**

- Do not implement provider PDF, A2A servers, crawlers, registries, or MCP wrappers.
- Do not change production, FTS, retrieval, or flags.
- Do not lift `decision_003`.
- Do not add a queued implementation task from this document alone.

**If a human later promotes this:** start at the 90-day experiment in §24, not at “the agent web.”

STOP.

No implementation.
No production changes.
