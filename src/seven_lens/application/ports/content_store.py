"""Application-facing error taxonomy for exact-byte memory content storage."""

from __future__ import annotations


class ContentStoreError(RuntimeError):
    """Fixed-message CAS failure; raw content is never echoed."""


class ContentStoreMissingError(ContentStoreError):
    """The requested object is intentionally absent and may be skipped as stale."""


class ContentStoreIntegrityError(ContentStoreError):
    """The requested object exists but violates the CAS/path integrity contract."""
