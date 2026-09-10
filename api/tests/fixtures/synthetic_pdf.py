"""Minimal, valid single-font PDF byte generator for M2 ingestion tests.

No new dependency: this writes raw PDF syntax (a Catalog, a Pages tree, one shared base-14
Helvetica font, and one content stream per page placing text with an absolute text matrix).
Never write a tracked .pdf from this -- callers get bytes, or write to tmp_path themselves.

Non-Latin characters receive free single-byte codes and a ToUnicode map. Helvetica's A glyph
supplies their geometry; the mapping supplies the actual Unicode extraction text. This tests
PDF text extraction, not Arabic glyph shaping, and requires no embedded font or dependency.
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


def _escape(text: str, encoding: dict[str, int]) -> bytes:
    raw = bytes(encoding[character] for character in text)
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
    characters = sorted({character for p in placements for character in p.text})
    encoding = {character: ord(character) for character in characters if ord(character) < 128}
    extended = [character for character in characters if character not in encoding]
    if len(extended) > 128:
        raise ValueError("synthetic PDF supports at most 128 distinct non-ASCII characters")
    encoding.update({character: index + 128 for index, character in enumerate(extended)})
    mappings = [
        f"<{code:02X}> <{character.encode('utf-16-be').hex().upper()}>"
        for character, code in encoding.items()
    ]
    cmap_lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /SyntheticUnicode def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<00> <FF>",
        "endcodespacerange",
    ]
    for offset in range(0, len(mappings), 100):
        group = mappings[offset : offset + 100]
        cmap_lines.extend([f"{len(group)} beginbfchar", *group, "endbfchar"])
    cmap_lines.extend(["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"])
    cmap = "\n".join(cmap_lines).encode("ascii")
    objects[4] = f"<< /Length {len(cmap)} >>\nstream\n".encode() + cmap + b"\nendstream"
    differences = "128 " + " ".join("/A" for _ in extended) if extended else ""
    objects[font_obj] = (
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        "/Encoding << /Type /Encoding /BaseEncoding /WinAnsiEncoding "
        f"/Differences [{differences}] >> "
        "/ToUnicode 4 0 R >>"
    ).encode("ascii")

    next_obj = 5
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
            ops.append(b"(" + _escape(pl.text, encoding) + b") Tj")
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


# ---------------------------------------------------------------------------------------
# Encryption, for the one layout whose producer ships password-protected statements.
#
# The standard security handler at revision 2 (40-bit RC4), written out here for the same
# reason the rest of this module writes raw PDF syntax: proving that an encrypted statement
# parses with its password and is refused without one must not cost a dependency. RC4 and MD5
# are what revision 2 specifies; neither is a security choice, and nothing outside a test
# fixture uses this.
# ---------------------------------------------------------------------------------------

_PAD = bytes(
    [
        0x28,
        0xBF,
        0x4E,
        0x5E,
        0x4E,
        0x75,
        0x8A,
        0x41,
        0x64,
        0x00,
        0x4E,
        0x56,
        0xFF,
        0xFA,
        0x01,
        0x08,
        0x2E,
        0x2E,
        0x00,
        0xB6,
        0xD0,
        0x68,
        0x3E,
        0x80,
        0x2F,
        0x0C,
        0xA9,
        0xFE,
        0x64,
        0x53,
        0x69,
        0x7A,
    ]
)
_PERMISSIONS = -1  # every permission granted; the fixture is about the password, not the rights


def _rc4(key: bytes, data: bytes) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        out.append(byte ^ state[(state[i] + state[j]) % 256])
    return bytes(out)


def _padded(password: str) -> bytes:
    raw = password.encode("latin-1")[:32]
    return raw + _PAD[: 32 - len(raw)]


def _escape_literal(data: bytes) -> bytes:
    return (
        data.replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
        .replace(b"\r", b"\\r")
    )


def build_encrypted_pdf(
    placements: list[Placement],
    password: str,
    page_count: int | None = None,
    page_size: tuple[float, float] = (612.0, 792.0),
) -> bytes:
    """The same document `build_pdf` writes, encrypted with `password` as the user password."""
    import hashlib
    import re as _re

    plain = build_pdf(placements, page_count=page_count, page_size=page_size)
    document_id = hashlib.md5(plain).digest()  # noqa: S324 - revision 2 specifies MD5

    owner_key = hashlib.md5(_padded(password)).digest()[:5]  # noqa: S324
    owner_entry = _rc4(owner_key, _padded(password))
    encryption_key = hashlib.md5(  # noqa: S324
        _padded(password)
        + owner_entry
        + (_PERMISSIONS & 0xFFFFFFFF).to_bytes(4, "little")
        + document_id
    ).digest()[:5]
    user_entry = _rc4(encryption_key, _PAD)

    def object_key(number: int) -> bytes:
        return hashlib.md5(  # noqa: S324
            encryption_key + number.to_bytes(3, "little") + (0).to_bytes(2, "little")
        ).digest()[:10]

    # Every stream is re-encrypted in place. The objects carry no strings of their own — the
    # text is inside the content streams — so streams are the whole of it.
    def encrypt_stream(match: _re.Match[bytes]) -> bytes:
        number = int(match.group(1))
        body = match.group(3)
        encrypted = _rc4(object_key(number), body)
        return (
            match.group(1)
            + b" 0 obj\n<< /Length "
            + str(len(encrypted)).encode()
            + b" >>\nstream\n"
            + encrypted
            + b"\nendstream"
        )

    rebuilt = _re.sub(
        rb"(\d+) 0 obj\n<< /Length (\d+) >>\nstream\n(.*?)\nendstream",
        encrypt_stream,
        plain,
        flags=_re.DOTALL,
    )

    # Re-lay the file out: encryption changes every stream's length, so every offset moves.
    objects: dict[int, bytes] = {}
    for match in _re.finditer(rb"(\d+) 0 obj\n(.*?)\nendobj\n", rebuilt, flags=_re.DOTALL):
        objects[int(match.group(1))] = match.group(2)
    encrypt_number = max(objects) + 1
    objects[encrypt_number] = (
        b"<< /Filter /Standard /V 1 /R 2 /O ("
        + _escape_literal(owner_entry)
        + b") /U ("
        + _escape_literal(user_entry)
        + b") /P "
        + str(_PERMISSIONS).encode()
        + b" >>"
    )

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(buf)
        buf += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    highest = max(objects)
    xref_offset = len(buf)
    buf += f"xref\n0 {highest + 1}\n".encode()
    buf += b"0000000000 65535 f \n"
    for number in range(1, highest + 1):
        buf += f"{offsets[number]:010d} 00000 n \n".encode()
    buf += (
        f"trailer\n<< /Size {highest + 1} /Root 1 0 R /Encrypt {encrypt_number} 0 R "
        f"/ID [<{document_id.hex()}> <{document_id.hex()}>] >>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    ).encode()
    return bytes(buf)
