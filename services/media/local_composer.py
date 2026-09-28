"""Bounded local narration mix. Inputs are verified byte snapshots, never URLs."""
import hashlib
import io
import json
from fractions import Fraction
from pathlib import Path
import subprocess
import tempfile
import wave

MAX_AUDIO_BYTES = 6_000_000
PROFILE = {"narration_gain": 1.0, "music_gain": 0.35, "threshold": 0.03,
           "ratio": 8, "attack_ms": 10, "release_ms": 150,
           "audio_codec": "aac", "audio_bitrate": "192k", "channels": 2,
           "sample_rate": 48000, "video_codec": "copy"}


def wav_duration(data):
    if not 44 <= len(data) <= MAX_AUDIO_BYTES:
        raise ValueError("audio_size_invalid")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if (audio.getsampwidth() != 2 or audio.getframerate() != 48000
                    or audio.getnchannels() not in (1, 2) or audio.getcomptype() != "NONE"):
                raise ValueError("pcm16_48khz_required")
            frames = audio.getnframes()
            if not 0 < frames <= 30 * 48000:
                raise ValueError("audio_duration_invalid")
            if len(audio.readframes(frames)) != frames * 2 * audio.getnchannels():
                raise ValueError("audio_truncated")
            return Fraction(frames, 48000)
    except (wave.Error, EOFError) as exc:
        raise ValueError("pcm16_wav_required") from exc


def video_duration(data):
    import av
    with av.open(io.BytesIO(data), options={"protocol_whitelist": ""}) as container:
        streams = container.streams.video
        if len(streams) != 1 or streams[0].duration is None:
            raise ValueError("video_duration_missing")
        duration = streams[0].duration * streams[0].time_base
        if not 0 < duration <= 30:
            raise ValueError("video_duration_invalid")
        return duration


def preflight(video, music, narration):
    duration = video_duration(video)
    wav_duration(music)
    if wav_duration(narration) > duration:
        raise ValueError("narration_longer_than_video")
    return duration


def _run(args):
    return subprocess.run(args, check=True, capture_output=True, timeout=60).stdout


def packet_signature(path):
    raw = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets",
                "-show_data_hash", "sha256", "-show_entries",
                "packet=pts_time,dts_time,duration_time,data_hash", "-of", "json", str(path)])
    return json.loads(raw)["packets"]


def compose(video, music, narration):
    duration = preflight(video, music, narration)
    # This directory belongs to this invocation only. Durable attempts are stored
    # separately by ingest_mp4; cleanup never touches published blobs.
    with tempfile.TemporaryDirectory(prefix="ben-mix-") as directory:
        root = Path(directory)
        sources = [root / name for name in ("video.mp4", "music.wav", "narration.wav")]
        for path, data in zip(sources, (video, music, narration)):
            path.write_bytes(data)
        seconds = format(float(duration), ".9f")
        graph = (
            f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.35,apad,atrim=duration={seconds}[m];"
            f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=1.0,apad,atrim=duration={seconds},asplit=2[n][sc];"
            "[m][sc]sidechaincompress=threshold=0.03:ratio=8:attack=10:release=150:makeup=1[d];"
            "[d][n]amix=inputs=2:duration=longest:normalize=0[a]"
        )
        output = root / "output.mp4"
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
        for path in sources:
            args += ["-protocol_whitelist", "file,pipe", "-i", str(path)]
        _run(args + ["-filter_complex", graph, "-map", "0:v:0", "-map", "[a]",
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                     "-ac", "2", "-map_metadata", "-1", "-movflags", "+faststart", str(output)])
        if packet_signature(sources[0]) != packet_signature(output):
            raise ValueError("video_stream_changed")
        for path, data in zip(sources, (video, music, narration)):
            if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(data).digest():
                raise ValueError("source_changed")
        result = output.read_bytes()
        if video_duration(result) != duration:
            raise ValueError("video_timing_changed")
        return result
