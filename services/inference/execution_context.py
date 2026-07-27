"""Request-scoped ExecutionContext + execution_id (Pass 1)."""
from __future__ import annotations

import uuid
from contextvars import ContextVar

from services.inference.contracts import ExecutionContext
from services.ops.request_context import get_request_id

_execution_context: ContextVar[ExecutionContext | None] = ContextVar(
    "ben_execution_context", default=None
)


def new_execution_id() -> str:
    return str(uuid.uuid4())


def get_execution_context() -> ExecutionContext | None:
    return _execution_context.get()


def set_execution_context(ctx: ExecutionContext | None) -> None:
    _execution_context.set(ctx)


def begin_execution_context(
    *,
    org_id: str | None,
    workspace_id: str | None,
    user_id: str | None = None,
    capability_key: str | None = None,
    pipeline: str = "unknown",
    provider: str | None = None,
    model: str | None = None,
    execution_id: str | None = None,
) -> ExecutionContext:
    ctx = ExecutionContext(
        request_id=get_request_id(),
        execution_id=execution_id or new_execution_id(),
        org_id=org_id,
        workspace_id=workspace_id,
        user_id=user_id,
        capability_key=capability_key,
        pipeline=pipeline,
        provider=provider,
        model=model,
        budget_mode="measure",
    )
    set_execution_context(ctx)
    return ctx


def require_execution_context(
    *,
    pipeline: str,
    org_id: str | None = None,
    workspace_id: str | None = None,
) -> ExecutionContext:
    """Return active context or create a measure-only fallback for bypass paths."""
    current = get_execution_context()
    if current is not None:
        return current
    return begin_execution_context(
        org_id=org_id,
        workspace_id=workspace_id,
        pipeline=pipeline,
    )
