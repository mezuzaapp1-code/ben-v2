"""Gate 1 only: bounded, repeatable canonicalization, with no provider dependency.

The WorkspaceFile remains the authoritative original. A canonical preview is a
derived raster, not a published media resource or a new asset ownership system.
"""
from dataclasses import dataclass
import hashlib
import io
import struct
import time
import warnings

from PIL import Image, ImageCms, ImageOps, PngImagePlugin, UnidentifiedImageError

MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_CANONICAL_BYTES = 64 * 1024 * 1024
CANONICAL_VERSION = "creative-lab-canonical-rgb-v1"


class LabInputError(ValueError):
    """Static diagnostic codes only; never expose paths or image metadata strings."""


@dataclass(frozen=True)
class CanonicalImage:
    png: bytes
    metadata: dict


def pixel_checksum(image: Image.Image) -> str:
    if image.mode != "RGB":
        raise LabInputError("lab_rgb_required")
    prefix = b"creative-lab-rgb8-v1\0" + struct.pack(">II", *image.size)
    return hashlib.sha256(prefix + image.tobytes()).hexdigest()


def canonicalize(original: bytes) -> CanonicalImage:
    started = time.perf_counter()
    if not original or len(original) > MAX_SOURCE_BYTES:
        raise LabInputError("lab_source_size_limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(original)) as probe:
                source_format = probe.format
                source_size = probe.size
                if source_format not in ("PNG", "JPEG"):
                    raise LabInputError("lab_png_jpeg_required")
                if probe.mode != "RGB" or "transparency" in probe.info:
                    raise LabInputError("lab_opaque_rgb_required")
                if getattr(probe, "n_frames", 1) != 1:
                    raise LabInputError("lab_static_image_required")
                if probe.width * probe.height > MAX_PIXELS:
                    raise LabInputError("lab_pixel_limit")
                profile = probe.info.get("icc_profile")
                gamma = probe.info.get("gamma")
                # An unprofiled non-sRGB transfer curve cannot be silently ignored.
                if not profile and ("chromaticity" in probe.info or
                                    (gamma is not None and abs(gamma - 0.45455) > 0.0001)):
                    raise LabInputError("lab_color_profile_required")
                probe.verify()
            with Image.open(io.BytesIO(original)) as decoded:
                decoded.load()
                orientation = decoded.getexif().get(274, 1)
                if type(orientation) is not int or orientation not in range(1, 9):
                    raise LabInputError("lab_invalid_orientation")
                raster = ImageOps.exif_transpose(decoded)
                if profile:
                    if len(profile) > 1024 * 1024:
                        raise LabInputError("lab_profile_size_limit")
                    raster = ImageCms.profileToProfile(
                        raster, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                        ImageCms.createProfile("sRGB"), outputMode="RGB", renderingIntent=0)
                else:
                    raster = raster.copy()
                checksum = pixel_checksum(raster)
                # Discard EXIF/ICC/text from the derivative. Explicit sRGB declaration
                # avoids generated ICC timestamps affecting deterministic PNG bytes.
                clean = Image.frombytes("RGB", raster.size, raster.tobytes())
                info = PngImagePlugin.PngInfo()
                info.add(b"sRGB", b"\x00")
                output = io.BytesIO()
                clean.save(output, format="PNG", pnginfo=info, compress_level=6)
                png = output.getvalue()
                if len(png) > MAX_CANONICAL_BYTES:
                    raise LabInputError("lab_canonical_size_limit")
                with Image.open(io.BytesIO(png)) as reloaded:
                    reloaded.load()
                    if pixel_checksum(reloaded) != checksum:
                        raise LabInputError("lab_canonical_reload_mismatch")
                metadata = {
                    "algorithm_version": CANONICAL_VERSION,
                    "original_sha256": hashlib.sha256(original).hexdigest(),
                    "original_byte_size": len(original), "original_format": source_format,
                    "original_dimensions": list(source_size), "original_mode": "RGB",
                    "exif_orientation": orientation, "orientation_normalized": orientation != 1,
                    "source_icc_sha256": hashlib.sha256(profile).hexdigest() if profile else None,
                    "source_gamma": gamma, "canonical_color_space": "sRGB",
                    "color_interpretation": "icc_converted" if profile else "assumed_srgb",
                    "canonical_dimensions": list(clean.size), "canonical_mode": "RGB",
                    "canonical_pixel_sha256": checksum,
                    "canonical_png_sha256": hashlib.sha256(png).hexdigest(),
                    "canonical_byte_size": len(png), "reload_pixel_identity": True,
                    "canonicalization_ms": round((time.perf_counter() - started) * 1000, 3),
                    "provider_calls": 0, "provider_generation_cost_usd": "0",
                    "local_compute_cost_usd": None,
                }
                return CanonicalImage(png, metadata)
    except LabInputError:
        raise
    except (OSError, ValueError, SyntaxError, TypeError, struct.error,
            UnidentifiedImageError, Image.DecompressionBombWarning,
            Image.DecompressionBombError, ImageCms.PyCMSError):
        raise LabInputError("lab_invalid_image_or_profile") from None
