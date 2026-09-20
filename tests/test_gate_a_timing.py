"""Gate A instrumentation: default-off, no payload/yield change, bounded overhead."""
from __future__ import annotations

import time

from services.ops.latency_path_audit import (
    audit_enabled,
    audit_snapshot,
    configure_audit_for_process,
    mark,
    reset_latency_audit,
)
from services.providers.gemini_provider import GeminiProvider


def test_gate_a_disabled_is_noop():
    configure_audit_for_process(enabled=False)
    assert audit_enabled() is False
    mark("t0_accepted")
    mark("p4_answer")
    assert audit_snapshot() == {}


def test_gate_a_snapshot_aliases_and_attempt():
    configure_audit_for_process(enabled=True)
    reset_latency_audit(path="gate_a", provider="google", model="gemini-3.8-flash")
    mark("t0_accepted")
    mark("t1_auth")
    mark("admission_skipped")
    mark("db_thread_ready")
    mark("files_skipped")
    mark("t2_context")
    mark("t3_routing")
    mark("httpx_client_ready")
    mark("t4_http_start")
    mark("t5_http_headers")
    mark("t6_first_sse")
    mark("p3_reasoning")
    mark("p4_answer")
    mark("t7_first_content")
    mark("t8_ben_forward")
    mark("t9_provider_end")
    mark("t10_ben_complete")
    snap = audit_snapshot()
    ga = snap["gate_a"]
    assert ga["ADMISSION"] == "skipped_on_chat_stream"
    assert ga["Pb_first_body"] == "UNOBSERVABLE"
    assert snap["provider_first_answer_ms"] is not None
    configure_audit_for_process(enabled=False)


def test_gemini_payload_unchanged_with_audit_on():
    configure_audit_for_process(enabled=True)
    reset_latency_audit(path="payload")
    a = GeminiProvider()._payload("ping", None)
    configure_audit_for_process(enabled=False)
    b = GeminiProvider()._payload("ping", None)
    assert a == b
    assert "generationConfig" not in a
    assert "thinkingConfig" not in a


def test_gemini_thought_then_answer_marks_without_changing_yields():
    configure_audit_for_process(enabled=True)
    reset_latency_audit(path="thought")

    class _Stream:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"candidates":[{"content":{"parts":[{"thought":true,"text":"hmm"}]}}]}'
            yield 'data: {"candidates":[{"content":{"parts":[{"text":"1973"}]}}]}'

    class _Cx:
        def stream(self, *a, **k):
            return _Stream()

    import os
    os.environ["GOOGLE_API_KEY"] = os.environ.get("GOOGLE_API_KEY", "") or "audit-dummy-not-a-secret"

    async def _run():
        chunks = []
        async for item in GeminiProvider().stream_message(
            _Cx(),
            model="gemini-3.8-flash",
            message="ping",
            tenant_id="00000000-0000-0000-0000-000000000001",
        ):
            if isinstance(item, str):
                chunks.append(item)
        return chunks

    chunks = __import__("asyncio").run(_run())
    assert chunks == ["hmm", "1973"]
    extra = audit_snapshot()["extra"]
    assert extra.get("p3_reasoning", {}).get("source") == "gemini_thought"
    assert extra.get("p4_answer", {}).get("source") == "gemini_text"
    from services.ops.json_log_formatter import STRUCTURED_FIELDS
    from services.ops.latency_path_audit import log_latency_audit

    snap = log_latency_audit()
    assert "P0_to_P4_ms" in (snap.get("gate_a") or {}) or snap.get("provider_first_answer_ms") is not None
    for key in (
        "attempt_id",
        "P0_to_P4_ms",
        "B0_to_B1_ms",
        "provider_first_answer_ms",
        "AUTH_ms",
    ):
        assert key in STRUCTURED_FIELDS
    configure_audit_for_process(enabled=False)


def test_gemini_http_error_still_raises_with_audit_on():
    """Cancellation/error path: instrumentation must not swallow provider HTTP errors."""
    configure_audit_for_process(enabled=True)
    reset_latency_audit(path="error")

    class _Err:
        status_code = 500

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def raise_for_status(self):
            req = __import__("httpx").Request("POST", "https://generativelanguage.googleapis.com/x")
            resp = __import__("httpx").Response(500, request=req)
            raise __import__("httpx").HTTPStatusError("HTTP 500", request=req, response=resp)

        async def aiter_lines(self):
            if False:
                yield ""

    class _Cx:
        def stream(self, *a, **k):
            return _Err()

    import os

    os.environ["GOOGLE_API_KEY"] = os.environ.get("GOOGLE_API_KEY", "") or "audit-dummy-not-a-secret"

    async def _run():
        async for _ in GeminiProvider().stream_message(
            _Cx(),
            model="gemini-3.8-flash",
            message="ping",
            tenant_id="00000000-0000-0000-0000-000000000001",
        ):
            pass

    try:
        __import__("asyncio").run(_run())
        raise AssertionError("expected HTTPStatusError")
    except __import__("httpx").HTTPStatusError:
        pass
    configure_audit_for_process(enabled=False)


