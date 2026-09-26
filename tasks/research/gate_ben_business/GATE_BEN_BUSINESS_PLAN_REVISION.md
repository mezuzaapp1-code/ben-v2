# PLAN REVISION — Procurement-first, private suppliers, MD path

**Mode:** RESEARCH ONLY. No implementation, migrations, production changes, or deploys.  
**Date:** 2026-09-13  
**Supersedes next-gate recommendation in** `GATE_BEN_BUSINESS_CHAT_NETWORK.md`  
**`decision_003`:** remains LOCKED

Previous next gate **B1 (domain-verified Business + accountable human for inbound network RFQs) is withdrawn.**

Replacement next gate: **P1 — Business draft + Private Supplier Directory + Procurement conversation.**

---

## 1. Procurement is a conversation capability

Do not build a procurement ERP.

Conversation kinds on an existing Thread:

| Mode | User is acting as | Intent direction | Counterpart |
|---|---|---|---|
| `STANDARD` | person / operator | none/unspecified | BEN only |
| `SALES` | supplying business | inbound demand | customer / BEN user / external agent |
| `PROCUREMENT` | buying business | outbound demand | private suppliers and/or BEN-discovered capabilities |

The utterance *“Request quotes from 5 suppliers for 20 tons of 12mm steel, delivered to Netanya by Wednesday.”* is a **Procurement** conversation. BEN extracts Intent, selects counterparts from the **private directory** (not a marketplace), opens RFQs, collects replies, normalizes Quotes, returns comparison **in the same thread**.

The user does not configure agents, A2A, MCP, or prompts. Mode can be chosen explicitly (“Procurement”) or inferred with confirmation. Inferring **which five suppliers** without a directory or a user pick requires business authority — ask; do not invent.

`STANDARD` / `SALES` / `PROCUREMENT` are **conversation capabilities**, not account types and not separate applications.

---

## 2. Existing suppliers are first-class

A BEN Business must run Procurement **before** BEN has network density. Therefore a **Private Supplier Directory** owned by that business is V1, not a later nice-to-have.

Sources: CSV, Excel, manual entry, uploaded files (Workspace Files already extract `.csv` / `.xlsx`). ERP later.

This is **not** a marketplace catalog. Another business cannot search it. Trust: `PRIVATE_ASSERTED` (the owner says these are their suppliers). Website-discovered names stay out.

### Must these objects enter the V1 model?

| Object | V1? | Why |
|---|---|---|
| **PrivateSupplier** | **YES** | First-class directory row. Name, optional VAT, notes, status |
| **SupplierContact** | **YES** (fields or child row) | email, phone, WhatsApp, language. Needed to send RFQ |
| **SupplierRelationship** | **NO as a graph table** | Membership in the private directory *is* the V1 relationship. `authorized_by` / `distributes` wait until partner routing |
| **BusinessContact** (seller-side inbox owner) | **DEFER** | Needed for Sales/network inbound, not for Procurement-first |
| **ProjectMember VENDOR** | **REUSE as ingest hint only** | Has name/email/phone but is project labor/invoice matching, has `hourly_rate`, is not a business-owned directory. **Do not make it SoR** |

---

## 3. Two trust domains (never merge)

| Domain | Meaning | May receive RFQ in V1 | May be advertised as MD |
|---|---|---|---|
| `PRIVATE_RELATIONSHIP` | Owner-uploaded / owner-entered supplier | Yes, with owner permission | No |
| `BEN_VERIFIED_CAPABILITY` | Domain-verified business + owner-verified capability | Not in first loop | Later |

Future: *“3 of my suppliers and 2 additional.”* = union query with **separate provenance on each Match**. Never collapse a private row into a BEN capability because names match.

`Rfq.target_kind`: `PRIVATE_SUPPLIER` | `BEN_CAPABILITY`. Required.

---

## 4. Network acquisition loop — cold start changes

Mechanism is valid **if** invitations follow a real RFQ, the supplier opts in, and the buyer did not enable “spam my vendors to join BEN.”

This **does** change cold start:

- Previous plan: recruit verified **sellers**, then find demand.
- Revised: a **buying** business gets value immediately from its own list; external suppliers **feel BEN as a useful RFQ** before joining; joining is optional and post-interaction.

Do not design invite-as-growth. Invite template, if any, is attached to an RFQ the supplier already received, rate-limited, and off by default.

MVP-B is the acquisition engine. MVP-A is the later density test.

---

## 5. Business Connector

