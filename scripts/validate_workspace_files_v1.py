"""Real File Library V1 validation — upload, process, search, isolate, chat path."""
from __future__ import annotations

import asyncio
import io
import os
import sys
import uuid
import zipfile
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")
load_dotenv(Path(r"C:\BEN-V2") / ".env")

from sqlalchemy import select, text

from database.connection import get_db_session
from database.models import Project, WorkspaceFile
from services.workspace_files import service as file_service
from services.workspace_files import storage


class FakeUpload:
    def __init__(self, filename: str, data: bytes, content_type: str = "application/octet-stream"):
        self.filename = filename
        self.content_type = content_type
        self._buf = io.BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


def _minimal_docx(paragraph: str) -> bytes:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p></w:body>
</w:document>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        zf.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>""",
        )
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def _minimal_xlsx(cell: str) -> bytes:
    shared = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1">
  <si><t>{cell}</t></si>
</sst>"""
    sheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData>
</worksheet>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>""",
        )
        zf.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>
</workbook>""",
        )
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def _minimal_pdf(text: str) -> bytes:
    # Tiny valid-enough PDF with text stream (pypdf may or may not extract).
    content = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET"
    objects = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
    )
    stream = content.encode("latin-1", errors="replace")
    objects.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode()
        + stream
        + b"\nendstream\nendobj\n"
    )
    objects.append(
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(out)


async def _ensure_workspace(org_id: uuid.UUID, name: str) -> uuid.UUID:
    async with get_db_session() as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        row = (
            await session.execute(
                select(Project).where(Project.org_id == org_id, Project.name == name).limit(1)
            )
        ).scalar_one_or_none()
        if row:
            return row.id
        pid = uuid.uuid4()
        proj = Project(
            id=pid,
            org_id=org_id,
            name=name,
        )
        session.add(proj)
        await session.commit()
        return pid


async def _resolve_org_id() -> uuid.UUID:
    """Pick an existing org under RLS, or bootstrap a dedicated validation org."""
    # Stable org for File Library validation (not News-owned).
    fallback = uuid.UUID("f11e0000-0000-4000-8000-0000000000f1")
    async with get_db_session() as session:
        # Probe without org: may return 0 under FORCE RLS.
        probed = (
            await session.execute(text("SELECT org_id FROM ben.projects LIMIT 1"))
        ).first()
        if probed and probed[0]:
            return uuid.UUID(str(probed[0]))

        # Try common local/beta org ids if present in env-backed data.
        for candidate in (
            os.environ.get("BEN_ANONYMOUS_ORG_ID"),
            os.environ.get("BEN_VALIDATION_ORG_ID"),
            str(fallback),
        ):
            if not candidate:
                continue
            try:
                oid = uuid.UUID(str(candidate))
            except ValueError:
                continue
            await session.execute(
                text("SELECT set_config('app.current_org_id', :oid, true)"),
                {"oid": str(oid)},
            )
            row = (
                await session.execute(select(Project).where(Project.org_id == oid).limit(1))
            ).scalar_one_or_none()
            if row is not None:
                return oid

        # Bootstrap validation org + will create workspaces below.
        await session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(fallback)},
        )
        return fallback


