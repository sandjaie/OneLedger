# api/oneledger/ingest/storage.py
"""Where an uploaded statement is put, and what it is called once it is there.

`pdf-statements/` holds real statements **with card numbers in the filenames**, so the uploaded
name is the one input this module treats as hostile. It never becomes a path, and it never
reaches a log line, an exception message or a response (spec §5.6). The stored name is the
checksum of the bytes, which makes the blob content-addressed: the same file uploaded twice is
the same file on disk, and the refusal that matters is the database's, which can name the
earlier import.

The masked name is kept only so the owner can recognise which file they uploaded. It is masked
here, before it goes anywhere, rather than on the way out of a query.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

# What formats an identifier rather than separating two numbers: whitespace, hyphen, en dash,
# dot, underscore, slash. `security-and-privacy.html` §1 and spec §5.6.
_SEPARATOR = r"[\s\-–._/]"
# A run of digits, possibly formatted by those separators, starting and ending with a digit.
_RUN = re.compile(rf"\d(?:(?:\d|{_SEPARATOR})*\d)?")
# Seven digits is the threshold: a four-digit year, a day and a short reference survive.
_IDENTIFIER_DIGITS = 7
_KEPT_DIGITS = 4


class StorageError(ValueError):
    """A refusal safe to show. It never carries the uploaded filename or the file's bytes."""


class DuplicateDocument(StorageError):
    """This household has imported these exact bytes before, and this says when."""


@dataclass(frozen=True)
class StoredDocument:
    sha256: str
    path: Path
    byte_size: int
    masked_filename: str


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mask_filename(name: str) -> str:
    """Replace every identifier-length digit run with `…` and its last four digits.

    The count is taken across separators, not within a contiguous run: checking only
    contiguous digits would leave `1234-5678-9012-3456` — a whole card number — untouched.
    A date written `2026.01.15` totals eight digits and is masked too. That is deliberate
    over-masking: the alternative is a rule that guesses which runs are identifiers, and a
    guess that is wrong once has stored an account number.
    """

    def replace(match: re.Match[str]) -> str:
        digits = [c for c in match.group() if c.isdigit()]
        if len(digits) < _IDENTIFIER_DIGITS:
            return match.group()
        return "…" + "".join(digits[-_KEPT_DIGITS:])

    return _RUN.sub(replace, name)


def _validated_name(name: str) -> str:
    """Refuse a name that tries to be a path, rather than masking it and moving on.

    The name never reaches a path in any case — the checksum does — so this changes no
    behaviour on the happy path. It exists because the attempt is the signal, and a refusal
    is cheaper to notice than a mask that quietly worked.
    """
    if not name or name in {".", ".."}:
        raise StorageError("the uploaded file has no usable name")
    if name != Path(name).name or "\\" in name or Path(name).is_absolute():
        raise StorageError("the uploaded file name may not contain a path")
    return name


def _documents_root() -> Path:
    configured = os.environ.get("DOCUMENTS_DIR")
    if not configured:
        # Naming the variable is the whole value of this message: without it the operator sees
        # a write failure and starts looking at permissions.
        raise StorageError("DOCUMENTS_DIR is not set, so no document can be stored")
    return Path(configured)


def store(
    data: bytes, household_id: UUID, original_filename: str, *, root: Path | None = None
) -> StoredDocument:
    """Write the bytes under `<root>/<household_id>/<sha256>.pdf` and describe what was stored."""
    masked = mask_filename(_validated_name(original_filename))
    directory = (root if root is not None else _documents_root()) / str(household_id)

    if directory.is_symlink():
        raise StorageError("the household document directory is a link and will not be written")
    directory.mkdir(parents=True, exist_ok=True)

    digest = checksum(data)
    path = directory / f"{digest}.pdf"
    if path.is_symlink():
        # A link placed here would send the statement wherever it points, under a name the
        # rest of the system believes is content-addressed.
        raise StorageError("the destination is a link and will not be written through")

    if not path.exists():
        # Written beside the target and moved into place. The name is a promise about the
        # bytes — nothing downstream re-checksums the file — so a half-written blob must
        # never be able to hold it. The temporary name is unique because two uploads of the
        # same statement would otherwise write the same scratch file at the same time.
        handle, pending = tempfile.mkstemp(dir=directory, suffix=".part")
        try:
            with os.fdopen(handle, "wb") as writer:
                writer.write(data)
            os.replace(pending, path)
        except BaseException:
            Path(pending).unlink(missing_ok=True)
            raise

    return StoredDocument(sha256=digest, path=path, byte_size=len(data), masked_filename=masked)


def refuse_if_already_imported(cur: psycopg.Cursor[Any], household_id: UUID, sha256: str) -> None:
    """Refuse a file this household has already imported, naming when and which batch.

    Layer 1 of the three (spec §4.7): free, and it catches the common case before a page is
    parsed. The blob write above is idempotent, so this — not the filesystem — is where a
    re-upload is stopped.
    """
    cur.execute(
        """SELECT d.uploaded_at::date, b.id
             FROM document d
             LEFT JOIN import_batch b ON b.document_id = d.id
            WHERE d.household_id = %s AND d.sha256 = %s
            ORDER BY b.created_at NULLS LAST
            LIMIT 1""",
        (household_id, sha256),
    )
    row = cur.fetchone()
    if row is None:
        return
    uploaded_on, batch_id = row
    named = f" as batch {batch_id}" if batch_id is not None else ""
    raise DuplicateDocument(f"this file was already imported on {uploaded_on}{named}")
