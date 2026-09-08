"""Document storage: the checksum, the path, and the filename that never becomes either.

`pdf-statements/` holds real statements with card numbers **in the filenames** (spec §5.6), so
the uploaded name is the one input here that is assumed hostile. It never reaches a path, a log
line, an error message or a response — the stored name is the checksum — and what is kept for
the owner to recognise the file by is masked before it goes anywhere.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from oneledger.ingest.storage import (
    DuplicateDocument,
    StorageError,
    checksum,
    mask_filename,
    refuse_if_already_imported,
    store,
)

HOUSEHOLD = UUID("22222222-2222-2222-2222-222222222222")
OTHER_HOUSEHOLD = UUID("33333333-3333-3333-3333-333333333333")
STATEMENT = b"%PDF-1.7\nnot really a pdf, but these are the bytes we checksum\n"
# The shape the working tree actually holds, card number and all.
CARD_FILENAME = "statement-4111-1111-1111-1111-jan-2026.pdf"


def test_the_checksum_is_the_sha256_of_the_bytes() -> None:
    assert checksum(STATEMENT) == hashlib.sha256(STATEMENT).hexdigest()


def test_the_stored_name_is_the_checksum_and_never_the_upload(tmp_path: Path) -> None:
    """The one rule the whole module exists for. `statement-4111-…​.pdf` on disk would put a
    card number in a directory listing, a backup manifest and every log line naming the path."""
    stored = store(STATEMENT, HOUSEHOLD, CARD_FILENAME, root=tmp_path)

    assert stored.path.name == f"{checksum(STATEMENT)}.pdf"
    assert stored.path.read_bytes() == STATEMENT
    assert stored.byte_size == len(STATEMENT)
    assert "4111" not in str(stored.path)
    assert "statement-4111" not in str(stored.path)


def test_the_household_owns_its_own_directory(tmp_path: Path) -> None:
    mine = store(STATEMENT, HOUSEHOLD, CARD_FILENAME, root=tmp_path)
    theirs = store(STATEMENT, OTHER_HOUSEHOLD, CARD_FILENAME, root=tmp_path)

    assert mine.path != theirs.path, "the same bytes for another household are another document"
    assert mine.path.parent.name == str(HOUSEHOLD)
    assert mine.sha256 == theirs.sha256, "but they are the same file, and say so"


def test_the_kept_filename_is_masked_before_it_leaves_the_request(tmp_path: Path) -> None:
    """§5.6: an identifier-length run is replaced by its last four digits, on a *normalized*
    view — checking only contiguous runs would store `4111111111111111` whole."""
    stored = store(STATEMENT, HOUSEHOLD, CARD_FILENAME, root=tmp_path)
    assert stored.masked_filename == "statement-…1111-jan-2026.pdf"
    assert "4111" not in stored.masked_filename


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("statement-1234-5678-9012-3456.pdf", "statement-…3456.pdf"),
        ("acct 1234 5678 9012.pdf", "acct …9012.pdf"),
        ("stmt_12345678.pdf", "stmt_…5678.pdf"),
        ("jan-2026.pdf", "jan-2026.pdf"),
        ("statement 2026.pdf", "statement 2026.pdf"),
        ("1234567.pdf", "…4567.pdf"),
        ("123456.pdf", "123456.pdf"),
        # Deliberate over-masking: a dotted date totals eight digits, and §5.6 counts
        # digits across separators rather than guessing which runs are identifiers.
        ("2026.01.15-statement.pdf", "…0115-statement.pdf"),
    ],
)
def test_the_mask_replaces_a_formatted_identifier_and_leaves_a_date(raw: str, masked: str) -> None:
    """Seven digits is the threshold: a year, a day and a short reference survive; anything
    long enough to be an account or card number does not, however it is punctuated."""
    assert mask_filename(raw) == masked


def test_a_name_that_is_a_path_is_refused(tmp_path: Path) -> None:
    """The name never reaches a path — but a name that *tries* to is refused rather than
    quietly masked, because the attempt is the signal."""
    for hostile in ["../escape.pdf", "/etc/passwd", "a/b.pdf", "..", "sub\\dir.pdf"]:
        with pytest.raises(StorageError):
            store(STATEMENT, HOUSEHOLD, hostile, root=tmp_path)


def test_a_symlinked_target_is_refused(tmp_path: Path) -> None:
    """A pre-placed symlink at the destination would write the statement wherever it points."""
    household_dir = tmp_path / str(HOUSEHOLD)
    household_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.pdf"
    elsewhere.write_bytes(b"")
    (household_dir / f"{checksum(STATEMENT)}.pdf").symlink_to(elsewhere)

    with pytest.raises(StorageError):
        store(STATEMENT, HOUSEHOLD, CARD_FILENAME, root=tmp_path)
    assert elsewhere.read_bytes() == b"", "the target was not written through"


def test_a_symlinked_household_directory_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "documents"
    root.mkdir()
    (root / str(HOUSEHOLD)).symlink_to(outside, target_is_directory=True)

    with pytest.raises(StorageError):
        store(STATEMENT, HOUSEHOLD, CARD_FILENAME, root=root)


def test_a_missing_documents_dir_refuses_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCUMENTS_DIR", raising=False)
    with pytest.raises(StorageError, match="DOCUMENTS_DIR"):
        store(STATEMENT, HOUSEHOLD, CARD_FILENAME)


def test_storing_the_same_bytes_twice_is_the_same_file(tmp_path: Path) -> None:
    """Re-uploading is not an error at this layer — the blob is content-addressed, so the
    second write is a no-op. The refusal is the database's, and it names the earlier batch."""
    first = store(STATEMENT, HOUSEHOLD, CARD_FILENAME, root=tmp_path)
    second = store(STATEMENT, HOUSEHOLD, "renamed.pdf", root=tmp_path)
    assert first.path == second.path
    assert second.path.read_bytes() == STATEMENT


class FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row
        self.params: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.params = params

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.row


def test_an_earlier_import_of_the_same_file_is_named(tmp_path: Path) -> None:
    batch = UUID("44444444-4444-4444-4444-444444444444")
    cursor = FakeCursor((date(2026, 2, 1), batch))
    with pytest.raises(DuplicateDocument) as raised:
        refuse_if_already_imported(cursor, HOUSEHOLD, checksum(STATEMENT))  # type: ignore[arg-type]

    assert "2026-02-01" in str(raised.value)
    assert str(batch) in str(raised.value)
    assert cursor.params == (HOUSEHOLD, checksum(STATEMENT))


def test_a_file_not_seen_before_passes() -> None:
    """The guard returns nothing and raises nothing; reaching the next line is the assertion."""
    refuse_if_already_imported(FakeCursor(None), HOUSEHOLD, checksum(STATEMENT))  # type: ignore[arg-type]


def test_no_refusal_repeats_the_uploaded_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Every path out of this module — the exception, its args, and anything logged — is
    checked against the raw name, because §5.6 forbids it in all three."""
    caplog.set_level(logging.DEBUG)
    with pytest.raises(StorageError) as raised:
        store(STATEMENT, HOUSEHOLD, "../4111-1111-1111-1111.pdf", root=tmp_path)

    message = f"{raised.value} {raised.value.args} {caplog.text}"
    assert "4111" not in message
    assert "1111" not in message


def test_a_failed_write_leaves_nothing_under_the_promised_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The filename is a promise about the bytes and nothing downstream re-checksums the file,
    so the blob is written beside its target and moved in. A write that dies partway must
    leave no file at all — not a truncated one wearing the checksum of the whole."""
    monkeypatch.setattr(
        "oneledger.ingest.storage.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("the volume went away")),
    )
    with pytest.raises(OSError):
        store(STATEMENT, HOUSEHOLD, CARD_FILENAME, root=tmp_path)

    household_dir = tmp_path / str(HOUSEHOLD)
    assert not (household_dir / f"{checksum(STATEMENT)}.pdf").exists(), "no half-written blob"
    assert list(household_dir.iterdir()) == [], "and no scratch file left behind either"


def test_a_successful_write_leaves_no_scratch_file(tmp_path: Path) -> None:
    stored = store(STATEMENT, HOUSEHOLD, CARD_FILENAME, root=tmp_path)
    assert [p.name for p in stored.path.parent.iterdir()] == [f"{checksum(STATEMENT)}.pdf"]
