"""Gate 1: real raster decoding; authorization routes use explicit test doubles."""
import hashlib
import io
import uuid

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from PIL import Image, ImageCms, PngImagePlugin

from services.media import creative_lab as lab
from routers import creative_lab as routes
from services.media.access import require_pilot


def image_bytes(fmt="PNG", *, mode="RGB", size=(17, 11), **save):
    image = Image.new(mode, size)
    if mode == "RGB":
        image.putdata([((x * 31) % 256, (x * 7) % 256, (x * 13) % 256)
                       for x in range(size[0] * size[1])])
    out = io.BytesIO()
    image.save(out, format=fmt, **save)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_canonical_repeatability_original_identity_and_reload(fmt):
    original = image_bytes(fmt)
    first, second = lab.canonicalize(original), lab.canonicalize(original)
    assert first.png == second.png
    assert first.metadata["original_sha256"] == hashlib.sha256(original).hexdigest()
    with Image.open(io.BytesIO(first.png)) as result:
        assert result.mode == "RGB"
        assert lab.pixel_checksum(result) == first.metadata["canonical_pixel_sha256"]
        assert result.info["srgb"] == 0
    assert lab.canonicalize(first.png).metadata["canonical_pixel_sha256"] == first.metadata["canonical_pixel_sha256"]
    assert first.metadata["provider_calls"] == 0
    assert first.metadata["local_compute_cost_usd"] is None


def test_exif_orientation_applied_exactly_once():
    exif = Image.Exif()
    exif[274] = 6
    source = image_bytes("JPEG", exif=exif)
    result = lab.canonicalize(source)
    assert result.metadata["canonical_dimensions"] == [11, 17]
    with Image.open(io.BytesIO(source)) as decoded, Image.open(io.BytesIO(result.png)) as canonical:
        assert decoded.transpose(Image.Transpose.ROTATE_270).tobytes() == canonical.tobytes()
        assert 274 not in canonical.getexif()
    assert lab.canonicalize(result.png).png == result.png


def test_valid_profile_converted_and_not_copied():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    result = lab.canonicalize(image_bytes(icc_profile=profile))
    assert result.metadata["color_interpretation"] == "icc_converted"
    assert result.metadata["source_icc_sha256"] == hashlib.sha256(profile).hexdigest()
    with Image.open(io.BytesIO(result.png)) as image:
        assert "icc_profile" not in image.info


@pytest.mark.parametrize("data", [b"", b"not an image", image_bytes()[:40],
    image_bytes(mode="RGBA"), image_bytes("JPEG", mode="CMYK"), image_bytes("GIF"),
    image_bytes(icc_profile=b"invalid profile")])
def test_invalid_unsupported_inputs_fail_closed(data):
    with pytest.raises(lab.LabInputError):
        lab.canonicalize(data)


def test_animated_png_rejected():
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, format="PNG", save_all=True,
        append_images=[Image.new("RGB", (4, 4), "blue")])
    with pytest.raises(lab.LabInputError, match="static"):
        lab.canonicalize(out.getvalue())


def test_byte_pixel_and_output_limits(monkeypatch):
    source = image_bytes()
    monkeypatch.setattr(lab, "MAX_SOURCE_BYTES", len(source) - 1)
    with pytest.raises(lab.LabInputError, match="size_limit"):
        lab.canonicalize(source)
    monkeypatch.setattr(lab, "MAX_SOURCE_BYTES", len(source))
    monkeypatch.setattr(lab, "MAX_PIXELS", 10)
    with pytest.raises(lab.LabInputError, match="pixel_limit"):
        lab.canonicalize(source)
    monkeypatch.setattr(lab, "MAX_PIXELS", 1000)
    monkeypatch.setattr(lab, "MAX_CANONICAL_BYTES", 1)
    with pytest.raises(lab.LabInputError, match="canonical_size_limit"):
        lab.canonicalize(source)


def test_unprofiled_nonstandard_gamma_not_silently_ignored():
    import struct
    info = PngImagePlugin.PngInfo()
    info.add(b"gAMA", struct.pack(">I", 100000))
    with pytest.raises(lab.LabInputError, match="color_profile_required"):
        lab.canonicalize(image_bytes(pnginfo=info))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["success", "disabled", "wrong_workspace", "corrupt_storage", "deleted_during_decode"])
async def test_preview_access_identity_and_no_store(tmp_path, monkeypatch, case):
    org, workspace, file = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    raw = image_bytes()
    path = tmp_path / "original.png"
    path.write_bytes(raw)
    calls = []

    async def get_file(**kwargs):
        assert kwargs == dict(org_id=org, workspace_id=workspace, file_id=file)
        calls.append("metadata")
        if case == "wrong_workspace" or (case == "deleted_during_decode" and len(calls) > 1):
            raise HTTPException(404, "File not found")
        return {"checksum": "0" * 64 if case == "corrupt_storage" else hashlib.sha256(raw).hexdigest(),
                "byte_size": len(raw)}

    async def open_file_bytes(**kwargs):
        assert kwargs == dict(org_id=org, workspace_id=workspace, file_id=file)
        return path, "image/png", "original.png"

    monkeypatch.setattr(routes, "get_file", get_file)
    monkeypatch.setattr(routes, "open_file_bytes", open_file_bytes)
    monkeypatch.setenv("BEN_CREATIVE_LAB_ENABLED", "0" if case == "disabled" else "1")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_pilot] = lambda: (org, "test-user")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/creative-lab/canonical?workspace_id={workspace}&file_id={file}")
    assert response.status_code == {"success": 200, "disabled": 404, "wrong_workspace": 404,
                                   "corrupt_storage": 422, "deleted_during_decode": 404}[case]
    if case == "success":
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["metadata"]["reload_pixel_identity"] is True
        assert str(path) not in response.text
    assert path.read_bytes() == raw


@pytest.mark.asyncio
async def test_preview_requires_pilot_before_file_access(monkeypatch):
    async def deny():
        raise HTTPException(404, "Media unavailable")
    async def must_not_read(**_):
        raise AssertionError("Unauthorized source read")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_pilot] = deny
    monkeypatch.setattr(routes, "get_file", must_not_read)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/creative-lab/canonical?workspace_id={uuid.uuid4()}&file_id={uuid.uuid4()}")
    assert r.status_code == 404
