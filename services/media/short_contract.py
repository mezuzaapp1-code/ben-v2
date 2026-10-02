"""Fixed render intent. No client URLs, provider JSON or guessed costs."""
import hashlib
import hmac
import json
import os
import time
import uuid
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

PROFILE = 'five-scenes-35s-portrait-v1'


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Caption(Closed):
    start_ms: int = Field(ge=0, le=34999, strict=True)
    end_ms: int = Field(gt=0, le=35000, strict=True)
    text: str = Field(min_length=1, max_length=160)


class ScenePlan(Closed):
    conversation_id: uuid.UUID
    request_version: Literal['short-render-v1'] = 'short-render-v1'
    profile_id: Literal['five-scenes-35s-portrait-v1'] = PROFILE
    image_resource_ids: list[uuid.UUID] = Field(min_length=5, max_length=5)
    narration_workspace_id: uuid.UUID
    narration_file_id: uuid.UUID
    captions: list[Caption] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def timing(self):
        end = 0
        for cue in self.captions:
            if cue.start_ms < end or cue.end_ms <= cue.start_ms:
                raise ValueError('ordered non-overlapping captions required')
            if any(ord(c) < 32 and c != '\n' for c in cue.text):
                raise ValueError('control characters unsupported')
            end = cue.end_ms
        return self


class RenderCommand(Closed):
    plan: ScenePlan
    quote_id: str = Field(min_length=1, max_length=4000)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def money(name):
    try:
        value = Decimal(os.environ[name])
        if not value.is_finite() or value <= 0 or value > 1000 or value.as_tuple().exponent < -8:
            raise ValueError()
        return value
    except (KeyError, ValueError, InvalidOperation):
        raise HTTPException(503, 'Short render pricing unavailable') from None


def signing_key():
    key = os.getenv('BEN_SHORT_QUOTE_SECRET', '')
    if len(key) < 32:
        raise HTTPException(503, 'Short render quoting unavailable')
    return key.encode()


def quote(org, user, snapshot):
    estimate = money('BEN_SHORT_ESTIMATE_USD')
    bound = money('BEN_SHORT_RESERVATION_USD')
    if estimate > bound or not os.getenv('BEN_SHORT_PRICING_VERSION'):
        raise HTTPException(503, 'Short render pricing unavailable')
    body = {'id': str(uuid.uuid4()), 'org': str(org), 'user': user,
            'fingerprint': digest(snapshot), 'expires': int(time.time()) + 600,
            'estimate': str(estimate), 'reserved': str(bound),
            'pricing_version': os.environ['BEN_SHORT_PRICING_VERSION']}
    raw = canonical(body)
    return raw + '.' + hmac.new(signing_key(), raw.encode(), hashlib.sha256).hexdigest()


def verify_quote(token, org, user, snapshot):
    try:
        raw, signature = token.rsplit('.', 1)
        if not hmac.compare_digest(signature, hmac.new(signing_key(), raw.encode(), hashlib.sha256).hexdigest()):
            raise ValueError()
        body = json.loads(raw)
        if (body['org'] != str(org) or body['user'] != user or body['expires'] <= time.time()
                or body['fingerprint'] != digest(snapshot)):
            raise ValueError()
        return body
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, 'Short render quote expired or changed') from None
