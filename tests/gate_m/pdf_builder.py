"""Multi-line synthetic PDF builder for Gate M.

Same object model as tests/test_document_intelligence_gate2.py::make_pdf
(Helvetica text that pypdf extracts). Adds T* line breaks so tables survive
extraction as separate lines. ASCII only (latin-1 object streams).
"""
from __future__ import annotations


def make_multiline_pdf(pages: list[str], *, font_size: int = 10) -> bytes:
    page_nums: list[int] = []
    content_nums: list[int] = []
    num = 4
    for _ in pages:
        page_nums.append(num)
        num += 1
        content_nums.append(num)
        num += 1
    objects: dict[int, str] = {}
    objects[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{p} 0 R" for p in page_nums)
    objects[2] = f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>"
    objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    leading = max(font_size + 2, 12)
    for i, tx in enumerate(pages):
        po, co = page_nums[i], content_nums[i]
        objects[po] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {co} 0 R >>"
        )
        lines = (tx or "").split("\n")
        parts = [f"BT /F1 {font_size} Tf {leading} TL 48 740 Td"]
        for li, line in enumerate(lines):
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if li == 0:
                parts.append(f"({safe}) Tj")
            else:
                parts.append(f"T* ({safe}) Tj")
        parts.append("ET")
        stream = "\n".join(parts) if tx else ""
        objects[co] = f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream"
    out = b"%PDF-1.4\n"
    offsets: dict[int, int] = {}
    for k in sorted(objects):
        offsets[k] = len(out)
        out += f"{k} 0 obj\n{objects[k]}\nendobj\n".encode("latin-1")
    xref = len(out)
    total = max(objects) + 1
    out += f"xref\n0 {total}\n".encode()
    out += b"0000000000 65535 f \n"
    for k in range(1, total):
        out += f"{offsets[k]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out
