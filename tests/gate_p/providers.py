"""Isolated provider document probes. Never mutates BEN production adapters."""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import httpx

from services.providers.model_registry import resolve_api_model, token_rates
from tests.gate_m.gold_document import GOLD_FILENAME
from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import decisive_in_source
from tests.gate_p.position import question_position
from tests.gate_p.score import (
    NEUTRAL_INSTRUCTIONS,
    citation_page_ok,
    evidence_overlap,
    model_answer_ok,
    parse_answer_json,
    summarize_rows,
)

TIMEOUT_S = 90.0
MAX_TOKENS = 800
TEMPERATURE = 0


def _cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    rates = token_rates(provider, model)
    if not rates:
        return 0.0
    inp, out = rates
    return float(input_tokens) * float(inp) + float(output_tokens) * float(out)


def _usage(payload: dict[str, Any]) -> tuple[int, int]:
    usage = payload.get("usage") or payload.get("usageMetadata") or {}
    inn = usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("promptTokenCount") or 0
    out = usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("candidatesTokenCount") or 0
    return int(inn or 0), int(out or 0)


def blocked_mode(mode: str, reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "mode": mode,
        "status": "BLOCKED",
        "blocked_reason": reason,
        "summary": {
            "methodology": "not_run",
            "questions": 50,
            "answer_correctness": "BLOCKED",
            "decisive_span_recall": "BLOCKED",
            "mrl": "BLOCKED",
            "unanswerable_precision": "BLOCKED",
            "early": "BLOCKED",
            "middle": "BLOCKED",
            "late": "BLOCKED",
            "exceptions": "BLOCKED",
            "multi_hop": "BLOCKED",
            "global": "BLOCKED",
            "table": "BLOCKED",
            "mean_latency_ms": None,
            "input_tokens": None,
            "output_tokens": None,
            "approx_cost_usd": None,
        },
        "rows": [],
    }
    if extra:
        payload.update(extra)
    return payload


def _question_prompt(question: dict[str, Any]) -> str:
    return (
        f"{NEUTRAL_INSTRUCTIONS}\n\nQuestion:\n{question['question']}\n"
    )


def _score_provider_row(
    question: dict[str, Any],
    *,
    raw_text: str,
    retrieved_texts: list[str] | None,
    recall_status: str,
    in_source: bool,
    latency_ms: float,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    model: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = parse_answer_json(raw_text)
    answer_ok = model_answer_ok(question, parsed)
    if recall_status == "NOT_OBSERVABLE":
        in_injected = None
    elif recall_status == "full_document":
        in_injected = bool(in_source)
    else:
        in_injected = evidence_overlap(question, retrieved_texts or [])
    passed = bool(answer_ok)
    mrl = False
    fail_cat = None
    if question["answerable"] and recall_status != "NOT_OBSERVABLE" and in_injected is False:
        mrl = True
        if question["category"] == "EXCEPTION":
            fail_cat = "EXCEPTION_MISSED"
        elif question["category"] == "MULTI_HOP":
            fail_cat = "MULTI_HOP"
        elif question["category"] == "GLOBAL":
            fail_cat = "GLOBAL_QUESTION"
        else:
            fail_cat = "CONTEXT_LOSS"
        passed = False
    elif not answer_ok:
        fail_cat = "UNANSWERABLE_FAILURE" if not question["answerable"] else "MODEL_REASONING_FAILURE"
    row = {
        "question_id": question["question_id"],
        "question": question["question"],
        "category": question["category"],
        "answerable": question["answerable"],
        "gold_answer": question["gold_answer"],
        "position": question_position(question),
        "model": model,
        "raw_preview": (raw_text or "")[:400],
        "parsed": parsed,
        "model_answer_ok": answer_ok,
        "extractive_answer_ok": None,
        "decisive_in_source": in_source,
        "decisive_in_injected": in_injected,
        "decisive_recall_status": recall_status,
        "pass": passed,
        "mrl": mrl,
        "failure_category": fail_cat,
        "citation_page_accuracy": citation_page_ok(question, parsed),
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
    }
    if extra:
        row.update(extra)
    return row


def _run_question_loop(
    *,
    mode: str,
    model: str,
    methodology: str,
    recall_status: str,
    extracted: dict[str, Any],
    ask,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for question in QUESTIONS:
        in_source = decisive_in_source(question, extracted["extracted_text"], extracted["pages"])
        t0 = time.perf_counter()
        try:
            raw_text, usage, retrieved, extra = ask(question)
            err = None
        except Exception as exc:
            raw_text, usage, retrieved, extra = "", (0, 0), None, {"error": type(exc).__name__}
            err = type(exc).__name__
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        inn, out = usage
        provider_key = (
            "openai"
            if "OPENAI" in mode
            else "anthropic"
            if "CLAUDE" in mode
            else "google"
            if "GEMINI" in mode
            else "xai"
        )
        row = _score_provider_row(
            question,
            raw_text=raw_text,
            retrieved_texts=retrieved,
            recall_status=recall_status,
            in_source=in_source,
            latency_ms=latency_ms,
            input_tokens=inn,
            output_tokens=out,
            cost_usd=_cost_usd(provider_key, model, inn, out),
            model=model,
            extra=extra,
        )
        if err:
            row["pass"] = False
            row["failure_category"] = "PROCESSING_FAILURE"
            row["error"] = err
        rows.append(row)
    return {
        "mode": mode,
        "status": "MEASURED",
        "model": model,
        "summary": summarize_rows(rows, methodology=methodology),
        "rows": rows,
    }


def run_openai_native(pdf_bytes: bytes, extracted: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return blocked_mode("OPENAI_NATIVE_DOCUMENT", "OPENAI_API_KEY missing")
    model = resolve_api_model("openai", "gpt-5.5-instant")
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    file_data = f"data:application/pdf;base64,{b64}"

    def ask(question: dict[str, Any]):
        body = {
            "model": model,
            "temperature": TEMPERATURE,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_file", "filename": GOLD_FILENAME, "file_data": file_data},
                        {"type": "input_text", "text": _question_prompt(question)},
                    ],
                }
            ],
        }
        with httpx.Client(timeout=TIMEOUT_S) as cx:
            r = cx.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
            )
            r.raise_for_status()
            data = r.json()
        text = ""
        for item in data.get("output") or []:
            for block in item.get("content") or []:
                if block.get("type") in {"output_text", "text"}:
                    text += block.get("text") or ""
        return text, _usage(data), None, {"provider_request_id": data.get("id")}

    result = _run_question_loop(
        mode="OPENAI_NATIVE_DOCUMENT",
        model=model,
        methodology="model_json_over_full_pdf",
        recall_status="full_document",
        extracted=extracted,
        ask=ask,
    )
    result["processes_entire_pdf"] = True
    result["performs_retrieval"] = False
    result["retrieved_chunks_observable"] = False
    result["citations_observable"] = False
    result["preserves_page_layout"] = "provider_page_images_plus_text"
    return result


