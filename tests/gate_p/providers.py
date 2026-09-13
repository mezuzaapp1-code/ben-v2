"""Isolated provider document modes. Uses existing env credentials only.

Does not modify BEN adapters, production flags, or provider defaults.
Cleans up uploaded files/stores when a live run happens.
"""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import httpx

from services.providers.anthropic_provider import ANTHROPIC_FAST_MODEL
from services.providers.gemini_provider import GEMINI_FAST_MODEL
from services.providers.model_registry import resolve_api_model, token_rates
from services.providers.openai_provider import OPENAI_CHAT_FAST_MODEL
from services.providers.xai_provider import XAI_FLAGSHIP_MODEL
from tests.gate_m.gold_document import GOLD_FILENAME
from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import contains, locator_in_text
from tests.gate_p.capability import key_present
from tests.gate_p.position import position_bucket
from tests.gate_p.prompt import parse_answer_payload, user_prompt
from tests.gate_p.score import answer_text_ok, classify_row, summarize_mode

TIMEOUT = httpx.Timeout(90.0, connect=15.0)


def _blocked(mode: str, reason: str, **extra: Any) -> dict[str, Any]:
    summary = {
        "mode": mode,
        "status": "blocked",
        "blocked_reason": reason,
        "answer_correctness": "n/a",
        "decisive_span_recall": "NOT_OBSERVABLE",
        "mrl": None,
        "unanswerable_precision": "n/a",
        "citation_page_accuracy": "NOT_OBSERVABLE",
        "early": "n/a",
        "middle": "n/a",
        "late": "n/a",
        "exceptions": "n/a",
        "multi_hop": "n/a",
        "global": "n/a",
        "table": "n/a",
        "mean_latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "approx_cost_usd": None,
    }
    summary.update(extra)
    return {"mode": mode, "summary": summary, "rows": []}


def _cost(provider: str, model: str, inn: int, out: int) -> float:
    inp, oup = token_rates(provider, model, prompt_tokens=inn)
    return float(inn) * float(inp) + float(out) * float(oup)


def _evidence_blob(payload: dict[str, Any]) -> str:
    parts = [str(payload.get("answer") or "")]
    page = payload.get("page")
    if page is not None:
        parts.append(f"[[PAGE {page}]]")
    for item in payload.get("supporting_evidence") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("text") or ""))
            if item.get("page") is not None:
                parts.append(f"[[PAGE {item.get('page')}]]")
        else:
            parts.append(str(item))
    return "\n".join(parts)


def _score_live_row(
    question: dict[str, Any],
    *,
    raw_text: str,
    latency_ms: float,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    model_id: str,
    retrieval_kind: str,
    evidence_available: bool | None,
    observed_evidence: str,
    citation_pages: list[int],
    error: str | None,
) -> dict[str, Any]:
    parsed = parse_answer_payload(raw_text)
    answer = parsed.get("answer") or ""
    blob = _evidence_blob(parsed) + "\n" + observed_evidence
    in_source = True
    if evidence_available is True:
        locators = [span.get("text_or_locator") or "" for span in (question.get("decisive_evidence") or [])]
        quoted = all(locator_in_text(loc, blob) for loc in locators) if locators else True
        # Native/file-search internals are hidden unless returned quotes/chunks contain locators.
        if observed_evidence.strip() or citation_pages:
            in_injected_flag = quoted
        else:
            in_injected_flag = None
    elif evidence_available is False:
        in_injected_flag = False
    else:
        in_injected_flag = None

    if question["answerable"]:
        answer_ok = answer_text_ok(question, answer)
    else:
        if parsed.get("answerable") is False or contains(answer, "not established"):
            answer_ok = True
        else:
            answer_ok = answer_text_ok(question, answer)

    gold_pages = [s.get("page") for s in question.get("decisive_evidence") or [] if s.get("page")]
    citation_ok = None
    if citation_pages and gold_pages:
        citation_ok = any(p in citation_pages for p in gold_pages)
    elif parsed.get("page") and gold_pages:
        citation_ok = parsed.get("page") in gold_pages

    fail_cat, residual = classify_row(
        question,
        in_source=in_source,
        in_injected=in_injected_flag,
        answer_ok=answer_ok and error is None,
        processing_ok=error is None,
        retrieval_kind=retrieval_kind,
        retrieved_empty=not (observed_evidence or citation_pages),
    )
    mrl = residual == "MATERIAL_RECOVERABLE_LOSS"
    if question["answerable"]:
        passed = answer_ok and error is None
    else:
        passed = answer_ok and error is None
    return {
        "question_id": question["question_id"],
        "question": question["question"],
        "category": question["category"],
        "answerable": question["answerable"],
        "gold_answer": question["gold_answer"],
        "position": position_bucket(question),
        "model_id": model_id,
        "model_answer": answer,
        "model_answerable": parsed.get("answerable"),
        "parse_ok": parsed.get("parse_ok"),
        "decisive_in_source": True,
        "decisive_in_injected": in_injected_flag,
        "extractive_answer_ok": answer_ok,
        "pass": passed,
        "mrl": mrl,
        "failure_category": fail_cat,
        "residual": residual,
        "citation_pages": citation_pages,
        "citation_page_accuracy": citation_ok,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
        "error": error,
    }


