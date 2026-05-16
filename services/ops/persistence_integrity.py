"""Persistence integrity checks and governance helpers (v1; no sensitive payloads)."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from services.message_format import decode_message

IntegrityCode = Literal[
    "orphan_message",
    "cross_tenant_access",
    "missing_thread_id",
    "malformed_council_envelope",
    "duplicate_council_synthesis",
    "synthesis_without_experts",
    "legacy_plain_assistant",
    "invalid_role",
]

# Persistence ownership (normative; see docs/DATA_GOVERNANCE.md)
STORE_THREADS = "ben.threads"
STORE_MESSAGES = "ben.messages"
STORE_COUNCIL_TRANSCRIPT = "ben.messages (JSON envelopes)"
STORE_SYNTHESIS_KO = "ben.knowledge_objects"
STORE_COGNITIVE_EVENTS = "ben.cognitive_events (schema present; runtime unused v1)"
STORE_RUNTIME_STATE = "non-persistent (in-process idempotency + metrics)"

_COUNCIL_KINDS = frozenset({"council_expert", "council_synthesis"})
_VALID_OUTCOMES = frozenset({"ok", "degraded", "timeout", "error"})
_BEN_PREFIX = '{"ben":'


@dataclass(frozen=True)
class IntegrityFinding:
    code: IntegrityCode
    message: str


def check_cross_tenant_access(*, expected_org_id: uuid.UUID, resource_org_id: uuid.UUID) -> IntegrityFinding | None:
    if expected_org_id != resource_org_id:
        return IntegrityFinding(
            code="cross_tenant_access",
            message="Resource org_id does not match bound tenant scope",
        )
    return None


def validate_council_member(member: dict[str, Any]) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    if not isinstance(member, dict):
        return [IntegrityFinding("malformed_council_envelope", "Council member must be an object")]
    for key in ("expert", "provider", "model", "outcome"):
        if not str(member.get(key) or "").strip():
            findings.append(
                IntegrityFinding(
                    "malformed_council_envelope",
                    f"Council member missing required field: {key}",
                )
            )
    outcome = str(member.get("outcome") or "ok")
    if outcome not in _VALID_OUTCOMES:
        findings.append(
            IntegrityFinding(
                "malformed_council_envelope",
                "Council member outcome must be ok|degraded|timeout|error",
            )
        )
    return findings


def validate_council_envelope_content(content: str) -> list[IntegrityFinding]:
    if not content.startswith(_BEN_PREFIX):
        return []
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [IntegrityFinding("malformed_council_envelope", "Council JSON envelope is not valid JSON")]
    if not isinstance(data, dict) or data.get("ben") != 1:
        return [IntegrityFinding("malformed_council_envelope", "Council envelope missing ben marker")]
    kind = data.get("kind")
    if kind == "council_expert":
        for key in ("expert", "provider", "model", "outcome"):
            if not str(data.get(key) or "").strip():
                return [
                    IntegrityFinding(
                        "malformed_council_envelope",
                        f"council_expert envelope missing {key}",
                    )
                ]
        outcome = str(data.get("outcome") or "ok")
        if outcome not in _VALID_OUTCOMES:
            return [
                IntegrityFinding(
                    "malformed_council_envelope",
                    "council_expert outcome invalid",
                )
            ]
    elif kind == "council_synthesis":
        syn = data.get("synthesis")
        if not isinstance(syn, dict):
            return [IntegrityFinding("malformed_council_envelope", "council_synthesis missing synthesis object")]
    elif kind == "chat":
        pass
    return []


def audit_message_row(
    *,
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    message_org_id: uuid.UUID,
    message_thread_id: uuid.UUID,
    role: str,
    content: str,
) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    cross = check_cross_tenant_access(expected_org_id=org_id, resource_org_id=message_org_id)
    if cross:
        findings.append(cross)
    if message_thread_id != thread_id:
        findings.append(IntegrityFinding("orphan_message", "Message thread_id does not match requested thread"))
    if role not in ("user", "assistant"):
        findings.append(IntegrityFinding("invalid_role", "Message role must be user or assistant"))
    findings.extend(validate_council_envelope_content(content))
    if role == "assistant" and content and not content.startswith(_BEN_PREFIX):
        findings.append(
            IntegrityFinding(
                "legacy_plain_assistant",
                "Assistant message uses legacy plain text (tolerated on rehydrate)",
            )
        )
    return findings


def audit_decoded_thread_messages(messages: list[dict[str, Any]]) -> list[IntegrityFinding]:
    """Post-decode integrity scan for GET /api/threads/{id} payloads (safe fields only)."""
    findings: list[IntegrityFinding] = []
    synthesis_count = 0
    expert_count = 0
    for msg in messages:
        if msg.get("kind") == "council_synthesis":
            synthesis_count += 1
        elif msg.get("expert_outcome") is not None:
            expert_count += 1
    if synthesis_count > 1:
        findings.append(
            IntegrityFinding(
                "duplicate_council_synthesis",
                "Thread contains more than one council_synthesis message",
            )
        )
    if synthesis_count == 1 and expert_count == 0:
        findings.append(
            IntegrityFinding(
                "synthesis_without_experts",
                "Synthesis present without preceding expert rows",
            )
        )
    return findings


def audit_thread_messages_for_org(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    rows: list[Any],
) -> list[IntegrityFinding]:
    """Audit ORM Message rows before rehydration."""
    findings: list[IntegrityFinding] = []
    decoded: list[dict[str, Any]] = []
    for m in rows:
        mid = getattr(m, "thread_id", None)
        morg = getattr(m, "org_id", None)
        if mid is None:
            findings.append(IntegrityFinding("missing_thread_id", "Message row missing thread_id"))
            continue
        findings.extend(
            audit_message_row(
                org_id=org_id,
                thread_id=thread_id,
                message_org_id=morg,
                message_thread_id=mid,
                role=str(getattr(m, "role", "")),
                content=str(getattr(m, "content", "")),
            )
        )
        decoded.append(decode_message(str(getattr(m, "role", "")), str(getattr(m, "content", ""))))
    findings.extend(audit_decoded_thread_messages(decoded))
    return findings


def findings_to_safe_codes(findings: list[IntegrityFinding]) -> list[str]:
    """Deduplicated integrity codes for logs/API (no message bodies)."""
    seen: set[str] = set()
    out: list[str] = []
    for f in findings:
        if f.code not in seen:
            seen.add(f.code)
            out.append(f.code)
    return out
