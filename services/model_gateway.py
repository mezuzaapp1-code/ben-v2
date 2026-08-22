"""T03 Model Gateway: tier routing, per-provider circuit breaker, adapter dispatch."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, status

from services.chat_prompt import GLOBAL_CHAT_SYSTEM
from services.message_format import provider_display_label
from services.providers.anthropic_provider import ANTHROPIC_FAST_MODEL, ANTHROPIC_FLAGSHIP_MODEL
from services.providers.gemini_provider import GEMINI_FAST_MODEL
from services.providers.model_registry import assert_model_registered, resolve_api_model, token_rates
from services.providers.openai_provider import OPENAI_CHAT_FAST_MODEL, OPENAI_REASONING_MODEL
from services.providers.xai_provider import XAI_FLAGSHIP_MODEL
from services.providers.speaking_registry import (
    all_provider_ids,
    gateway_for_provider_id,
    normalize_speaking_provider_id,
)
from services.attendance_service import calculate_attendance_pay, parse_worker_hours_text
from services.cost_engineering_service import (
    build_historical_baseline,
    detect_cost_anomalies,
    new_tender_id,
    parse_supplier_bid,
)
from services.native_tools_service import create_ledger_entry, list_ledger_entries, list_project_members
from services.upskilling_service import (
    STATUTORY_ASSET,
    TRAINABLE_ORIENTATION,
    build_proctor_session,
    derive_job_requirements,
    scan_certification_gaps,
    simulate_training_roi,
)
from services.project_memory_service import compute_location_logistics, DEFAULT_BASE_LOCATION
from services.invoice_tools import export_ledger_to_accountant
from services.inference.gateway_meter import (
    account_provider_attempt,
    classify_call_outcome,
)
from services.inference.usage_normalize import normalize_openai_usage, usage_missing
from services.ops.request_context import attach_request_id
from services.project_copilot_tools import attach_mutated_state
from services.providers.base_provider import ProviderStreamEnd
from services.project_memory_service import load_project_memory, save_project_memory
from services.project_copilot_tools import (
    get_cash_flow_forecast,
    get_lifecycle_overview,
    initiate_quotation_flow,
    issue_customer_invoice,
    process_captured_invoice,
    process_credit_memo,
)
from services.basalt_content_schema import build_corporate_content, normalize_lang
from services.basalt_public_service import (
    fetch_active_job_openings,
    fetch_verified_portfolio,
    list_pending_applications,
    mark_application_reviewed,
    resolve_basalt_org_id,
    submit_candidate_application,
)
from services.tactical_copilot_tools import (
    fetch_site_intelligence,
    initiate_tactical_quotation,
    log_daily_operations,
    onboard_project_member,
)
from services.ops.failure_classification import classify_failure
from services.ops.structured_log import log_info, log_warning
from services.providers.call_diagnostics import estimate_request_tokens
from services.ops.timeouts import CHAT_EXPLICIT_PROVIDER_TIMEOUT_S, HTTP_CLIENT_TIMEOUT_S
from services.providers import gateway_provider_api_key_env, get_gateway_provider
from services.providers.provider_errors import format_chat_provider_error, sanitize_provider_error_message

_CHAIN = ("openai", "anthropic", "google")
_FALLBACK = {
    "openai": OPENAI_CHAT_FAST_MODEL,
    "anthropic": ANTHROPIC_FAST_MODEL,
    "google": GEMINI_FAST_MODEL,
}
_RATES: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", OPENAI_CHAT_FAST_MODEL): token_rates("openai", OPENAI_CHAT_FAST_MODEL),
    ("openai", OPENAI_REASONING_MODEL): token_rates("openai", OPENAI_REASONING_MODEL),
    ("anthropic", ANTHROPIC_FLAGSHIP_MODEL): token_rates("anthropic", ANTHROPIC_FLAGSHIP_MODEL),
    ("anthropic", ANTHROPIC_FAST_MODEL): token_rates("anthropic", ANTHROPIC_FAST_MODEL),
    ("google", GEMINI_FAST_MODEL): token_rates("google", GEMINI_FAST_MODEL),
}


ALLOWED_CHAT_PROVIDER_IDS = all_provider_ids()


def _chat_provider_to_gateway(provider_id: str) -> str:
    return gateway_for_provider_id(provider_id)


_CB: dict[str, dict[str, float | int]] = {}


def _tier_primary(tier: str) -> tuple[str, str]:
    t = (tier or "free").lower()
    if t == "pro":
        return "anthropic", ANTHROPIC_FLAGSHIP_MODEL
    if t == "enterprise":
        return "openai", OPENAI_REASONING_MODEL
    return "openai", OPENAI_CHAT_FAST_MODEL


def normalize_chat_provider_id(raw: str | None) -> str | None:
    """UI provider id for /chat; None when omitted (tier routing)."""
    return normalize_speaking_provider_id(raw)


def normalize_model_override(raw: str | None) -> str | None:
    """Canonical BEN model id from UI; None when omitted."""
    if raw is None:
        return None
    model = (raw or "").strip()
    if not model:
        return None
    if len(model) > 128:
        raise ValueError("model_override must be at most 128 characters")
    return model


def resolve_dispatch_model(gateway_prov: str, model: str) -> str:
    """Validate canonical model against registry and map to provider API id."""
    canonical = assert_model_registered(gateway_prov, model)
    return resolve_api_model(gateway_prov, canonical)


def validate_chat_model_override(provider_id: str | None, model_override: str | None) -> None:
    """Fail fast before provider HTTP when UI sends an unknown model for the provider."""
    if not provider_id or not model_override:
        return
    gateway = _chat_provider_to_gateway(provider_id)
    resolve_dispatch_model(gateway, model_override)


def _model_for_gateway_provider(gateway_prov: str, tier: str) -> str:
    t = (tier or "free").lower()
    if gateway_prov == "openai":
        if t == "enterprise":
            return OPENAI_REASONING_MODEL
        return OPENAI_CHAT_FAST_MODEL
    if gateway_prov == "anthropic":
        if t in ("pro", "enterprise"):
            return os.getenv("ANTHROPIC_MODEL", "").strip() or ANTHROPIC_FLAGSHIP_MODEL
        return os.getenv("ANTHROPIC_MODEL", "").strip() or ANTHROPIC_FAST_MODEL
    if gateway_prov == "xai":
        return XAI_FLAGSHIP_MODEL
    if gateway_prov == "google":
        return (
            os.getenv("GEMINI_MODEL", "").strip()
            or os.getenv("GOOGLE_MODEL", "").strip()
            or GEMINI_FAST_MODEL
        )
    raise ValueError(f"unknown gateway provider: {gateway_prov}")


def _attempts(
    tier: str,
    *,
    provider_id: str | None = None,
    model_override: str | None = None,
) -> list[tuple[str, str]]:
    if provider_id:
        gateway = _chat_provider_to_gateway(provider_id)
        if model_override:
            return [(gateway, model_override)]
        return [(gateway, _model_for_gateway_provider(gateway, tier))]
    t = (tier or "free").lower()
    if t == "free":
        return [("openai", OPENAI_CHAT_FAST_MODEL)]
    p, m = _tier_primary(tier)
    out = [(p, m)]
    for x in _CHAIN:
        if x != p:
            out.append((x, _FALLBACK[x]))
    return out


def _chat_http_timeout_s(*, provider_id: str | None) -> float:
    if provider_id:
        return CHAT_EXPLICIT_PROVIDER_TIMEOUT_S
    return HTTP_CLIENT_TIMEOUT_S


def _cb_ready(name: str) -> bool:
    s = _CB.setdefault(name, {"n": 0, "until": 0.0})
    now = time.monotonic()
    if now < float(s["until"]):
        return False
    if s["until"]:
        s["n"], s["until"] = 0, 0.0
    return True


def _cb_ok(name: str) -> None:
    _CB[name] = {"n": 0, "until": 0.0}


def _cb_fail(name: str) -> None:
    s = _CB.setdefault(name, {"n": 0, "until": 0.0})
    s["n"] = int(s["n"]) + 1
    if int(s["n"]) >= 3:
        s["until"] = time.monotonic() + 60.0
        s["n"] = 0


def reset_circuit_breakers_for_tests() -> None:
    """Clear gateway circuit-breaker state between unit tests."""
    _CB.clear()


def _cost(prov: str, model: str, inp: int, out: int) -> float:
    ir, or_ = token_rates(prov, model, prompt_tokens=inp)
    return ir * inp + or_ * out


def _missing_key_message(*, provider_id: str | None, gateway_prov: str) -> str:
    if provider_id:
        label = provider_display_label(provider_id) or gateway_prov.title()
        return f"{label} is not configured (missing API key)"
    return f"{gateway_prov} is not configured (missing API key)"


def _ui_cost_usd(accounted: dict[str, Any] | None, *, fallback_prov: str = "", fallback_model: str = "", pi: int = 0, po: int = 0) -> float:
    if accounted and accounted.get("cost_usd") is not None:
        return round(float(accounted["cost_usd"]), 6)
    if fallback_prov and fallback_model and (pi or po):
        return round(_cost(fallback_prov, fallback_model, pi, po), 6)
    return 0.0


async def route_request(
    message: str,
    tenant_id: str,
    tier: str,
    *,
    provider_id: str | None = None,
    model_override: str | None = None,
    system: str | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    timeout_s = _chat_http_timeout_s(provider_id=provider_id)
    last: BaseException | None = None
    last_prov: str = ""
    last_accounted: dict[str, Any] | None = None
    attempts = _attempts(tier, provider_id=provider_id, model_override=model_override)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=5.0)) as cx:
        for prov, model in attempts:
            key_env = gateway_provider_api_key_env(prov)
            if not (os.getenv(key_env) or "").strip():
                if provider_id and _chat_provider_to_gateway(provider_id) == prov:
                    ms = (time.perf_counter() - t0) * 1000.0
                    await account_provider_attempt(
                        provider=prov,
                        model=model,
                        api_model=None,
                        outcome="rejected",
                        usage=usage_missing(),
                        latency_ms=ms,
                        stream=False,
                        error_class="MissingAPIKey",
                        org_id=tenant_id,
                        pipeline="chat",
                        extras={"reason": "missing_api_key"},
                    )
                    return {
                        "content": _missing_key_message(provider_id=provider_id, gateway_prov=prov),
                        "model_used": "",
                        "provider_used": prov,
                        "tokens": 0,
                        "cost_usd": 0.0,
                        "latency_ms": round(ms, 2),
                    }
                continue
            if not _cb_ready(prov):
                continue
            attempt_t0 = time.perf_counter()
            try:
                api_model = resolve_dispatch_model(prov, model)
            except ValueError as e:
                ms = (time.perf_counter() - t0) * 1000.0
                await account_provider_attempt(
                    provider=prov,
                    model=model,
                    api_model=None,
                    outcome="rejected",
                    usage=usage_missing(),
                    latency_ms=ms,
                    stream=False,
                    error_class=type(e).__name__,
                    org_id=tenant_id,
                    pipeline="chat",
                    extras={"reason": "invalid_model"},
                )
                return {
                    "content": str(e),
                    "model_used": "",
                    "provider_used": prov,
                    "tokens": 0,
                    "cost_usd": 0.0,
                    "latency_ms": round(ms, 2),
                }
            try:
                adapter = get_gateway_provider(prov)
                send_result = await adapter.send_message(
                    cx,
                    model=api_model,
                    message=message,
                    tenant_id=tenant_id,
                    system=system or GLOBAL_CHAT_SYSTEM,
                )
                content = send_result.content
                tok = send_result.total_tokens
                pi = send_result.prompt_tokens
                po = send_result.completion_tokens
                _cb_ok(prov)
                attempt_ms = (time.perf_counter() - attempt_t0) * 1000.0
                last_accounted = await account_provider_attempt(
                    provider=prov,
                    model=model,
                    api_model=api_model,
                    outcome="success",
                    usage=send_result.usage or usage_missing(),
                    latency_ms=round(attempt_ms, 2),
                    stream=False,
                    provider_request_id=send_result.provider_request_id,
                    finish_reason=send_result.finish_reason,
                    org_id=tenant_id,
                    pipeline="chat",
                )
                if prov != "anthropic":
                    log_info(
                        "chat provider adapter call completed",
                        subsystem="model_gateway",
                        provider=prov,
                        model=model,
                        duration_ms=int(attempt_ms),
                        operation="provider_send_message",
                        outcome="ok",
                        timeout_s=timeout_s,
                        request_chars=len(message),
                        request_tokens_est=estimate_request_tokens(message=message),
                        response_tokens=tok,
                        prompt_tokens=pi,
                        completion_tokens=po,
                    )
                if send_result.completion_truncated:
                    log_info(
                        "chat provider completion truncated",
                        subsystem="model_gateway",
                        provider=prov,
                        model=model,
                        operation="provider_send_message",
                        outcome="truncated",
                        completion_truncated=True,
                        truncation_detected=True,
                        completion_tokens=po,
                    )
                ms = (time.perf_counter() - t0) * 1000.0
                return {
                    "content": content,
                    "model_used": model,
                    "provider_used": prov,
                    "tokens": tok,
                    "cost_usd": _ui_cost_usd(last_accounted, fallback_prov=prov, fallback_model=model, pi=pi, po=po),
                    "latency_ms": round(ms, 2),
                    "completion_truncated": send_result.completion_truncated,
                    "execution_id": last_accounted.get("execution_id"),
                    "call_id": last_accounted.get("call_id"),
                    "usage_status": last_accounted.get("usage_status"),
                    "pricing_version": last_accounted.get("pricing_version"),
                }
            except BaseException as e:
                last = e
                last_prov = prov
                elapsed_ms = (time.perf_counter() - attempt_t0) * 1000.0
                last_accounted = await account_provider_attempt(
                    provider=prov,
                    model=model,
                    api_model=api_model,
                    outcome=classify_call_outcome(e),
                    usage=usage_missing(),
                    latency_ms=round(elapsed_ms, 2),
                    stream=False,
                    error_class=type(e).__name__,
                    org_id=tenant_id,
                    pipeline="chat",
                )
                if prov != "anthropic":
                    log_warning(
                        "chat provider adapter call failed",
                        subsystem="model_gateway",
                        provider=prov,
                        category=classify_failure(e),
                        exc=e,
                        duration_ms=int(elapsed_ms),
                        operation="provider_send_message",
                        outcome="error",
                        model=model,
                        timeout_s=timeout_s,
                        request_chars=len(message),
                        request_tokens_est=estimate_request_tokens(message=message),
                        error_class=type(e).__name__,
                        error_message=sanitize_provider_error_message(e),
                    )
                _cb_fail(prov)
    ms = (time.perf_counter() - t0) * 1000.0
    if last and last_prov:
        err = format_chat_provider_error(last_prov, last, timeout_s=timeout_s)
    elif last:
        err = format_chat_provider_error("", last, timeout_s=timeout_s)
    else:
        err = "No provider available"
    return {
        "content": err,
        "model_used": "",
        "provider_used": last_prov,
        "tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": round(ms, 2),
        "execution_id": (last_accounted or {}).get("execution_id"),
        "call_id": (last_accounted or {}).get("call_id"),
    }


async def route_request_stream(
    message: str,
    tenant_id: str,
    tier: str,
    *,
    provider_id: str | None = None,
    model_override: str | None = None,
    system: str | None = None,
) -> AsyncIterator[tuple[str, str, str]]:
    """Stream raw model tokens: yields (text_chunk, model, provider).

    Each provider HTTP attempt writes exactly one InferenceCallRecord.
    Final accounted summary is available via get_last_accounted_call().
    """
    timeout_s = _chat_http_timeout_s(provider_id=provider_id)
    attempts = _attempts(tier, provider_id=provider_id, model_override=model_override)
    last: BaseException | None = None
    last_prov = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=5.0)) as cx:
        for prov, model in attempts:
            key_env = gateway_provider_api_key_env(prov)
            if not (os.getenv(key_env) or "").strip():
                if provider_id and _chat_provider_to_gateway(provider_id) == prov:
                    await account_provider_attempt(
                        provider=prov,
                        model=model,
                        api_model=None,
                        outcome="rejected",
                        usage=usage_missing(),
                        latency_ms=0.0,
                        stream=True,
                        error_class="MissingAPIKey",
                        org_id=tenant_id,
                        pipeline="chat",
                        extras={"reason": "missing_api_key"},
                    )
                    yield (_missing_key_message(provider_id=provider_id, gateway_prov=prov), "", prov)
                    return
                continue
            if not _cb_ready(prov):
                continue
            try:
                api_model = resolve_dispatch_model(prov, model)
            except ValueError as e:
                await account_provider_attempt(
                    provider=prov,
                    model=model,
                    api_model=None,
                    outcome="rejected",
                    usage=usage_missing(),
                    latency_ms=0.0,
                    stream=True,
                    error_class=type(e).__name__,
                    org_id=tenant_id,
                    pipeline="chat",
                    extras={"reason": "invalid_model"},
                )
                yield (str(e), "", prov)
                return
            attempt_t0 = time.perf_counter()
            streamed_any = False
            stream_end: ProviderStreamEnd | None = None
            attempt_accounted = False
            try:
                adapter = get_gateway_provider(prov)
                async for item in adapter.stream_message(
                    cx,
                    model=api_model,
                    message=message,
                    tenant_id=tenant_id,
                    system=system or GLOBAL_CHAT_SYSTEM,
                ):
                    if isinstance(item, ProviderStreamEnd):
                        stream_end = item
                        continue
                    if item:
                        streamed_any = True
                        yield (item, model, prov)
                elapsed_ms = (time.perf_counter() - attempt_t0) * 1000.0
                await account_provider_attempt(
                    provider=prov,
                    model=model,
                    api_model=api_model,
                    outcome="success",
                    usage=(stream_end.usage if stream_end else usage_missing()),
                    latency_ms=round(elapsed_ms, 2),
                    stream=True,
                    provider_request_id=stream_end.provider_request_id if stream_end else None,
                    finish_reason=stream_end.finish_reason if stream_end else None,
                    org_id=tenant_id,
                    pipeline="chat",
                )
                attempt_accounted = True
                _cb_ok(prov)
                return
            except BaseException as e:
                last = e
                last_prov = prov
                if not attempt_accounted:
                    elapsed_ms = (time.perf_counter() - attempt_t0) * 1000.0
                    await account_provider_attempt(
                        provider=prov,
                        model=model,
                        api_model=api_model,
                        outcome=classify_call_outcome(e, streamed_any=streamed_any),
                        usage=(stream_end.usage if stream_end else usage_missing()),
                        latency_ms=round(elapsed_ms, 2),
                        stream=True,
                        provider_request_id=stream_end.provider_request_id if stream_end else None,
                        finish_reason=stream_end.finish_reason if stream_end else None,
                        error_class=type(e).__name__,
                        org_id=tenant_id,
                        pipeline="chat",
                    )
                    attempt_accounted = True
                _cb_fail(prov)
                if isinstance(e, (asyncio.CancelledError, GeneratorExit)):
                    raise
    if last and last_prov:
        err = format_chat_provider_error(last_prov, last, timeout_s=timeout_s)
    elif last:
        err = format_chat_provider_error("", last, timeout_s=timeout_s)
    else:
        err = "No provider available"
    yield (err, "", last_prov)


async def accounted_openai_chat_completion(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tenant_id: str,
    model: str,
    pipeline: str = "project_agent",
) -> dict[str, Any]:
    """OpenAI chat.completions with mandatory gateway accounting (tool-loop path)."""
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        await account_provider_attempt(
            provider="openai",
            model=model,
            api_model=model,
            outcome="rejected",
            usage=usage_missing(),
            latency_ms=0.0,
            stream=False,
            error_class="MissingAPIKey",
            org_id=tenant_id,
            pipeline=pipeline,
            extras={"reason": "missing_api_key"},
        )
        raise RuntimeError("OPENAI_API_KEY is not configured for project agent tools.")

    payload: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    attempt_t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "X-BEN-Tenant": tenant_id},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        elapsed_ms = (time.perf_counter() - attempt_t0) * 1000.0
        usage = normalize_openai_usage(data.get("usage"))
        choice = (data.get("choices") or [{}])[0]
        await account_provider_attempt(
            provider="openai",
            model=model,
            api_model=model,
            outcome="success",
            usage=usage,
            latency_ms=round(elapsed_ms, 2),
            stream=False,
            provider_request_id=str(data.get("id") or "") or None,
            finish_reason=str(choice.get("finish_reason") or "") or None,
            org_id=tenant_id,
            pipeline=pipeline,
            extras={"tools": bool(tools)},
        )
        return data
    except BaseException as e:
        elapsed_ms = (time.perf_counter() - attempt_t0) * 1000.0
        await account_provider_attempt(
            provider="openai",
            model=model,
            api_model=model,
            outcome=classify_call_outcome(e),
            usage=usage_missing(),
            latency_ms=round(elapsed_ms, 2),
            stream=False,
            error_class=type(e).__name__,
            org_id=tenant_id,
            pipeline=pipeline,
            extras={"tools": bool(tools)},
        )
        raise


# ---------------------------------------------------------------------------
# Native tool registry (LLM function calling + REST /tools/execute)
# ---------------------------------------------------------------------------

NATIVE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "initiate_quotation_flow",
            "description": (
                "Start or advance the guided quotation state machine: Location → Materials/Suppliers "
                "→ Risk Mitigation → Labor/Execution. Infuses travel and subsistence overhead from project memory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["start", "advance"], "description": "Start or advance the flow."},
                    "step_key": {
                        "type": "string",
                        "enum": ["location", "materials_suppliers", "risk_mitigation", "labor_execution"],
                    },
                    "step_data": {"type": "object", "description": "Captured data for the active step."},
                    "target_location": {"type": "string", "description": "Project site location (e.g. Shoham)."},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_captured_invoice",
            "description": "OCR-simulated invoice capture; logs EXPENSE to financial_ledger with vendor matching.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "image_url": {"type": "string"},
                    "filename": {"type": "string"},
                    "vendor_hint": {"type": "string"},
                    "amount_hint": {"type": "number"},
                    "currency_hint": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_credit_memo",
            "description": "Extract credit memo from receipt/PDF and log positive INCOME adjustment (vendor refund).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "image_url": {"type": "string"},
                    "filename": {"type": "string"},
                    "vendor_hint": {"type": "string"},
                    "amount_hint": {"type": "number"},
                    "currency_hint": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_customer_invoice",
            "description": "Issue milestone-based customer billing as pending INCOME on financial_ledger.",
            "parameters": {
                "type": "object",
                "properties": {
                    "milestone": {"type": "string"},
                    "amount": {"type": "number"},
                    "currency": {"type": "string"},
                    "due_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                    "description": {"type": "string"},
                },
                "required": ["milestone", "amount"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cash_flow_forecast",
            "description": (
                "Aggregate pending and finalized income/expenses; predict cash runway and safety triggers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "horizon_weeks": {"type": "integer", "description": "Forecast horizon (4-24 weeks)."},
                    "safety_threshold_nis": {"type": "number"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_lifecycle_overview",
            "description": "Lifecycle timestamps, days elapsed, and actual vs estimated margin variance.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_ledger_to_accountant",
            "description": "Aggregate financial_ledger into an accountant-ready summary report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["summary", "markdown"]},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_site_intelligence",
            "description": (
                "Query simulated Ministry of Labor / data.gov.il registries for site managers, "
                "crane permits, active safety orders, and enforcement penalty histories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "site_address": {"type": "string", "description": "Target construction site address or city."},
                    "contractor_name": {"type": "string", "description": "Registered contractor name."},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "initiate_tactical_quotation",
            "description": (
                "Build tactical quote using government site intelligence, hazard mapping, "
                "Or Akiva logistics, subsistence allowances, and safety premiums."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "site_address": {"type": "string"},
                    "contractor_name": {"type": "string"},
                    "base_quote_nis": {"type": "number"},
                    "crew_size": {"type": "integer"},
                    "duration_days": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "onboard_project_member",
            "description": (
                "Onboard employee/vendor with insurance and contract compliance checks; "
                "blocks assignment when safety profile is invalid."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "member_type": {"type": "string", "enum": ["EMPLOYEE", "VENDOR"]},
                    "role": {"type": "string"},
                    "hourly_rate": {"type": "number"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "insurance_policy_id": {"type": "string"},
                    "contract_valid_until": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                    "safety_profile_score": {"type": "integer", "description": "0-100 safety score"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_worker_response",
            "description": (
                "Parse flexible text-based worker hour reports, detect late arrivals / early departures "
                "/ partial shifts against standard shift (default 07:00-17:00), and build approval time-cards."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "worker_name": {"type": "string"},
                    "response_text": {"type": "string", "description": "Natural-language hours report from worker."},
                    "shift_start": {"type": "string", "description": "Standard shift start HH:MM (default 07:00)."},
                    "shift_end": {"type": "string", "description": "Standard shift end HH:MM (default 17:00)."},
                    "hourly_rate_nis": {"type": "number"},
                    "approve": {"type": "boolean", "description": "Approve a pending time-card."},
                    "time_card_id": {"type": "string", "description": "Target time-card UUID for approve/edit."},
                    "adjusted_hours": {"type": "number", "description": "Supervisor-adjusted decimal hours on approve."},
                },
                "required": ["worker_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_supplier_tender",
            "description": (
                "Parse supplier bids into a Cost Engineering matrix (material, logistics, operational, "
                "margin/risk), detect anomalies vs historical project metrics, and accept/counter offers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["analyze", "accept_bid", "counter_offer"],
                        "description": "Analyze bid, accept bid, or submit counter-offer.",
                    },
                    "bid_text": {"type": "string", "description": "Raw supplier quote or tender text."},
                    "supplier_name": {"type": "string"},
                    "total_bid_nis": {"type": "number"},
                    "tender_id": {"type": "string", "description": "Tender UUID for accept/counter actions."},
                    "counter_offer_nis": {"type": "number", "description": "Counter-offer amount in NIS."},
                    "materials": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Materials to add to next-day shopping log.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "define_tactical_job_requirements",
            "description": (
                "Break project engineering scope into statutory regulatory prerequisites "
                "vs localized up-trainable safety orientations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "engineering_scope": {
                        "type": "string",
                        "description": "Project engineering scope narrative.",
                    },
                },
                "required": ["engineering_scope"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_training_day_roi",
            "description": (
                "Scan member certification gaps, compare onsite proctor vs offsite training ROI "
                "with home-base transit, and optionally schedule a proctor session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["simulate", "schedule_proctor_session"],
                    },
                    "engineering_scope": {"type": "string"},
                    "scheduled_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                    "onsite_proctor_day_nis": {"type": "number"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_daily_operations",
            "description": (
                "Log clocked hours, friction events, and next-day material needs; "
                "synthesize operational briefing with fuel and subsistence overheads."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "clocked_hours": {"type": "number"},
                    "friction_events": {"type": "array", "items": {"type": "string"}},
                    "next_day_materials": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_basalt_application",
            "description": (
                "Review external basalt.co.il candidate applications: inbox flash, "
                "approve & onboard, or schedule training day for uncertified skills."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["inbox", "approve_onboard", "schedule_training"],
                    },
                    "application_id": {"type": "string"},
                    "scheduled_date": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
]

NATIVE_TOOL_NAMES: frozenset[str] = frozenset(
    t["function"]["name"] for t in NATIVE_TOOL_DEFINITIONS if t.get("type") == "function"
)


async def execute_native_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Dispatch a registered native tool by name with tenant + project scope."""
    if tool_name not in NATIVE_TOOL_NAMES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown native tool: {tool_name}")

    args = arguments or {}

    if tool_name == "initiate_quotation_flow":
        return await initiate_quotation_flow(
            org_id,
            project_id,
            action=args.get("action") or "start",
            step_key=args.get("step_key"),
            step_data=args.get("step_data"),
            target_location=args.get("target_location"),
        )

    if tool_name == "process_captured_invoice":
        return await process_captured_invoice(
            org_id,
            project_id,
            file_path=args.get("file_path"),
            image_url=args.get("image_url"),
            filename=args.get("filename"),
            vendor_hint=args.get("vendor_hint"),
            amount_hint=args.get("amount_hint"),
            currency_hint=args.get("currency_hint"),
        )

    if tool_name == "process_credit_memo":
        return await process_credit_memo(
            org_id,
            project_id,
            file_path=args.get("file_path"),
            image_url=args.get("image_url"),
            filename=args.get("filename"),
            vendor_hint=args.get("vendor_hint"),
            amount_hint=args.get("amount_hint"),
            currency_hint=args.get("currency_hint"),
        )

    if tool_name == "issue_customer_invoice":
        return await issue_customer_invoice(
            org_id,
            project_id,
            milestone=args.get("milestone") or "Milestone",
            amount=float(args.get("amount") or 0),
            currency=args.get("currency") or "ILS",
            due_date=args.get("due_date"),
            description=args.get("description"),
        )

    if tool_name == "get_cash_flow_forecast":
        return await get_cash_flow_forecast(
            org_id,
            project_id,
            horizon_weeks=int(args.get("horizon_weeks") or 8),
            safety_threshold_nis=float(args.get("safety_threshold_nis") or 5000),
        )

    if tool_name == "get_lifecycle_overview":
        return await get_lifecycle_overview(org_id, project_id)

    if tool_name == "export_ledger_to_accountant":
        return await export_ledger_to_accountant(
            org_id,
            project_id,
            format=args.get("format") or "summary",
        )

    if tool_name == "fetch_site_intelligence":
        return await fetch_site_intelligence(
            org_id,
            project_id,
            site_address=args.get("site_address"),
            contractor_name=args.get("contractor_name"),
        )

    if tool_name == "initiate_tactical_quotation":
        return await initiate_tactical_quotation(
            org_id,
            project_id,
            site_address=args.get("site_address"),
            contractor_name=args.get("contractor_name"),
            base_quote_nis=args.get("base_quote_nis"),
            crew_size=args.get("crew_size"),
            duration_days=args.get("duration_days"),
        )

    if tool_name == "onboard_project_member":
        return await onboard_project_member(
            org_id,
            project_id,
            name=args.get("name") or "",
            member_type=args.get("member_type") or "EMPLOYEE",
            role=args.get("role"),
            hourly_rate=args.get("hourly_rate"),
            email=args.get("email"),
            phone=args.get("phone"),
            insurance_policy_id=args.get("insurance_policy_id"),
            contract_valid_until=args.get("contract_valid_until"),
            safety_profile_score=args.get("safety_profile_score"),
        )

    if tool_name == "log_daily_operations":
        return await log_daily_operations(
            org_id,
            project_id,
            clocked_hours=args.get("clocked_hours"),
            friction_events=args.get("friction_events"),
            next_day_materials=args.get("next_day_materials"),
            notes=args.get("notes"),
        )

    if tool_name == "process_worker_response":
        return await process_worker_response(
            org_id,
            project_id,
            worker_name=args.get("worker_name") or "",
            response_text=args.get("response_text"),
            shift_start=args.get("shift_start"),
            shift_end=args.get("shift_end"),
            hourly_rate_nis=args.get("hourly_rate_nis"),
            approve=bool(args.get("approve")),
            time_card_id=args.get("time_card_id"),
            adjusted_hours=args.get("adjusted_hours"),
        )

    if tool_name == "analyze_supplier_tender":
        return await analyze_supplier_tender(
            org_id,
            project_id,
            action=args.get("action") or "analyze",
            bid_text=args.get("bid_text"),
            supplier_name=args.get("supplier_name"),
            total_bid_nis=args.get("total_bid_nis"),
            tender_id=args.get("tender_id"),
            counter_offer_nis=args.get("counter_offer_nis"),
            materials=args.get("materials"),
        )

    if tool_name == "define_tactical_job_requirements":
        return await define_tactical_job_requirements(
            org_id,
            project_id,
            engineering_scope=args.get("engineering_scope") or "",
        )

    if tool_name == "simulate_training_day_roi":
        return await simulate_training_day_roi(
            org_id,
            project_id,
            action=args.get("action") or "simulate",
            engineering_scope=args.get("engineering_scope"),
            scheduled_date=args.get("scheduled_date"),
            onsite_proctor_day_nis=args.get("onsite_proctor_day_nis"),
        )

    if tool_name == "review_basalt_application":
        return await review_basalt_application(
            org_id,
            project_id,
            action=args.get("action") or "inbox",
            application_id=args.get("application_id"),
            scheduled_date=args.get("scheduled_date"),
        )

    raise HTTPException(status.HTTP_404_NOT_FOUND, f"Native tool not implemented: {tool_name}")


