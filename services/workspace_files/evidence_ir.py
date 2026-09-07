"""WRAP sidecar evidence IR (ben.evidence_ir.v1).

Optional structured provenance stored beside pages/chunks. Current production
parsers emit no IR. Persistence is fail-closed behind BEN_DOC_EVIDENCE_IR_WRITE
(default OFF). Text is stored as emitted glyphs; missing metadata is explicit
UNAVAILABLE. Amounts are never inferred.
"""
from __future__ import annotations

import os
from typing import Any, Iterator, Mapping

IR_SCHEMA_VERSION = "ben.evidence_ir.v1"
WRITE_ENV = "BEN_DOC_EVIDENCE_IR_WRITE"

MARK_NATIVE = "NATIVE"
MARK_DERIVED = "BEN-DERIVED"
MARK_UNAVAILABLE = "UNAVAILABLE"
ALLOWED_MARKS = frozenset({MARK_NATIVE, MARK_DERIVED, MARK_UNAVAILABLE})

PAGE_REQUIRED_SLOTS = ("page_index", "header", "footer")
BLOCK_REQUIRED_SLOTS = ("type", "bbox", "table_id", "block_id", "confidence")
CELL_REQUIRED_SLOTS = ("row", "col", "is_header", "bbox", "confidence")
TABLE_REQUIRED_SLOTS = ("table_id", "html")


