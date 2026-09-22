"""Internal provider boundary. No authorization, destination, or storage ownership."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"
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
        if self.model != GEMINI_IMAGE_MODEL:
            raise MediaProviderError("unsupported_media_model")
        if not isinstance(self.prompt, str) or not self.prompt.strip() or len(self.prompt) > 8000:
            raise MediaProviderError("invalid_media_prompt")
        # Intentionally narrow first proof, not the provider's complete catalog.
        if self.aspect_ratio not in ("1:1", "16:9", "9:16") or self.image_size != "1K":
            raise MediaProviderError("unsupported_media_parameters")

    def normalized(self) -> dict[str, Any]:
        self.validate()
        return {"provider": "google", "model": self.model,
                "operation": "image_generation", "prompt": self.prompt,
                "parameters": {"aspect_ratio": self.aspect_ratio,
                               "image_size": self.image_size, "mime_type": "image/png"}}


@dataclass(frozen=True)
class ImageResult:
    # A provider result is NOT a published BEN resource.
    data: bytes = field(repr=False)
    mime_type: str
    returned_model: str
    operation_ref: str = field(repr=False)
    usage: dict[str, Any]
    duration_ms: float


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