async def _openai_native(pdf: bytes, model: str) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    b64 = base64.b64encode(pdf).decode("ascii")
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
        for question in QUESTIONS:
            body: dict[str, Any] = {
                "model": model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_file",
                                "filename": GOLD_FILENAME,
                                "file_data": f"data:application/pdf;base64,{b64}",
                            },
                            {"type": "input_text", "text": user_prompt(question["question"])},
                        ],
                    }
                ],
            }
            # Determinism where the model still accepts it.
            body_try = dict(body)
            body_try["temperature"] = 0
            t0 = time.perf_counter()
            error = None
            text = ""
            inn = out = 0
            try:
                r = await cx.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=body_try,
                )
                if r.status_code >= 400:
                    r = await cx.post(
                        "https://api.openai.com/v1/responses",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=body,
                    )
                r.raise_for_status()
                data = r.json()
                text = data.get("output_text") or ""
                if not text:
                    chunks = []
                    for item in data.get("output") or []:
                        for c in item.get("content") or []:
                            if c.get("text"):
                                chunks.append(c.get("text"))
                    text = "\n".join(chunks)
                usage = data.get("usage") or {}
                inn = int(usage.get("input_tokens") or 0)
                out = int(usage.get("output_tokens") or 0)
            except Exception as exc:
                error = type(exc).__name__
            latency = round((time.perf_counter() - t0) * 1000.0, 1)
            parsed = parse_answer_payload(text)
            pages = []
            if parsed.get("page"):
                pages.append(parsed["page"])
            rows.append(
                _score_live_row(
                    question,
                    raw_text=text,
                    latency_ms=latency,
                    input_tokens=inn,
                    output_tokens=out,
                    cost_usd=_cost("openai", model, inn, out),
                    model_id=model,
                    retrieval_kind="native",
                    evidence_available=True,
                    observed_evidence=_evidence_blob(parsed),
                    citation_pages=pages,
                    error=error,
                )
            )
    extra = {
        "status": "measured",
        "retrieval_kind": "native",
        "processes_entire_pdf": True,
        "performs_retrieval": False,
        "retrieved_chunks_observable": False,
        "citations_observable": False,
        "preserves_page_layout": True,
        "model_id": model,
        "decisive_span_recall_note": (
            "Full PDF was sent in the request. Returned citations are not a retrieval trace; "
            "decisive-span recall from hidden internals is NOT_OBSERVABLE unless quotes contain locators."
        ),
    }
    # Override recall to NOT_OBSERVABLE when no locator quotes.
    summary = summarize_mode("OPENAI_NATIVE_DOCUMENT", rows, extra=extra)
    if all(r.get("decisive_in_injected") is None for r in rows if r.get("answerable")):
        summary["decisive_span_recall"] = "NOT_OBSERVABLE"
    return {"mode": "OPENAI_NATIVE_DOCUMENT", "summary": summary, "rows": rows}