def llm_tools_for_thread_session(
    *,
    thread_id: uuid.UUID | str | None,
    provider_id: str | None = None,
) -> list[dict[str, Any]] | None:
    """Return project filesystem tools for project_setup threads only; None for lean regular chat."""
    from services.project_tool_router import conditional_project_tools

    return conditional_project_tools(thread_id=thread_id, provider_id=provider_id)


# ---------------------------------------------------------------------------
# Attendance parsing — flexible worker hour responses vs standard shift
# ---------------------------------------------------------------------------

_DEFAULT_HOURLY_NIS = 65.0


async def process_worker_response(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    worker_name: str,
    response_text: str | None = None,
    shift_start: str | None = None,
    shift_end: str | None = None,
    hourly_rate_nis: float | None = None,
    approve: bool = False,
    time_card_id: str | None = None,
    adjusted_hours: float | None = None,
) -> dict[str, Any]:
    """
    Parse worker hour text, flag LATE_ARRIVAL / EARLY_DEPARTURE / PARTIAL_SHIFT,
    and produce a daily attendance approval time-card with wage + subsistence proration.
    """
    name = (worker_name or "").strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "worker_name is required")

    matrix = await load_project_memory(org_id, project_id)
    schedule = matrix.get("shift_schedule") or {"start": "07:00", "end": "17:00"}
    std_start = (shift_start or schedule.get("start") or "07:00").strip()
    std_end = (shift_end or schedule.get("end") or "17:00").strip()
    matrix["shift_schedule"] = {"start": std_start, "end": std_end}

    attendance_log: list[dict[str, Any]] = list(matrix.get("attendance_log") or [])
    today = date.today().isoformat()
    rate = float(hourly_rate_nis or _DEFAULT_HOURLY_NIS)

    if approve and time_card_id:
        card = next((c for c in attendance_log if c.get("id") == time_card_id), None)
        if card is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Time-card not found")
        hours = float(adjusted_hours if adjusted_hours is not None else card.get("hours_worked") or 0)
        std_hours = float(card.get("standard_hours") or 10)
        card["hours_worked"] = round(hours, 2)
        card["status"] = "approved"
        card["approved_at"] = datetime.now(timezone.utc).isoformat()
        if adjusted_hours is not None:
            card["operational_flags"] = list(card.get("operational_flags") or [])
            card["supervisor_adjusted"] = True
        card["pay"] = calculate_attendance_pay(
            hours_worked=hours,
            standard_hours=std_hours,
            hourly_rate_nis=rate,
            daily_subsistence_nis=float(
                matrix.get("subsistence", {}).get("daily_allowance_nis") or 80
            ),
            operational_flags=card.get("operational_flags"),
            partial_shift_ratio=float(card.get("partial_shift_ratio") or 1.0),
        )
        matrix["attendance_log"] = attendance_log
        await save_project_memory(org_id, project_id, matrix)
        payload = {
            "tool": "process_worker_response",
            "action": "approved",
            "time_card": card,
            "attendance_summary": _attendance_summary(attendance_log, today),
        }
        return attach_request_id(attach_mutated_state("process_worker_response", payload))

    if not (response_text or "").strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "response_text is required unless approving an existing time-card",
        )

    try:
        parsed = parse_worker_hours_text(
            response_text,
            shift_start=std_start,
            shift_end=std_end,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e

    pay = calculate_attendance_pay(
        hours_worked=parsed["hours_worked"],
        standard_hours=parsed["standard_hours"],
        hourly_rate_nis=rate,
        daily_subsistence_nis=float(matrix.get("subsistence", {}).get("daily_allowance_nis") or 80),
        operational_flags=parsed["operational_flags"],
        partial_shift_ratio=parsed["partial_shift_ratio"],
    )

    time_card = {
        "id": str(uuid.uuid4()),
        "date": today,
        "worker_name": name,
        "poll_channel": "sms_whatsapp",
        "status": "pending",
        "hours_worked": parsed["hours_worked"],
        "standard_hours": parsed["standard_hours"],
        "shift_start": parsed["shift_start"],
        "shift_end": parsed["shift_end"],
        "arrival_time": parsed.get("arrival_time"),
        "departure_time": parsed.get("departure_time"),
        "variance_minutes": parsed["variance_minutes"],
        "operational_flags": parsed["operational_flags"],
        "partial_shift_ratio": parsed["partial_shift_ratio"],
        "reason_hints": parsed.get("reason_hints") or [],
        "parsed_from": parsed["parsed_from"],
        "pay": pay,
    }
    attendance_log.append(time_card)
    matrix["attendance_log"] = attendance_log[-60:]
    await save_project_memory(org_id, project_id, matrix)

    payload = {
        "tool": "process_worker_response",
        "action": "parsed",
        "time_card": time_card,
        "attendance_summary": _attendance_summary(attendance_log, today),
        "requires_approval": bool(time_card["operational_flags"]),
    }
    return attach_request_id(attach_mutated_state("process_worker_response", payload))


def _attendance_summary(attendance_log: list[dict[str, Any]], today: str) -> dict[str, Any]:
    today_cards = [c for c in attendance_log if c.get("date") == today]
    flagged = [c for c in today_cards if c.get("operational_flags")]
    pending = [c for c in today_cards if c.get("status") == "pending"]
    late = [c for c in today_cards if "LATE_ARRIVAL" in (c.get("operational_flags") or [])]
    early = [c for c in today_cards if "EARLY_DEPARTURE" in (c.get("operational_flags") or [])]
    return {
        "date": today,
        "total_workers": len(today_cards),
        "flagged_count": len(flagged),
        "pending_approval_count": len(pending),
        "late_arrival_count": len(late),
        "early_departure_count": len(early),
        "poll_channel": "sms_whatsapp",
        "food_allowance_range_nis": {"min": 65, "max": 100},
        "time_cards": today_cards,
    }


# ---------------------------------------------------------------------------
# Cost Engineering — supplier tender analysis & procurement actions
# ---------------------------------------------------------------------------

_LAYER_LABELS = {
    "base_material_cost": "Base Material Cost",
    "logistics_freight_overhead": "Logistics & Freight",
    "operational_overheads": "Operational Overheads",
    "supplier_margin_risk_premium": "Supplier Margin / Risk",
}


def _append_shopping_log(
    matrix: dict[str, Any],
    *,
    materials: list[str],
    note: str,
    supplier_name: str,
    amount_nis: float,
) -> dict[str, Any]:
    shopping = matrix.setdefault("shopping_log", [])
    entry = {
        "id": str(uuid.uuid4()),
        "date": date.today().isoformat(),
        "supplier_name": supplier_name,
        "materials": materials,
        "amount_nis": amount_nis,
        "note": note,
        "status": "scheduled",
    }
    shopping.append(entry)
    matrix["shopping_log"] = shopping[-40:]

    ops = matrix.setdefault("daily_operations", [])
    if ops:
        last = ops[-1]
        existing = list(last.get("next_day_materials") or [])
        for m in materials:
            if m not in existing:
                existing.append(m)
        last["next_day_materials"] = existing
    else:
        ops.append(
            {
                "date": date.today().isoformat(),
                "clocked_hours": 0,
                "friction_events": [],
                "next_day_materials": materials,
                "notes": note,
            }
        )
    matrix["daily_operations"] = ops[-30:]
    return entry


async def analyze_supplier_tender(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    action: str = "analyze",
    bid_text: str | None = None,
    supplier_name: str | None = None,
    total_bid_nis: float | None = None,
    tender_id: str | None = None,
    counter_offer_nis: float | None = None,
    materials: list[str] | None = None,
) -> dict[str, Any]:
    """
    Cost Engineering analysis for supplier tenders with historical anomaly detection.
    Accept/counter actions update financial_ledger and shopping logs (tenant-scoped).
    """
    act = (action or "analyze").strip().lower()
    if act not in ("analyze", "accept_bid", "counter_offer"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "action must be analyze, accept_bid, or counter_offer")

    matrix = await load_project_memory(org_id, project_id)
    tenders: list[dict[str, Any]] = list(matrix.get("procurement_tenders") or [])

    if act in ("accept_bid", "counter_offer"):
        if not tender_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "tender_id is required")
        tender = next((t for t in tenders if t.get("id") == tender_id), None)
        if tender is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tender not found")

        supplier = tender.get("supplier_name") or "Supplier"
        cm = tender.get("cost_matrix") or {}
        amount = float(
            counter_offer_nis if act == "counter_offer" and counter_offer_nis
            else cm.get("total_bid_nis") or 0
        )
        if amount <= 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid tender amount")

        mat_list = list(materials or [])
        if not mat_list and tender.get("bid_text"):
            mat_list = [f"Procurement: {supplier}"]

        if act == "accept_bid":
            ledger = await create_ledger_entry(
                org_id,
                project_id,
                entry_type="EXPENSE",
                amount=amount,
                currency="ILS",
                description=f"Accepted supplier tender — {supplier}"[:4000],
                status="pending",
            )
            tender["status"] = "accepted"
            tender["ledger_entry_id"] = ledger.get("id")
            shop = _append_shopping_log(
                matrix,
                materials=mat_list,
                note=f"Accepted bid from {supplier}",
                supplier_name=supplier,
                amount_nis=amount,
            )
            payload = {
                "tool": "analyze_supplier_tender",
                "action": "accept_bid",
                "tender": tender,
                "ledger_entry": ledger,
                "shopping_log_entry": shop,
            }
        else:
            if counter_offer_nis is None or counter_offer_nis <= 0:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "counter_offer_nis is required")
            ledger = await create_ledger_entry(
                org_id,
                project_id,
                entry_type="EXPENSE",
                amount=float(counter_offer_nis),
                currency="ILS",
                description=f"Counter-offer pending — {supplier}"[:4000],
                status="pending",
            )
            tender["status"] = "counter_offered"
            tender["counter_offer_nis"] = float(counter_offer_nis)
            tender["ledger_entry_id"] = ledger.get("id")
            shop = _append_shopping_log(
                matrix,
                materials=mat_list,
                note=f"Counter-offer ₪{counter_offer_nis} to {supplier}",
                supplier_name=supplier,
                amount_nis=float(counter_offer_nis),
            )
            payload = {
                "tool": "analyze_supplier_tender",
                "action": "counter_offer",
                "tender": tender,
                "counter_offer_nis": float(counter_offer_nis),
                "ledger_entry": ledger,
                "shopping_log_entry": shop,
            }

        matrix["procurement_tenders"] = tenders
        await save_project_memory(org_id, project_id, matrix)
        return attach_request_id(attach_mutated_state("analyze_supplier_tender", payload))

    try:
        parsed = parse_supplier_bid(
            bid_text or "",
            supplier_name=supplier_name,
            total_bid_nis=total_bid_nis,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e

    ledger_data = await list_ledger_entries(org_id, project_id)
    expenses = [
        e for e in (ledger_data.get("entries") or []) if e.get("entry_type") == "EXPENSE"
    ]
    prior_accepted = [t for t in tenders if t.get("status") == "accepted"]
    baseline = build_historical_baseline(expenses, prior_accepted)
    anomalies = detect_cost_anomalies(parsed["cost_matrix"], baseline)

    layers = [
        {
            "key": key,
            "label": _LAYER_LABELS[key],
            "amount_nis": parsed["cost_matrix"][key],
            "share_pct": parsed["cost_matrix"]["layer_pcts"].get(key, 0),
            "anomaly": next((a for a in anomalies if a.get("layer") == key), None),
        }
        for key in _LAYER_LABELS
    ]

    tender = {
        "id": new_tender_id(),
        "status": "evaluated",
        "supplier_name": parsed["supplier_name"],
        "bid_text": parsed.get("bid_text"),
        "cost_matrix": parsed["cost_matrix"],
        "anomalies": anomalies,
        "baseline": baseline,
        "layers": layers,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    tenders.append(tender)
    matrix["procurement_tenders"] = tenders[-30:]
    await save_project_memory(org_id, project_id, matrix)

    payload = {
        "tool": "analyze_supplier_tender",
        "action": "analyze",
        "tender": tender,
        "cost_matrix": parsed["cost_matrix"],
        "layers": layers,
        "anomalies": anomalies,
        "has_anomalies": len(anomalies) > 0,
        "baseline": baseline,
    }
    return attach_request_id(attach_mutated_state("analyze_supplier_tender", payload))


# ---------------------------------------------------------------------------
# Upskilling — role requirements, training ROI, proctor coordination
# ---------------------------------------------------------------------------


def _transit_cost_from_memory(matrix: dict[str, Any]) -> float:
    targets = matrix.get("location_logistics", {}).get("targets") or {}
    if not targets:
        logistics = compute_location_logistics("Shoham")
        return float(logistics.get("fuel_nis") or 120)
    return round(
        sum(float(v.get("fuel_nis") or 0) for v in targets.values()) / max(1, len(targets)),
        2,
    )


async def define_tactical_job_requirements(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    engineering_scope: str,
) -> dict[str, Any]:
    scope = (engineering_scope or "").strip()
    if not scope:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "engineering_scope is required")

    requirements = derive_job_requirements(scope)
    matrix = await load_project_memory(org_id, project_id)
    matrix["skill_blueprint"] = requirements["skill_blueprint"]
    matrix["engineering_scope"] = requirements["engineering_scope"]
    await save_project_memory(org_id, project_id, matrix)

    payload = {
        "tool": "define_tactical_job_requirements",
        "engineering_scope": requirements["engineering_scope"],
        "statutory_assets": requirements["statutory_assets"],
        "trainable_orientations": requirements["trainable_orientations"],
        "skill_blueprint": requirements["skill_blueprint"],
        "statutory_count": requirements["statutory_count"],
        "trainable_count": requirements["trainable_count"],
        "home_base": DEFAULT_BASE_LOCATION,
    }
    return attach_request_id(attach_mutated_state("define_tactical_job_requirements", payload))


async def simulate_training_day_roi(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    action: str = "simulate",
    engineering_scope: str | None = None,
    scheduled_date: str | None = None,
    onsite_proctor_day_nis: float | None = None,
) -> dict[str, Any]:
    act = (action or "simulate").strip().lower()
    if act not in ("simulate", "schedule_proctor_session"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "action must be simulate or schedule_proctor_session",
        )

    matrix = await load_project_memory(org_id, project_id)
    scope = (engineering_scope or matrix.get("engineering_scope") or "").strip()
    if scope and not matrix.get("skill_blueprint"):
        req = derive_job_requirements(scope)
        matrix["skill_blueprint"] = req["skill_blueprint"]
        matrix["engineering_scope"] = req["engineering_scope"]

    blueprint = matrix.get("skill_blueprint") or derive_job_requirements(
        scope or "construction site welding at height with electrical fit-out"
    )["skill_blueprint"]

    members_data = await list_project_members(org_id, project_id)
    member_names = [m.get("name") for m in (members_data.get("members") or []) if m.get("name")]
    compliance = matrix.get("member_compliance") or {}
    cert_registry = matrix.get("worker_certifications") or {}

    gaps = scan_certification_gaps(
        skill_blueprint=blueprint,
        member_compliance=compliance,
        cert_registry=cert_registry,
        project_members=member_names,
    )

    transit = _transit_cost_from_memory(matrix)
    proctor_day = float(onsite_proctor_day_nis or 4500)
    roi = simulate_training_roi(gaps=gaps, transit_per_worker_nis=transit, onsite_proctor_day_nis=proctor_day)

    session = None
    ledger_entry = None
    if act == "schedule_proctor_session":
        session = build_proctor_session(gaps=gaps, roi=roi, scheduled_date=scheduled_date)
        cost = float(roi.get("onsite_proctor", {}).get("total_nis") or proctor_day)
        ledger_entry = await create_ledger_entry(
            org_id,
            project_id,
            entry_type="EXPENSE",
            amount=cost,
            currency="ILS",
            description=f"Onsite proctor training day — {len(session['invitation_list'])} workers"[:4000],
            status="pending",
        )
        sessions = matrix.setdefault("training_sessions", [])
        session["ledger_entry_id"] = ledger_entry.get("id")
        sessions.append(session)
        matrix["training_sessions"] = sessions[-20:]

    matrix["last_training_roi"] = roi
    matrix["certification_gaps"] = gaps
    await save_project_memory(org_id, project_id, matrix)

    payload = {
        "tool": "simulate_training_day_roi",
        "action": act,
        "home_base": DEFAULT_BASE_LOCATION,
        "transit_per_worker_nis": transit,
        "certification_gaps": gaps,
        "roi_analysis": roi,
        "proctor_session": session,
        "ledger_entry": ledger_entry,
        "invitation_list": roi.get("affected_workers") or [],
        "margin_impact": roi.get("margin_impact"),
        "recommended_path": roi.get("recommended_path"),
    }
    return attach_request_id(attach_mutated_state("simulate_training_day_roi", payload))


# ---------------------------------------------------------------------------
# Basalt public corporate gateway — jobs, applications, portfolio, inbox review
# ---------------------------------------------------------------------------


async def get_basalt_public_jobs(org_id: uuid.UUID, *, lang: str | None = None) -> dict[str, Any]:
    """Public jobs feed — labor deficits from BEN recruitment tools."""
    return attach_request_id(await fetch_active_job_openings(org_id, lang=lang))


async def submit_basalt_public_application(
    org_id: uuid.UUID,
    *,
    candidate_name: str,
    email: str | None = None,
    phone: str | None = None,
    resume_text: str | None = None,
    desired_role: str | None = None,
    certifications: list[dict[str, Any]] | None = None,
    lang: str | None = None,
) -> dict[str, Any]:
    """Intercept external candidate forms into tenant storage under PENDING_REVIEW."""
    result = await submit_candidate_application(
        org_id,
        candidate_name=candidate_name,
        email=email,
        phone=phone,
        resume_text=resume_text,
        desired_role=desired_role,
        certifications=certifications,
        lang=lang,
    )
    return attach_request_id(result)


async def get_basalt_public_portfolio(org_id: uuid.UUID, *, lang: str | None = None) -> dict[str, Any]:
    """Verified portfolio milestones from financial_ledger."""
    return attach_request_id(await fetch_verified_portfolio(org_id, lang=lang))


async def get_basalt_corporate_content(*, lang: str | None = None) -> dict[str, Any]:
    """US Enterprise copywriting and EHS schema for basalt.co.il."""
    code = normalize_lang(lang)
    content = build_corporate_content(code)
    return attach_request_id({"org_id": str(resolve_basalt_org_id()), **content})


async def review_basalt_application(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    action: str = "inbox",
    application_id: str | None = None,
    scheduled_date: str | None = None,
) -> dict[str, Any]:
    """Internal review of basalt.co.il applications — inbox flash, onboard, or training."""
    act = (action or "inbox").strip().lower()
    if act not in ("inbox", "approve_onboard", "schedule_training"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "action must be inbox, approve_onboard, or schedule_training",
        )

    pending = await list_pending_applications(org_id, project_id)
    target = None
    if application_id:
        target = next((a for a in pending if a.get("id") == application_id), None)
        if target is None:
            matrix = await load_project_memory(org_id, project_id)
            all_apps = matrix.get("basalt_applications") or []
            target = next((a for a in all_apps if a.get("id") == application_id), None)
    elif pending:
        flash = [a for a in pending if a.get("pending_flash")]
        target = flash[0] if flash else pending[-1]

    onboard_result = None
    training_result = None

    if act == "approve_onboard" and target:
        onboard_result = await onboard_project_member(
            org_id,
            project_id,
            name=target["candidate_name"],
            member_type="EMPLOYEE",
            role=target.get("desired_role") or (target.get("skill_matrix") or [{}])[0].get("role"),
            email=target.get("email"),
            phone=target.get("phone"),
        )
        certs = target.get("certifications") or []
        if certs:
            matrix = await load_project_memory(org_id, project_id)
            registry = matrix.setdefault("worker_certifications", {})
            registry[target["candidate_name"]] = [
                {
                    "skill_id": c.get("cert_type", "general_safety"),
                    "status": "valid",
                    "source": "basalt_application",
                }
                for c in certs
            ]
            await save_project_memory(org_id, project_id, matrix)
        await mark_application_reviewed(org_id, project_id, target["id"], new_status="APPROVED_ONBOARDED")

    if act == "schedule_training" and target:
        training_result = await simulate_training_day_roi(
            org_id,
            project_id,
            action="schedule_proctor_session",
            scheduled_date=scheduled_date,
            engineering_scope=target.get("desired_role") or "classified site orientation",
        )
        await mark_application_reviewed(org_id, project_id, target["id"], new_status="TRAINING_SCHEDULED")

    payload = {
        "tool": "review_basalt_application",
        "action": act,
        "pending_count": len(pending),
        "application": target,
        "applications": pending,
        "onboard_result": onboard_result,
        "training_result": training_result,
        "corporate_content": build_corporate_content("en"),
    }
    return attach_request_id(attach_mutated_state("review_basalt_application", payload))