def run_openai_file_search(pdf_bytes: bytes, extracted: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return blocked_mode(
            "OPENAI_FILE_SEARCH",
            "OPENAI_API_KEY missing; also requires ephemeral vector store not present in BEN",
        )
    # Additional infra required even with a key. Do not create paid vector stores
    # from this gate unless the key is present AND a caller opts in.
    if os.getenv("GATE_P_ALLOW_OPENAI_VECTOR_STORE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return blocked_mode(
            "OPENAI_FILE_SEARCH",
            "Additional setup required: OpenAI vector store. "
            "Not present in BEN. Isolated create skipped (set GATE_P_ALLOW_OPENAI_VECTOR_STORE=on to opt in).",
            extra={"additional_setup_required": True},
        )
    return blocked_mode("OPENAI_FILE_SEARCH", "opt-in vector store path not executed in this run")


def run_claude_native(pdf_bytes: bytes, extracted: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return blocked_mode("CLAUDE_NATIVE_DOCUMENT", "ANTHROPIC_API_KEY missing")
    model = resolve_api_model("anthropic", "claude-sonnet-4.6")
    b64 = base64.b64encode(pdf_bytes).decode("ascii")

    def ask(question: dict[str, Any]):
        body = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                            "citations": {"enabled": True},
                        },
                        {"type": "text", "text": _question_prompt(question)},
                    ],
                }
            ],
        }
        with httpx.Client(timeout=TIMEOUT_S) as cx:
            r = cx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            r.raise_for_status()
            data = r.json()
        text = ""
        cited: list[str] = []
        pages: list[int] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text += block.get("text") or ""
                for cit in block.get("citations") or []:
                    if cit.get("cited_text"):
                        cited.append(cit["cited_text"])
                    if cit.get("start_page_number") is not None:
                        pages.append(int(cit["start_page_number"]))
        extra = {"citation_pages": pages, "provider_request_id": data.get("id")}
        retrieved = cited or None
        return text, _usage(data), retrieved, extra

    result = _run_question_loop(
        mode="CLAUDE_NATIVE_DOCUMENT",
        model=model,
        methodology="model_json_over_full_pdf_with_citations",
        recall_status="full_document",
        extracted=extracted,
        ask=ask,
    )
    result["processes_entire_pdf"] = True
    result["performs_retrieval"] = False
    result["retrieved_chunks_observable"] = "citations_only"
    result["citations_observable"] = True
    result["preserves_page_layout"] = "pdf_text_plus_page_images_when_citations_enabled"
    return result


