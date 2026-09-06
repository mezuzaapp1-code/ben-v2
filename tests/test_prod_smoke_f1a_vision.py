"""Focused tests for scripts/prod_smoke_f1a_vision.py — no production execution."""
from __future__ import annotations

import base64
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


def test_canary_png_halves_are_red_and_blue():
    facts = f1a.inspect_canary_png(f1a.build_canary_png_bytes())
    assert facts["top_rgb"] == (220, 24, 24)
    assert facts["bottom_rgb"] == (24, 48, 210)
    assert facts["top_is_red"] is True
    assert facts["bottom_is_blue"] is True
    assert facts["width"] == 32
    assert facts["height"] == 32


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

    synonym_only = f1a.score_image_understanding("The image is magenta and indigo.")
    assert synonym_only["understands_image"] is False


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


def test_authorization_negative_is_hold_not_same_user_workspace():
    assert f1a.same_principal_workspace_is_not_foreign() is True
    hold = f1a.authorization_negative_hold()
    assert hold["status"] == "HOLD"
    assert hold["success"] is False
    assert "foreign" in hold["reason"]
    src = SCRIPT.read_text(encoding="utf-8")
    assert "run_authorization_negative" not in src
    assert "F1a Vision Foreign" not in src
    assert "f1a_vision_canary=HOLD" in src
    assert "f1a_vision_canary=PASS" not in src


def test_redacted_secret_hides_value():
    secret = f1a.RedactedSecret("eyJhbGciOiJIUzI1NiJ9.aaa.bbb")
    assert "eyJ" not in repr(secret)
    assert "eyJ" not in str(secret)
    assert secret.get().startswith("eyJ")


def test_main_unhandled_exception_does_not_print_secret(monkeypatch, capsys):
    def boom():
        raise RuntimeError("Bearer eyJhbGciOiJIUzI1NiJ9.aaa.bbb")

    monkeypatch.setattr(f1a, "execute_canary", boom)
    rc = f1a.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert "eyJ" not in out
    assert "Bearer" not in out
    assert "unhandled_RuntimeError" in out
    assert "f1a_vision_canary=FAIL" in out


def test_same_image_and_hebrew_prompt_for_all_providers():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "PROVIDERS = (\"gpt\", \"claude\", \"gemini\", \"grok\")" in src
    assert "for provider in PROVIDERS:" in src
    assert "message = encode_vision_turn(file_id)" in src
    assert "prompt: str = HEBREW_PROMPT" in src
    assert src.count("encode_vision_turn(") >= 2
    from services.model_gateway import _attempts

    for provider in f1a.PROVIDERS:
        attempts = _attempts("free", provider_id=provider)
        assert len(attempts) == 1


def test_file_ref_is_not_an_image_payload_without_bytes():
    from services.providers.vision_input import (
        VisionImage,
        anthropic_user_content,
        gemini_user_parts,
        openai_user_content,
    )
    from services.vision.current_turn import build_provider_user_content

    file_id = str(uuid.uuid4())
    encoded = f1a.encode_vision_turn(file_id)
    png = f1a.build_canary_png_bytes()
    b64 = base64.b64encode(png).decode("ascii")
    assert b64 not in encoded
    assert "data:image" not in encoded

    empty_parts = build_provider_user_content(encoded, [])
    gpt_empty = openai_user_content(empty_parts)
    claude_empty = anthropic_user_content(empty_parts)
    gemini_empty = gemini_user_parts(empty_parts)
    assert gpt_empty == f1a.HEBREW_PROMPT or (
        isinstance(gpt_empty, list) and not any("image_url" in block for block in gpt_empty)
    )
    assert claude_empty == f1a.HEBREW_PROMPT or (
        isinstance(claude_empty, list) and not any(block.get("type") == "image" for block in claude_empty)
    )
    assert not any("inlineData" in part for part in gemini_empty)

    image = VisionImage(file_id=file_id, media_type="image/png", data=png)
    parts = build_provider_user_content(encoded, [image])
    gpt = openai_user_content(parts)
    claude = anthropic_user_content(parts)
    gemini = gemini_user_parts(parts)
    assert isinstance(gpt, list)
    assert any(block.get("type") == "image_url" for block in gpt)
    assert b64 in str(gpt)
    assert isinstance(claude, list)
    assert any(block.get("type") == "image" for block in claude)
    assert any(block.get("source", {}).get("data") == b64 for block in claude)
    assert any(part.get("inlineData", {}).get("data") == b64 for part in gemini)


def test_provider_adapters_construct_image_payloads():
    root = ROOT / "services" / "providers"
    vision = (root / "vision_input.py").read_text(encoding="utf-8")
    assert "image_url" in vision
    assert "data:{self.media_type};base64" in vision
    assert '"type": "image"' in vision
    assert "inlineData" in vision
    openai = (root / "openai_provider.py").read_text(encoding="utf-8")
    anthropic = (root / "anthropic_provider.py").read_text(encoding="utf-8")
    gemini = (root / "gemini_provider.py").read_text(encoding="utf-8")
    xai = (root / "xai_provider.py").read_text(encoding="utf-8")
    assert "openai_user_content" in openai
    assert "anthropic_user_content" in anthropic
    assert "gemini_user_parts" in gemini
    assert "openai_user_content" in xai
    chat = (ROOT / "services" / "chat_service.py").read_text(encoding="utf-8")
    assert "load_current_turn_vision_images" in chat
    assert 'user_content": vision_user_content' in chat or "user_content=vision_user_content" in chat
    gateway = (ROOT / "services" / "model_gateway.py").read_text(encoding="utf-8")
    assert "if user_content:" in gateway
    assert "attempts = attempts[:1]" in gateway
