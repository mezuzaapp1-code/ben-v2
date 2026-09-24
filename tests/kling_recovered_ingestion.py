"""Offline byte recovery only. Never fabricate the lost authoritative execution row."""
import base64
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from auth.beta_gate import derive_beta_org_id
from services.media.video_storage import ingest_mp4, video_path
from tests.kling_recovery_snapshot import seal, unseal

EXECUTION = "9a8a062c-9d30-41cb-bd3e-8f791f4360fd"
RESOURCE = "5c2afd98-d618-41ed-882e-9927f893f842"
REQUEST = "01a0d376-4474-7373-a6ce-bc183b21b07f"


def ingest_existing(source, destination, credential):
    reference = unseal(source / "provider-result.enc", credential)
    assert reference["original_ben_execution_id"] == EXECUTION
    assert reference["provider_request_id"] == REQUEST
    recovered = unseal(source / "provider-video.enc", credential)
    data = base64.b64decode(recovered["bytes_base64"], validate=True)
    assert hashlib.sha256(data).hexdigest() == recovered["properties"]["checksum"]
    org, resource = derive_beta_org_id("kling-proof"), uuid.UUID(RESOURCE)
    start = time.monotonic()
    stored = ingest_mp4(data, org_id=org, resource_id=resource, duration_seconds=3, aspect_ratio="16:9")
    elapsed = round((time.monotonic() - start)*1000, 2)
    _, path = video_path(org, resource)
    assert path.read_bytes() == data and stored.checksum == recovered["properties"]["checksum"]
    assert ingest_mp4(data, org_id=org, resource_id=resource, duration_seconds=3) == stored
    assert not stored.audio_present
    destination.mkdir(parents=True, exist_ok=True)
    # Preserve verified immutable bytes encrypted after the disposable runner ends.
    seal(destination / "ben-ingested-video.enc", {"execution_id": EXECUTION,
         "reserved_resource_id": RESOURCE, "org_id": str(org), "stored": stored.__dict__,
         "bytes_base64": base64.b64encode(path.read_bytes()).decode(),
         "authoritative_row_available": False, "published": False}, credential)
    report = {"generation_requests": 0, "provider_requests": 0, "ingestion": "PASS",
        "execution_id": EXECUTION, "reserved_resource_id": RESOURCE, "checksum": stored.checksum,
        "byte_size": stored.byte_size, "width": stored.width, "height": stored.height,
        "duration_seconds": stored.duration_seconds, "audio_present": stored.audio_present,
        "validation_ingestion_ms": elapsed, "checksum_reload": "PASS", "immutable_reingestion": "PASS",
        "published": False, "original_lifecycle_restored": False, "training_status": "not_approved",
        "blocker": "Original authoritative database row and conversation/source identities were not retained."}
    serialized = json.dumps(report, indent=2)
    assert credential not in serialized and REQUEST not in serialized
    (destination / "ingestion.json").write_text(serialized)
    return report


if __name__ == "__main__":
    assert os.getenv("GITHUB_ACTIONS") == "true"
    assert os.getenv("GITHUB_REF") == "refs/heads/astra/ben-media-v1"
    ingest_existing(Path("recovered-input"), Path("recovered-ingestion"), os.environ["FAL_KEY"])
