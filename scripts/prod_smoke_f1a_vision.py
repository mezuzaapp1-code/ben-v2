"""F1a production Vision canary — isolated verification only.

Signs in through the existing Clerk modal on the BEN UI (same path as
``scripts/verify_frontend_bearer_e2e.py``), then uploads one image and sends
the same Hebrew current-turn Vision prompt to GPT, Claude, Gemini, and Grok.

Canonical credentials (no aliases):
  CLERK_TEST_EMAIL
  CLERK_TEST_PASSWORD

Never prints passwords, cookies, JWTs, Authorization headers, Clerk session
tokens, or provider keys. Does not mint a Clerk session via the backend SDK.
Does not use Railway dashboard tokens or UI-email aliases. Does not mock providers.

This module is safe to import. Production execution is ``main()`` only.
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
import uuid
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.chat_language import detect_language_code
from services.message_format import encode_user_turn
from services.vision.current_turn import user_turn_file_ref_ids

FRONTEND_URL = os.environ.get("BEN_FRONTEND_URL", "https://ben-v2.vercel.app").rstrip("/")
API_BASE = os.environ.get("BEN_API_BASE", "https://ben-v2-production.up.railway.app").rstrip("/")

CREDENTIAL_EMAIL_ENV = "CLERK_TEST_EMAIL"
CREDENTIAL_PASSWORD_ENV = "CLERK_TEST_PASSWORD"

CANARY_FILENAME = "canary-file-alpha.png"
HEBREW_PROMPT = (
    "מהם שני הצבעים בתמונה? ענה בעברית במשפט קצר אחד. "
    "אל תסתמך על שם הקובץ."
)
PROVIDERS = ("gpt", "claude", "gemini", "grok")

# Exact visual facts encoded in the PNG (top red, bottom blue). Extra color
# synonyms are not accepted — PASS must name these families, not a vibe.
_RED_TOKENS = ("אדום", "אדומה", "אדומים", "red")
_BLUE_TOKENS = ("כחול", "כחולה", "כחולים", "blue")
_FILENAME_LEAK_TOKENS = ("canary-file-alpha", "canary_file_alpha", "alpha.png")
CANARY_TOP_RGB = (220, 24, 24)
CANARY_BOTTOM_RGB = (24, 48, 210)
AUTHZ_HOLD_REASON = (
    "no_foreign_tenant_file: a file_id the smoke user is unauthorized to access "
    "cannot be obtained without using another tenant's data; same-user second "
    "workspace is not unauthorized access"
)
_DENY_SNIPPETS = (
    "not available in the current workspace",
    "image reference is not valid",
    "select an active workspace",
    "unauthorized",
    "capability_denied",
)

_SECRET_SHAPE = re.compile(
    r"(?i)(bearer\s+\S+|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.|sk_(?:test|live)_|pk_(?:test|live)_)"
)

_CLERK_IDENTIFIER_SELECTORS = (
    'input[name="identifier"]',
    'input[type="email"]',
    'input[name="emailAddress"]',
)
_CLERK_CONTINUE_SELECTORS = (
    'button:has-text("Continue")',
    'button[type="submit"]',
)
_CLERK_PASSWORD_SELECTORS = (
    'input[name="password"]',
    'input[type="password"]',
)
_CLERK_SUBMIT_SELECTORS = (
    'button:has-text("Continue")',
    'button:has-text("Sign in")',
    'button[type="submit"]',
)

_GATEWAY_TO_PROVIDER = {
    "openai": "gpt",
    "anthropic": "claude",
    "google": "gemini",
    "xai": "grok",
}


class CanaryFailClosed(RuntimeError):
    """Missing evidence — never skip, never mock."""


class RedactedSecret:
    """Hides credential/token values from repr/str/tracebacks."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        object.__setattr__(self, "_value", value)

    def get(self) -> str:
        return object.__getattribute__(self, "_value")

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__


def require_clerk_test_credentials() -> tuple[str, str]:
    email = os.environ.get(CREDENTIAL_EMAIL_ENV, "").strip()
    password = os.environ.get(CREDENTIAL_PASSWORD_ENV, "").strip()
    if not email or not password:
        raise CanaryFailClosed(
            f"missing_credentials:{CREDENTIAL_EMAIL_ENV},{CREDENTIAL_PASSWORD_ENV}"
        )
    return email, password