def test_instrumentation_overhead_off_vs_on():
    def _burst(n=2000):
        t = time.perf_counter()
        for i in range(n):
            mark("t0_accepted")
            mark("t7_first_content")
        return (time.perf_counter() - t) * 1000.0

    configure_audit_for_process(enabled=False)
    off_ms = _burst()
    configure_audit_for_process(enabled=True)
    reset_latency_audit()
    on_ms = _burst()
    configure_audit_for_process(enabled=False)
    # First-write-wins: ON path is a handful of dict writes, not per-token logs.
    assert on_ms < 50
    assert off_ms < 50


def test_json_log_emits_gate_a_timings_without_payload():
    import json
    import logging

    from services.ops.json_log_formatter import BenOpsJsonFormatter

    record = logging.LogRecord(
        name="ben.ops",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="latency path audit",
        args=(),
        exc_info=None,
    )
    record.subsystem = "latency_path_audit"
    record.P0_to_P4_ms = 123.4
    record.B0_to_B1_ms = 18.0
    record.attempt_id = 1
    record.message_text = "should-not-leak"
    payload = json.loads(BenOpsJsonFormatter().format(record))
    assert payload["P0_to_P4_ms"] == 123.4
    assert payload["B0_to_B1_ms"] == 18.0
    assert payload["attempt_id"] == 1
    assert "should-not-leak" not in payload.values()
    assert "Authorization" not in json.dumps(payload)


def test_health_latency_path_audit_flag_reads_env(monkeypatch):
    from services.health_service import env_checks

    monkeypatch.delenv("BEN_LATENCY_PATH_AUDIT", raising=False)
    assert env_checks()["latency_path_audit"] is False
    monkeypatch.setenv("BEN_LATENCY_PATH_AUDIT", "1")
    assert env_checks()["latency_path_audit"] is True
    monkeypatch.setenv("BEN_LATENCY_PATH_AUDIT", "0")
    assert env_checks()["latency_path_audit"] is False


def test_thread_resolve_micro_timing_derived_durations():
    configure_audit_for_process(enabled=True)
    reset_latency_audit(path="chat_stream")
    mark("t0_accepted")
    time.sleep(0.002)
    mark("t1_auth")
    time.sleep(0.003)
    mark("TR0")
    time.sleep(0.001)
    mark("TR1")
    time.sleep(0.004)
    mark("TR2")
    time.sleep(0.005)
    mark("TR3")
    time.sleep(0.006)
    mark("TR4")
    time.sleep(0.002)
    mark("TR5")
    time.sleep(0.001)
    mark("TR6")
    mark("db_thread_ready")
    snap = audit_snapshot()
    ga = snap["gate_a"]
    assert ga["DB_thread_ms"] is not None
    assert ga["thread_resolve_ms"] is not None
    assert ga["session_checkout_ms"] is not None
    assert ga["set_config_ms"] is not None
    assert ga["insert_flush_ms"] is not None
    assert ga["commit_ms"] is not None
    assert ga["sqlite_metadata_ms"] is not None
    assert ga["return_tail_ms"] is not None
    assert ga["pre_thread_overhead_ms"] == round(ga["DB_thread_ms"] - ga["thread_resolve_ms"], 1)
    assert ga["thread_resolve_ms"] >= 18
    assert ga["set_config_ms"] >= 3
    assert ga["insert_flush_ms"] >= 4
    assert ga["commit_ms"] >= 5
    assert ga["pool_pre_ping"] == "UNOBSERVABLE"
    # Historical alias is unchanged: still t1_auth → db_thread_ready.
    assert ga["DB_thread_ms"] >= ga["thread_resolve_ms"]
    from services.ops.json_log_formatter import STRUCTURED_FIELDS

    for key in (
        "thread_resolve_ms",
        "session_checkout_ms",
        "set_config_ms",
        "insert_flush_ms",
        "commit_ms",
        "sqlite_metadata_ms",
        "return_tail_ms",
        "pre_thread_overhead_ms",
    ):
        assert key in STRUCTURED_FIELDS
    configure_audit_for_process(enabled=False)


def test_thread_resolve_marks_are_noop_when_audit_disabled():
    configure_audit_for_process(enabled=False)
    mark("TR0")
    mark("TR6")
    assert audit_snapshot() == {}


