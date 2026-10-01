"""Real local codec tests: no providers, network, or production database."""
import subprocess
from pathlib import Path

import pytest

from services.media import mobile_video as mobile
from services.media.video_storage import inspect_mp4


def make_video(tmp_path, *, codec="libx264", seconds=1, hdr=False):
    path = tmp_path / "source.mp4"
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=320x180:rate=30", "-t", str(seconds),
            "-c:v", codec, "-threads", "2"]
    if codec == "libx265":
        args += ["-x265-params", "pools=1:frame-threads=1:log-level=error"]
    if hdr:
        args += ["-vf", "format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc", "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020",
                 "-color_trc", "smpte2084", "-colorspace", "bt2020nc"]
    subprocess.run([*args, str(path)], check=True, capture_output=True, timeout=60)
    return path.read_bytes()


@pytest.mark.parametrize("codec,hdr", [("libx264", False), ("libx265", False), ("libx265", True)])
def test_real_conversion(tmp_path, codec, hdr):
    original = make_video(tmp_path, codec=codec, hdr=hdr)
    converted, profile = mobile.convert_mobile(original)
    assert profile.hdr == hdr
    assert (tmp_path / "source.mp4").read_bytes() == original
    assert inspect_mp4(converted, duration_seconds=1, profile="mobile-v1")[:2] == (1280, 720)


def test_extended_budget_is_opt_in(tmp_path):
    converted, profile = mobile.convert_mobile(make_video(tmp_path, seconds=9))
    inspect_mp4(converted, duration_seconds=9, profile="mobile-v1")
    with pytest.raises(ValueError, match="decode limit"):
        inspect_mp4(converted, duration_seconds=9)


def test_post_conversion_corruption_is_rejected(tmp_path, monkeypatch):
    original = make_video(tmp_path)
    run = mobile._run
    def corrupt(args, **kwargs):
        run(args, **kwargs)
        if args[0] == "ffmpeg":
            path = Path(args[-1])
            path.write_bytes(path.read_bytes()[:128])
    monkeypatch.setattr(mobile, "_run", corrupt)
    with pytest.raises(mobile.MobileVideoError) as error:
        mobile.convert_mobile(original)
    assert error.value.code == "VIDEO_UNSUPPORTED_FORMAT"
    assert str(tmp_path) not in str(error.value)


def test_corrupt_source_rejected(tmp_path):
    original = make_video(tmp_path)
    with pytest.raises(mobile.MobileVideoError):
        mobile.convert_mobile(original[:128])


def test_long_source_rejected_before_conversion(tmp_path, monkeypatch):
    original = make_video(tmp_path, seconds=31)
    run = mobile._run
    def forbid_conversion(args, **kwargs):
        assert args[0] != "ffmpeg"
        return run(args, **kwargs)
    monkeypatch.setattr(mobile, "_run", forbid_conversion)
    with pytest.raises(mobile.MobileVideoError) as error:
        mobile.convert_mobile(original)
    assert error.value.status_code == 422
    assert error.value.code == "VIDEO_DURATION_EXCEEDED"


def test_size_rejected_before_probe(monkeypatch):
    monkeypatch.setattr(mobile, "MAX_VIDEO_BYTES", 12)
    with pytest.raises(mobile.MobileVideoError) as error:
        mobile.convert_mobile(b"0" * 13)
    assert error.value.status_code == 413


def test_busy_slot():
    mobile._SLOT.acquire()
    try:
        with pytest.raises(mobile.MobileVideoError) as error:
            mobile.convert_mobile(b"0000ftyp0000")
        assert error.value.code == "VIDEO_PROCESSING_BUSY"
    finally:
        mobile._SLOT.release()


def test_timeout_sanitized_and_slot_released(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private-path", 1)
    monkeypatch.setattr(mobile.subprocess, "run", timeout)
    with pytest.raises(mobile.MobileVideoError) as error:
        mobile.convert_mobile(b"0000ftyp0000")
    assert error.value.code == "VIDEO_PROCESSING_TIMEOUT"
    assert "private-path" not in str(error.value)
    assert mobile._SLOT.acquire(blocking=False)
    mobile._SLOT.release()


def test_phone_color_audio_and_rotation(tmp_path):
    make_video(tmp_path)
    phone = tmp_path / "phone.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmp_path / "source.mp4"),
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1",
        "-vf", "setparams=color_primaries=bt470bg:color_trc=smpte170m:colorspace=bt470bg",
        "-c:v", "libx265", "-threads", "2", "-x265-params", "pools=1:frame-threads=1:log-level=error",
        "-c:a", "aac", str(phone)], capture_output=True, check=True, timeout=60)
    rotated = tmp_path / "rotated.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-display_rotation:v:0", "90", "-i", str(phone), "-c", "copy",
        str(rotated)], capture_output=True, check=True, timeout=30)
    data, profile = mobile.convert_mobile(rotated.read_bytes())
    assert profile.sd_color and profile.audio and profile.rotation in (90, 270)
    assert inspect_mp4(data, aspect_ratio="9:16", duration_seconds=1, profile="mobile-v1")[:2] == (720, 1280)
