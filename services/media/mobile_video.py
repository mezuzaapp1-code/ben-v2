"""Bounded local mobile conversion. No URLs, providers, DB writes or raw logs."""
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import threading

from services.media.contracts import MAX_VIDEO_BYTES
from services.media.video_storage import inspect_mp4

_SLOT = threading.BoundedSemaphore(1)


class MobileVideoError(ValueError):
    MESSAGES = {
        "VIDEO_SIZE_EXCEEDED": (413, "Video must be 64 MiB or smaller."),
        "VIDEO_DURATION_EXCEEDED": (422, "Video must be 30 seconds or shorter."),
        "VIDEO_UNSUPPORTED_FORMAT": (422, "Video format or color characteristics are unsupported."),
        "VIDEO_PROCESSING_TIMEOUT": (422, "Video processing exceeded the time limit."),
        "VIDEO_PROCESSING_BUSY": (503, "Video processing is busy. Try again later."),
    }

    def __init__(self, code="VIDEO_UNSUPPORTED_FORMAT"):
        self.code = code
        self.status_code, message = self.MESSAGES[code]
        self.detail = {"code": code, "message": message}
        super().__init__(message)


@dataclass(frozen=True)
class MobileProfile:
    codec: str
    duration: float
    width: int
    height: int
    rotation: int
    hdr: bool
    assumed_bt709: bool
    vfr: bool
    fps: str
    aspect_ratio: str
    audio: bool
    sd_color: bool = False


def _run(args, *, timeout, stdout=None):
    try:
        p = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=stdout or subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise MobileVideoError("VIDEO_PROCESSING_TIMEOUT") from None
    except OSError:
        raise MobileVideoError() from None
    if p.returncode:
        raise MobileVideoError()


def _probe(path, scratch, *, packets=False):
    dest = scratch / ("packets.json" if packets else "metadata.json")
    args = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-f", "mov"]
    if packets:
        args += ["-select_streams", "v:0", "-show_packets", "-show_entries", "packet=pts_time,duration_time"]
    else:
        args += ["-show_streams", "-show_format"]
    with dest.open("wb") as out:
        _run([*args, "-of", "json", str(path)], timeout=15, stdout=out)
    if dest.stat().st_size > 4_000_000:
        raise MobileVideoError()
    try:
        return json.loads(dest.read_bytes())
    except (ValueError, OSError):
        raise MobileVideoError() from None


def _number(value):
    try:
        number = float(Fraction(str(value)))
        if not math.isfinite(number):
            raise ValueError()
        return number
    except (ValueError, ZeroDivisionError, TypeError):
        raise MobileVideoError() from None


def analyze(metadata, packets):
    """Pure profile validation for bounded ffprobe results; not proof of decoding."""
    try:
        streams = metadata["streams"]
        video = [s for s in streams if s["codec_type"] == "video"]
        audio = [s for s in streams if s["codec_type"] == "audio"]
        if len(video) != 1 or len(audio) > 1 or len(streams) > 8:
            raise MobileVideoError()
        v = video[0]
        duration = _number(v.get("duration", metadata["format"].get("duration")))
        if duration > 30:
            raise MobileVideoError("VIDEO_DURATION_EXCEEDED")
        if duration <= 0 or v["codec_name"] not in ("hevc", "h264"):
            raise MobileVideoError()
        w, h = int(v["width"]), int(v["height"])
        if min(w, h) < 16 or max(w, h) > 3840 or w*h > 3840*2160:
            raise MobileVideoError()
        side = v.get("side_data_list", [])
        if any(any(word in s.get("side_data_type", "").lower() for word in ("dovi", "dolby")) for s in side):
            raise MobileVideoError()
        rotation = _number(next((s["rotation"] for s in side if "rotation" in s), v.get("tags", {}).get("rotate", 0)))
        if rotation % 90 != 0:
            raise MobileVideoError()
        rotation = int(rotation) % 360
        sar = _number(v.get("sample_aspect_ratio", "1:1").replace(":", "/"))
        if sar != 1:
            raise MobileVideoError()
        transfer, primaries, matrix = (v.get(k, "unknown") for k in ("color_transfer", "color_primaries", "color_space"))
        hdr = transfer in ("smpte2084", "arib-std-b67")
        sd_color = (transfer, primaries, matrix) == ("smpte170m", "bt470bg", "bt470bg")
        if hdr:
            if primaries != "bt2020" or matrix != "bt2020nc" or v.get("pix_fmt") != "yuv420p10le":
                raise MobileVideoError()
        elif not sd_color and (transfer not in ("unknown", "bt709") or primaries not in ("unknown", "bt709")
              or matrix not in ("unknown", "bt709") or v.get("pix_fmt") not in ("yuv420p", "yuv420p10le")):
            raise MobileVideoError()
        assumed = not hdr and "unknown" in (transfer, primaries, matrix)
        if assumed and v.get("pix_fmt") != "yuv420p":
            raise MobileVideoError()
        times = sorted(_number(p["pts_time"]) for p in packets["packets"])
        if not 2 <= len(times) <= 1802:
            raise MobileVideoError()
        steps = [b-a for a, b in zip(times, times[1:])]
        if min(steps) <= 0 or max(steps) > 1 or abs(times[-1]-times[0]-duration) > max(.15, max(steps)*2):
            raise MobileVideoError()
        rate = (len(times)-1)/(times[-1]-times[0])
        if not 1 <= rate <= 61:
            raise MobileVideoError()
        target = min(("24000/1001", "24", "25", "30000/1001", "30"), key=lambda f: abs(_number(f)-rate))
        if audio:
            a = audio[0]
            if a.get("codec_name") not in ("aac", "pcm_s16le") or int(a.get("channels", 0)) not in (1, 2):
                raise MobileVideoError()
            if abs(_number(a.get("start_time", 0))-_number(v.get("start_time", 0))) > .1:
                raise MobileVideoError()
        display_w, display_h = (h, w) if rotation in (90, 270) else (w, h)
        return MobileProfile(v["codec_name"], duration, w, h, rotation, hdr, assumed,
            max(steps)-min(steps) > .001, target, "16:9" if display_w >= display_h else "9:16", bool(audio), sd_color)
    except MobileVideoError:
        raise
    except (KeyError, TypeError, OverflowError, ValueError):
        raise MobileVideoError() from None


