"""Current-turn image input for speaking-provider adapters.

Adapters keep their native multimodal contracts. This module only carries
authorized image bytes and ordered user content. It must not include
storage keys, filesystem paths, or BEN envelopes.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

VISION_ANALYZE = "vision.analyze"

VISION_MEDIA_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/webp",
        "image/gif",
    }
)

_MEDIA_TYPE_ALIASES = {
    "image/jpg": "image/jpeg",
}


def normalize_vision_media_type(raw: str | None) -> str | None:
    token = str(raw or "").strip().lower()
    if not token:
        return None
    token = _MEDIA_TYPE_ALIASES.get(token, token)
    if token not in VISION_MEDIA_TYPES:
        return None
    return "image/jpeg" if token == "image/jpg" else token


def is_vision_media_type(raw: str | None) -> bool:
    return normalize_vision_media_type(raw) is not None


@dataclass(frozen=True)
class VisionImage:
    """Authorized image bytes for one current-turn attachment."""

    file_id: str
    media_type: str
    data: bytes

    def base64_data(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.base64_data()}"


@dataclass(frozen=True)
class UserTextPart:
    text: str


ProviderUserPart = UserTextPart | VisionImage


def openai_user_content(parts: list[ProviderUserPart]) -> str | list[dict]:
    """OpenAI/xAI Chat Completions user content (string when text-only)."""
    blocks: list[dict] = []
    for part in parts:
        if isinstance(part, UserTextPart):
            if part.text:
                blocks.append({"type": "text", "text": part.text})
            continue
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": part.data_url()},
            }
        )
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return str(blocks[0].get("text") or "")
    return blocks


def anthropic_user_content(parts: list[ProviderUserPart]) -> str | list[dict]:
    """Anthropic Messages user content (string when text-only)."""
    blocks: list[dict] = []
    for part in parts:
        if isinstance(part, UserTextPart):
            if part.text:
                blocks.append({"type": "text", "text": part.text})
            continue
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": part.media_type,
                    "data": part.base64_data(),
                },
            }
        )
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return str(blocks[0].get("text") or "")
    return blocks


def gemini_user_parts(parts: list[ProviderUserPart]) -> list[dict]:
    """Gemini generateContent user parts (native inlineData)."""
    out: list[dict] = []
    for part in parts:
        if isinstance(part, UserTextPart):
            if part.text:
                out.append({"text": part.text})
            continue
        out.append(
            {
                "inlineData": {
                    "mimeType": part.media_type,
                    "data": part.base64_data(),
                }
            }
        )
    if not out:
        out.append({"text": ""})
    return out
