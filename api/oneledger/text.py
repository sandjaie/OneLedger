"""Shared free-text normalization for owner-entered fields (title, name, label, reason, note)
across route modules. Hoisted from `routes/register.py`, which had the only generic form; every
other route re-implemented the same NFKC-collapse-and-bound rule under its own field name."""

from __future__ import annotations

import unicodedata


def normalize_bounded(value: str, field: str, limit: int) -> str:
    """NFKC-normalize `value`, collapse runs of whitespace to single spaces, and require the
    result be 1-`limit` characters — otherwise raise `ValueError` naming `field` and `limit`."""
    collapsed = " ".join(unicodedata.normalize("NFKC", value).split())
    if not 1 <= len(collapsed) <= limit:
        raise ValueError(f"{field} must be 1-{limit} characters")
    return collapsed
