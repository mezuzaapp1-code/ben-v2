"""Real FFmpeg, original migrations, tenant RLS and immutable attempt delivery."""
import hashlib
import io
import math
from pathlib import Path
import struct
import uuid
import wave
from unittest.mock import patch

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text

from services.media.local_composer import compose, preflight, wav_duration, packet_signature
from services.media.narration import create_narration
from services.media.service import MediaService
from services.workspace_files.storage import write_upload
from tests.test_media_repository import repository, ORG, OTHER, THREAD
from tests.test_media_migration import migration_sql
from tests.test_media_attempt_isolation import ready
from tests.test_veo_media import mp4


def wav(seconds=3, rate=48000, channels=1, width=2):
    result = io.BytesIO()
    with wave.open(result, "wb") as out:
        out.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        frames = int(rate * seconds)
        if width == 2:
            out.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i * 440 * 2 * math.pi / rate)))
                                     * channels for i in range(frames)))
        else:
            out.writeframes(bytes(frames * channels * width))
    return result.getvalue()


@pytest_asyncio.fixture
async def setup(repository, tmp_path, monkeypatch):
    repo, admin = repository
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    await admin.execute(migration_sql(filename="034_narration_replacement.py"))
    # Existing Project boundary; audio table itself comes from real migration 022.
    await admin.execute("CREATE TABLE ben.projects(id uuid PRIMARY KEY, org_id uuid NOT NULL)")
    await admin.execute(migration_sql(filename="022_workspace_files_v1.py"))
    async with repo.transaction(ORG) as session:
        role = await session.scalar(text("SELECT current_user"))
    await admin.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA ben TO {role}")
    workspace = uuid.uuid4()
    await admin.execute("INSERT INTO ben.projects VALUES($1,$2)", workspace, ORG)
    monkeypatch.setattr("services.media.service.pilot_principals", lambda: {(ORG, "tester")})
    monkeypatch.setattr("services.media.narration.pilot_principals", lambda: {(ORG, "tester")})
    inert = object()
    svc = MediaService(repo, inert, inert, inert, inert, local_narration=True)
    original = mp4()
    source = await svc.publish_video_attempt(await ready(repo), original, attempt_id=uuid.uuid4())

    async def asset(data, org=ORG, user="tester"):
        file_id = uuid.uuid4()
        class Upload:
            stream = io.BytesIO(data)
            async def read(self, size):
                return self.stream.read(size)
        key, size, checksum = await write_upload(org_id=org, workspace_id=workspace,
            file_id=file_id, filename="sample.wav", upload=Upload())
        await admin.execute("""INSERT INTO ben.workspace_files
            (id,org_id,workspace_id,original_filename,display_name,media_type,byte_size,
             checksum,storage_key,uploaded_by,status)
            VALUES($1,$2,$3,'sample.wav','sample.wav','audio/wav',$4,$5,$6,$7,'uploaded')""",
            file_id, org, workspace, size, checksum, key, user)
        return file_id
    music, narration = await asset(wav(4)), await asset(wav(3))
    async def create(**overrides):
        args = dict(video_resource_id=source["resource_id"], workspace_id=workspace,
                    music_file_id=music, narration_file_id=narration)
        args.update(overrides)
        return await create_narration(svc, ORG, "tester", "narration-test", THREAD, **args)
    return svc, admin, create, asset, source, original, narration


