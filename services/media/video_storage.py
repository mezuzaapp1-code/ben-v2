"""Bounded MP4 validation into the existing BEN media byte store; no new asset system."""
import io
import time
from dataclasses import dataclass

import av

from services.media.contracts import MAX_VIDEO_BYTES
from services.media.image_storage import media_path, publish_bytes, StoredImage


@dataclass(frozen=True)
class StoredVideo(StoredImage):
    duration_seconds: float = 0
    audio_present: bool = False


def video_path(org_id, resource_id):
    return media_path(org_id, resource_id, extension="mp4")


def inspect_mp4(data, *, aspect_ratio="16:9", duration_seconds=4):
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
            if container.streams.audio:
                sound = container.streams.audio[0].codec_context
                if sound.name != "aac" or sound.sample_rate > 48000 or len(sound.layout.channels) > 2:
                    raise ValueError("unsupported media audio")
            frames, audio_frames, last_time = 0, 0, None
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
                if frames > 240 or audio_frames > 1000 or time.monotonic() - started > 30:
                    raise ValueError("media decode limit")
            if frames < 1 or last_time is None or last_time < duration - 0.15:
                raise ValueError("truncated media video")
            audio = bool(container.streams.audio)
            if audio and not audio_frames:
                raise ValueError("invalid media audio")
            return width, height, duration, audio
    except (av.FFmpegError, OSError, OverflowError):
        pass
    raise ValueError("invalid media video")


def ingest_mp4(data, *, org_id, resource_id, aspect_ratio="16:9", duration_seconds=4):
    width, height, duration, audio = inspect_mp4(data, aspect_ratio=aspect_ratio, duration_seconds=duration_seconds)
    key, dest = video_path(org_id, resource_id)
    stored = publish_bytes(data, key, dest, width, height, "video/mp4")
    return StoredVideo(**stored.__dict__, duration_seconds=duration, audio_present=audio)
