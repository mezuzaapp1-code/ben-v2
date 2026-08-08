"""Provider-independent document parsing contract for Document Intelligence (Gate 2).

Source bytes -> DocumentParser -> StructuredDocument (per-page extraction truth).

The contract is intentionally decoupled from any single parser vendor (pypdf,
Docling, Gemini, OpenAI, ...). Gate 2 ships a first pypdf-based PDF adapter plus
simple adapters for text-like and image files. No OCR is performed here; image /
image-only pages are reported as ``needs_ocr`` so a future gate can process them.

Invariant: every detected source page produces exactly one PageResult. No page
is ever silently dropped, and resource limits are represented explicitly as
``skipped`` pages rather than truncated away.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Bump when the extraction algorithm/adapter output changes in a way that should
# invalidate previously persisted pages/chunks.
EXTRACTION_VERSION = 1

# Deterministic, explicit resource ceiling. Pages beyond this are represented as
# `skipped` (failure_code=resource_limit) and mark the document truncated — never
# silently dropped.
MAX_EXTRACT_PAGES = int(os.getenv("BEN_MAX_EXTRACT_PAGES", "1000"))

PAGE_EXTRACTED = "extracted"
PAGE_EMPTY = "empty"
PAGE_NEEDS_OCR = "needs_ocr"
PAGE_FAILED = "failed"
PAGE_SKIPPED = "skipped"
PAGE_STATUSES = (PAGE_EXTRACTED, PAGE_EMPTY, PAGE_NEEDS_OCR, PAGE_FAILED, PAGE_SKIPPED)


@dataclass(frozen=True)
class PageResult:
    page_number: int  # 1-based source page
    status: str
    text: str = ""
    char_count: int = 0
    needs_ocr: bool = False
    failure_code: str | None = None
    failure_detail: str | None = None


@dataclass(frozen=True)
class StructuredDocument:
    source_page_count: int
    pages: tuple[PageResult, ...]
    extraction_version: int
    parser_id: str
    parser_version: str
    truncated: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _mk_page(page_number: int, *, text: str | None, has_images: bool, error: str | None) -> PageResult:
    """Deterministically classify a single page from raw extraction signals."""
    if error:
        return PageResult(
            page_number=page_number,
            status=PAGE_FAILED,
            failure_code=(error.split(":")[0][:64] or "extract_error"),
            failure_detail=error[:500],
        )
    body = (text or "").replace("\x00", " ").strip()
    if body:
        return PageResult(page_number=page_number, status=PAGE_EXTRACTED, text=body, char_count=len(body))
    if has_images:
        return PageResult(page_number=page_number, status=PAGE_NEEDS_OCR, needs_ocr=True, failure_code="needs_ocr")
    return PageResult(page_number=page_number, status=PAGE_EMPTY)


def _assemble_document(
    page_items: list[tuple[str | None, bool, str | None]],
    *,
    source_page_count: int,
    parser_id: str,
    parser_version: str,
    max_pages: int = MAX_EXTRACT_PAGES,
    warnings: tuple[str, ...] = (),
) -> StructuredDocument:
    """Build a StructuredDocument, representing every source page exactly once.

    ``page_items`` are the (text, has_images, error) signals for the pages that
    were actually attempted (source order, up to max_pages). Any remaining source
    pages become explicit ``skipped``/resource_limit rows and mark the document
    truncated.
    """
    pages: list[PageResult] = []
    attempted = min(len(page_items), max_pages, source_page_count) if source_page_count else min(len(page_items), max_pages)
    for i in range(attempted):
        text, has_images, error = page_items[i]
        pages.append(_mk_page(i + 1, text=text, has_images=has_images, error=error))
    truncated = source_page_count > attempted
    for pn in range(attempted + 1, source_page_count + 1):
        pages.append(
            PageResult(
                page_number=pn,
                status=PAGE_SKIPPED,
                failure_code="resource_limit",
                failure_detail=f"page {pn} beyond max_pages={max_pages}",
            )
        )
    return StructuredDocument(
        source_page_count=source_page_count,
        pages=tuple(pages),
        extraction_version=EXTRACTION_VERSION,
        parser_id=parser_id,
        parser_version=parser_version,
        truncated=truncated,
        warnings=tuple(warnings),
    )


class DocumentParser:
    """Base contract: parse(source) -> StructuredDocument."""

    parser_id = "base"
    parser_version = "0"

    def parse(self, path: Path, *, media_type: str, filename: str, max_pages: int = MAX_EXTRACT_PAGES) -> StructuredDocument:  # noqa: D401
        raise NotImplementedError


class PdfDocumentParser(DocumentParser):
    parser_id = "pypdf"

    @property
    def parser_version(self) -> str:  # type: ignore[override]
        try:
            import pypdf

            return str(getattr(pypdf, "__version__", "unknown"))
        except Exception:  # pragma: no cover
            return "unavailable"

    def _page_signals(self, page) -> tuple[str | None, bool, str | None]:
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            return None, False, f"extract_error:{type(exc).__name__}:{str(exc)[:120]}"
        has_images = False
        if not text.strip():
            try:
                has_images = len(list(page.images)) > 0
            except Exception:  # noqa: BLE001
                has_images = False
        return text, has_images, None

    def parse(self, path: Path, *, media_type: str, filename: str, max_pages: int = MAX_EXTRACT_PAGES) -> StructuredDocument:
        try:
            from pypdf import PdfReader
        except ImportError:
            return _assemble_document(
                [], source_page_count=0, parser_id=self.parser_id, parser_version="unavailable",
                max_pages=max_pages, warnings=("pdf_parser_unavailable",),
            )
        reader = PdfReader(str(path))
        source_page_count = len(reader.pages)
        items: list[tuple[str | None, bool, str | None]] = []
        for idx in range(min(source_page_count, max_pages)):
            items.append(self._page_signals(reader.pages[idx]))
        return _assemble_document(
            items, source_page_count=source_page_count, parser_id=self.parser_id,
            parser_version=self.parser_version, max_pages=max_pages,
        )


class GenericTextParser(DocumentParser):
    """Single-page adapter for text-like formats (txt/md/csv/json/docx/xlsx).

    Reuses the existing per-type text extraction and represents the file as one
    page (non-paginated sources have no reliable page identity in V1).
    """

    parser_id = "generic_text"
    parser_version = "1"

    def parse(self, path: Path, *, media_type: str, filename: str, max_pages: int = MAX_EXTRACT_PAGES) -> StructuredDocument:
        from services.workspace_files.extract import extract_text

        text, err = extract_text(path, media_type=media_type, filename=filename)
        if err and text is None:
            item = (None, False, err)
        else:
            item = (text or "", False, None)
        return _assemble_document(
            [item], source_page_count=1, parser_id=self.parser_id, parser_version=self.parser_version,
            max_pages=max_pages,
        )


class ImagePlaceholderParser(DocumentParser):
    """Image files are single-page, image-only -> needs_ocr (no OCR in Gate 2)."""

    parser_id = "image"
    parser_version = "1"

    def parse(self, path: Path, *, media_type: str, filename: str, max_pages: int = MAX_EXTRACT_PAGES) -> StructuredDocument:
        return _assemble_document(
            [("", True, None)], source_page_count=1, parser_id=self.parser_id,
            parser_version=self.parser_version, max_pages=max_pages,
        )


def resolve_parser(media_type: str, filename: str) -> DocumentParser:
    suffix = Path(filename or "").suffix.lower()
    mt = (media_type or "").lower()
    if suffix == ".pdf" or mt == "application/pdf":
        return PdfDocumentParser()
    if mt.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return ImagePlaceholderParser()
    return GenericTextParser()
