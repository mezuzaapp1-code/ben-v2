"""Internal narration admission and worker; existing resource IDs only."""
import asyncio
import hashlib
import uuid

from fastapi import HTTPException
from sqlalchemy import text

from services.media.access import pilot_principals, media_unavailable
from services.media.local_composer import MAX_AUDIO_BYTES, PROFILE, compose, preflight, wav_duration
from services.media.metadata import request_fingerprint
from services.workspace_files.storage import files_root, sanitize_filename


async def audio_bytes(repo, org, user, workspace, file_id):
    async def record():
        async with repo.transaction(org) as session:
            row = (await session.execute(text("""SELECT f.id,f.storage_key,f.checksum,f.byte_size
                FROM ben.workspace_files f JOIN ben.projects p ON p.id=f.workspace_id
                WHERE f.org_id=:org AND p.org_id=:org AND f.workspace_id=:workspace
                AND f.id=:id AND f.uploaded_by=:user AND f.media_type='audio/wav'
                AND f.status IN ('uploaded','ready')"""),
                {"org": org, "workspace": workspace, "id": file_id, "user": user})).mappings().first()
            if not row:
                raise HTTPException(404, "Authorized WAV asset required")
            return dict(row)
    row = await record()
    root = files_root().resolve()
    parts = row["storage_key"].split("/")
    if (len(parts) != 4 or parts[:3] != [str(org), str(workspace), str(file_id)]
            or sanitize_filename(parts[-1]) != parts[-1] or not parts[-1].lower().endswith(".wav")):
        raise HTTPException(503, "Audio unavailable")
    path = root.joinpath(*parts)
    if path.resolve() != path or not path.is_relative_to(root):
        raise HTTPException(503, "Audio unavailable")
    def read():
        with path.open("rb") as handle:
            return handle.read(MAX_AUDIO_BYTES + 1)
    try:
        data = await asyncio.to_thread(read)
    except OSError:
        raise HTTPException(503, "Audio unavailable") from None
    if len(data) != row["byte_size"] or hashlib.sha256(data).hexdigest() != row["checksum"]:
        raise HTTPException(503, "Audio integrity mismatch")
    if await record() != row:
        raise HTTPException(409, "Audio changed during read")
    wav_duration(data)
    return data


async def load_sources(service, org, user, refs):
    video_ref, music_ref, narration_ref = refs
    video = await service.resource_bytes(org, user, uuid.UUID(video_ref["resource_id"]))
    audio = []
    for ref in (music_ref, narration_ref):
        audio.append(await audio_bytes(service.repo, org, user,
            uuid.UUID(ref["workspace_id"]), uuid.UUID(ref["file_id"])))
    result = (video, *audio)
    for ref, data in zip(refs, result):
        if ref.get("checksum") and hashlib.sha256(data).hexdigest() != ref["checksum"]:
            raise HTTPException(409, "Source changed since admission")
    return result


async def create_narration(service, org, user, key, conversation, *, video_resource_id,
                           workspace_id, music_file_id, narration_file_id):
    if not service.local_narration or (org, user) not in pilot_principals():
        raise media_unavailable()
    source = await service.repo.read(org, user, resource=uuid.UUID(str(video_resource_id)))
    if source["state"] != "succeeded" or source["mime_type"] != "video/mp4":
        raise HTTPException(422, "Ready BEN video required")
    refs = [{"role": "video", "resource_id": str(source["resource_id"])}]
    refs += [{"role": role, "workspace_id": str(uuid.UUID(str(workspace_id))),
              "file_id": str(uuid.UUID(str(value)))}
             for role, value in (("music", music_file_id), ("narration", narration_file_id))]
    sources = await load_sources(service, org, user, refs)
    duration = await asyncio.to_thread(preflight, *sources)
    for ref, data in zip(refs, sources):
        ref["checksum"] = hashlib.sha256(data).hexdigest()
    # Reuse the source's already validated dimensions/duration profile.
    params = source["request_payload"]["parameters"]
    snapshot = {"normalization_version": "narration-v1", "provider": "local_composer",
        "model": "ffmpeg_stream_copy", "operation": "narration_replacement", "prompt": "",
        "parameters": {"mix": dict(PROFILE), "aspect_ratio": params["aspect_ratio"],
                       "duration_seconds": float(duration)},
        "destination": {"conversation_id": str(conversation), "workspace_id": str(workspace_id)},
        "input_resource_refs": refs, "experiment_id": None}
    return await service.repo.create(org, user, key, snapshot, request_fingerprint(snapshot))


async def run_local(service, row):
    row = await service.repo.local_transition(row, "running")
    if row is None:
        return
    try:
        sources = await load_sources(service, row["org_id"], row["created_by"],
                                     row["request_payload"]["input_resource_refs"])
        output = await asyncio.to_thread(compose, *sources)
        # Reauthorize sources after potentially slow rendering, before publication.
        await load_sources(service, row["org_id"], row["created_by"],
                           row["request_payload"]["input_resource_refs"])
    except (HTTPException, ValueError):
        await service.repo.change(row, state="failed", error_code="local_source_invalid")
        return
    row = await service.repo.local_transition(row, "ingesting")
    if row:
        # Never delete durable blobs after an uncertain DB response.
        await service.publish_video_attempt(row, output, attempt_id=uuid.uuid4())
