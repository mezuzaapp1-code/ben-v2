# Media V1 migration boundary

Migration `033_media_executions` adds one table. It changes no existing file,
document-job, chat, inference, or execution-event ownership semantics. There is
no media route, provider adapter, or deployment in this migration commit.

The execution row owns one preallocated generated resource ID. Provider operation
references, provider output locators, request payloads, and storage keys are
internal: future API responses must use an explicit allowlist, never serialize
this row wholesale. Usage dimensions and nullable cost metadata are not token
accounting. No secrets belong in request_payload or provider_output.

Organization RLS is enabled and forced for reads and writes. As with BEN's
existing policies, the application must bind an authenticated organization and
must run under a non-superuser/non-BYPASSRLS database role. Workspace and
conversation selectors are not authority; runtime authorization is required
before creating an execution, reading a result, and publishing it. No destination
FK is added because conversations also exist in SQLite and a deleted destination
must not erase an in-flight paid operation. Workspace IDs retain BEN's current
project/workspace equivalence. A projectless conversation is supported.

The table provides organization-scoped request uniqueness, globally unique output
identity, due-reconciliation indexes, paired lease fields, version/counters,
deadline, and required successful-output metadata. Service code must still enforce
legal state transitions, request-fingerprint conflicts, fencing, deadlines,
authorization, and idempotent publication. Schema checks cannot prove bytes exist
or that a destination was authorized. They must not be treated as that proof.

Deleting a resource should revoke delivery using deleted_at while retaining
operational history. Publication rechecks destination existence and authorization;
the future runtime must not attach to a deleted destination. No automatic provider
cancellation or regeneration is implied by deletion, expiry, or ingestion failure.

## Local validation

`python -m pytest tests/test_media_migration.py -q -p no:cacheprovider`

The integration cases require `MEDIA_TEST_DATABASE_URL` pointing to a fresh empty
loopback PostgreSQL database named `media_v1_test_*`. They never use DATABASE_URL.
The test connection needs permission to create a transaction-scoped test role.
Tests roll back their table, role, and data changes. Missing configuration produces
explicit skips, not a claimed database pass.

Validated on disposable PostgreSQL 16.15 on Windows:

- All six migration tests passed, including non-superuser tenant read/write
  isolation, missing tenant context, owner-transfer denial, idempotency/resource
  uniqueness, output-state checks, index catalog, downgrade and re-upgrade.
- Full Alembic upgrade from an empty database through 033 passed.
- Alembic downgrade to 032 and re-upgrade to head passed. Existing table inventory
  was unchanged across that round trip.
- Provider/registry/chat and measurement checks: 86 passed, one existing test
  failed because it hardcodes `/workspace`. That unchanged assertion also passed
  when invoked with ROOT set to the actual Windows checkout. No test was edited
  to remove or weaken the assertion.

Downgrade removes the new table and its data. It has been exercised only in the
disposable test database. Production application or rollback requires separate
authorization.

## Pre-flight stop

BytePlus credentials were absent from both the local environment and the existing
Railway backend secret store under BytePlus/ModelArk/ARK/Seedream/Seedance-related
names. Only presence booleans were printed; no credential values were retained.
Google key and durable storage configuration were present. Configuration presence
does not prove deployed-byte durability or browser delivery.

Phase 1 live verification cannot proceed until a usable BytePlus ModelArk
credential is available through an approved backend/local secret mechanism.
Provider model/API details, access, output transport and recovery guarantees must
still be verified before implementing the adapter. No provider calls, production
migration, secret changes, or deployment were performed.
