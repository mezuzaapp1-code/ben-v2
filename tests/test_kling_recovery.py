"""No-network read-only investigation, diagnostic and encrypted recovery tests."""
import base64
import json
import uuid

from cryptography.exceptions import InvalidTag
import httpx
import pytest

from tests.kling_existing_recovery import investigate, ROOT
from tests.kling_recovery_snapshot import seal, unseal, capture, restore
from tests.test_kling_live_proof import OneGenerationTransport
from tests.test_kling_media import OP, POLL, RESULT, FILE, response, completed, output, video, KEY
from tests.test_kling_repository import context, ORG, USER, service_for, admit, due
from tests.test_media_repository import repository
from tests.test_media_migration import migration_sql
from sqlalchemy import text


def test_ciphertext_wrong_key_tamper_and_no_cleartext(tmp_path):
    path = tmp_path / "recovery.enc"
    payload = {"execution_id": str(uuid.uuid4()), "provider_operation_ref": OP, "state": "submitted"}
    seal(path, payload, KEY)
    assert unseal(path, KEY) == payload
    assert KEY.encode() not in path.read_bytes() and OP.encode() not in path.read_bytes()
    with pytest.raises(InvalidTag):
        unseal(path, "wrong-secret")
    raw = bytearray(path.read_bytes()); raw[-1] ^= 1; path.write_bytes(raw)
    with pytest.raises(InvalidTag):
        unseal(path, KEY)
    with pytest.raises(AssertionError):
        seal(path, {"secret": KEY}, KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 202, 301, 307, 401, 403, 404, 500])
async def test_every_poll_status_and_shape_recorded_without_secrets(status):
    metrics = {}
    transport = OneGenerationTransport(metrics, lambda: httpx.MockTransport(lambda _:
        response({"status": "IN_PROGRESS", "request_id": OP, "detail": KEY, "unexpected": KEY}, status)))
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        result = await client.get(POLL)
        assert result.status_code == status and result.json()["status"] == "IN_PROGRESS"
    observed = metrics["http_observations"][0]
    assert observed["http_status"] == status and observed["state"] == "IN_PROGRESS"
    assert observed["endpoint"].endswith("/requests/{request_id}/status")
    serialized = json.dumps(metrics)
    assert OP not in serialized and KEY not in serialized and "unexpected" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_existing_operation_reads_only_and_handles_unavailable_output(tmp_path, unavailable):
    reference = str(uuid.uuid4())
    calls = []
    def handler(req):
        calls.append(req)
        assert req.method == "GET"
        if str(req.url).endswith("/status"):
            return response(completed())
        if req.url.host == "queue.fal.run":
            return response({"detail": "Output not available"}, 410) if unavailable else response(output())
        assert str(req.url) == FILE and "authorization" not in req.headers
        return httpx.Response(200, content=video())
    report = await investigate(reference, KEY, tmp_path, transport=httpx.MockTransport(handler))
    assert report["generation_requests"] == 0 and all(c.method == "GET" for c in calls)
    assert report["result_recovered"] == (not unavailable)
    if unavailable:
        assert len(calls) == 2 and report["requests"][-1]["http_status"] == 410
    else:
        recovered = unseal(tmp_path / "provider-video.enc", KEY)
        assert base64.b64decode(recovered["bytes_base64"]) == video()
        assert report["actual_output"]["width"] == 1280 and len(calls) == 3
    assert reference not in (tmp_path / "investigation.json").read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_gap", [False, True])
async def test_original_rows_bytes_and_operation_survive_teardown_without_post(context, monkeypatch, tmp_path, receipt_gap):
    repo, admin, request = context
    initial = await admit(service_for(repo, lambda _: pytest.fail("no submit in recovery test")), request)
    claimed = await repo.claim(ORG, "before-crash")
    submitted = await repo.mark_submitting(claimed)
    receipt = {"execution_id": str(initial["execution_id"]), "request_id": OP,
               "status_url": POLL, "response_url": RESULT}
    if not receipt_gap:
        await repo.change(submitted, state="submitted", provider_operation_ref=OP,
                          provider_state="Pending", provider_output={"polling_url": POLL})
    archive = tmp_path / "recovery.enc"
    await capture(admin, ORG, tmp_path, archive, KEY, diagnostics={"http_status": 202}, receipt=receipt)
    # Reproduce destructive test-fixture teardown, then rebuild only the empty
    # original schema. Restore comes solely from encrypted evidence, not memory.
    async with repo.transaction(ORG) as session:
        role = await session.scalar(text("SELECT current_user"))
    assert role.startswith("media_runtime_") and role.replace("_", "").isalnum()
    await admin.execute("DROP SCHEMA ben CASCADE")
    await admin.execute("CREATE SCHEMA ben")
    await admin.execute("CREATE TABLE ben.threads (id uuid PRIMARY KEY, org_id uuid NOT NULL)")
    await admin.execute(migration_sql())
    await admin.execute(f"GRANT USAGE ON SCHEMA ben TO {role}")
    await admin.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA ben TO {role}")
    new_root = tmp_path / "new-runner"
    restored = await restore(admin, new_root, archive, KEY)
    assert restored["diagnostics"]["http_status"] == 202
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(new_root))
    calls = []
    def handler(req):
        calls.append(req.method)
        assert req.method == "GET", "New submission forbidden after restore"
        if str(req.url) == POLL:
            return response(completed())
        if str(req.url) == RESULT:
            return response(output())
        return httpx.Response(200, content=video())
    await due(admin)
    service = service_for(repo, handler)
    await service.tick(ORG)
    row = await repo.read(ORG, USER, execution=initial["execution_id"])
    assert row["state"] == "succeeded" and row["resource_id"] == initial["resource_id"]
    assert row["provider_operation_ref"] == OP and row["submit_attempts"] == 1
    assert await service.resource_bytes(ORG, USER, row["resource_id"]) == video()
    assert (await admit(service, request))["execution_id"] == initial["execution_id"]
    assert calls == ["GET", "GET", "GET"]
    with pytest.raises(AssertionError):
        await restore(admin, tmp_path / "overwrite", archive, KEY)


@pytest.mark.asyncio
async def test_snapshot_pending_without_acceptance_cannot_resume(context, tmp_path):
    repo, admin, request = context
    await admit(service_for(repo, lambda _: pytest.fail("no submit")), request)
    archive = tmp_path / "pending.enc"
    await capture(admin, ORG, tmp_path, archive, KEY, diagnostics={})
    await admin.execute("DELETE FROM ben.media_executions")
    await admin.execute("DELETE FROM ben.threads")
    with pytest.raises(AssertionError):
        await restore(admin, tmp_path / "empty-runner", archive, KEY)
    assert await admin.fetchval("SELECT count(*) FROM ben.media_executions") == 0


def test_recovered_bytes_use_existing_ingestion_without_fabricating_lifecycle(tmp_path, monkeypatch):
    from tests.kling_recovered_ingestion import ingest_existing, EXECUTION, REQUEST as REAL_REQUEST
    source = tmp_path / "input"
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path / "store"))
    import hashlib
    data = video()
    seal(source / "provider-result.enc", {"original_ben_execution_id": EXECUTION,
         "provider_request_id": REAL_REQUEST}, KEY)
    seal(source / "provider-video.enc", {"bytes_base64": base64.b64encode(data).decode(),
         "properties": {"checksum": hashlib.sha256(data).hexdigest()}}, KEY)
    report = ingest_existing(source, tmp_path / "output", KEY)
    assert report["ingestion"] == "PASS" and report["provider_requests"] == 0
    assert not report["published"] and not report["original_lifecycle_restored"]
