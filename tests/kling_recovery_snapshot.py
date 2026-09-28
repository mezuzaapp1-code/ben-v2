"""Proof-only encrypted recovery evidence, never another operational authority.

The existing protected FAL_KEY derives an encryption key; credentials are never
serialized. Restoring requires that same secret and an empty disposable test DB.
"""
import base64
import json
import os
from pathlib import Path
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

MAGIC = b"BEN-KLING-RECOVERY-1\n"


def encryption_key(credential, salt):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=MAGIC).derive(credential.encode())


def seal(path, payload, credential):
    data = json.dumps(payload, default=str, allow_nan=False).encode()
    assert credential and credential.encode() not in data, "Credential must never enter snapshot"
    salt, nonce = os.urandom(16), os.urandom(12)
    encrypted = MAGIC + salt + nonce + AESGCM(encryption_key(credential, salt)).encrypt(nonce, data, MAGIC)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        handle.write(encrypted)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def unseal(path, credential):
    raw = path.read_bytes()
    assert raw.startswith(MAGIC)
    payload = raw[len(MAGIC):]
    salt, nonce, encrypted = payload[:16], payload[16:28], payload[28:]
    return json.loads(AESGCM(encryption_key(credential, salt)).decrypt(nonce, encrypted, MAGIC))


async def capture(admin, org, store_root, path, credential, *, diagnostics, receipt=None):
    assert (await admin.fetchval("SELECT current_database()")).startswith("media_v1_test_")
    rows = await admin.fetch("SELECT * FROM ben.media_executions WHERE org_id=$1 ORDER BY created_at", org)
    threads = await admin.fetch("SELECT id,org_id FROM ben.threads WHERE org_id=$1", org)
    files = {}
    root = store_root.resolve()
    for file in root.rglob("*"):
        if file.is_file() and file.name in ("output.png", "output.mp4", "result.json"):
            assert not file.is_symlink() and file.resolve().is_relative_to(root)
            files[file.relative_to(root).as_posix()] = base64.b64encode(file.read_bytes()).decode()
    payload = {"schema_version": "ben-kling-proof-recovery-v1", "source_commit": os.getenv("GITHUB_SHA"),
               "org_id": str(org), "rows": [dict(row) for row in rows],
               "threads": [dict(row) for row in threads], "files": files,
               "acceptance_receipt": receipt, "diagnostics": diagnostics,
               "generation_requests_allowed_on_resume": 0}
    seal(path, payload, credential)


async def restore(admin, store_root, path, credential):
    """Restore the original IDs/rows into an EMPTY disposable proof DB, never submit.

    Acceptance receipt closes the gap between HTTP acceptance and DB reference
    commit. Only a validated known operation can become resumable submitted work.
    """
    from services.media.contracts import KLING_VIDEO_MODEL
    from services.media.fal_kling_video import operation_url
    payload = unseal(path, credential)
    assert payload["schema_version"] == "ben-kling-proof-recovery-v1"
    assert payload["generation_requests_allowed_on_resume"] == 0
    assert (await admin.fetchval("SELECT current_database()")).startswith("media_v1_test_")
    assert await admin.fetchval("SELECT count(*) FROM ben.media_executions") == 0
    assert await admin.fetchval("SELECT count(*) FROM ben.threads") == 0
    org = uuid.UUID(payload["org_id"])
    root = store_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for relative, encoded in payload["files"].items():
        file = (root / relative).resolve()
        assert file.is_relative_to(root) and file.name in ("output.png", "output.mp4", "result.json")
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("xb") as handle:
            handle.write(base64.b64decode(encoded, validate=True))
    columns = [r["column_name"] for r in await admin.fetch("""SELECT column_name FROM information_schema.columns
        WHERE table_schema='ben' AND table_name='media_executions' ORDER BY ordinal_position""")]
    async with admin.transaction():
        for thread in payload["threads"]:
            assert uuid.UUID(thread["org_id"]) == org
            await admin.execute("INSERT INTO ben.threads(id,org_id) VALUES($1,$2)", uuid.UUID(thread["id"]), org)
        for row in payload["rows"]:
            assert set(row) == set(columns) and uuid.UUID(row["org_id"]) == org
            # JSONB columns must remain JSON values, not JSON-encoded strings.
            for key in ("request_payload", "provider_output", "usage_dimensions"):
                if isinstance(row[key], str):
                    row[key] = json.loads(row[key])
            if row["model"] == KLING_VIDEO_MODEL:
                receipt = payload.get("acceptance_receipt")
                if row["state"] in ("pending", "submitting", "submission_unknown") and receipt:
                    assert receipt["execution_id"] == row["execution_id"] and row["submit_attempts"] == 1
                    poll = operation_url(receipt["request_id"], receipt["status_url"])
                    assert receipt["response_url"] == poll.removesuffix("/status")
                    row.update(state="submitted", provider_operation_ref=receipt["request_id"],
                        provider_state="Pending", provider_output={"schema_version": "media-fal-reconcile-v1", "polling_url": poll})
                # Reject ambiguous/pending work: the recovery runner can NEVER submit.
                assert row["state"] in ("submitted", "running", "ingesting", "succeeded", "failed", "expired")
                assert row["provider_operation_ref"] and row["submit_attempts"] == 1
            else:
                assert row["state"] == "succeeded", "Only completed synthetic source rows may restore"
            row.update(lease_owner=None, lease_expires_at=None)
            await admin.execute("INSERT INTO ben.media_executions SELECT * FROM jsonb_populate_record(NULL::ben.media_executions, $1::jsonb)", json.dumps(row))
    return payload
