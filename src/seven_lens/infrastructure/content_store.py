"""Bounded local SHA-256 content-addressed store with atomic publication."""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from seven_lens.application.ports.content_store import (
    ContentStoreError,
    ContentStoreIntegrityError,
    ContentStoreMissingError,
)

__all__ = [
    "ContentStoreError",
    "ContentStoreIntegrityError",
    "ContentStoreMissingError",
    "FileContentStore",
    "StoredContent",
]


@dataclass(frozen=True, slots=True)
class StoredContent:
    content_hash: str
    size: int


class FileContentStore:
    def __init__(self, root: Path, *, maximum_bytes: int = 4_000_000) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("content store root must be an absolute Path")
        if type(maximum_bytes) is not int or maximum_bytes < 1:
            raise ValueError("maximum_bytes must be positive")
        self._root = root
        self._maximum_bytes = maximum_bytes
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink():
            raise ContentStoreError("content store root must not be a symlink")
        self._resolved_root = root.resolve(strict=True)

    def put(self, content: bytes, *, declared_hash: str | None = None) -> StoredContent:
        if type(content) is not bytes or not content or len(content) > self._maximum_bytes:
            raise ContentStoreError("content is empty, malformed, or oversized")
        digest = hashlib.sha256(content).hexdigest()
        if declared_hash is not None and declared_hash != digest:
            raise ContentStoreError("declared content hash does not match bytes")
        target = self._target(digest)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (
            target.parent.is_symlink()
            or target.parent.resolve(strict=True).parent != self._resolved_root
        ):
            raise ContentStoreError("content path escapes the store")
        if target.exists():
            if target.is_symlink() or target.read_bytes() != content:
                raise ContentStoreError("existing content identity collision")
            return StoredContent(digest, len(content))
        temporary = target.with_name(f".{digest}.{secrets.token_hex(8)}.tmp")
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.read_bytes() != content:
                raise ContentStoreError("staged content verification failed")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredContent(digest, len(content))

    def get(self, content_hash: str) -> bytes:
        target = self._target(content_hash)
        if (
            target.parent.is_symlink()
            or target.parent.resolve(strict=False).parent != self._resolved_root
        ):
            raise ContentStoreIntegrityError("content object path is not confined")
        if target.is_symlink():
            raise ContentStoreIntegrityError("content object is a symlink")
        if not target.exists():
            raise ContentStoreMissingError("content object is missing")
        if not target.is_file():
            raise ContentStoreIntegrityError("content object is not a regular file")
        try:
            content = target.read_bytes()
        except FileNotFoundError as error:
            raise ContentStoreMissingError("content object is missing") from error
        except OSError as error:
            raise ContentStoreError("content object read failed") from error
        if (
            len(content) > self._maximum_bytes
            or hashlib.sha256(content).hexdigest() != content_hash
        ):
            raise ContentStoreIntegrityError("content object verification failed")
        return content

    def verify(self, content_hash: str) -> bool:
        try:
            self.get(content_hash)
        except ContentStoreError:
            return False
        return True

    def _target(self, content_hash: str) -> Path:
        if (
            type(content_hash) is not str
            or len(content_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in content_hash)
        ):
            raise ContentStoreError("content hash is invalid")
        target = self._root / content_hash[:2] / content_hash
        if target.parent.parent.resolve(strict=False) != self._resolved_root:
            raise ContentStoreError("content path escapes the store")
        return target
