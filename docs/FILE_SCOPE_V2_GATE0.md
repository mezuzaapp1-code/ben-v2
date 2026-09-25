# File Scope V2 — Gate 0 contract

Status: specification and baseline fixtures only; NOT runtime enforcement.
Baseline: `3a413a2f003aadf823438402d057f111e0919308`.

## Scope and ownership

BEN remains a modular monolith. Resource Access is the security boundary;
Context Eligibility is a small evaluator. No provider changes or schema changes
are authorized by this gate.

A user's personal resources remain private even when the authenticated tenant
is an organization. Tenant equality is necessary, never sufficient. Neither
organization role nor department membership grants access. No organization
hierarchy, departments, public/shared libraries, approvals, enterprise sharing,
Rooms, RBAC, or policy platform will be built here. Library is a future extension only.

New Chat ownership must come from authenticated creation and a durable,
authoritative binding to the principal. A private Project likewise requires
authoritative access, not merely a matching tenant. File ownership binds to its
container. `uploaded_by` and `source_chat_id` remain provenance.

Legacy records with no authoritative individual owner/access binding are
UNRESOLVED and denied for NEW protected-resource capabilities. Do not infer
ownership from uploaded_by, source_chat_id, opener, tenant membership, or
historical behavior. No automatic attribution/backfill is required for V2.
Existing ordinary legacy Chat remains intact where possible; this compatibility
does not grant permission to reuse direct-source artifacts in new executions.
This bounded exclusion resolves the Gate-0 ownership policy blocker. It is a
contract, not runtime enforcement or permission to mutate legacy records here.

Preserve legitimate Project-file IDs, bytes, processing, and authorized use.
Do not freeze tenant-wide access as a desired security regression contract.

## Resource Access contract

Inputs: server-authenticated principal; server-validated destination/execution
scope; resource ID; requested action. Resolve authoritative container/resource
state server-side. Client ownership claims are untrusted.

Actions include list/discover, read/download, use in context, create, delete, and
background publication. Permission for one action does not imply another.

Result: allow/deny (or unavailable), safe reason, canonical identities, action,
execution binding, and current access/lifecycle snapshot. This is a bounded
internal decision, not a persistent or replayable credential. Do not expose
storage keys in a capability-facing descriptor.

Mandatory denial: wrong tenant, wrong private owner, wrong Chat/Project,
unresolved legacy ownership, missing/deleted resource, or unsupported authority.
A denial never falls back to legacy tenant-only file access. Endpoints may use
non-enumerating 404 responses; internal reason codes need not disclose metadata.

An internal policy decision function is the extension point for future explicit
grants. No grants or approval workflows are implemented now. Future usage policy
can narrow security permission, never override denial.

## Context Eligibility contract

Inputs: candidate identity/category, direct source identities, provenance
completeness, lifecycle/readiness, execution identity, and fresh Resource Access
decisions. Output: eligible/ineligible plus reason. No I/O, retrieval, content
loading, history assembly, summarization, persistence, model execution, or
Council/Add Opinion orchestration belongs in the evaluator.

Original bytes, extracted text, pages/chunks, retrieval evidence, stored excerpts,
Initial Read, and direct-source caches require current source permission.
Ordinary answers generated during authorized use remain conversation records
governed by conversation permission. No recursive source-set propagation or
universal retraction is required. A protected source artifact must not be
relabeled as an ordinary answer to bypass a denial. Unknown source identity for
a source-bearing artifact fails closed.

Retrieval filters authorized candidates before search/ranking. Selected content
is validated at execution admission. History and Vision are consumers of the
same decisions, not alternative policy authorities.

## Direct-source inventory and provenance

* WorkspaceFile: bytes via storage_key; extracted_text; identity/checksum.
* WorkspaceFilePage / WorkspaceFileChunk: parsed text and searchable evidence.
* WorkspaceFileEvidenceIR: structured extracted evidence.
* Retrieval packs and persisted response_evidence excerpts: protected source
  representations; citation IDs alone are not authorization.
* Initial Read: assistant message with source_event/source_file_id, persisted in
  PostgreSQL and SQLite. It remains source-bound despite being a message.
* Thread.source_state: pending/active/recent IDs are selection state, not grants.
* Direct-source caches/previews/in-flight packs: must not authorize future reuse.
  A complete persistent-cache inventory is not yet verified.
* Ordinary generated answers: conversation records; protect conversation access.

Preserve stable source ID, actual version if known (otherwise immutable content
identity/checksum where appropriate), server used_at, execution/message
association, originating container, and content category. Do not invent source
publication dates or versions. Existing used_files is incomplete direct
provenance, not proof of authorization or complete lineage.

## Lifecycle and jobs

DELETE denies resource use; REVOKE denies the affected principal/action/scope.
SUPERSEDE and STALE do not independently revoke access. Future information-use
policy can add requirements; it is not implemented in this gate.

Validate at enqueue, protected input acquisition, external submission admission,
and protected publication/conversation write. Jobs carry identifiers and defined
authority, not saved ALLOW results. Revocation committed before admission must
deny. Already-transmitted content cannot be recalled; later publication/use must
still be checked. Async completions must not publish after a relevant denial.

## Frozen boundaries and rollback

Preserve authorized Project lifecycle/retrieval, text-only Chat, existing vision
contracts, authorized Opinion/Council, stream protocol, routing/model identity,
accounting, retry/fallback, and Large Paste. Generated Media retains its stronger
created_by/pilot checks; future integration must AND resource security with
Media restrictions. Do not migrate Media here.

Gate 0 changes only docs/tests/fixtures. Baseline commit is the source rollback
anchor. Later mixed-version DB/worker rollback must be tested on a disposable
PostgreSQL instance; it is NOT proven by unit tests. Never roll back to a known
authorization bypass as a release strategy.

## Gate 1 proposal (not authorized here)

After Gate 0 is unblocked: enforce authoritative thread access before anchored
opinion history reads/writes and validate upload/Initial Read destinations,
including publication revalidation. Prove authorized positive behavior and
denied side-effect-free behavior. No schema migration, standalone upload wiring,
or provider changes in that gate. If missing ownership representation makes
this scope impossible, return for a scope decision rather than falling back to
tenant equality.

## Evidence discipline

The JSON scenarios are acceptance fixtures for a future implementation, not an
implemented authorization engine. Strict xfails in the Gate-0 test module expose
known current gaps. They are release blockers, not security passes. Their xfail
markers must be removed when fixes land. Mocked DB tests do not prove RLS/FKs.