def _filter(profile):
    w, h = (1280, 720) if profile.aspect_ratio == "16:9" else (720, 1280)
    color = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable,"
             "zscale=t=bt709:m=bt709:r=limited,format=yuv420p" if profile.hdr else
             "scale=in_color_matrix=bt709:out_color_matrix=bt709:out_range=tv,format=yuv420p")
    if profile.sd_color:
        color = "colorspace=ispace=bt470bg:itrc=smpte170m:iprimaries=bt470bg:all=bt709:range=tv,format=yuv420p"
    return (color + f",scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={profile.fps},sidedata=mode=delete,"
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709")


def convert_mobile(data):
    """Return a validated derivative and profile; caller owns durable publication.

    Semaphore covers probing, conversion and validation in this Python process.
    Explicit temporary directory isolates concurrent processes/attempts. Bytes of
    the original are never mutated. No external programs receive network URLs.
    """
    if len(data) > MAX_VIDEO_BYTES:
        raise MobileVideoError("VIDEO_SIZE_EXCEEDED")
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise MobileVideoError()
    if not _SLOT.acquire(blocking=False):
        raise MobileVideoError("VIDEO_PROCESSING_BUSY")
    try:
        with tempfile.TemporaryDirectory(prefix="ben-mobile-") as directory:
            scratch = Path(directory)
            source, output = scratch / "source.mov", scratch / "output.mp4"
            source.write_bytes(data)
            metadata = _probe(source, scratch)
            # Cheap limits before the packet walk.
            raw_duration = _number(metadata.get("format", {}).get("duration", 0))
            if raw_duration > 30:
                raise MobileVideoError("VIDEO_DURATION_EXCEEDED")
            profile = analyze(metadata, _probe(source, scratch, packets=True))
            args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-xerror",
                    "-protocol_whitelist", "file", "-threads", "2", "-f", "mov", "-i", str(source),
                    "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-map_chapters", "-1",
                    "-filter_threads", "1", "-vf", _filter(profile), "-c:v", "libx264", "-threads", "2",
                    "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-movflags", "+faststart", "-fs", str(MAX_VIDEO_BYTES), str(output)]
            # Default FFmpeg autorotate is the only rotation mechanism.
            _run(args, timeout=120)
            converted = output.read_bytes()
            result = _probe(output, scratch)
            v = next(s for s in result["streams"] if s["codec_type"] == "video")
            if any(v.get(k) != "bt709" for k in ("color_transfer", "color_primaries", "color_space")):
                raise MobileVideoError()
            if any(abs(_number(s.get("rotation", 0))) > .01 for s in v.get("side_data_list", [])):
                raise MobileVideoError()
            if abs(_number(v.get("avg_frame_rate"))-_number(profile.fps)) > .01:
                raise MobileVideoError()
            sound = [s for s in result["streams"] if s["codec_type"] == "audio"]
            if bool(sound) != profile.audio:
                raise MobileVideoError()
            if sound and (abs(_number(sound[0].get("start_time", 0))-_number(v.get("start_time", 0))) > .1
                          or abs(_number(sound[0].get("duration", 0))-profile.duration) > .15):
                raise MobileVideoError()
            inspect_mp4(converted, aspect_ratio=profile.aspect_ratio,
                         duration_seconds=profile.duration, profile="mobile-v1")
            if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(data).digest():
                raise MobileVideoError()
            return converted, profile
    except MobileVideoError:
        raise
    except (ValueError, OSError, KeyError, StopIteration):
        raise MobileVideoError() from None
    finally:
        _SLOT.release()
