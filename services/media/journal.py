"""Private bounded result journal closes the ingestion/restart gap, not a job queue.

Only media_executions determines state. A missing journal after uncertain submit
never permits resubmission. Keep original provider bytes and private references.
"""
import base64
import json
import os
import tempfile
from pathlib import Path

from services.media.contracts import ImageResult, MAX_RESPONSE_BYTES
from services.media.image_storage import image_path
from services.workspace_files.storage import _fsync_file_and_dir


def journal_path(row):
    return image_path(row["org_id"], row["resource_id"])[1].with_name("result.json")


def save_result(row, result):
    path = journal_path(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"execution_id": str(row["execution_id"]),
                         "data": base64.b64encode(result.data).decode(),
                         "mime_type": result.mime_type, "returned_model": result.returned_model,
                         "operation_ref": result.operation_ref, "usage": result.usage,
                         "duration_ms": result.duration_ms}, separators=(",", ":")).encode()
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("media journal too large")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".result-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            _fsync_file_and_dir(handle, path.parent)
        # A second writer may only converge on byte-for-byte identical evidence.
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError("media journal conflict")
        with path.open("r+b") as handle:
            _fsync_file_and_dir(handle, path.parent)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def load_result(row):
    path = journal_path(row)
    if not path.exists():
        return None
    with path.open("rb") as handle:
        raw = handle.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("media journal too large")
    data = json.loads(raw)
    if data.pop("execution_id") != str(row["execution_id"]) or data["returned_model"] != row["model"]:
        raise ValueError("media journal identity mismatch")
    data["data"] = base64.b64decode(data["data"], validate=True)
    return ImageResult(**data)