@pytest.mark.asyncio
async def test_full_worker_stream_copy_attempt_readback(setup, tmp_path):
    svc, admin, create, _, source, original, _ = setup
    execution = await create()
    # Legacy intent remains byte-for-byte stable across mobile feature rollout.
    assert "validation_profile" not in execution["request_payload"]["parameters"]
    assert (await create())["execution_id"] == execution["execution_id"]
    assert await svc.repo.claim(ORG, "unsupported-worker") is None
    assert await svc.tick(ORG)
    result = await svc.repo.read(ORG, "tester", execution=execution["execution_id"])
    assert result["state"] == "succeeded"
    assert "/attempts/" in result["storage_key"] and result["resource_id"] != source["resource_id"]
    assert result["submit_attempts"] == result["poll_attempts"] == 0
    data = await svc.resource_bytes(ORG, "tester", result["resource_id"])
    assert hashlib.sha256(data).hexdigest() == result["checksum"]
    assert await svc.resource_bytes(ORG, "tester", source["resource_id"]) == original
    a, b = tmp_path / "source.mp4", tmp_path / "result.mp4"
    a.write_bytes(original)
    b.write_bytes(data)
    assert packet_signature(a) == packet_signature(b)
    import av
    with av.open(io.BytesIO(data)) as container:
        assert len(container.streams) == 2
        audio = container.streams.audio[0]
        assert audio.codec_context.name == "aac" and audio.codec_context.sample_rate == 48000
        assert audio.codec_context.channels == 2
    for org, user in ((OTHER, "tester"), (ORG, "different-user")):
        with pytest.raises(HTTPException):
            await svc.resource_bytes(org, user, result["resource_id"])
    assert not await svc.tick(ORG)


@pytest.mark.asyncio
async def test_long_narration_rejected_before_execution_or_ffmpeg(setup):
    svc, admin, create, asset, *_ = setup
    too_long = await asset(wav(4.01))
    with patch("services.media.narration.compose", side_effect=AssertionError("must not render")):
        with pytest.raises(ValueError, match="narration_longer_than_video"):
            await create(narration_file_id=too_long)
    assert await admin.fetchval("SELECT count(*) FROM ben.media_executions WHERE operation='narration_replacement'") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["checksum", "org", "user", "wrong_rate"])
async def test_audio_admission_rejects_invalid_sources(setup, kind):
    svc, admin, create, asset, _, _, narration = setup
    if kind == "checksum":
        await admin.execute("UPDATE ben.workspace_files SET checksum=$1 WHERE id=$2", "0" * 64, narration)
    elif kind == "org":
        narration = await asset(wav(), org=OTHER)
    elif kind == "user":
        narration = await asset(wav(), user="other-user")
    else:
        narration = await asset(wav(rate=44100))
    with pytest.raises((HTTPException, ValueError)):
        await create(narration_file_id=narration)
    assert await admin.fetchval("SELECT count(*) FROM ben.media_executions WHERE operation='narration_replacement'") == 0


@pytest.mark.asyncio
async def test_worker_revalidates_sources_after_admission(setup):
    svc, admin, create, _, _, _, narration = setup
    row = await create()
    await admin.execute("UPDATE ben.workspace_files SET checksum=$1 WHERE id=$2", "f" * 64, narration)
    with patch("services.media.narration.compose", side_effect=AssertionError("must not render")):
        await svc.tick(ORG)
    result = await svc.repo.read(ORG, "tester", execution=row["execution_id"])
    assert result["state"] == "failed" and result["storage_key"] is None


@pytest.mark.parametrize("data", [b"ID3-not-wav", wav(rate=44100), wav(width=1), wav(channels=3), wav()[:-10]],
                         ids=["mp3", "44khz", "8bit", "three_channels", "truncated"])
def test_strict_audio_profile(data):
    with pytest.raises(ValueError):
        wav_duration(data)


def test_real_composer_and_duration_guard():
    video = mp4()
    output = compose(video, wav(4), wav(3))
    assert output != video
    with pytest.raises(ValueError, match="narration_longer_than_video"):
        preflight(video, wav(4), wav(4.01))


@pytest.mark.asyncio
async def test_local_lease_takeover_and_expired_transition(setup):
    from services.media.narration import run_local
    svc, admin, create, *_ = setup
    created = await create()
    alpha = await svc.repo.claim(ORG, "alpha", local=True)
    alpha = await svc.repo.local_transition(alpha, "running")
    assert await svc.repo.claim(ORG, "beta", local=True) is None
    await admin.execute("UPDATE ben.media_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=$1", alpha["execution_id"])
    assert await svc.repo.local_transition(alpha, "ingesting") is None
    beta = await svc.repo.claim(ORG, "beta", local=True)
    assert beta["version"] > alpha["version"]
    assert await svc.repo.local_transition(alpha, "ingesting") is None
    await run_local(svc, beta)
    result = await svc.repo.read(ORG, "tester", execution=created["execution_id"])
    assert result["state"] == "succeeded" and result["lease_owner"] is None
