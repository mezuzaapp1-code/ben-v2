"""Internal provider boundary. No authorization, destination, or storage ownership."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"
BFL_IMAGE_MODEL = "flux-2-pro"
IMAGE_PROVIDERS = {GEMINI_IMAGE_MODEL: "google", BFL_IMAGE_MODEL: "bfl"}
VEO_VIDEO_MODEL = "veo-3.1-fast-generate-preview"
MEDIA_PROVIDERS = {**IMAGE_PROVIDERS, VEO_VIDEO_MODEL: "google"}
MAX_VIDEO_BYTES = 64 * 1024 * 1024
MAX_VIDEO_JOURNAL_BYTES = 90 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 30 * 1024 * 1024


class MediaProviderError(Exception):
    """Only static codes escape transport; never retain request/response objects.

    An uncertain submission is NOT permission to retry. The execution owner must
    persist submission_unknown. Even a definite rejection is never retried here.
    """

    def __init__(self, code: str, *, submission_unknown: bool = False):
        super().__init__(code)
        self.code = code
        self.submission_unknown = submission_unknown


@dataclass(frozen=True)
class ImageRequest:
    model: str
    prompt: str = field(repr=False)
    aspect_ratio: str = "1:1"
    image_size: str = "1K"

    def validate(self) -> None:
        if self.model not in IMAGE_PROVIDERS:
            raise MediaProviderError("unsupported_media_model")
        if not isinstance(self.prompt, str) or not self.prompt.strip() or len(self.prompt) > 8000:
            raise MediaProviderError("invalid_media_prompt")
        # Intentionally narrow first proof, not the provider's complete catalog.
        if self.aspect_ratio not in ("1:1", "16:9", "9:16") or self.image_size != "1K":
            raise MediaProviderError("unsupported_media_parameters")

    def normalized(self) -> dict[str, Any]:
        self.validate()
        return {"provider": IMAGE_PROVIDERS[self.model], "model": self.model,
                "operation": "image_generation", "prompt": self.prompt,
                "parameters": {"aspect_ratio": self.aspect_ratio,
                               "image_size": self.image_size, "mime_type": "image/png"}}


@dataclass(frozen=True)
class ImageResult:
    # A provider result is NOT a published BEN resource.
    data: bytes = field(repr=False)
    mime_type: str
    returned_model: str
    operation_ref: str | None = field(repr=False)
    usage: dict[str, Any]
    duration_ms: float | None


def normalize_usage(raw: Any) -> dict[str, Any]:
    """Allowlist documented counters; missing is not zero, units stay explicit.

    Preserve modality detail for future accounting without copying arbitrary
    provider fields (which may contain prompts, credentials, or signed URLs).
    """
    result: dict[str, Any] = {"schema_version": "media-usage-v1", "image_count": 1,
                              "image_count_source": "observed_output", "provider_usage": None}
    if not isinstance(raw, dict):
        return result
    clean: dict[str, Any] = {}
    for key in ("total_input_tokens", "total_output_tokens", "total_cached_tokens",
                "total_thought_tokens", "total_tool_use_tokens", "total_tokens"):
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 10**12:
            clean[key] = value
    for key in ("input_tokens_by_modality", "output_tokens_by_modality"):
        entries = raw.get(key)
        if isinstance(entries, list):
            accepted = []
            for item in entries[:16]:
                if (isinstance(item, dict) and item.get("modality") in
                        ("text", "image", "video", "audio") and
                        type(item.get("tokens")) is int and 0 <= item["tokens"] <= 10**12):
                    accepted.append({"modality": item["modality"], "tokens": item["tokens"]})
            if accepted:
                clean[key] = accepted
    if clean:
        result["provider_usage"] = {"unit": "tokens", "source": "provider_reported", **clean}
    return result


@dataclass(frozen=True)
class VideoRequest:
    model: str
    prompt: str = field(repr=False)
    source_resource_id: str
    aspect_ratio: str = "16:9"
    duration_seconds: int = 4
    resolution: str = "720p"

    def validate(self):
        import uuid
        if self.model != VEO_VIDEO_MODEL:
            raise MediaProviderError("unsupported_media_model")
        if not isinstance(self.prompt, str) or not self.prompt.strip() or len(self.prompt) > 2000:
            raise MediaProviderError("invalid_media_prompt")
        try:
            uuid.UUID(self.source_resource_id)
        except (ValueError, TypeError, AttributeError):
            raise MediaProviderError("invalid_media_source") from None
        if (self.aspect_ratio not in ("16:9", "9:16") or type(self.duration_seconds) is not int
                or self.duration_seconds != 4 or self.resolution != "720p"):
            raise MediaProviderError("unsupported_media_parameters")

    def normalized(self):
        self.validate()
        return {"provider": "google", "model": self.model, "operation": "image_to_video",
                "prompt": self.prompt, "parameters": {"aspect_ratio": self.aspect_ratio,
                "duration_seconds": self.duration_seconds, "resolution": self.resolution,
                "audio": "native", "person_generation": "allow_adult", "mime_type": "video/mp4"}}


# Same internal byte-result contract and journal for both media modalities.
VideoResult = ImageResult