async def main() -> int:
    org_id = await _resolve_org_id()
    print(f"org_id={org_id}")

    ws_a = await _ensure_workspace(org_id, "File Library Validate A")
    ws_b = await _ensure_workspace(org_id, "File Library Validate B")
    print(f"workspace_a={ws_a}")
    print(f"workspace_b={ws_b}")

    results = []

    # TXT via chat path
    txt = await file_service.upload_file(
        org_id=org_id,
        workspace_id=ws_a,
        upload=FakeUpload("chat-note.txt", b"UNIQUE_TOKEN_FLIB_ALPHA searchable text", "text/plain"),
        uploaded_by="validator",
        source_chat_id="thread-validate-1",
    )
    results.append(("txt_chat", txt["status"], txt["id"], txt.get("checksum")))
    assert txt["workspace_id"] == str(ws_a)
    assert txt["source_chat_id"] == "thread-validate-1"
    assert txt["status"] == "ready"
    assert txt["checksum"]

    # PDF
    pdf = await file_service.upload_file(
        org_id=org_id,
        workspace_id=ws_a,
        upload=FakeUpload("brief.pdf", _minimal_pdf("UNIQUE_TOKEN_FLIB_PDF"), "application/pdf"),
        uploaded_by="validator",
    )
    results.append(("pdf", pdf["status"], pdf["id"], pdf.get("failure_code")))
    assert pdf["status"] == "ready"

    # DOCX
    docx = await file_service.upload_file(
        org_id=org_id,
        workspace_id=ws_a,
        upload=FakeUpload(
            "memo.docx",
            _minimal_docx("UNIQUE_TOKEN_FLIB_DOCX"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        uploaded_by="validator",
    )
    results.append(("docx", docx["status"], docx["id"], None))
    assert docx["status"] == "ready"

    # CSV
    csv = await file_service.upload_file(
        org_id=org_id,
        workspace_id=ws_a,
        upload=FakeUpload("rows.csv", b"a,b\n1,UNIQUE_TOKEN_FLIB_CSV\n", "text/csv"),
        uploaded_by="validator",
    )
    results.append(("csv", csv["status"], csv["id"], None))
    assert csv["status"] == "ready"

    # XLSX
    xlsx = await file_service.upload_file(
        org_id=org_id,
        workspace_id=ws_a,
        upload=FakeUpload(
            "sheet.xlsx",
            _minimal_xlsx("UNIQUE_TOKEN_FLIB_XLSX"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        uploaded_by="validator",
    )
    results.append(("xlsx", xlsx["status"], xlsx["id"], None))
    assert xlsx["status"] == "ready"

    # Image
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    img = await file_service.upload_file(
        org_id=org_id,
        workspace_id=ws_a,
        upload=FakeUpload("dot.png", png_bytes, "image/png"),
        uploaded_by="validator",
    )
    results.append(("png", img["status"], img["id"], img.get("preview_kind")))
    assert img["status"] == "ready"
    assert img["preview_kind"] == "image"

    # Unsupported / unsafe
    try:
        await file_service.upload_file(
            org_id=org_id,
            workspace_id=ws_a,
            upload=FakeUpload("bad.exe", b"MZ\x90\x00", "application/octet-stream"),
            uploaded_by="validator",
        )
        print("FAIL: exe should be rejected")
        return 1
    except Exception as exc:  # noqa: BLE001
        results.append(("exe_rejected", "ok", str(getattr(exc, "status_code", "")), str(exc)[:80]))

    # Missing workspace
    try:
        await file_service.upload_file(
            org_id=org_id,
            workspace_id=uuid.uuid4(),
            upload=FakeUpload("x.txt", b"hi", "text/plain"),
        )
        print("FAIL: missing workspace should 404")
        return 1
    except Exception as exc:  # noqa: BLE001
        results.append(("missing_ws", "ok", str(getattr(exc, "status_code", "")), ""))

    # Search workspace-bounded
    found = await file_service.list_files(
        org_id=org_id, workspace_id=ws_a, q="UNIQUE_TOKEN_FLIB_ALPHA", limit=50
    )
    assert found["count"] >= 1
    assert all(i["workspace_id"] == str(ws_a) for i in found["items"])

    other = await file_service.list_files(
        org_id=org_id, workspace_id=ws_b, q="UNIQUE_TOKEN_FLIB_ALPHA", limit=50
    )
    assert other["count"] == 0

    # Cross-workspace read rejected
    try:
        await file_service.get_file(org_id=org_id, workspace_id=ws_b, file_id=uuid.UUID(txt["id"]))
        print("FAIL: cross-workspace get should 404")
        return 1
    except Exception as exc:  # noqa: BLE001
        results.append(("cross_get", "ok", str(getattr(exc, "status_code", "")), ""))

    # Authenticated content path
    path, media, name = await file_service.open_file_bytes(
        org_id=org_id, workspace_id=ws_a, file_id=uuid.UUID(txt["id"])
    )
    assert path.exists()
    assert "UNIQUE_TOKEN_FLIB_ALPHA" in path.read_text(encoding="utf-8")
    results.append(("content", "ok", name, media))

    # Persistence reload
    listed = await file_service.list_files(org_id=org_id, workspace_id=ws_a, limit=100)
    ids = {i["id"] for i in listed["items"]}
    for label, _status, fid, _extra in results:
        if label in {"txt_chat", "pdf", "docx", "csv", "xlsx", "png"}:
            assert fid in ids, f"{label} missing after reload"

    # Storage under workspace root
    key = await _storage_key(org_id, ws_a, uuid.UUID(txt["id"]))
    abs_path = storage.absolute_path_for_key(key)
    assert abs_path.exists()
    results.append(("storage", "ok", str(abs_path), ""))

    # Never land in News persistence
    file_ids = [uuid.UUID(txt["id"]), uuid.UUID(pdf["id"]), uuid.UUID(docx["id"])]
    async with get_db_session() as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        for fid in file_ids:
            sdv = (
                await session.execute(
                    text(
                        "SELECT 1 FROM ben.source_document_versions "
                        "WHERE id = :fid LIMIT 1"
                    ),
                    {"fid": str(fid)},
                )
            ).first()
            art = (
                await session.execute(
                    text("SELECT 1 FROM ben.news_articles WHERE id = :fid LIMIT 1"),
                    {"fid": str(fid)},
                )
            ).first()
            assert sdv is None, f"WorkspaceFile {fid} leaked into source_document_versions"
            assert art is None, f"WorkspaceFile {fid} leaked into news_articles"
    results.append(("news_absent", "ok", str(len(file_ids)), ""))

    print("--- RESULTS ---")
    for row in results:
        print(row)
    print(f"workspace_a_file_count={listed['count']}")
    print("OK: File Library V1 real validation passed")
    return 0


async def _storage_key(org_id: uuid.UUID, ws: uuid.UUID, fid: uuid.UUID) -> str:
    async with get_db_session() as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        row = await session.get(WorkspaceFile, fid)
        assert row and row.workspace_id == ws
        return row.storage_key


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
