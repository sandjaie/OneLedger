"""Minimal, valid single-font PDF byte generator for M2 ingestion tests.

No new dependency: this writes raw PDF syntax (a Catalog, a Pages tree, one shared base-14
Helvetica font, and one content stream per page placing text with an absolute text matrix).
Never write a tracked .pdf from this -- callers get bytes, or write to tmp_path themselves.

Text is encoded latin-1 (Helvetica/WinAnsi territory). Arabic-range codepoints do not survive
that encoding -- there is no Arabic glyph in base-14 Helvetica to round-trip through, so the
`filter_script:arabic` engine tests build synthetic Word lists directly instead of routing
through this generator (engine.py never imports pdfplumber, so that is first-class). See the
spike test in test_synthetic_pdf.py for what does and does not survive the round trip.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Placement:
    page: int  # 0-based page index
    x: float
    y: float
    text: str
    size: float = 10.0


def _escape(text: str) -> bytes:
    raw = text.encode("latin-1", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def build_pdf(
    placements: list[Placement],
    page_count: int | None = None,
    page_size: tuple[float, float] = (612.0, 792.0),
) -> bytes:
    """Build a minimal PDF. `page_count` lets a caller request pages with zero placements
    (the scanned-page case) beyond the highest page index used in `placements`."""
    if page_count is not None:
        n_pages = page_count
    else:
        n_pages = max((p.page for p in placements), default=-1) + 1
    n_pages = max(n_pages, 1)

    by_page: dict[int, list[Placement]] = {i: [] for i in range(n_pages)}
    for p in placements:
        by_page.setdefault(p.page, []).append(p)

    objects: dict[int, bytes] = {}
    font_obj = 3
    objects[font_obj] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    next_obj = 4
    kids: list[int] = []
    width, height = page_size
    for i in range(n_pages):
        content_obj, next_obj = next_obj, next_obj + 1
        page_obj, next_obj = next_obj, next_obj + 1
        kids.append(page_obj)

        ops = [b"BT"]
        for pl in by_page.get(i, []):
            ops.append(f"/F1 {pl.size} Tf".encode("latin-1"))
            ops.append(f"1 0 0 1 {pl.x} {pl.y} Tm".encode("latin-1"))
            ops.append(b"(" + _escape(pl.text) + b") Tj")
        ops.append(b"ET")
        stream = b"\n".join(ops)
        objects[content_obj] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream"
        )
        objects[page_obj] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /Font << /F1 {font_obj} 0 R >> >> /Contents {content_obj} 0 R >>"
        ).encode("latin-1")

    objects[2] = (
        "<< /Type /Pages /Kids [" + " ".join(f"{k} 0 R" for k in kids) + f"] /Count {n_pages} >>"
    ).encode("latin-1")
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    buf = bytearray(header)
    offsets: dict[int, int] = {}
    for n in sorted(objects):
        offsets[n] = len(buf)
        buf += f"{n} 0 obj\n".encode("latin-1")
        buf += objects[n]
        buf += b"\nendobj\n"

    max_obj = max(objects)
    xref_offset = len(buf)
    buf += f"xref\n0 {max_obj + 1}\n".encode("latin-1")
    buf += b"0000000000 65535 f \n"
    for n in range(1, max_obj + 1):
        buf += f"{offsets[n]:010d} 00000 n \n".encode("latin-1")
    trailer = f"trailer\n<< /Size {max_obj + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF"
    buf += trailer.encode("latin-1")
    return bytes(buf)
