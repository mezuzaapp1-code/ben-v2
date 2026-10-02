"""Bounded MP4 validation into the existing BEN media byte store; no new asset system."""
import io
import time
from dataclasses import dataclass

import av

from services.media.contracts import MAX_VIDEO_BYTES
from services.media.image_storage import media_path, publish_bytes, StoredImage
from services.workspace_files.storage import DurableStorageUnavailable


@dataclass(frozen=True)
class StoredVideo(StoredImage):
    duration_seconds: float = 0
    audio_present: bool = False


def video_path(org_id, resource_id):
    return media_path(org_id, resource_id, extension="mp4")


def inspect_mp4(data, *, aspect_ratio="16:9", duration_seconds=4, profile="legacy"):
    # Extended decode budget is opt-in; provider validation stays unchanged.
    if profile not in ("legacy", "mobile-v1", "short-v1"):
        raise ValueError("unsupported validation profile")
    if profile == "mobile-v1" and not 0 < duration_seconds <= 30:
        raise ValueError("invalid mobile duration")
    if profile == "short-v1" and duration_seconds != 35:
        raise ValueError("invalid short duration")
    frame_limit = 1052 if profile == "short-v1" else 902 if profile == "mobile-v1" else 240
    audio_limit = 1800 if profile == "short-v1" else 1500 if profile == "mobile-v1" else 1000
    if not data or len(data) > MAX_VIDEO_BYTES or data[4:8] != b"ftyp":
        raise ValueError("invalid media video")
    started = time.monotonic()
    try:
        # In-memory local bytes only; disable external protocol access.
        with av.open(io.BytesIO(data), format="mp4", options={"protocol_whitelist": ""}) as container:
            if len(container.streams.video) != 1 or len(container.streams.audio) > 1 or len(container.streams) > 2:
                raise ValueError("unsupported media streams")
            stream = container.streams.video[0]
            width, height = stream.width, stream.height
            expected = (1280, 720) if aspect_ratio == "16:9" else (720, 1280)
            if (width, height) != expected or stream.codec_context.name != "h264":
                raise ValueError("unsupported media video format")
            if not stream.duration or not stream.time_base:
                raise ValueError("missing media duration")
            duration = float(stream.duration * stream.time_base)
            if abs(duration - duration_seconds) > 0.1:
                raise ValueError("unexpected media duration")
            if profile == "short-v1":
                if not container.streams.audio or stream.average_rate != 30:
                    raise ValueError("short audio and CFR required")
                a = container.streams.audio[0]
                if a.duration is None or abs(float(a.duration*a.time_base)-35) > 0.1:
                    raise ValueError("short audio duration mismatch")
            if container.streams.audio:
                sound = container.streams.audio[0].codec_context
                if sound.name != "aac" or sound.sample_rate > 48000 or len(sound.layout.channels) > 2:
                    raise ValueError("unsupported media audio")
            frames, audio_frames, last_time = 0, 0, None
            audio_start, audio_end = None, None
            for frame in container.decode():
                if isinstance(frame, av.VideoFrame):
                    frames += 1
                    if frame.width != width or frame.height != height or frame.time is None:
                        raise ValueError("invalid media frame")
                    if last_time is not None and frame.time <= last_time:
                        raise ValueError("invalid media timestamps")
                    last_time = frame.time
                else:
                    audio_frames += 1
                    if profile == "short-v1":
                        if frame.time is None or not frame.sample_rate:
                            raise ValueError("missing audio timestamps")
                        if audio_end is not None and abs(frame.time-audio_end) > 0.1:
                            raise ValueError("discontinuous short audio")
                        if audio_start is None:
                            audio_start = frame.time
                        audio_end = frame.time + frame.samples/frame.sample_rate
                if frames > frame_limit or audio_frames > audio_limit or time.monotonic() - started > 30:
                    raise ValueError("media decode limit")
            if frames < 1 or last_time is None or last_time < duration - 0.15:
                raise ValueError("truncated media video")
            audio = bool(container.streams.audio)
            if audio and not audio_frames:
                raise ValueError("invalid media audio")
            if profile == "short-v1" and (audio_start is None or abs(audio_start) > 0.1
                    or audio_end is None or abs(audio_end-duration) > 0.1):
                raise ValueError("truncated short audio")
            return width, height, duration, audio
    except (av.FFmpegError, OSError, OverflowError):
        pass
    raise ValueError("invalid media video")


def ingest_mp4(data, *, org_id, resource_id, aspect_ratio="16:9", duration_seconds=4,
               execution_id=None, attempt_id=None, profile="legacy"):
    width, height, duration, audio = inspect_mp4(data, aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds, profile=profile)
    if execution_id is None and attempt_id is None:
        key, dest = video_path(org_id, resource_id)
    else:
        from services.media.attempt_storage import attempt_path
        key, dest = attempt_path(org_id, resource_id, execution_id, attempt_id)
    try:
        stored = publish_bytes(data, key, dest, width, height, "video/mp4")
    except DurableStorageUnavailable:
        # Map the existing byte-store failure to the bounded ingestion retry path.
        # Do not leak filesystem error details or change image-provider behavior.
        raise ValueError("media video publication failed") from None
    return StoredVideo(**stored.__dict__, duration_seconds=duration, audio_present=audio)