def build_canary_png_bytes(width: int = 32, height: int = 32) -> bytes:
    """Deterministic PNG: top half red, bottom half blue. No color in filename."""
    if width < 2 or height < 2:
        raise CanaryFailClosed("canary_png_too_small")
    rows: list[bytes] = []
    split = height // 2
    for y in range(height):
        pixel = CANARY_TOP_RGB if y < split else CANARY_BOTTOM_RGB
        rows.append(b"\x00" + bytes(pixel * width))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def inspect_canary_png(data: bytes) -> dict[str, Any]:
    """Decode the canary PNG and verify encoded red/blue halves."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CanaryFailClosed("canary_png_signature")
    pos = 8
    width = height = None
    idat = b""
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", body[:10])
            if bit_depth != 8 or color_type != 2:
                raise CanaryFailClosed("canary_png_not_rgb8")
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if not width or not height or not idat:
        raise CanaryFailClosed("canary_png_incomplete")
    raw = zlib.decompress(idat)
    row_bytes = 1 + width * 3
    if len(raw) != row_bytes * height:
        raise CanaryFailClosed("canary_png_size_mismatch")
    split = height // 2
    top: list[tuple[int, int, int]] = []
    bottom: list[tuple[int, int, int]] = []
    for y in range(height):
        row = raw[y * row_bytes : (y + 1) * row_bytes]
        if row[0] != 0:
            raise CanaryFailClosed("canary_png_filter")
        pixels = [(row[i], row[i + 1], row[i + 2]) for i in range(1, row_bytes, 3)]
        (top if y < split else bottom).extend(pixels)
    if not top or not bottom:
        raise CanaryFailClosed("canary_png_halves_empty")
    if any(px != CANARY_TOP_RGB for px in top):
        raise CanaryFailClosed("canary_png_top_not_red")
    if any(px != CANARY_BOTTOM_RGB for px in bottom):
        raise CanaryFailClosed("canary_png_bottom_not_blue")
    return {
        "width": width,
        "height": height,
        "top_rgb": CANARY_TOP_RGB,
        "bottom_rgb": CANARY_BOTTOM_RGB,
        "top_is_red": True,
        "bottom_is_blue": True,
    }


def encode_vision_turn(file_id: str, prompt: str = HEBREW_PROMPT, name: str = CANARY_FILENAME) -> str:
    encoded = encode_user_turn(
        [
            {"type": "text", "text": prompt},
            {"type": "file_ref", "file_id": str(file_id), "name": name},
        ]
    )
    ids = user_turn_file_ref_ids(encoded)
    if ids != [str(uuid.UUID(str(file_id)))]:
        raise CanaryFailClosed("vision_turn_file_ref_missing")
    if prompt not in encoded:
        raise CanaryFailClosed("vision_turn_prompt_missing")
    return encoded


def _normalize_haystack(text: str) -> str:
    return str(text or "").strip().lower()


def _has_visual_token(hay: str, token: str) -> bool:
    token_l = token.lower()
    if re.fullmatch(r"[a-z]+", token_l):
        return re.search(rf"(?<![a-z]){re.escape(token_l)}(?![a-z])", hay) is not None
    return token_l in hay


def score_response_language(text: str) -> str | None:
    return detect_language_code(text or "")


def score_image_understanding(text: str, *, filename: str = CANARY_FILENAME) -> dict[str, Any]:
    hay = _normalize_haystack(text)
    has_red = any(_has_visual_token(hay, tok) for tok in _RED_TOKENS)
    has_blue = any(_has_visual_token(hay, tok) for tok in _BLUE_TOKENS)
    filename_leak = any(tok.lower() in hay for tok in _FILENAME_LEAK_TOKENS)
    if filename and filename.lower() in hay:
        filename_leak = True
    understands = bool(has_red and has_blue)
    return {
        "understands_image": understands,
        "mentions_red": has_red,
        "mentions_blue": has_blue,
        "filename_echo": filename_leak,
        "pixels_not_filename": understands,
    }


def same_principal_workspace_is_not_foreign() -> bool:
    """Same smoke user + second workspace is not an unauthorized file_id."""
    return True


def authorization_negative_hold() -> dict[str, Any]:
    """Do not weaken this gate with a same-user workspace or a random UUID."""
    return {
        "status": "HOLD",
        "success": False,
        "reason": AUTHZ_HOLD_REASON,
    }


def is_authorization_deny(
    *,
    http_status: int | None,
    error_message: str | None,
    response_text: str | None,
) -> bool:
    if http_status in {401, 403, 404, 409}:
        return True
    blob = f"{error_message or ''} {response_text or ''}".lower()
    return any(snippet in blob for snippet in _DENY_SNIPPETS)


def redact_secrets(value: Any) -> Any:
    """Drop/redact secret-shaped strings before printing."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lk = str(key).lower()
            if lk in {"authorization", "cookie", "set-cookie", "password", "token", "jwt", "secret"}:
                out[key] = "REDACTED"
                continue
            out[key] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        if _SECRET_SHAPE.search(value):
            return "[redacted-secret-shape]"
        return value
    return value


