# Gate 0 remediation — Windows environment

This report supersedes the checkpoint results in FILE_SCOPE_V2_GATE0_RESULTS.md.
Accepted starting commit: `a72a02da3b8d4880ae3c3b4d23eda920f17abcc1`.

## 1. Decision

**BLOCKED / STOP.** Gate 1 has not started. Ownership policy is now resolved;
real PostgreSQL execution and the production frontend build remain blocked by
the current Windows process environment. No security control was disabled.

## 2. Commit

The new commit SHA is supplied in the task response. All changes are tests,
test infrastructure, and documentation. No production or dependency files changed.

## 3. PostgreSQL setup and result

Downloaded PostgreSQL **16.15-4 Windows x64**, using the link on the
[official EDB binaries page](https://www.enterprisedb.com/download-postgresql-binaries),
as directed by the [PostgreSQL Windows page](https://www.postgresql.org/download/windows/).
Archive URL: `https://get.enterprisedb.com/postgresql/postgresql-16.15-4-windows-x64-binaries.zip`.
Locally computed SHA-256 (not an independently verified vendor signature):
`f5f55b03bd54ce0dd1c51d524b54c7e015abd4d620af27d6971288a2dbe4a8f8`.
Only bin/lib/share were extracted into this task's `work/postgresql16/pgsql`.
No Windows service, global installation, production database, or SQLite substitute.

The helper now accepts `BEN_TEST_PG_BIN` and `BEN_TEST_PG_ROOT`, resolves migrations
relative to the repository, uses Windows executable suffixes/locale, and guards
cleanup after partial startup. It still uses random credentials, owned cluster
identity verification, SCRAM authentication, loopback only, and non-default ports.

`initdb` completed, but `pg_ctl start` failed twice:

```text
pg_ctl: could not create restricted token: error code 87
pg_ctl: could not start server: error code 3
```

Both attempts confirmed cleanup. The committed runner
`python -m tests.run_file_scope_gate0_postgres` failed at startup before migration
or tests. **Zero live PostgreSQL tests completed; no database pass is claimed.**
The runner prepares owned file-scope and Media databases, applies the existing
migration chain, sets test URLs before test imports, rejects unexpected skips,
and tears down its cluster. Its successful migration/test path remains unverified.

Intended exact runner test set:

```text
tests/test_file_initial_read_jobs_postgres.py
tests/test_document_processing_runner.py
tests/test_media_repository.py
tests/test_file_scope_v2_gate0.py
tests/test_workspace_files_v1.py
tests/test_project_auth.py
tests/test_project_library.py
tests/test_security_gate_a.py
```

To resume in a Windows environment where native child processes and pipes work,
use a Python environment containing repository requirements, then from repo root:

```powershell
$env:BEN_TEST_PG_BIN = '<task scratch>\postgresql16\pgsql\bin'
$env:BEN_TEST_PG_ROOT = '<existing writable scratch directory>'
python -m tests.run_file_scope_gate0_postgres
```

## 4. Frontend build

`node node_modules/vite/bin/vite.js build` still fails while loading config because
esbuild cannot spawn its piped service. This is reproducible outside repository
tooling: `spawnSync(process.execPath, ['--version'], {stdio:'pipe'})` returns
`EPERM`, whereas `stdio:'inherit'` exits 0. Both Node **20.12.0** and **24.19.0**
show that distinction. Running the installed esbuild executable directly returns
**0.21.5**. Its ACL includes ReadAndExecute for CodexSandboxUsers; no Zone.Identifier
was found. A granted network permission did not change the failure.

Confirmed failure layer: Windows child-process pipe permissions, not a
Vite-specific application/config failure. The exact enforcing mechanism
(sandbox token/pipe ACL versus endpoint security) is not proven. There is no
evidence justifying dependency reinstall, path rewrites, or antivirus changes.
Executable installation works in direct execution; production build is **NOT proven**.
No alternate execution tool was used to evade the restriction.

## 5. Legacy ownership resolution

**Sufficient for a bounded File Scope V2 contract.** Ambiguous legacy resources
are denied for NEW protected-resource capabilities; ownership migration/backfill
is not a prerequisite. Ordinary legacy Chat stays intact where possible. It may
not become a fallback route to protected source representations. No ownership
inference from uploader, source_chat_id, opener, tenant membership, or historical
behavior. Organization-user personal resources remain private. No departments,
hierarchy, libraries, Rooms, approvals, or enterprise RBAC. Runtime enforcement
is deferred to an authorized implementation gate.

## 6. Large Paste

**B — pre-existing deterministic failure.** The static assertion in
`test_large_paste_does_not_touch_workspace_file_modules` bans every workspace-files
import in message_format.py, which already imports `sanitize_response_evidence`.
The import predates Gate 0 (file history includes commit `44ef277`), and both the
assertion and runtime file are unchanged from the accepted checkpoint. It failed
in the previous checkpoint run, again alone (1 failed), and again in this expanded
run. It is neither flaky nor environment-dependent. It remains an ordinary FAIL;
no assertion removal, skip, xfail, or runtime workaround was added.

## 7. Exact test results and counts

Expanded backend command: Python 3.11.9, `-m pytest -q -p no:cacheprovider`, unique
scratch `--basetemp`, and these files:

```text
tests/test_file_scope_v2_gate0.py
tests/test_workspace_files_v1.py
tests/test_workspace_files_chat_context.py
tests/test_current_turn_vision.py
tests/test_chat_provider_routing.py
tests/test_gate_c_adhoc_expert_evidence.py
tests/test_provider_adapters.py
tests/test_inference_accounting_pass1.py
tests/test_large_paste_v1.py
tests/test_project_auth.py
tests/test_project_library.py
tests/test_security_gate_a.py
```

Result: **200 PASS / 4 FAIL / 4 XFAIL**, 21 coroutine warnings. Two failures were
Project test reads using Windows cp1255 for UTF-8 source. Explicit UTF-8 in the
tests fixed them; rerun of `test_project_library.py test_project_auth.py`:
**19 PASS**. Remaining failures: Large Paste above and
`test_health_and_ready_remain_public` (health returns 503 with unavailable DB).
No health assertion or production readiness behavior was changed.

New `test_disposable_postgres_safety.py`: **4 PASS**, covering partial startup
cleanup, failed-stop data retention, rejection of unrelated deletion targets,
and distinguishing expected security xfails from unexecuted/skipped tests.

Latest outcome per distinct backend test: **206 PASS / 2 FAIL / 4 XFAIL** across
212 tests. This is a deduplicated result after targeted reruns, not a single
all-green run. No live-DB tests are included in those totals.

Eight frontend regression scripts all PASS: `test-project-library`,
`test-large-paste`, `test-clean-chat-isolation`, `test-chat-stream-ownership`,
`test-draft-upload-source-state`, `test-grok-provider-menu`,
`test-deepseek-provider-menu`, `test-astra-gpt-menu` (each `node scripts/<name>.mjs`).
These eight script results are reported separately from pytest counts.

## 8. Files changed

* docs/FILE_SCOPE_V2_GATE0.md
* docs/FILE_SCOPE_V2_GATE0_REMEDIATION.md
* tests/disposable_postgres.py
* tests/run_file_scope_gate0_postgres.py
* tests/test_disposable_postgres_safety.py
* tests/test_project_library.py

## 9–13. Regressions and security carry-forward

Project mocked/auth/frontend coverage is green after the encoding fix; real
database behavior is not yet verified. Provider regression coverage passes.
Runtime diff against the accepted checkpoint is empty.

Four strict XFAIL security RED fixtures remain unmodified and visible:

* `test_unresolved_private_project_is_not_authorized_by_tenant_alone`
* `test_deleted_initial_read_is_not_replayed_as_ordinary_history`
* `test_anchored_opinion_authorizes_before_sqlite_read`
* `test_initial_read_authorizes_destination_before_history`

Provider layer untouched: **YES**. Project runtime behavior unchanged: **YES**.
Generated Media security unchanged: **YES**, but real Media database regression
execution is still blocked. The red fixtures are implementation carry-forward,
not intended Gate-0 fixes or evidence of security passing.

## 14–16. Estimate, next gate, recommendation

Conditional full-project envelope remains **40–64 focused engineering hours**,
excluding environment recovery. Ownership no longer requires a speculative
legacy backfill project. No reliable environment recovery duration is known.

Proposed Gate 1 (NOT authorized): resolve all four RED fixtures by adding the
minimum authoritative private-resource/destination checks upstream of protected
access; block unresolved legacy resources for new protected capabilities; check
anchored Opinion before history access, Initial Read destinations/publication,
and source-bound Initial Read history before new execution. Prove authorized
positive behavior and denied side-effect-free behavior. Keep ordinary legacy
Chat intact where possible. If durable new ownership needs schema work, present
that separately before migration. No standalone upload wiring, provider changes,
Media migration, recursive lineage, or enterprise policy.

Recommendation: **STOP at Gate 0** until the host permits normal PostgreSQL
startup and Node pipe creation. Then run the committed database launcher and
production build. Neither environment limitation has been silently waived.