Product name: **Connect my business**. Customer inputs: name, channels, files, supplier/customer lists, later URL.

BEN infers: draft profile, candidate capabilities, suggested conversation modes.  
BEN must **not** infer: commercial policy, published capabilities, live prices, “these five are your steel suppliers” without a list or confirmation.

Complexity stays in BEN. Connector is a **wizard over P1 objects**, not a new agent runtime. URL/MD parts of the connector **defer** (former B7).

---

## 6. Machine Discoverability (MD)

MD = machines can understand, discover, match, and eventually interact. Not SEO rank.

Do **not** implement MD now. Preserve the path in schema:

`url` + `verification_level` on Business  
`provenance` on Capability (`PUBLIC_OBSERVED` / `INFERRED` / `OWNER_VERIFIED` / `LIVE_CONNECTED`)  
`publishable` default false  
`md_status` = `none | draft | publishable` (none in V1)

PrivateSupplier has **no** MD fields. Private lists are not machine-discoverable.

---

## 7. Sales and procurement are one graph

Same identity. Roles are per interaction.

Shared: `Intent`, `Rfq`, `Quote`, `ActionEvent`, later `Capability` as the *public/verified* side of a business.

Separated: conversation mode; `Intent.direction` (`BUY` | `SELL`); RFQ `target_kind`; who is accountable (buyer user vs selling human).

Do not create buyer accounts and seller accounts.

---

## 8. Token economy stays separate

`InferenceCallRecord` / usage packages remain the **only** AI usage ledger.

RFQ, Quote, Match, lead, TIME TO ACTION never live there. A procurement turn may *emit* an inference row for extraction cost; that does not make the RFQ a token object.

---

## 9. True MVP re-evaluation

| | MVP-A (previous) | MVP-B (procurement-first) |
|---|---|---|
| Loop | User → verified BEN Business → human quote | Business → private suppliers → comparison in same chat |
| Time to first value | Slow (need verified counterparties) | **Fast** (owner already has 5 numbers) |
| New infra | Domain verify, capabilities, matcher, seller inbox | Directory + mode + RFQ/Quote + outbound notify + paste-back normalize |
| Network density | **Required** | **None** |
| Security | Impersonation on a public network | Outbound to known contacts; abuse = blasting *their* list (owner-gated, confirm N, rate limit) |
| Channels | Seller inbox | `wa.me` / mailto already; **inbound capture via paste in V1** |
| Willingness to pay | Speculative (leads) | **Familiar** (RFQ desk / comparison) |
| Complexity | Medium-high | Medium (collection is the hard part; paste-back keeps it small) |
| Later network | Weak acquisition | **Stronger**: suppliers experience demand first |

**Choose MVP-B as the true MVP.** Keep MVP-A as the *next* commercial-loop after a buyer exists and at least some suppliers join or BEN capabilities exist.

First useful complete path:

```
Procurement chat
→ Intent (qty, spec, place, date, N suppliers)
→ owner confirms 5 PrivateSuppliers
→ RFQ objects
→ WhatsApp/email deep links
→ owner pastes replies into the same thread
→ Quote rows
→ comparison card in chat
```

WhatsApp Business API and inbound email are **not** required for the first loop.

---

## 10. Next gate answers (A–F)

### A. Does B1 remain the correct NEXT GATE?

**No.**

B1 as defined (ownership + domain verification + accountable human so a **selling** business can receive **network** demand) optimizes a network that does not exist and delays the loop that does not need density.

### B. Why the Procurement-first MVP does not keep B1

Procurement-first needs a **directory owner**, not a **domain-verified public node**. The Clerk org already identifies the person. Domain proof is a Sales/MD publish control. An accountable *seller* human is required when BEN routes **inbound** demand; in MVP-B the accountable human is the **buyer user** already in the thread, and supplier humans are **private contacts**.

### C. Replacement NEXT GATE

**P1 — Business draft + Private Supplier Directory + Procurement conversation**

PURPOSE: A BEN organization can own a draft Business, upload/enter suppliers, and start a Procurement thread that can later emit RFQs.

HYPOTHESIS: Existing Clerk org + Project/workspace + CSV/XLSX files + VENDOR-shaped contacts are enough to stand up a **private** directory without domain verification.

USER VALUE: “My suppliers are in BEN; I can talk Procurement.” First value is **organization of counterparts**, not yet a sent RFQ (sending can be P2 in the same spine if P1 is kept small — see split).

**Recommended split so P1 stays SMALL:**

