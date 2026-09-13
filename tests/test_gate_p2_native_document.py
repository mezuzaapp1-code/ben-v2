"""Gate P2 fail-closed credential probe. Never asserts on secret values."""
from __future__ import annotations

import json
import os

from tests.gate_p2.credentials import (
    PROVIDER_KEY_NAMES,
    assert_no_secret_values,
    key_present,
    probe,
)
from tests.gate_p2.run import run_gate_p2


def test_key_present_is_boolean_only():
    for name in PROVIDER_KEY_NAMES:
        assert key_present(name) in {True, False}


def test_probe_payload_has_no_secret_values():
    payload = probe()
    assert_no_secret_values(payload)
    blob = json.dumps(payload)
    for name in PROVIDER_KEY_NAMES:
        value = os.getenv(name, "").strip()
        assert name in blob
        if value:
            assert value not in blob
    assert payload["KEY_PRESENT"]["OPENAI_API_KEY"] is False or payload["KEY_PRESENT"]["OPENAI_API_KEY"] is True
    assert payload["file_search_in_scope"] is False


def test_gate_p2_stops_when_injection_impossible():
    payload = run_gate_p2()
    assert_no_secret_values(payload)
    if not payload["secure_environment"]["safe_injection_possible"]:
        assert payload["gate_p2_status"] == "BLOCKED"
        assert payload["secure_environment"]["action"] == "STOP"
        for name in ("OPENAI", "CLAUDE", "GEMINI"):
            assert payload["providers"][name]["correct"] == "BLOCKED"
            assert payload["providers"][name]["DOCUMENT_AVAILABLE_TO_MODEL"] == "NOT_RUN"
        assert payload["comparison"]["BEN_PREFIX_2000"]["decisive_span_recall"] == "21/43"
        assert payload["comparison"]["BEN_EXISTING_FTS"]["decisive_span_recall"] == "42/43"


def test_gold_pack_untouched():
    from tests.gate_m.gold_questions import QUESTIONS

    assert len(QUESTIONS) == 50
    assert sum(1 for q in QUESTIONS if q["answerable"]) == 43
