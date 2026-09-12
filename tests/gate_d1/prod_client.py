"""Production Gate D1 canary runner. Does not change code paths or ranking.

Enables nothing by itself. Talks to production after the existing FTS allowlist
is pointed at one workspace. Cleans up files/threads it creates.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from tests.gate_m.gold_document import GOLD_FILENAME
from tests.gate_m.measure import contains, extractive_answer_ok, locator_in_text
from tests.gate_p.position import position_bucket
from tests.gate_p.score import classify_row

BASE = "https://ben-v2-production.up.railway.app"
ABSENT_QUERY = "zxqv9nonesuchgate4xyz"
FTS_MODES = {"chunks", "mixed"}


def _headers(bearer: str) -> dict[str, str]:
    token = bearer.strip()
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    return {"Authorization": token}


def parse_stream_line(line: str) -> dict[str, Any] | None:
    raw = (line or "").strip()
    if not raw:
        return None
    if raw.startswith("data:"):
        raw = raw[5:].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def evidence_blob(done: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in ((done.get("response_evidence") or {}).get("evidence") or []):
        if isinstance(item, dict):
            parts.append(str(item.get("excerpt") or ""))
            if item.get("page") is not None:
                parts.append(f"[[PAGE {item.get('page')}]]")
    for page in done.get("evidence_pages") or []:
        parts.append(f"[[PAGE {page}]]")
    return "\n".join(parts)


def chunk_ids(done: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in ((done.get("response_evidence") or {}).get("evidence") or []):
        if isinstance(item, dict) and item.get("chunk_id"):
            out.append(str(item["chunk_id"]))
    return out


def source_ids(done: dict[str, Any]) -> list[str]:
    out: list[str] = []
    evidence = done.get("response_evidence") or {}
    for item in evidence.get("sources") or []:
        if isinstance(item, dict) and item.get("source_id"):
            out.append(str(item["source_id"]))
    for item in done.get("workspace_files_used") or []:
        if isinstance(item, dict) and item.get("id"):
            out.append(str(item["id"]))
        elif isinstance(item, str):
            out.append(item)
    return out


def score_done(question: dict[str, Any], done: dict[str, Any], *, latency_ms: float, http_status: int) -> dict[str, Any]:
    blob = evidence_blob(done)
    in_injected = True
    spans = question.get("decisive_evidence") or []
    if spans:
        in_injected = all(locator_in_text(s.get("text_or_locator") or "", blob) for s in spans)
    answer_ok = extractive_answer_ok(question, blob)
    if not question["answerable"]:
        forbidden = list(question.get("forbidden_answers") or [])
        leaked = any(contains(blob, a) for a in forbidden if a)
        answer_ok = not leaked
    retrieved_empty = (done.get("chunks_selected") or 0) == 0 and not blob
    mode = str(done.get("retrieval_mode") or "")
    fail_cat, residual = classify_row(
        question,
        in_source=True,
        in_injected=in_injected,
        answer_ok=answer_ok,
        processing_ok=http_status < 500,
        retrieval_kind="fts" if mode in FTS_MODES else "prefix",
        retrieved_empty=retrieved_empty,
    )
    gold_pages = [s.get("page") for s in spans if s.get("page")]
    pages = list(done.get("evidence_pages") or [])
    citation_ok = None
    if gold_pages and pages:
        citation_ok = any(p in pages for p in gold_pages)
    passed = (in_injected and answer_ok) if question["answerable"] else answer_ok
    used = done.get("workspace_files_used") or []
    return {
        "question_id": question["question_id"],
        "question": question["question"],
        "category": question["category"],
        "answerable": question["answerable"],
        "position": position_bucket(question),
        "http_status": http_status,
        "retrieval_mode": done.get("retrieval_mode"),
        "fallback_reason": done.get("fallback_reason"),
        "chunks_considered": done.get("chunks_considered"),
        "chunks_selected": done.get("chunks_selected"),
        "evidence_pages": pages,
        "chunk_ids": chunk_ids(done),
        "source_ids": source_ids(done),
        "used_files": used,
        "response_evidence_mode": (done.get("response_evidence") or {}).get("retrieval_mode"),
        "workspace_files_injected": done.get("workspace_files_injected"),
        "thread_id": done.get("thread_id"),
        "sqlite_assistant_id": done.get("sqlite_assistant_id"),
        "model_used": done.get("model_used"),
        "provider_used": done.get("provider_used"),
        "fts_latency_ms": done.get("fts_latency_ms"),
        "latency_ms": latency_ms,
        "transcript_has_workspace_files_tag": "<workspace_files" in str(done.get("response") or ""),
        "decisive_in_source": True,
        "decisive_in_injected": in_injected,
        "extractive_answer_ok": answer_ok,
        "pass": passed,
        "mrl": residual == "MATERIAL_RECOVERABLE_LOSS",
        "failure_category": fail_cat,
        "residual": residual,
        "citation_page_accuracy": citation_ok,
        "pollution": "<workspace_files" in str(done.get("response") or ""),
    }


async def chat_stream(
    cx: httpx.AsyncClient,
    bearer: str,
    *,
    message: str,
    project_id: str,
    thread_id: str | None = None,
    provider_id: str = "gpt",
    attempts: int = 4,
) -> tuple[int, dict[str, Any], float]:
    payload: dict[str, Any] = {
        "message": message,
        "provider_id": provider_id,
        "project_id": project_id,
        "preferred_language": "en",
    }
    if thread_id:
        payload["thread_id"] = thread_id
    last_status = 0
    last_done: dict[str, Any] = {}
    last_latency = 0.0
    for attempt in range(attempts):
        t0 = time.perf_counter()
        try:
            async with cx.stream(
                "POST",
                f"{BASE}/chat/stream",
                headers={**_headers(bearer), "Content-Type": "application/json"},
                json=payload,
                timeout=httpx.Timeout(180.0, connect=20.0),
            ) as response:
                status = response.status_code
                body = ""
                done: dict[str, Any] = {}
                async for line in response.aiter_lines():
                    body += line + "\n"
                    event = parse_stream_line(line)
                    if event and event.get("type") == "done":
                        done = event
                latency = round((time.perf_counter() - t0) * 1000.0, 1)
                if not done:
                    done = {"raw": body[:2000], "retrieval_mode": None}
                last_status, last_done, last_latency = status, done, latency
                if status in {429, 502, 503} and attempt < attempts - 1:
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                return status, done, latency
        except (httpx.TimeoutException, httpx.TransportError):
            last_latency = round((time.perf_counter() - t0) * 1000.0, 1)
            if attempt < attempts - 1:
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            return last_status or 599, last_done or {"retrieval_mode": None}, last_latency
    return last_status, last_done, last_latency


async def upload_gold(cx: httpx.AsyncClient, bearer: str, workspace_id: str, pdf: bytes) -> dict[str, Any]:
    r = await cx.post(
        f"{BASE}/api/workspaces/{workspace_id}/files",
        headers=_headers(bearer),
        files={"file": (GOLD_FILENAME, pdf, "application/pdf")},
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()


async def wait_ready(
    cx: httpx.AsyncClient,
    bearer: str,
    workspace_id: str,
    file_id: str,
    *,
    timeout_s: float = 360.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        r = await cx.get(
            f"{BASE}/api/workspaces/{workspace_id}/files/{file_id}",
            headers=_headers(bearer),
            params={"include_text_preview": "true"},
            timeout=30.0,
        )
        r.raise_for_status()
        last = r.json()
        status = str(last.get("status") or "").lower()
        index_status = str(last.get("index_status") or "").lower()
        if status == "failed":
            raise RuntimeError(f"file processing failed: {last}")
        if status == "ready" and index_status == "indexed" and int(last.get("indexed_chunk_count") or 0) > 0:
            return last
        await asyncio.sleep(2.0)
    raise TimeoutError(f"file not indexed: {last}")


async def delete_file(cx: httpx.AsyncClient, bearer: str, workspace_id: str, file_id: str) -> tuple[int, int]:
    r = await cx.delete(
        f"{BASE}/api/workspaces/{workspace_id}/files/{file_id}",
        headers=_headers(bearer),
        timeout=30.0,
    )
    gone = await cx.get(
        f"{BASE}/api/workspaces/{workspace_id}/files/{file_id}",
        headers=_headers(bearer),
        timeout=30.0,
    )
    return r.status_code, gone.status_code


async def get_thread(cx: httpx.AsyncClient, bearer: str, thread_id: str) -> dict[str, Any]:
    r = await cx.get(f"{BASE}/api/threads/{thread_id}", headers=_headers(bearer), timeout=30.0)
    r.raise_for_status()
    return r.json()


async def get_project(cx: httpx.AsyncClient, bearer: str, project_id: str) -> dict[str, Any]:
    r = await cx.get(f"{BASE}/api/projects/{project_id}", headers=_headers(bearer), timeout=30.0)
    r.raise_for_status()
    return r.json()


def thread_polluted(detail: dict[str, Any]) -> bool:
    blob = json.dumps(detail)
    return "<workspace_files" in blob