def evidence_ir_write_enabled() -> bool:
    """Fail-safe OFF unless env is an explicit truthy token."""
    return os.getenv(WRITE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def slot(mark: str, value: Any = None) -> dict[str, Any]:
    if mark not in ALLOWED_MARKS:
        raise ValueError(f"invalid provenance mark: {mark!r}")
    if mark == MARK_UNAVAILABLE:
        return {"mark": MARK_UNAVAILABLE, "value": None}
    return {"mark": mark, "value": value}


def unavailable() -> dict[str, Any]:
    return slot(MARK_UNAVAILABLE)


def native(value: Any) -> dict[str, Any]:
    return slot(MARK_NATIVE, value)


def derived(value: Any) -> dict[str, Any]:
    return slot(MARK_DERIVED, value)


def evidence_ir_payload(doc: Any) -> dict[str, Any] | None:
    """Return an IR mapping attached to a StructuredDocument, else None.

    Parsers that do not set ``evidence_ir`` produce no sidecar row.
    """
    raw = getattr(doc, "evidence_ir", None)
    if not isinstance(raw, Mapping) or not raw:
        return None
    return dict(raw)


def ir_counts(payload: Mapping[str, Any] | None) -> dict[str, int]:
    n_tables = 0
    n_blocks = 0
    if not payload:
        return {"n_tables": 0, "n_blocks": 0}
    for page in payload.get("pages") or ():
        if not isinstance(page, Mapping):
            continue
        n_blocks += len(page.get("blocks") or ())
        n_tables += len(page.get("tables") or ())
    return {"n_tables": n_tables, "n_blocks": n_blocks}


def _require_slot(obj: Mapping[str, Any], key: str, *, where: str) -> dict[str, Any]:
    if key not in obj:
        raise ValueError(f"{where}: missing provenance slot {key!r}")
    raw = obj[key]
    if not isinstance(raw, Mapping) or "mark" not in raw or "value" not in raw:
        raise ValueError(f"{where}.{key}: slot must be {{mark, value}}")
    mark = raw["mark"]
    if mark not in ALLOWED_MARKS:
        raise ValueError(f"{where}.{key}: invalid mark {mark!r}")
    value = raw["value"]
    if mark == MARK_UNAVAILABLE and value is not None:
        raise ValueError(f"{where}.{key}: UNAVAILABLE value must be null")
    if set(raw) - {"mark", "value"}:
        raise ValueError(f"{where}.{key}: unexpected slot keys")
    return {"mark": mark, "value": value}


def _require_text(obj: Mapping[str, Any], *, where: str) -> str:
    if "text" not in obj:
        raise ValueError(f"{where}: missing native text")
    text = obj["text"]
    if not isinstance(text, str):
        raise ValueError(f"{where}: text must be a string (native glyphs)")
    return text


def validate_evidence_ir(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a validated copy. Missing optional metadata must be UNAVAILABLE."""
    if payload.get("schema") != IR_SCHEMA_VERSION:
        raise ValueError(f"unsupported IR schema: {payload.get('schema')!r}")
    pages_in = payload.get("pages")
    if not isinstance(pages_in, list):
        raise ValueError("pages must be a list")
    pages: list[dict[str, Any]] = []
    for i, page in enumerate(pages_in):
        if not isinstance(page, Mapping):
            raise ValueError(f"pages[{i}] must be an object")
        loc = f"pages[{i}]"
        out_page: dict[str, Any] = {
            key: _require_slot(page, key, where=loc) for key in PAGE_REQUIRED_SLOTS
        }
        blocks_in = page.get("blocks")
        tables_in = page.get("tables")
        if not isinstance(blocks_in, list):
            raise ValueError(f"{loc}.blocks must be a list")
        if not isinstance(tables_in, list):
            raise ValueError(f"{loc}.tables must be a list")
        out_page["blocks"] = [_validate_block(b, where=f"{loc}.blocks[{j}]") for j, b in enumerate(blocks_in)]
        out_page["tables"] = [_validate_table(t, where=f"{loc}.tables[{j}]") for j, t in enumerate(tables_in)]
        pages.append(out_page)
    return {"schema": IR_SCHEMA_VERSION, "pages": pages}


def _validate_block(block: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(block, Mapping):
        raise ValueError(f"{where} must be an object")
    out = {key: _require_slot(block, key, where=where) for key in BLOCK_REQUIRED_SLOTS}
    out["text"] = _require_text(block, where=where)
    return out


def _validate_cell(cell: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(cell, Mapping):
        raise ValueError(f"{where} must be an object")
    out = {key: _require_slot(cell, key, where=where) for key in CELL_REQUIRED_SLOTS}
    out["text"] = _require_text(cell, where=where)
    return out


def _validate_table(table: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(table, Mapping):
        raise ValueError(f"{where} must be an object")
    out = {key: _require_slot(table, key, where=where) for key in TABLE_REQUIRED_SLOTS}
    rows_in = table.get("rows")
    cells_in = table.get("cells")
    rows: list[dict[str, Any]] = []
    if isinstance(rows_in, list):
        for ri, row in enumerate(rows_in):
            if not isinstance(row, Mapping):
                raise ValueError(f"{where}.rows[{ri}] must be an object")
            row_cells = row.get("cells")
            if not isinstance(row_cells, list):
                raise ValueError(f"{where}.rows[{ri}].cells must be a list")
            rows.append(
                {
                    "row": _require_slot(row, "row", where=f"{where}.rows[{ri}]")
                    if "row" in row
                    else derived(ri),
                    "cells": [
                        _validate_cell(c, where=f"{where}.rows[{ri}].cells[{ci}]")
                        for ci, c in enumerate(row_cells)
                    ],
                }
            )
    elif isinstance(cells_in, list):
        rows.append(
            {
                "row": derived(0),
                "cells": [
                    _validate_cell(c, where=f"{where}.cells[{ci}]")
                    for ci, c in enumerate(cells_in)
                ],
            }
        )
    else:
        raise ValueError(f"{where}: tables require rows[] or cells[]")
    out["rows"] = rows
    return out


def iter_native_texts(payload: Mapping[str, Any]) -> Iterator[str]:
    """Yield stored text strings (block text, header/footer values, cell text, table HTML)."""
    for page in payload.get("pages") or ():
        if not isinstance(page, Mapping):
            continue
        for key in ("header", "footer"):
            slot_obj = page.get(key)
            if isinstance(slot_obj, Mapping) and isinstance(slot_obj.get("value"), str):
                yield slot_obj["value"]
        for block in page.get("blocks") or ():
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                yield block["text"]
        for table in page.get("tables") or ():
            if not isinstance(table, Mapping):
                continue
            html = table.get("html")
            if isinstance(html, Mapping) and isinstance(html.get("value"), str):
                yield html["value"]
            for row in table.get("rows") or ():
                if not isinstance(row, Mapping):
                    continue
                for cell in row.get("cells") or ():
                    if isinstance(cell, Mapping) and isinstance(cell.get("text"), str):
                        yield cell["text"]
            for cell in table.get("cells") or ():
                if isinstance(cell, Mapping) and isinstance(cell.get("text"), str):
                    yield cell["text"]


def payload_contains_substring(payload: Mapping[str, Any], needle: str) -> bool:
    return any(needle in text for text in iter_native_texts(payload))