async def _openai_file_search(pdf: bytes, model: str) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {api_key}"}
    vector_id = None
    file_id = None
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
        try:
            up = await cx.post(
                "https://api.openai.com/v1/files",
                headers=headers,
                files={"file": (GOLD_FILENAME, pdf, "application/pdf"), "purpose": (None, "assistants")},
            )
            up.raise_for_status()
            file_id = up.json().get("id")
            vs = await cx.post("https://api.openai.com/v1/vector_stores", headers=headers, json={"name": "gate-p-gold"})
            vs.raise_for_status()
            vector_id = vs.json().get("id")
            attach = await cx.post(
                f"https://api.openai.com/v1/vector_stores/{vector_id}/files",
                headers=headers,
                json={"file_id": file_id},
            )
            attach.raise_for_status()
            for _ in range(30):
                st = await cx.get(f"https://api.openai.com/v1/vector_stores/{vector_id}/files/{file_id}", headers=headers)
                if st.status_code < 400 and (st.json().get("status") in {"completed", "ready"}):
                    break
                time.sleep(1.0)
            for question in QUESTIONS:
                t0 = time.perf_counter()
                error = None
                text = ""
                inn = out = 0
                observed = ""
                pages: list[int] = []
                try:
                    r = await cx.post(
                        "https://api.openai.com/v1/responses",
                        headers=headers,
                        json={
                            "model": model,
                            "input": user_prompt(question["question"]),
                            "tools": [{"type": "file_search", "vector_store_ids": [vector_id]}],
                            "include": ["file_search_call.results"],
                        },
                    )
                    r.raise_for_status()
                    data = r.json()
                    text = data.get("output_text") or ""
                    blobs = []
                    for item in data.get("output") or []:
                        if item.get("type") == "file_search_call":
                            for res in item.get("results") or []:
                                blobs.append(str(res.get("text") or ""))
                        for c in item.get("content") or []:
                            if c.get("text"):
                                text = text or c.get("text")
                            for ann in c.get("annotations") or []:
                                if ann.get("type") == "file_citation" and isinstance(ann.get("text"), str):
                                    blobs.append(ann.get("text"))
                    observed = "\n".join(blobs)
                    usage = data.get("usage") or {}
                    inn = int(usage.get("input_tokens") or 0)
                    out = int(usage.get("output_tokens") or 0)
                except Exception as exc:
                    error = type(exc).__name__
                latency = round((time.perf_counter() - t0) * 1000.0, 1)
                parsed = parse_answer_payload(text)
                if parsed.get("page"):
                    pages.append(parsed["page"])
                rows.append(
                    _score_live_row(
                        question,
                        raw_text=text,
                        latency_ms=latency,
                        input_tokens=inn,
                        output_tokens=out,
                        cost_usd=_cost("openai", model, inn, out),
                        model_id=model,
                        retrieval_kind="file_search",
                        evidence_available=True if observed else None,
                        observed_evidence=observed + "\n" + _evidence_blob(parsed),
                        citation_pages=pages,
                        error=error,
                    )
                )
        finally:
            if vector_id:
                try:
                    await cx.delete(f"https://api.openai.com/v1/vector_stores/{vector_id}", headers=headers)
                except Exception:
                    pass
            if file_id:
                try:
                    await cx.delete(f"https://api.openai.com/v1/files/{file_id}", headers=headers)
                except Exception:
                    pass
    extra = {
        "status": "measured",
        "retrieval_kind": "file_search",
        "processes_entire_pdf": False,
        "performs_retrieval": True,
        "retrieved_chunks_observable": True,
        "citations_observable": True,
        "preserves_page_layout": False,
        "model_id": model,
    }
    summary = summarize_mode("OPENAI_FILE_SEARCH", rows, extra=extra)
    if rows and all(r.get("decisive_in_injected") is None for r in rows if r.get("answerable")):
        summary["decisive_span_recall"] = "NOT_OBSERVABLE"
    return {"mode": "OPENAI_FILE_SEARCH", "summary": summary, "rows": rows}


