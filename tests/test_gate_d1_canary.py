"""Gate D1 harness tests. Does not hit production or enable global FTS."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from services.workspace_files.chunk_retriever import chunk_retrieval_enabled
from tests.gate_d1.decision import decide_gate_d1
from tests.gate_d1.prod_client import parse_stream_line, score_done, thread_polluted
from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import WS


def test_gold_set_is_unmodified_fifty_questions():
    assert len(QUESTIONS) == 50
    assert {q["question_id"] for q in QUESTIONS} == {f"M{i:02d}" for i in range(1, 51)}


def test_chunk_fts_remains_off_without_allowlist(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", raising=False)
    assert chunk_retrieval_enabled(WS) is False


def test_allowlist_does_not_enable_other_workspaces(monkeypatch):
    canary = uuid.uuid4()
    other = uuid.uuid4()
    monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", "on")
    monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", str(canary))
    assert chunk_retrieval_enabled(canary) is True
    assert chunk_retrieval_enabled(other) is False


def test_parse_ndjson_and_sse_done_lines():
    payload = {"type": "done", "retrieval_mode": "chunks", "chunks_selected": 3}
    raw = json.dumps(payload)
    assert parse_stream_line(raw)["retrieval_mode"] == "chunks"
    assert parse_stream_line(f"data: {raw}")["chunks_selected"] == 3
    assert parse_stream_line("data: [DONE]") is None


def test_score_done_requires_observable_chunk_evidence():
    q = next(x for x in QUESTIONS if x["question_id"] == "M01")
    chunk = str(uuid.uuid4())
    fid = str(uuid.uuid4())
    done = {
        "retrieval_mode": "chunks",
        "chunks_selected": 1,
        "evidence_pages": [1],
        "workspace_files_used": [{"id": fid, "name": "ben_gold_supply_agreement.pdf"}],
        "response_evidence": {
            "retrieval_mode": "chunks",
            "sources": [{"source_id": fid, "source_type": "workspace_file", "display_name": "gold.pdf"}],
            "evidence": [
                {
                    "chunk_id": chunk,
                    "source_id": fid,
                    "excerpt": "Agreement ID: BEN-GOLD-SA-2026-001",
                    "page": 1,
                }
            ],
        },
        "response": "The agreement ID is BEN-GOLD-SA-2026-001",
        "thread_id": str(uuid.uuid4()),
    }
    row = score_done(q, done, latency_ms=12.0, http_status=200)
    assert row["retrieval_mode"] == "chunks"
    assert row["chunk_ids"] == [chunk]
    assert row["decisive_in_injected"] is True
    assert row["pass"] is True
    assert row["pollution"] is False


def test_score_done_does_not_infer_fts_from_answer_quality():
    q = next(x for x in QUESTIONS if x["question_id"] == "M01")
    done = {
        "retrieval_mode": "off",
        "fallback_reason": "flag_off",
        "chunks_selected": 0,
        "evidence_pages": [],
        "workspace_files_used": [],
        "response": "The agreement ID is BEN-GOLD-SA-2026-001",
        "response_evidence": {
            "retrieval_mode": "prefix_fallback",
            "sources": [],
            "evidence": [{"excerpt": "Agreement ID: BEN-GOLD-SA-2026-001", "origin": "ben_retrieval"}],
        },
    }
    row = score_done(q, done, latency_ms=9.0, http_status=200)
    assert row["chunk_ids"] == []
    assert row["retrieval_mode"] == "off"


def test_thread_pollution_detects_workspace_files_tag():
    assert thread_polluted({"messages": [{"content": "<workspace_files retrieval_mode=\"chunks\">"}]})
    assert not thread_polluted({"messages": [{"content": "Agreement ID is cited."}]})


def test_decide_pass_for_gate_p_like_canary():
    rows = []
    for q in QUESTIONS:
        mrl = q["question_id"] == "M09"
        rows.append(
            {
                "question_id": q["question_id"],
                "answerable": q["answerable"],
                "category": q["category"],
                "position": "LATE" if q["question_id"] == "M23" else "EARLY",
                "http_status": 200,
                "retrieval_mode": "chunks",
                "chunk_ids": ["11111111-1111-1111-1111-111111111111"],
                "pass": not mrl,
                "mrl": mrl,
                "decisive_in_injected": q["question_id"] != "M09",
                "failure_category": "RANKING_FAILURE" if mrl else None,
            }
        )
    artifacts = {
        "pre_health": {"version": "44ef277d65eb0675ad9f23d6922209d66d2f5728"},
        "fts_actually_used": True,
        "retrieval_modes_observed": ["chunks"],
        "canary_summary": {
            "decisive_span_recall": "42/43",
            "mrl": 1,
            "late": "17/17",
            "exceptions": "5/5",
            "unanswerable_precision": "7/7",
        },
        "control_rows": [{"retrieval_mode": "off", "chunk_ids": []}],
        "canary_rows": rows,
        "fallback": {"http_status": 200, "error_500": False},
        "transcript_pollution": "PASS",
        "cleanup": {"canary_get_after": 404, "control_get_after": 404},
        "allowlist_reverted": True,
    }
    decision = decide_gate_d1(artifacts)
    assert decision["status"] == "PASS"
    assert decision["rollout"] == "NOT YET"


def test_decide_fail_when_control_uses_fts():
    decision = decide_gate_d1(
        {
            "pre_health": {"version": "44ef277d65eb0675ad9f23d6922209d66d2f5728"},
            "fts_actually_used": True,
            "retrieval_modes_observed": ["chunks"],
            "canary_summary": {
                "decisive_span_recall": "42/43",
                "mrl": 1,
                "late": "17/17",
                "exceptions": "5/5",
                "unanswerable_precision": "7/7",
            },
            "control_rows": [{"retrieval_mode": "chunks", "chunk_ids": ["abc"]}],
            "canary_rows": [{"question_id": "M01", "http_status": 200, "mrl": False}],
            "fallback": {"http_status": 200, "error_500": False},
            "transcript_pollution": "PASS",
            "cleanup": {"canary_get_after": 404, "control_get_after": 404},
            "allowlist_reverted": True,
        }
    )
    assert decision["status"] == "FAIL"
    assert decision["rollout"] == "NO"


def test_committed_canary_result_is_isolated_pass():
    payload = json.loads(
        (Path(__file__).resolve().parents[0] / "fixtures" / "gate_d1" / "canary_result.json").read_text()
    )
    assert payload["status"] == "PASS"
    assert payload["rollout"] == "NOT YET"
    assert payload["fts_actually_used"] is True
    assert payload["retrieval_mode_observed"] == "chunks"
    assert payload["decisive_span_recall"] == "42/43"
    assert payload["mrl"] == "1/50"
    assert payload["non_canary_workspace_unaffected"] == "PASS"
    assert payload["cleanup"]["allowlist_reverted"] is True
