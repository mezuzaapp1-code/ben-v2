"""Credential presence probe for Gate P2.

Never prints, logs, writes, or returns secret values. Presence only.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

PROVIDER_KEY_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
)

NATIVE_REQUIRED = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
)


def key_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def presence_map() -> dict[str, bool]:
    return {name: key_present(name) for name in PROVIDER_KEY_NAMES}


def _railway_graphql(query: str) -> dict[str, Any]:
    token = os.getenv("RAILWAY_TOKEN", "").strip()
    if not token:
        return {"ok": False, "error": "RAILWAY_TOKEN_absent"}
    req = urllib.request.Request(
        "https://backboard.railway.app/graphql/v2",
        data=json.dumps({"query": query}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        msg = type(exc).__name__
        if hasattr(exc, "read"):
            try:
                body = json.loads(exc.read().decode())
                errs = body.get("errors") or []
                if errs:
                    msg = str(errs[0].get("message") or msg)[:160]
            except Exception:
                pass
        return {"ok": False, "error": msg}
    errors = payload.get("errors") or []
    if errors:
        return {"ok": False, "error": str(errors[0].get("message") or "graphql_error")[:160]}
    return {"ok": True, "error": None}


def railway_injection_possible() -> dict[str, Any]:
    """Can this process read Railway project variables? Never returns values."""
    token_present = key_present("RAILWAY_TOKEN")
    me = _railway_graphql("query { me { id } }") if token_present else {"ok": False, "error": "RAILWAY_TOKEN_absent"}
    project_token = (
        _railway_graphql("query { projectToken { projectId environmentId } }")
        if token_present
        else {"ok": False, "error": "RAILWAY_TOKEN_absent"}
    )
    injectable = bool(me.get("ok") or project_token.get("ok"))
    return {
        "RAILWAY_TOKEN": token_present,
        "railway_me_authorized": bool(me.get("ok")),
        "railway_project_token_valid": bool(project_token.get("ok")),
        "me_error": me.get("error"),
        "project_token_error": project_token.get("error"),
        "can_inject_provider_keys": injectable,
    }


def cursor_injected_secret_names() -> list[str]:
    raw = os.getenv("CLOUD_AGENT_INJECTED_SECRET_NAMES") or os.getenv("CLOUD_AGENT_ALL_SECRET_NAMES") or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


def probe() -> dict[str, Any]:
    presence = presence_map()
    railway = railway_injection_possible()
    injected = cursor_injected_secret_names()
    google = presence["GOOGLE_API_KEY"] or presence["GEMINI_API_KEY"]
    native_ready = {
        "OPENAI": presence["OPENAI_API_KEY"],
        "CLAUDE": presence["ANTHROPIC_API_KEY"],
        "GEMINI": google,
        "GROK": presence["XAI_API_KEY"],
    }
    any_native = any(native_ready[k] for k in ("OPENAI", "CLAUDE", "GEMINI"))
    return {
        "KEY_PRESENT": presence,
        "native_ready": native_ready,
        "any_native_document_key": any_native,
        "cursor_injected_secret_names": injected,
        "railway": railway,
        "safe_injection_possible": bool(any_native or railway["can_inject_provider_keys"]),
        "file_search_in_scope": False,
        "note": (
            "Presence only. Secret values are not returned. "
            "File Search / vector stores are out of scope for Gate P2."
        ),
    }


def assert_no_secret_values(payload: Any) -> None:
    """Fail closed if a probe payload appears to contain a key value."""
    blob = json.dumps(payload, default=str)
    for name in PROVIDER_KEY_NAMES:
        value = os.getenv(name, "").strip()
        if value and value in blob:
            raise RuntimeError("probe payload must not contain secret values")
    token = os.getenv("RAILWAY_TOKEN", "").strip()
    if token and token in blob:
        raise RuntimeError("probe payload must not contain RAILWAY_TOKEN value")
    for prefix in ("sk-", "sk_ant", "xai-", "AIza"):
        if prefix in blob and prefix not in json.dumps(list(PROVIDER_KEY_NAMES)):
            # Allow the prefix only if it is part of a documented error string we control.
            if prefix in blob:
                raise RuntimeError("probe payload must not contain key-shaped material")
