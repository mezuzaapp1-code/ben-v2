"""Load the committed Gate M prefix baseline. Does not rerun prefix retrieval."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_p.position import position_bucket
from tests.gate_p.score import summarize_mode

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "gate_m" / "gate_m_baseline.json"
QUESTION_BY_ID = {q["question_id"]: q for q in QUESTIONS}


def load_prefix_mode() -> dict[str, Any]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for raw in payload.get("rows") or []:
        gold = QUESTION_BY_ID[raw["question_id"]]
        row = dict(raw)
        row["position"] = position_bucket(gold)
        row["mode"] = "BEN_PREFIX_2000"
        rows.append(row)
    extra = {
        "status": "measured",
        "retrieval_kind": "prefix",
        "processes_entire_pdf": False,
        "performs_retrieval": False,
        "retrieved_chunks_observable": True,
        "citations_observable": False,
        "preserves_page_layout": False,
        "model_id": "extractive-oracle-over-prefix",
        "source": "committed Gate M baseline (not rerun)",
        "per_file_max_chars": payload.get("summary", {}).get("per_file_max_chars"),
        "extracted_chars": payload.get("summary", {}).get("extracted_chars"),
        "prefix_chars": payload.get("summary", {}).get("prefix_chars"),
        "retrieval_mode_observed": payload.get("summary", {}).get("retrieval_mode_observed"),
    }
    return {
        "mode": "BEN_PREFIX_2000",
        "summary": summarize_mode("BEN_PREFIX_2000", rows, extra=extra),
        "rows": rows,
    }
