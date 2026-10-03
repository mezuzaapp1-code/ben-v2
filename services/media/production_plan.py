"""Planning-only contract. No provider commands, URLs, prices or trusted asset claims."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Attachment(Closed):
    attachment_id: UUID
    source_id: UUID
    source_kind: Literal['chat_photo', 'media_resource']
    role: Literal['animate_source', 'character_reference', 'background', 'style_reference']


class Transition(Closed):
    kind: Literal['cut', 'crossfade'] = 'cut'
    overlap_ms: int = Field(default=0, ge=0, le=1000, strict=True)

    @model_validator(mode='after')
    def valid(self):
        if (self.kind == 'cut') != (self.overlap_ms == 0):
            raise ValueError('cuts require zero overlap; crossfades require positive overlap')
        return self


class Animated(Closed):
    kind: Literal['animated_attachment']
    attachment_id: UUID
    motion_prompt: str = Field(min_length=1, max_length=2000)


class Generated(Closed):
    kind: Literal['generated_scene']
    prompt: str = Field(min_length=1, max_length=2000)
    reference_ids: tuple[UUID, ...] = Field(default=(), max_length=6)


class TextOverlay(Closed):
    kind: Literal['text']
    text: str = Field(min_length=1, max_length=160)
    position: Literal['top', 'center', 'bottom'] = 'center'


class SpriteOverlay(Closed):
    kind: Literal['sprite']
    attachment_id: UUID
    count: int = Field(ge=1, le=8, strict=True)
    layout: Literal['side_by_side'] = 'side_by_side'


Overlay = Annotated[TextOverlay | SpriteOverlay, Field(discriminator='kind')]


class Composition(Closed):
    kind: Literal['controlled_composition']
    background_prompt: str = Field(min_length=1, max_length=2000)
    overlays: tuple[Overlay, ...] = Field(min_length=1, max_length=8)


Visual = Annotated[Animated | Generated | Composition, Field(discriminator='kind')]


class Scene(Closed):
    scene_id: UUID
    duration_ms: int = Field(ge=1000, le=15000, strict=True)
    narration_text: str = Field(default='', max_length=1500)
    visual: Visual
    transition_to_next: Transition = Field(default_factory=Transition)


class ProductionPlan(Closed):
    schema_version: Literal['production-plan-v1'] = 'production-plan-v1'
    profile: Literal['three-scenes-15s-v1'] = 'three-scenes-15s-v1'
    original_brief: str = Field(min_length=1, max_length=12000)
    language: Literal['en-US', 'he-IL'] = 'en-US'
    aspect_ratio: Literal['9:16'] = '9:16'
    width: Literal[720] = 720
    height: Literal[1280] = 1280
    target_duration_ms: Literal[15000] = 15000
    duration_tolerance_ms: int = Field(default=100, ge=0, le=100, strict=True)
    attachments: tuple[Attachment, ...] = Field(default=(), max_length=12)
    scenes: tuple[Scene, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode='after')
    def valid(self):
        ids = [s.scene_id for s in self.scenes]
        assets = {a.attachment_id: a for a in self.attachments}
        if len(set(ids)) != len(ids) or len(assets) != len(self.attachments):
            raise ValueError('duplicate scene or attachment ID')
        for i, scene in enumerate(self.scenes):
            overlap = scene.transition_to_next.overlap_ms
            incoming = self.scenes[i-1].transition_to_next.overlap_ms if i else 0
            if incoming + overlap > scene.duration_ms:
                raise ValueError('overlapping transitions cannot consume the middle scene')
            if i == len(self.scenes)-1:
                if overlap or scene.transition_to_next.kind != 'cut':
                    raise ValueError('last scene cannot transition')
            elif overlap >= min(scene.duration_ms, self.scenes[i+1].duration_ms):
                raise ValueError('transition consumes a scene')
            visual = scene.visual
            refs = ([visual.attachment_id] if isinstance(visual, Animated) else
                    list(visual.reference_ids) if isinstance(visual, Generated) else
                    [o.attachment_id for o in visual.overlays if isinstance(o, SpriteOverlay)])
            if any(ref not in assets for ref in refs):
                raise ValueError('unknown attachment reference')
            if isinstance(visual, Animated) and assets[visual.attachment_id].role != 'animate_source':
                raise ValueError('animation requires animate_source role')
        if abs(self.timeline()[-1]['end_ms']-self.target_duration_ms) > self.duration_tolerance_ms:
            raise ValueError('timeline does not match target duration')
        return self

    def timeline(self):
        start, result = 0, []
        for scene in self.scenes:
            end = start + scene.duration_ms
            result.append(dict(scene_id=str(scene.scene_id), start_ms=start, end_ms=end))
            start = end-scene.transition_to_next.overlap_ms
        return result


class SavePlan(Closed):
    conversation_id: UUID
    parent_version_id: UUID | None = None
    plan: ProductionPlan