async def _claude_native(pdf: bytes, model: str) -> dict[str, Any]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    b64 = base64.b64encode(pdf).decode("ascii")
    rows: list[dict[str, Any]] = []
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
        for question in QUESTIONS:
            t0 = time.perf_counter()
            error = None
            text = ""
            inn = out = 0
            observed = ""
            pages: list[int] = []
            try:
                r = await cx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json={
                        "model": model,
                        "max_tokens": 1024,
                        "temperature": 0,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "document",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "application/pdf",
                                            "data": b64,
                                        },
                                        "citations": {"enabled": True},
                                    },
                                    {"type": "text", "text": user_prompt(question["question"])},
                                ],
                            }
                        ],
                    },
                )
                r.raise_for_status()
                data = r.json()
                chunks = []
                cites = []
                for b in data.get("content") or []:
                    if b.get("type") == "text":
                        chunks.append(b.get("text") or "")
                        for c in b.get("citations") or []:
                            cites.append(str(c.get("cited_text") or ""))
                            if c.get("start_page_number"):
                                pages.append(int(c.get("start_page_number")))
                            if c.get("document_location", {}).get("page"):
                                pages.append(int(c["document_location"]["page"]))
                text = "\n".join(chunks)
                observed = "\n".join(cites)
                usage = data.get("usage") or {}
                inn = int(usage.get("input_tokens") or 0)
                out = int(usage.get("output_tokens") or 0)
            except Exception as exc:
                error = type(exc).__name__
            latency = round((time.perf_counter() - t0) * 1000.0, 1)
            parsed = parse_answer_payload(text)
            if parsed.get("page"):
                pages.append(parsed["page"])
            rows.append(
                _score_live_row(
                    question,
                    raw_text=text,
                    latency_ms=latency,
                    input_tokens=inn,
                    output_tokens=out,
                    cost_usd=_cost("anthropic", model, inn, out),
                    model_id=model,
                    retrieval_kind="native",
                    evidence_available=True,
                    observed_evidence=observed + "\n" + _evidence_blob(parsed),
                    citation_pages=pages,
                    error=error,
                )
            )
    extra = {
        "status": "measured",
        "retrieval_kind": "native",
        "processes_entire_pdf": True,
        "performs_retrieval": False,
        "retrieved_chunks_observable": False,
        "citations_observable": True,
        "preserves_page_layout": True,
        "model_id": model,
    }
    summary = summarize_mode("CLAUDE_NATIVE_DOCUMENT", rows, extra=extra)
    return {"mode": "CLAUDE_NATIVE_DOCUMENT", "summary": summary, "rows": rows}


