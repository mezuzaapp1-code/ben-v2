"""Isolated Gate 4A chunk FTS measurement against the Gate M gold file.

Enables BEN_WORKSPACE_CHUNK_RETRIEVAL only for a throwaway workspace UUID in
this process. Does not change production flags or schema.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

from tests.gate_m.gold_document import GOLD_FILENAME, PAGES
from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import (
    classify_failure,
    decisive_in_injected,
    decisive_in_source,
    extract_gold_pdf,
    extractive_answer_ok,
)
from tests.gate_p.position import position_bucket
from tests.gate_p.score import classify_row, summarize_mode
from services.workspace_files.chunking import chunk_structured_document
from services.workspace_files.document_parser import PdfDocumentParser

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None


async def _open():
    if asyncpg is None:
        raise RuntimeError("asyncpg not installed")
    dsn = os.getenv("BEN_TEST_PG_DSN") or os.getenv("DATABASE_URL") or ""
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    present = await conn.fetchval("SELECT to_regclass('ben.workspace_file_chunks') IS NOT NULL")
    if not present:
        await conn.close()
        raise RuntimeError("ben.workspace_file_chunks missing")
    return conn


async def _mk_workspace(conn, org_id: uuid.UUID) -> uuid.UUID:
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1,$2,$3,'active')",
        ws,
        org_id,
        "gate-p-fts-isolated",
    )
    return ws


async def _cleanup(conn, ws: uuid.UUID) -> None:
    await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)


def _enable(ws: uuid.UUID) -> tuple[str | None, str | None]:
    prev_flag = os.environ.get("BEN_WORKSPACE_CHUNK_RETRIEVAL")
    prev_allow = os.environ.get("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS")
    os.environ["BEN_WORKSPACE_CHUNK_RETRIEVAL"] = "on"
    os.environ["BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS"] = str(ws)
    return prev_flag, prev_allow


def _restore(prev_flag: str | None, prev_allow: str | None) -> None:
    if prev_flag is None:
        os.environ.pop("BEN_WORKSPACE_CHUNK_RETRIEVAL", None)
    else:
        os.environ["BEN_WORKSPACE_CHUNK_RETRIEVAL"] = prev_flag
    if prev_allow is None:
        os.environ.pop("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", None)
    else:
        os.environ["BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS"] = prev_allow


async def run_fts_isolated(tmp_pdf: Path) -> dict[str, Any]:
    from database.connection import dispose_engine
    from services.workspace_files.chunk_retriever import chunk_retrieval_enabled
    from services.workspace_files.service import load_ready_files_context

    extracted = extract_gold_pdf(tmp_pdf)
    doc = PdfDocumentParser().parse(
        tmp_pdf, media_type="application/pdf", filename=GOLD_FILENAME
    )
    chunks = chunk_structured_document(doc)
    org_id = uuid.uuid4()
    conn = await _open()
    ws = await _mk_workspace(conn, org_id)
    fid = uuid.uuid4()
    prev = _enable(ws)
    rows_out: list[dict[str, Any]] = []
    try:
        if chunk_retrieval_enabled(uuid.uuid4()):
            raise RuntimeError("FTS leaked beyond the throwaway workspace")
        if not chunk_retrieval_enabled(ws):
            raise RuntimeError("FTS did not enable for the throwaway workspace")

        await conn.execute(
            """
            INSERT INTO ben.workspace_files
                (id, org_id, workspace_id, project_id, original_filename, display_name,
                 media_type, byte_size, checksum, storage_key, status, extracted_text,
                 extraction_status, index_status, indexed_chunk_count, extraction_truncated,
                 page_count, extraction_version, chunking_version)
            VALUES ($1,$2,$3,$3,$4,$4,'application/pdf',$5,'gate-p',$6,'ready',$7,
                    'complete','indexed',$8,false,$9,1,1)
            """,
            fid,
            org_id,
            ws,
            GOLD_FILENAME,
            len(tmp_pdf.read_bytes()),
            f"gate-p/{fid}",
            extracted["extracted_text"],
            len(chunks),
            len(PAGES),
        )
        for ch in chunks:
            await conn.execute(
                """
                INSERT INTO ben.workspace_file_chunks
                    (id, org_id, workspace_id, file_id, page_number, page_chunk_index,
                     document_chunk_index, text, char_count, extraction_version, chunking_version)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,1,1)
                """,
                uuid.uuid4(),
                org_id,
                ws,
                fid,
                ch.page_number,
                ch.page_chunk_index,
                ch.document_chunk_index,
                ch.text,
                ch.char_count,
            )
        await conn.close()
        conn = None
        await dispose_engine()

        processing_ok = extracted["legacy_status"] == "ready" and bool(chunks)
        for question in QUESTIONS:
            t0 = time.perf_counter()
            ctx = await load_ready_files_context(
                org_id,
                ws,
                max_chars=12_000,
                user_query=question["question"],
            )
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            injected = ctx.block or ""
            in_source = decisive_in_source(question, extracted["extracted_text"], extracted["pages"])
            in_injected = decisive_in_injected(question, injected)
            answer_ok = extractive_answer_ok(question, injected)
            if not question["answerable"]:
                forbidden = list(question.get("forbidden_answers") or [])
                from tests.gate_m.measure import contains

                leaked = any(contains(extracted["extracted_text"], a) for a in forbidden if a)
                answer_ok = not leaked
            retrieved_empty = (ctx.chunks_selected or 0) == 0 and not injected
            fail_cat, residual = classify_row(
                question,
                in_source=in_source,
                in_injected=in_injected,
                answer_ok=answer_ok,
                processing_ok=processing_ok,
                retrieval_kind="fts",
                retrieved_empty=retrieved_empty,
            )
            if question["answerable"] and fail_cat is None and residual == "NONE":
                # Keep Gate M labels when they add no new information.
                _legacy_fail, _legacy_res = classify_failure(
                    question,
                    in_source=in_source,
                    in_injected=in_injected,
                    answer_ok=answer_ok,
                    processing_ok=processing_ok,
                )
                if _legacy_fail and in_injected:
                    fail_cat, residual = _legacy_fail, _legacy_res
            evidence = ctx.response_evidence or {}
            pages_in_evidence = [
                item.get("page")
                for item in (evidence.get("evidence") or [])
                if isinstance(item, dict) and item.get("page") is not None
            ]
            gold_pages = [s.get("page") for s in question.get("decisive_evidence") or [] if s.get("page")]
            citation_ok = None
            if gold_pages and (pages_in_evidence or ctx.evidence_pages):
                observed = set(pages_in_evidence) | set(ctx.evidence_pages or ())
                citation_ok = any(p in observed for p in gold_pages)
            mrl = residual == "MATERIAL_RECOVERABLE_LOSS"
            passed = (
                in_injected and answer_ok and processing_ok
                if question["answerable"]
                else answer_ok and processing_ok
            )
            rows_out.append(
                {
                    "question_id": question["question_id"],
                    "question": question["question"],
                    "category": question["category"],
                    "answerable": question["answerable"],
                    "gold_answer": question["gold_answer"],
                    "position": position_bucket(question),
                    "processing_ok": processing_ok,
                    "decisive_in_source": in_source,
                    "decisive_in_injected": in_injected,
                    "extractive_answer_ok": answer_ok,
                    "pass": passed,
                    "mrl": mrl,
                    "failure_category": fail_cat,
                    "residual": residual,
                    "used_files": list(ctx.used_files or ()),
                    "retrieval_mode": ctx.retrieval_mode,
                    "fallback_reason": ctx.fallback_reason,
                    "chunks_considered": ctx.chunks_considered,
                    "chunks_selected": ctx.chunks_selected,
                    "evidence_pages": list(ctx.evidence_pages or ()),
                    "citation_page_accuracy": citation_ok,
                    "injected_chars": ctx.chars,
                    "latency_ms": latency_ms,
                    "fts_latency_ms": ctx.fts_latency_ms,
                }
            )
    finally:
        _restore(*prev)
        try:
            from services.workspace_files.chunk_retriever import chunk_retrieval_enabled as _cre

            if _cre(ws):
                raise RuntimeError("FTS flag still on after restore")
        except Exception:
            pass
        if conn is not None:
            try:
                await _cleanup(conn, ws)
                await conn.close()
            except Exception:
                pass
        else:
            try:
                conn2 = await _open()
                await _cleanup(conn2, ws)
                await conn2.close()
            except Exception:
                pass
        try:
            await dispose_engine()
        except Exception:
            pass

    extra = {
        "status": "measured",
        "retrieval_kind": "fts",
        "processes_entire_pdf": False,
        "performs_retrieval": True,
        "retrieved_chunks_observable": True,
        "citations_observable": True,
        "preserves_page_layout": False,
        "model_id": "extractive-oracle-over-fts-chunks",
        "chunk_count": len(chunks),
        "flag_scope": "throwaway workspace allowlist in this process only",
    }
    return {
        "mode": "BEN_EXISTING_FTS",
        "summary": summarize_mode("BEN_EXISTING_FTS", rows_out, extra=extra),
        "rows": rows_out,
    }


def blocked_fts(reason: str) -> dict[str, Any]:
    return {
        "mode": "BEN_EXISTING_FTS",
        "summary": {
            "mode": "BEN_EXISTING_FTS",
            "status": "blocked",
            "blocked_reason": reason,
            "answer_correctness": "n/a",
            "decisive_span_recall": "n/a",
            "mrl": None,
            "unanswerable_precision": "n/a",
        },
        "rows": [],
    }