def test_resolve_thread_id_source_keeps_original_db_sequence():
    from pathlib import Path

    src = Path("services/thread_service.py").read_text()
    start = src.index("async def resolve_thread_id")
    end = src.index("async def create_conversation_thread")
    body = src[start:end]
    assert 'mark("TR0")' in body
    assert 'mark("TR1")' in body
    assert 'mark("TR2")' in body
    assert 'mark("TR3")' in body
    assert 'mark("TR4")' in body
    assert 'mark("TR5")' in body
    assert 'mark("TR6")' in body
    assert body.index('mark("TR0")') < body.index("get_db_session()")
    assert body.index('mark("TR1")') < body.index("await _set_org")
    assert body.index("await _set_org") < body.index('mark("TR2")')
    assert body.index("await session.flush()") < body.index('mark("TR3")')
    assert body.index("await session.commit()") < body.index('mark("TR4")')
    assert body.index("upsert_thread_metadata") < body.index('mark("TR5")')
    assert body.count("await session.flush()") == 1
    assert body.count("await session.commit()") == 1
    assert body.count("await _set_org") == 1
    assert "pool_pre_ping=" not in body
    assert "create_async_engine" not in body


def test_context_micro_timing_derived_durations():
    configure_audit_for_process(enabled=True)
    reset_latency_audit(path="chat_stream")
    mark("db_thread_ready")
    time.sleep(0.002)
    mark("CX0")
    time.sleep(0.003)
    mark("CX1")
    time.sleep(0.001)
    mark("CX2")
    time.sleep(0.004)
    mark("CX3")
    time.sleep(0.005)
    mark("CX4")
    time.sleep(0.006)
    mark("CX5")
    time.sleep(0.001)
    mark("CX6")
    time.sleep(0.007)
    mark("CX7")
    time.sleep(0.002)
    mark("files_skipped")
    mark("t2_context")
    ga = audit_snapshot()["gate_a"]
    assert ga["CONTEXT_ms"] is not None
    assert ga["context_preamble_ms"] is not None
    assert ga["sqlite_history_ms"] is not None
    assert ga["context_pg_session_ms"] is not None
    assert ga["context_set_config_ms"] is not None
    assert ga["context_thread_lookup_ms"] is not None
    assert ga["context_messages_query_ms"] is not None
    assert ga["context_history_compose_ms"] is not None
    assert ga["knowledge_inject_ms"] is not None
    assert ga["context_tail_ms"] is not None
    assert ga["CONTEXT_ms"] >= 30
    assert ga["context_set_config_ms"] >= 3
    assert ga["knowledge_inject_ms"] >= 6
    assert ga["set_config_ms"] is None
    from services.ops.json_log_formatter import STRUCTURED_FIELDS

    for key in (
        "context_preamble_ms",
        "sqlite_history_ms",
        "context_pg_session_ms",
        "context_set_config_ms",
        "context_thread_lookup_ms",
        "context_messages_query_ms",
        "context_history_compose_ms",
        "knowledge_inject_ms",
        "context_tail_ms",
    ):
        assert key in STRUCTURED_FIELDS
    configure_audit_for_process(enabled=False)


def test_context_marks_are_noop_when_audit_disabled():
    configure_audit_for_process(enabled=False)
    mark("CX0")
    mark("CX7")
    assert audit_snapshot() == {}


def test_context_history_load_source_keeps_original_db_sequence():
    from pathlib import Path

    src = Path("services/thread_service.py").read_text()
    start = src.index("async def _load_chat_history_messages")
    end = src.index("async def build_chat_message_with_thread_context")
    body = src[start:end]
    assert 'mark("CX0")' in body
    assert 'mark("CX1")' in body
    assert 'mark("CX2")' in body
    assert 'mark("CX3")' in body
    assert 'mark("CX4")' in body
    assert 'mark("CX5")' in body
    assert body.index('mark("CX0")') < body.index("list_thread_messages")
    assert body.index("list_thread_messages") < body.index('mark("CX1")')
    assert body.index('mark("CX2")') < body.index("await _set_org")
    assert body.index("await _set_org") < body.index('mark("CX3")')
    assert body.index("session.get(Thread") < body.index('mark("CX4")')
    assert "session.execute(msg_q)" in body
    assert body.count("await _set_org") == 1
    assert body.count("get_db_session()") == 1
    assert "pool_pre_ping=" not in body

    app = Path("services/chat_service.py").read_text()
    std = app[
        app.index("live_user_text = expand_user_message_for_provider") : app.index(
            "if vision_user_content:"
        )
    ]
    assert 'mark("CX6")' in std
    assert 'mark("CX7")' in std
    assert std.index('mark("CX6")') < std.index("await inject_knowledge_few_shot")
    assert std.index("await inject_knowledge_few_shot") < std.index('mark("CX7")')
