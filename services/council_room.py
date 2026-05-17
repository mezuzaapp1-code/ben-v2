"""In-process Shared Response Room for a single council execution (v1)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from services.ops.idempotency import get_client_request_id

RoomStatus = Literal[
    "pending",
    "experts_running",
    "experts_complete",
    "synthesizing",
    "complete",
    "degraded",
    "failed",
]

ROOM_PENDING: RoomStatus = "pending"
ROOM_EXPERTS_RUNNING: RoomStatus = "experts_running"
ROOM_EXPERTS_COMPLETE: RoomStatus = "experts_complete"
ROOM_SYNTHESIZING: RoomStatus = "synthesizing"
ROOM_COMPLETE: RoomStatus = "complete"
ROOM_DEGRADED: RoomStatus = "degraded"
ROOM_FAILED: RoomStatus = "failed"


@dataclass
class RoomMember:
    expert: str
    provider: str
    model: str
    outcome: str
    response: str
    expert_index: int
    cost: float = 0.0

    def to_member(self) -> dict[str, Any]:
        return {
            "expert": self.expert,
            "provider": self.provider,
            "model": self.model,
            "outcome": self.outcome,
            "response": self.response,
            "expert_index": self.expert_index,
        }


@dataclass
class CouncilRoom:
    room_id: uuid.UUID
    question_id: str
    question_text: str
    thread_id: uuid.UUID | None = None
    status: RoomStatus = ROOM_PENDING
    members: list[RoomMember] = field(default_factory=list)
    synthesis: dict[str, Any] | None = None

    @classmethod
    def create(
        cls,
        *,
        question: str,
        thread_id: uuid.UUID | None = None,
        question_id: str | None = None,
    ) -> CouncilRoom:
        room_id = uuid.uuid4()
        qid = (question_id or get_client_request_id() or "").strip() or str(room_id)
        return cls(
            room_id=room_id,
            question_id=qid,
            question_text=question,
            thread_id=thread_id,
            status=ROOM_PENDING,
        )

    @property
    def room_id_str(self) -> str:
        return str(self.room_id)

    def mark_experts_running(self) -> None:
        self.status = ROOM_EXPERTS_RUNNING

    def add_member(self, member: RoomMember) -> None:
        self.members.append(member)

    def mark_experts_complete(self) -> None:
        self.status = ROOM_EXPERTS_COMPLETE

    def mark_synthesizing(self) -> None:
        self.status = ROOM_SYNTHESIZING

    def attach_synthesis(self, synthesis: dict[str, Any] | None) -> None:
        self.synthesis = synthesis

    def finalize_status(self) -> None:
        if not self.members:
            self.status = ROOM_FAILED
            return
        outcomes = [m.outcome for m in self.members]
        ok_count = sum(1 for o in outcomes if o == "ok")
        if ok_count == 0 and self.synthesis is None:
            self.status = ROOM_FAILED
        elif self.synthesis is None or any(o != "ok" for o in outcomes):
            self.status = ROOM_DEGRADED
        else:
            self.status = ROOM_COMPLETE

    def to_council_members(self) -> list[dict[str, Any]]:
        return [m.to_member() for m in self.members]

    def to_http_room(self) -> dict[str, Any]:
        return {
            "id": self.room_id_str,
            "question_id": self.question_id,
            "status": self.status,
            "member_count": len(self.members),
        }