async def _gemini_native(pdf: bytes, model: str) -> dict[str, Any]:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    b64 = base64.b64encode(pdf).decode("ascii")
    rows: list[dict[str, Any]] = []
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
        for question in QUESTIONS:
            t0 = time.perf_counter()
            error = None
            text = ""
            inn = out = 0
            try:
                r = await cx.post(
                    url,
                    params={"key": api_key},
                    json={
                        "contents": [
                            {
                                "parts": [
                                    {"inline_data": {"mime_type": "application/pdf", "data": b64}},
                                    {"text": user_prompt(question["question"])},
                                ]
                            }
                        ],
                        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
                    },
                )
                r.raise_for_status()
                data = r.json()
                parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts)
                usage = data.get("usageMetadata") or {}
                inn = int(usage.get("promptTokenCount") or 0)
                out = int(usage.get("candidatesTokenCount") or 0)
            except Exception as exc:
                error = type(exc).__name__
            latency = round((time.perf_counter() - t0) * 1000.0, 1)
            parsed = parse_answer_payload(text)
            pages = [parsed["page"]] if parsed.get("page") else []
            rows.append(
                _score_live_row(
                    question,
                    raw_text=text,
                    latency_ms=latency,
                    input_tokens=inn,
                    output_tokens=out,
                    cost_usd=_cost("google", model, inn, out),
                    model_id=model,
                    retrieval_kind="native",
                    evidence_available=True,
                    observed_evidence=_evidence_blob(parsed),
                    citation_pages=pages,
                    error=error,
                )
            )
    extra = {
        "status": "measured",
        "retrieval_kind": "native",
        "processes_entire_pdf": True,
        "performs_retrieval": False,
        "retrieved_chunks_observable": False,
        "citations_observable": False,
        "preserves_page_layout": True,
        "model_id": model,
    }
    summary = summarize_mode("GEMINI_NATIVE_PDF", rows, extra=extra)
    if rows and all(r.get("decisive_in_injected") is None for r in rows if r.get("answerable")):
        summary["decisive_span_recall"] = "NOT_OBSERVABLE"
    return {"mode": "GEMINI_NATIVE_PDF", "summary": summary, "rows": rows}


async def _gemini_file_search(pdf: bytes, model: str) -> dict[str, Any]:
    """Gemini File Search. Store APIs vary; any setup failure is BLOCKED, not invented."""
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    headers = {"x-goog-api-key": api_key}
    async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
        created = await cx.post(
            "https://generativelanguage.googleapis.com/v1beta/fileSearchStores",
            headers=headers,
            json={"displayName": "gate-p-gold"},
        )
        if created.status_code >= 400:
            return _blocked(
                "GEMINI_FILE_SEARCH",
                f"File Search store create failed HTTP {created.status_code}",
                additional_setup_required="Gemini File Search store API not available with this key/project",
            )
        store = created.json()
        store_name = store.get("name")
        try:
            up = await cx.post(
                f"https://generativelanguage.googleapis.com/v1beta/{store_name}:uploadToFileSearchStore",
                headers=headers,
                files={"file": (GOLD_FILENAME, pdf, "application/pdf")},
            )
            if up.status_code >= 400:
                return _blocked(
                    "GEMINI_FILE_SEARCH",
                    f"File Search upload failed HTTP {up.status_code}",
                )
            rows: list[dict[str, Any]] = []
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            for question in QUESTIONS:
                t0 = time.perf_counter()
                error = None
                text = ""
                inn = out = 0
                observed = ""
                try:
                    r = await cx.post(
                        url,
                        params={"key": api_key},
                        json={
                            "contents": [{"parts": [{"text": user_prompt(question["question"])}]}],
                            "tools": [{"file_search": {"file_search_store_names": [store_name]}}],
                            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
                        },
                    )
                    r.raise_for_status()
                    data = r.json()
                    cand = (data.get("candidates") or [{}])[0]
                    parts = (cand.get("content") or {}).get("parts") or []
                    text = "".join(p.get("text", "") for p in parts)
                    grounding = cand.get("groundingMetadata") or {}
                    chunks = grounding.get("groundingChunks") or []
                    observed = json.dumps(chunks)[:8000]
                    usage = data.get("usageMetadata") or {}
                    inn = int(usage.get("promptTokenCount") or 0)
                    out = int(usage.get("candidatesTokenCount") or 0)
                except Exception as exc:
                    error = type(exc).__name__
                latency = round((time.perf_counter() - t0) * 1000.0, 1)
                parsed = parse_answer_payload(text)
                pages = [parsed["page"]] if parsed.get("page") else []
                rows.append(
                    _score_live_row(
                        question,
                        raw_text=text,
                        latency_ms=latency,
                        input_tokens=inn,
                        output_tokens=out,
                        cost_usd=_cost("google", model, inn, out),
                        model_id=model,
                        retrieval_kind="file_search",
                        evidence_available=True if observed else None,
                        observed_evidence=observed + "\n" + _evidence_blob(parsed),
                        citation_pages=pages,
                        error=error,
                    )
                )
            extra = {
                "status": "measured",
                "retrieval_kind": "file_search",
                "processes_entire_pdf": False,
                "performs_retrieval": True,
                "retrieved_chunks_observable": True,
                "citations_observable": True,
                "preserves_page_layout": False,
                "model_id": model,
            }
            return {
                "mode": "GEMINI_FILE_SEARCH",
                "summary": summarize_mode("GEMINI_FILE_SEARCH", rows, extra=extra),
                "rows": rows,
            }
        finally:
            if store_name:
                try:
                    await cx.delete(
                        f"https://generativelanguage.googleapis.com/v1beta/{store_name}",
                        headers=headers,
                    )
                except Exception:
                    pass


