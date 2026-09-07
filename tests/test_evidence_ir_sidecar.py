"""WRAP sidecar: experimental evidence IR types + flag-gated persistence.

Does not change process_file, extract.py, PdfDocumentParser behavior, parser
routing, or EXTRACTION_VERSION. Current parsers emit no IR.
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio

from services.workspace_files import storage
from services.workspace_files.document_parser import (
    EXTRACTION_VERSION,
    PdfDocumentParser,
    resolve_parser,
)
from services.workspace_files.evidence_ir import (
    IR_SCHEMA_VERSION,
    MARK_UNAVAILABLE,
    WRITE_ENV,
    evidence_ir_write_enabled,
    ir_counts,
    iter_native_texts,
    payload_contains_substring,
    validate_evidence_ir,
)
from services.workspace_files.extraction_pipeline import (
    _legacy_projection,
    run_structured_extraction,
)
from tests.test_document_intelligence_gate2 import (
    _cleanup,
    _mk_file_with_bytes,
    _mk_workspace,
    make_pdf,
)

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "evidence_ir"
AMOUNT_07 = "141,600.00"
PUA_E934 = "\ue934"
GERSHAYIM = "\u05f4"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _as_json(val):
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8")
    if isinstance(val, str):
        return json.loads(val)
    return val


def _attach_ir(monkeypatch, payload: dict) -> None:
    real = PdfDocumentParser.parse

    def wrapped(self, *args, **kwargs):
        return replace(real(self, *args, **kwargs), evidence_ir=payload)

    monkeypatch.setattr(PdfDocumentParser, "parse", wrapped)


# =========================================================================== #
# PURE UNIT — schema, glyphs, flag, production-path isolation
# =========================================================================== #
def test_write_flag_default_off(monkeypatch):
    monkeypatch.delenv(WRITE_ENV, raising=False)
    assert evidence_ir_write_enabled() is False
    monkeypatch.setenv(WRITE_ENV, "off")
    assert evidence_ir_write_enabled() is False
    monkeypatch.setenv(WRITE_ENV, "on")
    assert evidence_ir_write_enabled() is True


def test_extraction_version_not_bumped():
    assert EXTRACTION_VERSION == 1
    src = pathlib.Path("services/workspace_files/document_parser.py").read_text()
    assert "EXTRACTION_VERSION = 1" in src


def test_excluded_production_paths_untouched():
    service = pathlib.Path("services/workspace_files/service.py").read_text()
    extract = pathlib.Path("services/workspace_files/extract.py").read_text()
    drain = pathlib.Path("services/workspace_files/drain.py").read_text()
    parser = pathlib.Path("services/workspace_files/document_parser.py").read_text()
    assert "run_structured_extraction" not in service
    assert "extraction_pipeline" not in service
    assert "evidence_ir" not in extract
    assert "BEN_DOC_EVIDENCE_IR_WRITE" not in extract
    assert "evidence_ir" not in drain
    pdf_class = parser.split("class PdfDocumentParser", 1)[1].split("class ", 1)[0]
    assert "evidence_ir" not in pdf_class
    assemble = parser.split("def _assemble_document", 1)[1].split("class DocumentParser", 1)[0]
    assert "evidence_ir" not in assemble


def test_no_mistral_import_in_production_paths():
    for path in (
        "services/workspace_files/document_parser.py",
        "services/workspace_files/extract.py",
        "services/workspace_files/drain.py",
        "services/workspace_files/extraction_pipeline.py",
        "services/workspace_files/evidence_ir.py",
        "services/workspace_files/service.py",
    ):
        src = pathlib.Path(path).read_text().lower()
        for needle in ("mistral", "mistralai", "api.mistral"):
            assert needle not in src, f"{path} must not reference {needle}"


def test_evidence_ir_schema_unavailable_explicit():
    payload = validate_evidence_ir(_load("unavailable_metadata.json"))
    page = payload["pages"][0]
    assert page["header"]["mark"] == MARK_UNAVAILABLE and page["header"]["value"] is None
    assert page["footer"]["mark"] == MARK_UNAVAILABLE and page["footer"]["value"] is None
    block = page["blocks"][0]
    for key in ("bbox", "table_id", "block_id", "confidence"):
        assert key in block
        assert block[key]["mark"] == MARK_UNAVAILABLE
        assert block[key]["value"] is None
    assert block["text"] == "QT-2024-1847 control line"


def test_evidence_ir_rejects_omitted_or_null_as_native():
    bad = _load("unavailable_metadata.json")
    del bad["pages"][0]["blocks"][0]["bbox"]
    with pytest.raises(ValueError, match="missing provenance slot"):
        validate_evidence_ir(bad)
    bad2 = _load("unavailable_metadata.json")
    bad2["pages"][0]["header"] = {"mark": MARK_UNAVAILABLE, "value": "secret"}
    with pytest.raises(ValueError, match="UNAVAILABLE value must be null"):
        validate_evidence_ir(bad2)


def test_evidence_ir_does_not_mutate_glyphs():
    for name in (
        "07_scanned_mixed_mistral.json",
        "chrome_printed_proposal_mistral.json",
        "10_production_failure_twin_mistral.json",
    ):
        raw = _load(name)
        before = list(iter_native_texts(raw))
        validated = validate_evidence_ir(raw)
        after = list(iter_native_texts(validated))
        assert before == after
        for text in after:
            assert text == "".join(text)  # identity; no rewrite


def test_07_amount_141600_still_missing_and_pua_preserved():
    payload = validate_evidence_ir(_load("07_scanned_mixed_mistral.json"))
    blob = "".join(iter_native_texts(payload))
    assert AMOUNT_07 not in blob
    assert "141600" not in blob.replace(",", "")
    assert PUA_E934 in blob
    assert blob.count(PUA_E934) == 5
    amount_blocks = [b["text"] for b in payload["pages"][0]["blocks"] if PUA_E934 in b["text"]]
    assert amount_blocks == ["סכום כולל " + (PUA_E934 * 5)]
    assert payload["pages"][0]["header"]["mark"] == MARK_UNAVAILABLE
    assert payload["pages"][0]["blocks"][0]["type"]["mark"] == "NATIVE"
    assert payload["pages"][0]["blocks"][0]["bbox"]["mark"] == "NATIVE"


def test_no_amount_inferred():
    src = pathlib.Path("services/workspace_files/evidence_ir.py").read_text()
    assert AMOUNT_07 not in src
    fixture = (FIXTURES / "07_scanned_mixed_mistral.json").read_text(encoding="utf-8")
    assert AMOUNT_07 not in fixture


def test_native_header_tables_cells_and_chrome_skus():
    header_ir = validate_evidence_ir(_load("10_production_failure_twin_mistral.json"))
    assert header_ir["pages"][0]["header"] == {"mark": "NATIVE", "value": "QT-2024-1847"}
    assert header_ir["pages"][0]["blocks"][0]["type"]["value"] == "header"
    assert header_ir["pages"][0]["blocks"][0]["text"] == "QT-2024-1847"

    chrome = validate_evidence_ir(_load("chrome_printed_proposal_mistral.json"))
    assert chrome["pages"][0]["header"]["mark"] == MARK_UNAVAILABLE
    tables = chrome["pages"][0]["tables"]
    assert tables
    table = tables[0]
    assert table["table_id"]["mark"] == "NATIVE"
    assert table["html"]["mark"] == "NATIVE"
    cells = [c for row in table["rows"] for c in row["cells"]]
    assert any(c["is_header"]["value"] is True for c in cells)
    texts = [c["text"] for c in cells]
    for sku in ("SKU-APP-440", "SKU-SUP-12M", "SVC-IMPL"):
        assert sku in texts
    for amount in ("₪186,400.00", "₪74,560.00", "₪45,273.60"):
        assert amount in texts
    html = table["html"]["value"]
    assert "SKU-APP-440" in html
    # Chrome Mistral uses ASCII geresh, not U+05F4; do not "repair".
    blob = "".join(iter_native_texts(chrome))
    assert GERSHAYIM not in blob
    assert 'בע"' in blob or 'מק"' in blob or 'סה"' in blob
    counts = ir_counts(chrome)
    assert counts["n_tables"] == 1
    assert counts["n_blocks"] >= 1


def test_pypdf_parser_still_flat(tmp_path):
    pdf = tmp_path / "08.pdf"
    pdf.write_bytes(make_pdf(["Quote QT-2024-1847", "line two"]))
    parser = resolve_parser("application/pdf", "08.pdf")
    assert isinstance(parser, PdfDocumentParser)
    doc = parser.parse(pdf, media_type="application/pdf", filename="08.pdf")
    assert doc.evidence_ir is None
    assert doc.extraction_version == EXTRACTION_VERSION == 1
    assert doc.parser_id == "pypdf"
    assert all(p.text for p in doc.pages)
    assert "QT-2024-1847" in doc.pages[0].text


def test_legacy_projection_unaffected_by_attached_ir(tmp_path):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(make_pdf(["Alpha transformer body"]))
    doc = PdfDocumentParser().parse(pdf, media_type="application/pdf", filename="p.pdf")
    injected = replace(doc, evidence_ir=_load("chrome_printed_proposal_mistral.json"))
    status, text, code, message = _legacy_projection(injected, "complete")
    assert status == "ready"
    assert code is None and message is None
    assert "Alpha transformer body" in (text or "")
    assert "SKU-APP-440" not in (text or "")
    assert AMOUNT_07 not in (text or "")


def test_no_backfill_upload_path_does_not_invoke_pipeline():
    src = pathlib.Path("services/workspace_files/service.py").read_text()
    assert "run_structured_extraction" not in src
    assert "extraction_pipeline" not in src


def test_sanitize_response_evidence_still_drops_unknown_ir_keys():
    from services.workspace_files.response_evidence import sanitize_response_evidence

    sid = "00000000-0000-0000-0000-000000000001"
    raw = {
        "retrieval_mode": "prefix_fallback",
        "sources": [
            {"source_id": sid, "source_type": "workspace_file", "display_name": "f.pdf"}
        ],
        "evidence": [
            {
                "source_id": sid,
                "excerpt": "hello",
                "origin": "ben_retrieval",
                "bbox": [1, 2, 3, 4],
                "table_id": "tbl-0.html",
                "evidence_ir": {"schema": IR_SCHEMA_VERSION},
            }
        ],
    }
    clean = sanitize_response_evidence(raw)
    item = clean["evidence"][0]
    assert item["excerpt"] == "hello"
    assert "bbox" not in item
    assert "table_id" not in item
    assert "evidence_ir" not in item


def test_031_migration_is_additive_and_follows_030():
    src = pathlib.Path(
        "database/migrations/versions/031_workspace_file_evidence_ir.py"
    ).read_text()
    assert 'down_revision = "030_file_initial_read_jobs"' in src
    upgrade = src.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "op.drop_table" not in upgrade
    assert "op.drop_column" not in upgrade
    assert "workspace_file_evidence_ir" in upgrade
    assert "workspace_file_pages" not in upgrade
    assert "workspace_file_chunks" not in upgrade


# =========================================================================== #
# DB INTEGRATION
# =========================================================================== #
async def _open():
    if asyncpg is None:
        pytest.skip("asyncpg not installed")
    try:
        conn = await asyncpg.connect(_DSN)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres unavailable: {exc}")
    present = await conn.fetchval(
        "SELECT to_regclass('ben.workspace_file_evidence_ir') IS NOT NULL"
    )
    if not present:
        await conn.close()
        pytest.skip("Evidence IR schema (031) not applied")
    return conn


@pytest_asyncio.fixture
async def fresh_engine():
    from database.connection import dispose_engine

    await dispose_engine()
    yield
    await dispose_engine()


async def _ir_count(conn, fid) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM ben.workspace_file_evidence_ir WHERE file_id=$1", fid
    )


@pytest.mark.asyncio
async def test_flag_off_structured_extraction_writes_zero_ir_and_keeps_text(
    monkeypatch, fresh_engine
):
    monkeypatch.delenv(WRITE_ENV, raising=False)
    assert evidence_ir_write_enabled() is False
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="off.pdf", media_type="application/pdf",
        data=make_pdf(["Alpha transformer", "Beta gantry"]),
    )
    try:
        _attach_ir(monkeypatch, _load("chrome_printed_proposal_mistral.json"))
        diag = await run_structured_extraction(org, ws, fid)
        assert diag.get("error") is None
        assert diag["final_extraction_status"] == "complete"
        assert diag.get("evidence_ir_written") is False
        row = await conn.fetchrow(
            "SELECT status, extracted_text, extraction_version FROM ben.workspace_files WHERE id=$1",
            fid,
        )
        assert row["status"] == "ready"
        assert row["extraction_version"] == 1
        assert "Alpha transformer" in (row["extracted_text"] or "")
        assert "SKU-APP-440" not in (row["extracted_text"] or "")
        chunks = await conn.fetch(
            "SELECT text FROM ben.workspace_file_chunks WHERE file_id=$1 ORDER BY document_chunk_index",
            fid,
        )
        assert chunks
        assert all("SKU-APP-440" not in c["text"] for c in chunks)
        assert await _ir_count(conn, fid) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1", fid
        ) == 2
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_flag_on_pypdf_emits_zero_sidecar_rows(monkeypatch, fresh_engine):
    monkeypatch.setenv(WRITE_ENV, "on")
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="pypdf.pdf", media_type="application/pdf",
        data=make_pdf(["Quote QT-2024-1847"]),
    )
    try:
        diag = await run_structured_extraction(org, ws, fid)
        assert diag.get("error") is None
        assert diag.get("evidence_ir_written") is False
        assert diag.get("n_tables") == 0 and diag.get("n_blocks") == 0
        row = await conn.fetchrow(
            "SELECT extracted_text FROM ben.workspace_files WHERE id=$1", fid
        )
        assert "QT-2024-1847" in (row["extracted_text"] or "")
        assert await _ir_count(conn, fid) == 0
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_flag_on_injected_ir_persists_native_payload(monkeypatch, fresh_engine):
    monkeypatch.setenv(WRITE_ENV, "on")
    payload = _load("07_scanned_mixed_mistral.json")
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="07.pdf", media_type="application/pdf",
        data=make_pdf(["flat page text only"]),
    )
    try:
        _attach_ir(monkeypatch, payload)
        diag = await run_structured_extraction(org, ws, fid)
        assert diag.get("error") is None
        assert diag["evidence_ir_written"] is True
        assert diag["n_blocks"] == ir_counts(payload)["n_blocks"]
        stored = await conn.fetchrow(
            "SELECT payload, ir_schema_version, extraction_version, parser_id "
            "FROM ben.workspace_file_evidence_ir WHERE file_id=$1",
            fid,
        )
        assert stored["ir_schema_version"] == IR_SCHEMA_VERSION
        assert stored["extraction_version"] == 1
        assert stored["parser_id"] == "pypdf"
        got = _as_json(stored["payload"])
        assert payload_contains_substring(got, PUA_E934)
        assert not payload_contains_substring(got, AMOUNT_07)
        texts = list(iter_native_texts(got))
        assert texts == list(iter_native_texts(validate_evidence_ir(payload)))
        # Flat compatibility: extracted_text stays parser page text, not IR.
        extracted = await conn.fetchval(
            "SELECT extracted_text FROM ben.workspace_files WHERE id=$1", fid
        )
        assert "flat page text only" in extracted
        assert PUA_E934 not in extracted
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_flag_off_does_not_delete_existing_ir(monkeypatch, fresh_engine):
    payload = _load("10_production_failure_twin_mistral.json")
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="keep.pdf", media_type="application/pdf",
        data=make_pdf(["keep me"]),
    )
    try:
        monkeypatch.setenv(WRITE_ENV, "on")
        original_parse = PdfDocumentParser.parse
        _attach_ir(monkeypatch, payload)
        await run_structured_extraction(org, ws, fid)
        assert await _ir_count(conn, fid) == 1
        monkeypatch.setenv(WRITE_ENV, "off")
        monkeypatch.setattr(PdfDocumentParser, "parse", original_parse)
        diag = await run_structured_extraction(org, ws, fid)
        assert diag.get("evidence_ir_written") is False
        assert await _ir_count(conn, fid) == 1
        header = _as_json(
            await conn.fetchval(
                "SELECT payload->'pages'->0->'header' FROM ben.workspace_file_evidence_ir WHERE file_id=$1",
                fid,
            )
        )
        assert header["mark"] == "NATIVE"
        assert header["value"] == "QT-2024-1847"
        extracted = await conn.fetchval(
            "SELECT extracted_text FROM ben.workspace_files WHERE id=$1", fid
        )
        assert "keep me" in extracted
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_evidence_ir_write_failure_rolls_back_pages_and_chunks(
    monkeypatch, fresh_engine
):
    monkeypatch.setenv(WRITE_ENV, "on")
    _attach_ir(monkeypatch, _load("unavailable_metadata.json"))

    def boom(*_a, **_k):
        raise RuntimeError("ir persist boom")

    monkeypatch.setattr(
        "services.workspace_files.extraction_pipeline.WorkspaceFileEvidenceIR",
        boom,
    )
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="boom.pdf", media_type="application/pdf",
        data=make_pdf(["will roll back"]),
    )
    try:
        diag = await run_structured_extraction(org, ws, fid)
        assert diag.get("final_index_status") != "indexed"
        f = await conn.fetchrow(
            "SELECT extraction_status, index_status, extracted_text FROM ben.workspace_files WHERE id=$1",
            fid,
        )
        assert f["index_status"] != "indexed"
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1", fid
        ) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid
        ) == 0
        assert await _ir_count(conn, fid) == 0
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_rls_evidence_ir_cross_org_impossible(fresh_engine):
    conn = await _open()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    ws_a = await _mk_workspace(conn, org_a)
    ws_b = await _mk_workspace(conn, org_b)
    payload = json.dumps(_load("unavailable_metadata.json"))
    try:
        f_a, key_a = await _mk_file_with_bytes(
            conn, org_a, ws_a, filename="a.pdf", media_type="application/pdf",
            data=make_pdf(["A"]),
        )
        f_b, key_b = await _mk_file_with_bytes(
            conn, org_b, ws_b, filename="b.pdf", media_type="application/pdf",
            data=make_pdf(["B"]),
        )
        await conn.execute(
            """
            INSERT INTO ben.workspace_file_evidence_ir
                (org_id, workspace_id, file_id, ir_schema_version, extraction_version,
                 parser_id, parser_version, payload)
            VALUES ($1,$2,$3,$4,1,'test','0',$5::jsonb)
            """,
            org_a, ws_a, f_a, IR_SCHEMA_VERSION, payload,
        )
        await conn.execute(
            """
            INSERT INTO ben.workspace_file_evidence_ir
                (org_id, workspace_id, file_id, ir_schema_version, extraction_version,
                 parser_id, parser_version, payload)
            VALUES ($1,$2,$3,$4,1,'test','0',$5::jsonb)
            """,
            org_b, ws_b, f_b, IR_SCHEMA_VERSION, payload,
        )
        rel = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'workspace_file_evidence_ir'"
        )
        assert rel["relrowsecurity"] is True
        assert rel["relforcerowsecurity"] is True
        await conn.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='di_rls_probe') "
            "THEN CREATE ROLE di_rls_probe NOLOGIN; END IF; END $$;"
        )
        await conn.execute("GRANT USAGE ON SCHEMA ben TO di_rls_probe")
        await conn.execute("GRANT SELECT, INSERT ON ben.workspace_file_evidence_ir TO di_rls_probe")
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE di_rls_probe")
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org_a))
            visible = await conn.fetch(
                "SELECT org_id FROM ben.workspace_file_evidence_ir"
            )
            assert visible
            assert all(r["org_id"] == org_a for r in visible)
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(
                    """
                    INSERT INTO ben.workspace_file_evidence_ir
                        (org_id, workspace_id, file_id, ir_schema_version, extraction_version,
                         parser_id, parser_version, payload)
                    VALUES ($1,$2,$3,$4,1,'x','0',$5::jsonb)
                    """,
                    org_b, ws_b, f_b, IR_SCHEMA_VERSION, payload,
                )
    finally:
        await _cleanup(conn, key_a, ws_a)
        await _cleanup(conn, key_b, ws_b)
        await conn.close()


@pytest.mark.asyncio
async def test_additive_migration_keeps_pages_chunks(fresh_engine):
    conn = await _open()
    try:
        for table in (
            "workspace_files",
            "workspace_file_pages",
            "workspace_file_chunks",
            "workspace_file_evidence_ir",
        ):
            assert await conn.fetchval(f"SELECT to_regclass('ben.{table}') IS NOT NULL")
        cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='ben' AND table_name='workspace_files'"
            )
        }
        assert "extracted_text" in cols
        page_cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='ben' AND table_name='workspace_file_pages'"
            )
        }
        assert "page_number" in page_cols
        assert "payload" not in page_cols  # pages stay coverage-only
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_historical_file_without_pages_untouched(fresh_engine):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid = uuid.uuid4()
    try:
        await conn.execute(
            """
            INSERT INTO ben.workspace_files
                (id, org_id, workspace_id, project_id, original_filename, display_name,
                 media_type, byte_size, checksum, storage_key, status, extracted_text)
            VALUES ($1,$2,$3,$3,'old.pdf','old.pdf','application/pdf',1,'x',$4,'ready',$5)
            """,
            fid, org, ws, f"k/{fid}", "historical prefix QT-2024-1847",
        )
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1", fid
        ) == 0
        assert await _ir_count(conn, fid) == 0
        text = await conn.fetchval(
            "SELECT extracted_text FROM ben.workspace_files WHERE id=$1", fid
        )
        assert text == "historical prefix QT-2024-1847"
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()


@pytest.mark.asyncio
async def test_evidence_ir_cascades_from_file(fresh_engine):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="c.pdf", media_type="application/pdf",
        data=make_pdf(["x"]),
    )
    try:
        await conn.execute(
            """
            INSERT INTO ben.workspace_file_evidence_ir
                (org_id, workspace_id, file_id, ir_schema_version, extraction_version,
                 parser_id, parser_version, payload)
            VALUES ($1,$2,$3,$4,1,'test','0',$5::jsonb)
            """,
            org, ws, fid, IR_SCHEMA_VERSION,
            json.dumps(_load("unavailable_metadata.json")),
        )
        assert await _ir_count(conn, fid) == 1
        await conn.execute("DELETE FROM ben.workspace_files WHERE id=$1", fid)
        assert await _ir_count(conn, fid) == 0
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()
