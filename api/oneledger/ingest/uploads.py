"""Private multipart PDF reading shared by statement import routes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from shutil import copyfileobj
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import HTTPException, UploadFile

from .words import extract_pages


@contextmanager
def uploaded_pages(file: UploadFile, password: str | None) -> Iterator[tuple[bytes, list[Any]]]:
    """Read an upload once and remove its private scratch copy on every exit."""
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=422, detail="the uploaded file is empty")
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(prefix="oneledger-parse-", suffix=".pdf", delete=False) as temp:
            temporary_path = Path(temp.name)
            file.file.seek(0)
            copyfileobj(file.file, temp)
        yield data, extract_pages(temporary_path, password=password)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
