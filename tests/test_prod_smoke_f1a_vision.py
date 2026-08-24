"""Focused tests for scripts/prod_smoke_f1a_vision.py — no production execution."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import prod_smoke_f1a_vision as f1a  # noqa: E402
from services.vision.current_turn import user_turn_file_ref_ids  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prod_smoke_f1a_vision.py"
E2E = ROOT / "scripts" / "verify_frontend_bearer_e2e.py"


def test_canary_png_is_png_and_nonempty():
    data = f1a.build_canary_png_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) > 80
    assert data.endswith(b"IEND\xaeB`\x82")


def test_filename_has_no_color_words():
    name = f1a.CANARY_FILENAME.lower()
    for token in ("red", "blue", "אדום", "כחול", "green", "yellow"):
        assert token not in name
    assert name.endswith(".png")
    assert "canary" in name


def test_hebrew_prompt_is_hebrew_instruction():
    assert "מהם שני הצבעים" in f1a.HEBREW_PROMPT
    assert "בעברית" in f1a.HEBREW_PROMPT
    assert f1a.score_response_language(f1a.HEBREW_PROMPT) == "he"


def test_encode_vision_turn_uses_file_ref_not_bytes():
    file_id = str(uuid.uuid4())
    encoded = f1a.encode_vision_turn(file_id)
    assert user_turn_file_ref_ids(encoded) == [file_id]
    assert f1a.HEBREW_PROMPT in encoded
    assert "data:image" not in encoded
    assert "base64" not in encoded.lower()


def test_image_understanding_requires_both_colors():
    ok = f1a.score_image_understanding("התמונה אדומה בחלק העליון וכחולה בחלק התחתון.")
    assert ok["understands_image"] is True
    assert ok["mentions_red"] is True
    assert ok["mentions_blue"] is True

    filename_only = f1a.score_image_understanding("This is canary-file-alpha.png attached.")
    assert filename_only["understands_image"] is False
    assert filename_only["filename_echo"] is True

    red_only = f1a.score_image_understanding("רק אדום")
    assert red_only["understands_image"] is False


def test_response_language_hebrew_vs_english():
    he = "הצבעים בתמונה הם אדום וכחול ברור מאוד לבדיקה."
    en = "The image shows a red band and a blue band for this check."
    assert f1a.score_response_language(he) == "he"
    assert f1a.score_response_language(en) == "en"


def test_authorization_deny_detects_workspace_error_not_success():
    assert f1a.is_authorization_deny(
        http_status=200,
        error_message="This image is not available in the current workspace.",
        response_text="",
    )
    assert f1a.is_authorization_deny(http_status=403, error_message=None, response_text=None)
    assert not f1a.is_authorization_deny(
        http_status=200,
        error_message=None,
        response_text="התמונה אדומה וכחולה.",
    )


def test_parse_ndjson_and_stream_collect():
    raw = (
        '{"type":"meta","provider_id":"gpt"}\n'
        '{"type":"chunk","content":"אדום "}\n'
        '{"type":"chunk","content":"וכחול"}\n'
        '{"type":"done","provider_id":"gpt","model_used":"gpt-4o","execution_id":"ex1"}\n'
    )
    events = f1a.parse_ndjson_events(raw)
    text, error, done = f1a.collect_stream_text(events)
    assert text == "אדום וכחול"
    assert error is None
    assert done["model_used"] == "gpt-4o"
    assert f1a.resolve_reported_provider("gpt", done) == "gpt"


def test_collect_stream_error_event():
    events = f1a.parse_ndjson_events(
        '{"type":"error","message":"This image is not available in the current workspace."}\n'
    )
    text, error, done = f1a.collect_stream_text(events)
    assert done is None
    assert "not available" in (error or "")
    assert text == ""


def test_redact_secrets_strips_bearer_and_jwt_shapes():
    payload = {
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.aaa.bbb",
        "note": "ok",
        "nested": {"token": "secret-value"},
    }
    redacted = f1a.redact_secrets(payload)
    rendered = str(redacted)
    assert "Bearer " not in rendered
    assert "eyJ" not in rendered
    assert redacted["note"] == "ok"
    assert redacted["nested"]["token"] == "REDACTED"


def test_require_credentials_fail_closed(monkeypatch):
    monkeypatch.delenv("CLERK_TEST_EMAIL", raising=False)
    monkeypatch.delenv("CLERK_TEST_PASSWORD", raising=False)
    with pytest.raises(f1a.CanaryFailClosed, match="missing_credentials"):
        f1a.require_clerk_test_credentials()


def test_require_credentials_reads_canonical_names(monkeypatch):
    monkeypatch.setenv("CLERK_TEST_EMAIL", "smoke@example.com")
    monkeypatch.setenv("CLERK_TEST_PASSWORD", "not-a-real-password")
    monkeypatch.delenv("BEN_UI_EMAIL", raising=False)
    email, password = f1a.require_clerk_test_credentials()
    assert email == "smoke@example.com"
    assert password == "not-a-real-password"


def test_script_source_guards():
    src = SCRIPT.read_text(encoding="utf-8")
    e2e = E2E.read_text(encoding="utf-8")
    assert "CLERK_TEST_EMAIL" in src
    assert "CLERK_TEST_PASSWORD" in src
    assert "import clerk_session_bearer" not in src
    assert "from clerk_session_bearer" not in src
    assert "users.list" not in src
    assert "os.environ.get(\"RAILWAY_TOKEN\")" not in src
    assert "os.getenv(\"RAILWAY_TOKEN\")" not in src
    assert "os.environ.get(\"BEN_UI_EMAIL\")" not in src
    assert "os.environ.get(\"BEN_UI_PASSWORD\")" not in src
    assert 'input[name="identifier"]' in src and 'input[name="identifier"]' in e2e
    assert 'input[name="password"]' in src and 'input[name="password"]' in e2e
    assert 'button:has-text("Continue")' in src and 'button:has-text("Continue")' in e2e
    assert "CanaryFailClosed" in src
    for provider in ("gpt", "claude", "gemini", "grok"):
        assert f'"{provider}"' in src


def test_execute_canary_not_invoked_by_import():
    assert f1a.FRONTEND_URL.startswith("http")
    assert callable(f1a.main)
    assert callable(f1a.execute_canary)
