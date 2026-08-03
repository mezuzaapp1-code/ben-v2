"""Bounded text extraction for Workspace File Library V1."""
from __future__ import annotations

from pathlib import Path

MAX_EXTRACT_CHARS = 200_000


def _clip(text: str) -> str:
    clean = (text or "").replace("\x00", " ")
    if len(clean) > MAX_EXTRACT_CHARS:
        return clean[:MAX_EXTRACT_CHARS]
    return clean


def extract_text(path: Path, *, media_type: str, filename: str) -> tuple[str | None, str | None]:
    """
    Returns (extracted_text, failure_code).
    failure_code None means success (text may be empty for images).
    """
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".markdown", ".csv", ".json"} or media_type.startswith("text/"):
            raw = path.read_bytes()
            for enc in ("utf-8", "utf-8-sig", "latin-1"):
                try:
                    return _clip(raw.decode(enc)), None
                except UnicodeDecodeError:
                    continue
            return _clip(raw.decode("utf-8", errors="replace")), None

        if suffix == ".pdf" or media_type == "application/pdf":
            return _extract_pdf(path)

        if suffix == ".docx":
            return _extract_docx(path)

        if suffix == ".xlsx":
            return _extract_xlsx(path)

        if media_type.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            return "", None  # stored; preview via authenticated bytes

        if suffix == ".pptx":
            return "", None  # stored only in V1

        return None, "unsupported_extraction"
    except Exception as exc:  # noqa: BLE001
        return None, f"extract_error:{type(exc).__name__}:{str(exc)[:120]}"


def _extract_pdf(path: Path) -> tuple[str | None, str | None]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError:
            # Fallback: mark ready without text if no PDF lib — still stored.
            return "", "pdf_parser_unavailable"

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages[:80]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            continue
    text = _clip("\n".join(parts).strip())
    return text, None


def _extract_docx(path: Path) -> tuple[str | None, str | None]:
    try:
        import zipfile
        from xml.etree import ElementTree as ET
    except ImportError:
        return None, "docx_parser_unavailable"

    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
        root = ET.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        texts = [node.text for node in root.findall(".//w:t", ns) if node.text]
        return _clip("\n".join(texts)), None
    except Exception as exc:  # noqa: BLE001
        return None, f"docx_extract_failed:{str(exc)[:80]}"


def _extract_xlsx(path: Path) -> tuple[str | None, str | None]:
    try:
        import zipfile
        from xml.etree import ElementTree as ET
    except ImportError:
        return None, "xlsx_parser_unavailable"

    try:
        with zipfile.ZipFile(path) as zf:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                for si in root.findall("m:si", ns):
                    bits = [t.text or "" for t in si.findall(".//m:t", ns)]
                    shared.append("".join(bits))
            # Also pull sheet1 cell values when shared strings missing.
            sheet_names = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")]
            cells: list[str] = []
            for name in sheet_names[:3]:
                root = ET.fromstring(zf.read(name))
                ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                for c in root.findall(".//m:c", ns):
                    v = c.find("m:v", ns)
                    if v is None or v.text is None:
                        continue
                    if c.get("t") == "s":
                        try:
                            cells.append(shared[int(v.text)])
                        except (ValueError, IndexError):
                            cells.append(v.text)
                    else:
                        cells.append(v.text)
            combined = shared + cells
            return _clip("\n".join(combined)), None
    except Exception as exc:  # noqa: BLE001
        return None, f"xlsx_extract_failed:{str(exc)[:80]}"