async def _grok_native(pdf: bytes, model: str) -> dict[str, Any]:
    api_key = os.getenv("XAI_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {api_key}"}
    file_id = None
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
        try:
            up = await cx.post(
                "https://api.x.ai/v1/files",
                headers=headers,
                files={"file": (GOLD_FILENAME, pdf, "application/pdf"), "purpose": (None, "assistants")},
            )
            if up.status_code >= 400:
                return _blocked(
                    "GROK_NATIVE_DOCUMENT",
                    f"xAI Files API upload failed HTTP {up.status_code}; BEN Chat Completions path has no document parts",
                    current_ben_integration="chat_completions_images_only",
                )
            file_id = up.json().get("id")
            for question in QUESTIONS:
                t0 = time.perf_counter()
                error = None
                text = ""
                inn = out = 0
                try:
                    r = await cx.post(
                        "https://api.x.ai/v1/responses",
                        headers=headers,
                        json={
                            "model": model,
                            "input": [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "input_text", "text": user_prompt(question["question"])},
                                        {"type": "input_file", "file_id": file_id},
                                    ],
                                }
                            ],
                        },
                    )
                    r.raise_for_status()
                    data = r.json()
                    text = data.get("output_text") or json.dumps(data.get("output") or [])[:4000]
                    usage = data.get("usage") or {}
                    inn = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                    out = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
                except Exception as exc:
                    error = type(exc).__name__
                latency = round((time.perf_counter() - t0) * 1000.0, 1)
                parsed = parse_answer_payload(text)
                pages = [parsed["page"]] if parsed.get("page") else []
                rows.append(
                    _score_live_row(
                        question,
                        raw_text=text,
                        latency_ms=latency,
                        input_tokens=inn,
                        output_tokens=out,
                        cost_usd=_cost("xai", model, inn, out),
                        model_id=model,
                        retrieval_kind="native",
                        evidence_available=True,
                        observed_evidence=_evidence_blob(parsed),
                        citation_pages=pages,
                        error=error,
                    )
                )
        finally:
            if file_id:
                try:
                    await cx.delete(f"https://api.x.ai/v1/files/{file_id}", headers=headers)
                except Exception:
                    pass
    extra = {
        "status": "measured",
        "retrieval_kind": "native",
        "processes_entire_pdf": None,
        "performs_retrieval": True,
        "retrieved_chunks_observable": False,
        "citations_observable": False,
        "preserves_page_layout": False,
        "model_id": model,
        "note": "xAI Responses attachment_search is implicit; retrieved chunks NOT_OBSERVABLE",
    }
    summary = summarize_mode("GROK_NATIVE_DOCUMENT", rows, extra=extra)
    if rows and all(r.get("decisive_in_injected") is None for r in rows if r.get("answerable")):
        summary["decisive_span_recall"] = "NOT_OBSERVABLE"
    return {"mode": "GROK_NATIVE_DOCUMENT", "summary": summary, "rows": rows}


