"""Pure editing-document contract; no persistence, identity or provider calls.

Source IDs and duration are untrusted claims until resolved by the application.
The one-day bound is a document-parser budget, NOT a supported render duration.
"""
from __future__ import annotations

import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_DOCUMENT_BYTES = 1_000_000
UUID_PATTERN = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class TextStyle(Closed):
    font: Literal['Arial', 'Segoe UI', 'Tahoma', 'Verdana', 'Georgia', 'Times New Roman', 'Courier New'] = 'Arial'
    size: float = Field(default=5, ge=2, le=12, strict=True, allow_inf_nan=False)
    color: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$', strict=True)
    background: str = Field(default='#000000', pattern=r'^#[0-9a-fA-F]{6}$', strict=True)
    opacity: float = Field(default=70, ge=0, le=100, strict=True, allow_inf_nan=False)
    position: Literal['bottom', 'top', 'center', 'custom'] = 'bottom'
    alignment: Literal['auto', 'left', 'center', 'right'] = 'auto'
    backgroundMode: Literal['none', 'solid'] = 'solid'
    shadow: Literal['none', 'soft', 'depth', 'glow'] = 'none'
    outlineColor: str = Field(default='#000000', pattern=r'^#[0-9a-fA-F]{6}$', strict=True)
    outlineWidth: float = Field(default=0, ge=0, le=.08, strict=True, allow_inf_nan=False)
    spacing: float = Field(default=0, ge=-.05, le=.3, strict=True, allow_inf_nan=False)
    bold: bool = Field(default=False, strict=True)
    italic: bool = Field(default=False, strict=True)
    underline: bool = Field(default=False, strict=True)
    x: float = Field(default=50, ge=0, le=100, strict=True, allow_inf_nan=False)
    y: float = Field(default=80, ge=0, le=100, strict=True, allow_inf_nan=False)

    @field_validator('color', 'background', 'outlineColor')
    @classmethod
    def exact_color(cls, value):
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            raise ValueError('Invalid color')
        return value


class TimedText(Closed):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$', strict=True)
    text: str = Field(max_length=1000, strict=True)
    start: float = Field(ge=0, strict=True, allow_inf_nan=False)
    end: float = Field(gt=0, strict=True, allow_inf_nan=False)

    @field_validator('id')
    @classmethod
    def exact_id(cls, value):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
            raise ValueError('Invalid text ID')
        return value

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('Text end must follow start')
        return self


class Cue(TimedText):
    @field_validator('text')
    @classmethod
    def not_blank(cls, value):
        if re.fullmatch(r'[\s\ufeff]*', value):
            raise ValueError('Subtitle text must not be blank')
        return value


class TextLayer(TimedText):
    # Empty/whitespace text layers remain editable drafts, unlike subtitle cues.
    style: TextStyle


class VideoBody(Closed):
    cues: tuple[Cue, ...] = Field(max_length=300)
    style: TextStyle
    textLayers: tuple[TextLayer, ...] = Field(default=(), max_length=20)

    @model_validator(mode='after')
    def unique(self):
        for items in (self.cues, self.textLayers):
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError('Duplicate text ID within a track')
        return self


def uuid_string(value):
    if not isinstance(value, str) or not UUID_PATTERN.fullmatch(value):
        raise ValueError('Expected canonical UUID string')
    return value.lower()


class VideoSource(Closed):
    resource_id: str
    duration_seconds: float = Field(gt=0, le=86400, strict=True, allow_inf_nan=False)
    timebase: Literal['seconds'] = 'seconds'
    _uuid = field_validator('resource_id', mode='before')(uuid_string)


class VideoEditDocument(Closed):
    schema_version: Literal['video-edit-v1']
    document_id: str
    kind: Literal['video']
    source: VideoSource
    body: VideoBody
    _uuid = field_validator('document_id', mode='before')(uuid_string)

    @model_validator(mode='after')
    def timeline(self):
        if any(item.end > self.source.duration_seconds + .05
               for item in (*self.body.cues, *self.body.textLayers)):
            raise ValueError('Text timing exceeds source duration')
        return self


def _json_value(value, depth=0):
    """Enforce JSON semantics even when the caller passes Python objects."""
    if depth > 16:
        raise ValueError('Document nesting exceeds limit')
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        value.encode('utf-8', errors='strict')
    elif isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError('Expected finite number')
    elif isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
    elif isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            _json_value(key, depth + 1)
            _json_value(item, depth + 1)
    else:
        raise ValueError('Expected JSON document')


def validate_edit_document(value: dict) -> VideoEditDocument:
    _json_value(value)
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError('Document is too large')
    document = VideoEditDocument.model_validate(value)
    if len(document.model_dump_json().encode('utf-8')) > MAX_DOCUMENT_BYTES:
        raise ValueError('Normalized document is too large')
    return document


def parse_edit_document(raw: str | bytes) -> VideoEditDocument:
    if not isinstance(raw, (str, bytes)) or len(raw if isinstance(raw, bytes) else raw.encode('utf-8')) > MAX_DOCUMENT_BYTES:
        raise ValueError('Document is too large or not JSON text')

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result

    return validate_edit_document(json.loads(raw, object_pairs_hook=unique_pairs))