def parse_ndjson_events(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in str(raw or "").splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        try:
            parsed = json.loads(trimmed)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def collect_stream_text(events: list[dict[str, Any]]) -> tuple[str, str | None, dict[str, Any] | None]:
    chunks: list[str] = []
    error: str | None = None
    done: dict[str, Any] | None = None
    for event in events:
        kind = event.get("type")
        if kind == "chunk":
            chunks.append(str(event.get("content") or ""))
        elif kind == "error":
            error = str(event.get("message") or event.get("detail") or "stream_error")
        elif kind == "done":
            done = event
    text = "".join(chunks)
    if done and not text:
        text = str(done.get("response") or "")
    return text, error, done


def resolve_reported_provider(requested: str, done: dict[str, Any] | None) -> str | None:
    if not done:
        return None
    pid = str(done.get("provider_id") or "").strip().lower()
    if pid in PROVIDERS:
        return pid
    used = str(done.get("provider_used") or "").strip().lower()
    return _GATEWAY_TO_PROVIDER.get(used, used or None)


def contains_secret_shape(text: str) -> bool:
    return bool(_SECRET_SHAPE.search(text or ""))


def safe_print(label: str, payload: Any) -> None:
    redacted = redact_secrets(payload)
    rendered = json.dumps(redacted, ensure_ascii=False, default=str)
    if contains_secret_shape(rendered):
        print(f"{label}=REDACTED_SECRET_SHAPE")
        return
    print(f"{label}={rendered}")


@dataclass
class ProviderResult:
    provider: str
    success: bool
    http_status: int | None
    model: str | None
    reported_provider: str | None
    language: str | None
    understands_image: bool
    filename_echo: bool
    error: str | None
    trace_id: str | None
    execution_id: str | None
    preview: str | None


def _preview_text(text: str, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


# --- Playwright login (same modal path as verify_frontend_bearer_e2e.py) ---


def _fill_first(page, selectors: tuple[str, ...], value: str) -> bool:
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count():
            loc.first.fill(value)
            return True
    return False


def _click_first(page, selectors: tuple[str, ...]) -> bool:
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count():
            loc.first.click()
            return True
    return False


def click_visible_sign_in(page) -> bool:
    sign_in = page.get_by_role("button", name=re.compile(r"sign in", re.I))
    count = sign_in.count()
    for i in range(count):
        btn = sign_in.nth(i)
        if btn.is_visible():
            btn.click()
            return True
    return False


def sign_in_clerk_modal(page, email: str, password: str) -> None:
    """Existing Clerk modal flow from verify_frontend_bearer_e2e.py."""
    page.wait_for_timeout(2000)
    if not _fill_first(page, _CLERK_IDENTIFIER_SELECTORS, email):
        raise CanaryFailClosed("clerk_identifier_not_found")
    _click_first(page, _CLERK_CONTINUE_SELECTORS)
    page.wait_for_timeout(1500)
    if not _fill_first(page, _CLERK_PASSWORD_SELECTORS, password):
        raise CanaryFailClosed("clerk_password_not_found")
    if not _click_first(page, _CLERK_SUBMIT_SELECTORS):
        raise CanaryFailClosed("clerk_submit_not_found")
    page.wait_for_timeout(5000)
    page.wait_for_function(
        "() => Boolean(window.Clerk && window.Clerk.session && window.Clerk.user)",
        timeout=20_000,
    )


def confirm_authenticated_ui(page) -> None:
    """Signed-out chrome is gone; Clerk session is present. Sign out lives in Settings."""
    ready = page.evaluate(
        "() => Boolean(window.Clerk && window.Clerk.session && window.Clerk.user)"
    )
    if not ready:
        raise CanaryFailClosed("clerk_session_not_ready")
    banner = page.locator(".clerk-signin-banner")
    if banner.count() and banner.first.is_visible():
        raise CanaryFailClosed("sign_in_banner_still_visible")
    sign_in = page.get_by_role("button", name=re.compile(r"^sign in$", re.I))
    visible_sign_in = any(sign_in.nth(i).is_visible() for i in range(sign_in.count()))
    if visible_sign_in:
        raise CanaryFailClosed("sign_in_still_visible")


def capture_clerk_session_token(page) -> RedactedSecret:
    token = page.evaluate(
        """async () => {
            const clerk = window.Clerk;
            if (!clerk || !clerk.session) return '';
            const t = await clerk.session.getToken();
            return (typeof t === 'string') ? t : '';
        }"""
    )
    if not isinstance(token, str) or len(token) < 20:
        raise CanaryFailClosed("clerk_session_token_missing")
    return RedactedSecret(token)


def _auth_headers(token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if extra:
        headers.update(extra)
    return headers


def _detail_text(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, dict):
            return str(detail.get("message") or detail.get("code") or "")
        return str(payload.get("message") or "")
    return str(payload or "")


def api_json(client, method: str, path: str, token: str, **kwargs) -> tuple[int, Any, str | None]:
    url = urljoin(API_BASE + "/", path.lstrip("/"))
    headers = _auth_headers(token, kwargs.pop("headers", None))
    response = client.request(method, url, headers=headers, **kwargs)
    trace = response.headers.get("x-request-id") or response.headers.get("X-Request-ID")
    try:
        body: Any = response.json()
    except Exception:
        body = {"raw": (response.text or "")[:400]}
    return response.status_code, body, trace


def post_chat_stream(
    client,
    token: str,
    *,
    message: str,
    provider_id: str,
    project_id: str,
    client_request_id: str,
) -> tuple[int, list[dict[str, Any]], str | None, str | None]:
    url = urljoin(API_BASE + "/", "chat/stream")
    headers = _auth_headers(
        token,
        {
            "Content-Type": "application/json",
            "X-Request-ID": client_request_id,
            "X-Workspace-ID": project_id,
        },
    )
    body = {
        "message": message,
        "tier": "free",
        "provider_id": provider_id,
        "preferred_language": "he",
        "project_id": project_id,
        "client_request_id": client_request_id,
    }
    with client.stream("POST", url, headers=headers, json=body, timeout=180.0) as response:
        trace = response.headers.get("x-request-id") or response.headers.get("X-Request-ID")
        raw = "".join(response.iter_text())
        status = response.status_code
    if status != 200:
        try:
            parsed = json.loads(raw) if raw.strip().startswith("{") else {"detail": raw[:400]}
        except json.JSONDecodeError:
            parsed = {"detail": raw[:400]}
        return status, [parsed] if isinstance(parsed, dict) else [], trace, _detail_text(parsed)
    return status, parse_ndjson_events(raw), trace, None


def run_provider_turn(
    client,
    token: str,
    *,
    provider: str,
    project_id: str,
    file_id: str,
) -> ProviderResult:
    message = encode_vision_turn(file_id)
    client_request_id = f"f1a-vision-{provider}-{uuid.uuid4()}"
    status, events, header_trace, http_error = post_chat_stream(
        client,
        token,
        message=message,
        provider_id=provider,
        project_id=project_id,
        client_request_id=client_request_id,
    )
    text, stream_error, done = collect_stream_text(events)
    error = http_error or stream_error
    vision = score_image_understanding(text)
    language = score_response_language(text) if text else None
    reported = resolve_reported_provider(provider, done)
    model = str((done or {}).get("model_used") or "").strip() or None
    trace = header_trace or str((done or {}).get("execution_id") or "") or client_request_id
    routing_ok = reported == provider
    success = (
        status == 200
        and error is None
        and bool(text.strip())
        and routing_ok
        and vision["understands_image"]
        and language == "he"
    )
    return ProviderResult(
        provider=provider,
        success=success,
        http_status=status,
        model=model,
        reported_provider=reported,
        language=language,
        understands_image=bool(vision["understands_image"]),
        filename_echo=bool(vision["filename_echo"]),
        error=error,
        trace_id=trace,
        execution_id=str((done or {}).get("execution_id") or "") or None,
        preview=_preview_text(str(redact_secrets(text or error or ""))),
    )


def execute_canary() -> int:
    email, password = require_clerk_test_credentials()
    png = build_canary_png_bytes()
    inspect_canary_png(png)
    failures: list[str] = []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise CanaryFailClosed("playwright_not_installed") from None

    import httpx

    session = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(FRONTEND_URL, wait_until="networkidle", timeout=60_000)
            if not click_visible_sign_in(page):
                raise CanaryFailClosed("sign_in_button_not_visible")
            sign_in_clerk_modal(page, email, password)
            confirm_authenticated_ui(page)
            session = capture_clerk_session_token(page)
        finally:
            browser.close()

    if session is None:
        raise CanaryFailClosed("clerk_session_token_missing")
    token = session.get()

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        status, _projects_body, _ = api_json(client, "GET", "/api/projects", token)
        if status != 200:
            raise CanaryFailClosed(f"projects_list_http_{status}")
        print("auth_customer_state=PASS")

        stamp = uuid.uuid4().hex[:8]
        status, created, _ = api_json(
            client,
            "POST",
            "/api/projects",
            token,
            json={"name": f"F1a Vision Canary {stamp}"},
        )
        project_id = str((created or {}).get("id") or "").strip()
        if status not in {200, 201} or not project_id:
            raise CanaryFailClosed(f"project_create_failed_http_{status}")

        upload_url = urljoin(API_BASE + "/", f"api/workspaces/{project_id}/files")
        upload = client.post(
            upload_url,
            headers=_auth_headers(token),
            files={"file": (CANARY_FILENAME, png, "image/png")},
            timeout=60.0,
        )
        try:
            upload_body = upload.json()
        except Exception:
            raise CanaryFailClosed("upload_response_not_json") from None
        file_id = str((upload_body or {}).get("id") or "").strip()
        if upload.status_code not in {200, 201} or not file_id:
            raise CanaryFailClosed(f"upload_failed_http_{upload.status_code}")
        try:
            uuid.UUID(file_id)
        except ValueError:
            raise CanaryFailClosed("upload_file_id_invalid") from None
        safe_print(
            "upload",
            {
                "http_status": upload.status_code,
                "file_id": file_id,
                "workspace_id": project_id,
                "filename": CANARY_FILENAME,
            },
        )

        results: list[ProviderResult] = []
        for provider in PROVIDERS:
            result = run_provider_turn(
                client,
                token,
                provider=provider,
                project_id=project_id,
                file_id=file_id,
            )
            results.append(result)
            safe_print("provider", asdict(result))
            if not result.success:
                failures.append(f"provider_{provider}")

        try:
            deleted = client.delete(
                urljoin(API_BASE + "/", f"api/workspaces/{project_id}/files/{file_id}"),
                headers=_auth_headers(token),
                timeout=30.0,
            )
            print(f"cleanup_file=best_effort_http_{deleted.status_code}")
        except Exception:
            print("cleanup_file=best_effort_failed")
        print("cleanup_project=none_no_delete_api")
        print("cleanup_threads=none_left_as_smoke_artifacts")

        authz = authorization_negative_hold()
        safe_print("authorization_negative", authz)

    session = None
    token = None
    if failures:
        print("FAILURES:", ",".join(failures))
        print("f1a_vision_canary=FAIL")
        return 1
    print("f1a_vision_canary=HOLD")
    print("hold_reason=authorization_negative_requires_foreign_tenant_file")
    return 2


def main() -> int:
    try:
        return execute_canary()
    except CanaryFailClosed as exc:
        print(f"FAIL fail_closed={exc}")
        print("f1a_vision_canary=FAIL")
        return 1
    except Exception as exc:
        print(f"FAIL fail_closed=unhandled_{type(exc).__name__}")
        print("f1a_vision_canary=FAIL")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