async def run_provider_modes(pdf: bytes) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if key_present("openai"):
        openai_model = resolve_api_model("openai", OPENAI_CHAT_FAST_MODEL)
        out["OPENAI_NATIVE_DOCUMENT"] = await _openai_native(pdf, openai_model)
        out["OPENAI_FILE_SEARCH"] = await _openai_file_search(pdf, openai_model)
    else:
        out["OPENAI_NATIVE_DOCUMENT"] = _blocked(
            "OPENAI_NATIVE_DOCUMENT",
            "OPENAI_API_KEY not present in this environment",
            processes_entire_pdf=True,
            performs_retrieval=False,
            retrieved_chunks_observable=False,
            citations_observable=False,
            model_id=OPENAI_CHAT_FAST_MODEL,
        )
        out["OPENAI_FILE_SEARCH"] = _blocked(
            "OPENAI_FILE_SEARCH",
            "OPENAI_API_KEY not present in this environment",
            processes_entire_pdf=False,
            performs_retrieval=True,
            retrieved_chunks_observable=True,
            citations_observable=True,
            model_id=OPENAI_CHAT_FAST_MODEL,
        )
    if key_present("anthropic"):
        out["CLAUDE_NATIVE_DOCUMENT"] = await _claude_native(
            pdf, resolve_api_model("anthropic", ANTHROPIC_FAST_MODEL)
        )
    else:
        out["CLAUDE_NATIVE_DOCUMENT"] = _blocked(
            "CLAUDE_NATIVE_DOCUMENT",
            "ANTHROPIC_API_KEY not present in this environment",
            processes_entire_pdf=True,
            performs_retrieval=False,
            retrieved_chunks_observable=False,
            citations_observable=True,
            model_id=ANTHROPIC_FAST_MODEL,
        )
    out["CLAUDE_PROVIDER_RETRIEVAL"] = _blocked(
        "CLAUDE_PROVIDER_RETRIEVAL",
        "Anthropic has no hosted File Search / retrieval API comparable to OpenAI or Gemini; Files API is document storage for native document blocks",
        processes_entire_pdf=None,
        performs_retrieval=False,
        retrieved_chunks_observable=False,
        citations_observable=False,
        not_applicable=True,
    )
    if key_present("google"):
        gemini_model = resolve_api_model("google", GEMINI_FAST_MODEL)
        out["GEMINI_NATIVE_PDF"] = await _gemini_native(pdf, gemini_model)
        out["GEMINI_FILE_SEARCH"] = await _gemini_file_search(pdf, gemini_model)
    else:
        out["GEMINI_NATIVE_PDF"] = _blocked(
            "GEMINI_NATIVE_PDF",
            "GOOGLE_API_KEY not present in this environment",
            processes_entire_pdf=True,
            performs_retrieval=False,
            retrieved_chunks_observable=False,
            citations_observable=False,
            model_id=GEMINI_FAST_MODEL,
        )
        out["GEMINI_FILE_SEARCH"] = _blocked(
            "GEMINI_FILE_SEARCH",
            "GOOGLE_API_KEY not present in this environment",
            processes_entire_pdf=False,
            performs_retrieval=True,
            retrieved_chunks_observable=True,
            citations_observable=True,
            model_id=GEMINI_FAST_MODEL,
        )
    if key_present("xai"):
        out["GROK_NATIVE_DOCUMENT"] = await _grok_native(
            pdf, resolve_api_model("xai", XAI_FLAGSHIP_MODEL)
        )
    else:
        out["GROK_NATIVE_DOCUMENT"] = _blocked(
            "GROK_NATIVE_DOCUMENT",
            "XAI_API_KEY not present; BEN Chat Completions adapter also has no document parts",
            processes_entire_pdf=None,
            performs_retrieval=True,
            retrieved_chunks_observable=False,
            citations_observable=False,
            model_id=XAI_FLAGSHIP_MODEL,
        )
    return out