def run_claude_provider_retrieval() -> dict[str, Any]:
    return blocked_mode(
        "CLAUDE_PROVIDER_RETRIEVAL",
        "No applicable Anthropic API retrieval product for this document QA task. "
        "Files API is upload/reuse. Project RAG is claude.ai-only.",
    )


def run_gemini_native(pdf_bytes: bytes, extracted: dict[str, Any]) -> dict[str, Any]:
    key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        return blocked_mode("GEMINI_NATIVE_PDF", "GOOGLE_API_KEY / GEMINI_API_KEY missing")
    model = resolve_api_model("google", "gemini-3.5-flash")
    b64 = base64.b64encode(pdf_bytes).decode("ascii")

    def ask(question: dict[str, Any]):
        body = {
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": "application/pdf", "data": b64}},
                        {"text": _question_prompt(question)},
                    ]
                }
            ],
            "generationConfig": {"temperature": TEMPERATURE, "maxOutputTokens": MAX_TOKENS},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        with httpx.Client(timeout=TIMEOUT_S) as cx:
            r = cx.post(url, params={"key": key}, json=body)
            r.raise_for_status()
            data = r.json()
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        return text, _usage(data), None, {}

    result = _run_question_loop(
        mode="GEMINI_NATIVE_PDF",
        model=model,
        methodology="model_json_over_full_pdf",
        recall_status="full_document",
        extracted=extracted,
        ask=ask,
    )
    result["processes_entire_pdf"] = True
    result["performs_retrieval"] = False
    result["retrieved_chunks_observable"] = False
    result["citations_observable"] = False
    result["preserves_page_layout"] = "native_pdf_vision"
    return result


def run_gemini_file_search() -> dict[str, Any]:
    key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        return blocked_mode(
            "GEMINI_FILE_SEARCH",
            "GOOGLE_API_KEY missing; also requires a File Search store not present in BEN",
        )
    return blocked_mode(
        "GEMINI_FILE_SEARCH",
        "Additional setup required: Gemini File Search store. Not present in BEN. "
        "Isolated store creation skipped.",
        extra={"additional_setup_required": True},
    )


def run_grok_native(pdf_bytes: bytes, extracted: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("XAI_API_KEY", "").strip()
    if not key:
        return blocked_mode("GROK_NATIVE_DOCUMENT", "XAI_API_KEY missing")
    model = resolve_api_model("xai", "grok-4.6")
    # Current BEN adapter is Chat Completions and cannot attach PDFs. Isolated
    # path uses Files + Responses. Upload one file, then ask.
    files = {"file": (GOLD_FILENAME, pdf_bytes, "application/pdf")}
    try:
        with httpx.Client(timeout=TIMEOUT_S) as cx:
            up = cx.post(
                "https://api.x.ai/v1/files",
                headers={"Authorization": f"Bearer {key}"},
                data={"purpose": "assistants"},
                files=files,
            )
            up.raise_for_status()
            file_id = up.json().get("id")
            if not file_id:
                return blocked_mode("GROK_NATIVE_DOCUMENT", "xAI Files upload returned no id")

            def ask(question: dict[str, Any]):
                body = {
                    "model": model,
                    "temperature": TEMPERATURE,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_file", "file_id": file_id},
                                {"type": "input_text", "text": _question_prompt(question)},
                            ],
                        }
                    ],
                }
                r = cx.post(
                    "https://api.x.ai/v1/responses",
                    headers={"Authorization": f"Bearer {key}"},
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                text = ""
                for item in data.get("output") or data.get("choices") or []:
                    if isinstance(item, dict):
                        if item.get("content"):
                            for block in item["content"]:
                                if isinstance(block, dict):
                                    text += block.get("text") or block.get("output_text") or ""
                        msg = (item.get("message") or {})
                        text += str(msg.get("content") or "")
                return text, _usage(data), None, {"file_id_present": True}

            result = _run_question_loop(
                mode="GROK_NATIVE_DOCUMENT",
                model=model,
                methodology="model_json_over_attached_pdf",
                recall_status="NOT_OBSERVABLE",
                extracted=extracted,
                ask=ask,
            )
            result["processes_entire_pdf"] = "unknown_provider_internal"
            result["performs_retrieval"] = "attachment_search_implicit"
            result["retrieved_chunks_observable"] = False
            result["citations_observable"] = False
            result["preserves_page_layout"] = "unknown"
            try:
                cx.delete(
                    f"https://api.x.ai/v1/files/{file_id}",
                    headers={"Authorization": f"Bearer {key}"},
                )
            except Exception:
                pass
            return result
    except httpx.HTTPStatusError as exc:
        return blocked_mode("GROK_NATIVE_DOCUMENT", f"xAI HTTP {exc.response.status_code}")
    except Exception as exc:
        return blocked_mode("GROK_NATIVE_DOCUMENT", type(exc).__name__)