- **P1 (NEXT):** draft `Business` (unverified), `PrivateSupplier` + contact fields, ingest from CSV/XLSX/manual, Thread mode `PROCUREMENT` (and `STANDARD`; `SALES` stub). No domain verify. No BEN matcher. No MD publish.
- **P2:** Intent overlay in Procurement mode + RFQ to N private contacts via `wa.me`/mailto + paste-back Quote normalize + comparison in thread.

Do **not** start P2 automatically. P1 is the only next gate.

PASS P1: one invited business uploads ≥5 suppliers with at least one contact channel; can open a Procurement conversation bound to that Business.  
MUST NOT START: domain verification, public capabilities, Agent Cards, FTS changes, auth/RLS redesign, HF models, WhatsApp Business API.

SECURITY IN P1: directory is org-scoped; not listable outside the org; ingest confirms mapping; no invite-to-BEN; no inferred “these rows are suppliers” without owner accept.

### D. Minimum schema that preserves the path

Land now (P1–P2):

```
Business
  org_id
  name
  url nullable
  verification_level: unverified | domain_verified | org_verified
  md_status: none | draft | publishable   # V1: none
  publishable: false

PrivateSupplier
  business_id
  name
  external_ref nullable
  trust: PRIVATE_ASSERTED
  email, phone, whatsapp nullable
  notes
  status: active | archived
  # NOT searchable as MD

Thread  (EXTEND, do not replace)
  conversation_mode: standard | sales | procurement
  business_id nullable

Intent
  thread_id, source_message_id
  direction: buy | sell
  raw_text
  slots (qty, unit, spec, location, required_by, n_counterparts)
  budget only if user supplied

Rfq
  intent_id
  target_kind: PRIVATE_SUPPLIER | BEN_CAPABILITY
  target_private_supplier_id nullable
  target_capability_id nullable
  status

Quote
  rfq_id
  structured lines
  raw_response_text
  actor (human paste | later channel)

ActionEvent
  TIME TO ACTION timestamps
```

Reserve without building the engine:

```
BusinessCapability
  business_id
  provenance: PUBLIC_OBSERVED | INFERRED | OWNER_VERIFIED | LIVE_CONNECTED
  publishable default false
  evidence_ref nullable

# RoutingPolicy — DEFER table until Sales inbound exists
# Match.reason includes target_kind — P2+
```

Do **not** add: marketplace Supplier, buyer/seller user types, token rows on RFQ, Agent Card tables, Offer, distributor graph.

### E. Existing BEN components for the first Procurement loop (least new infra)

| Need | Existing | Gap |
|---|---|---|
| Who owns the list | Clerk org + `Project` workspace | Thin `Business` row so directory is not trapped on one project |
| Upload CSV/Excel | `WorkspaceFile` extract `.csv`/`.xlsx` | Mapping UI + owner accept → `PrivateSupplier` |
| Contact fields | `ProjectMember` name/email/phone | Copy *shape*, new table |
| Conversation | `Thread` / `Message` / chat | `conversation_mode` |
| Understand request | `model_gateway` structured output | Procurement Intent schema |
| Notify supplier | `WhatsAppLink` / `wa.me`, Action Cards | Generate RFQ text; no inbound API |
| Normalize reply | `analyze_supplier_tender` / quotation tools (project memory) | **Learn, do not reuse as SoR** — they write project memory and ledgers, not Quote objects |
| Usage cost | `InferenceCallRecord` | Keep separate |
| Hebrew/English | `chat_language` | Reuse |

Least-new-path: **do not** send RFQs through copilot quotation state machines. **Do** reuse file ingest + WhatsApp deep links + chat thread. **Do** add the small objects in D.

### F. Explicitly DEFERRED

- Domain / ownership verification (old B1)
- Public / verified capabilities as live match targets (old B2/B4)
- Sales inbox for inbound BEN demand (MVP-A)
- URL crawl / Connect-from-URL (old B7)
- MD publication, A2A cards, registry
- `ROUTE_PARTNER` / FANOUT to BEN network mixed with private in one click (until both domains exist)
- SupplierRelationship graph
- WhatsApp Business API, inbound email gateway
- Offers, payments, autonomy, embeddings, HF models
- Invite-to-BEN growth campaigns
- Mixing usage tokens with RFQ objects
- Changing FTS, auth/RLS, production flags
- Lifting `decision_003`

---

STOP.

No implementation. No migrations. No production changes. No deployment. P2 does not start automatically.
