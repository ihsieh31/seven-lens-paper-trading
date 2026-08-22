"""Injected GET-only source adapter boundary; this module performs no network itself."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit


class SourceTransportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GetRequest:
    url: str
    timeout_seconds: int
    maximum_bytes: int
    request_id: str


@dataclass(frozen=True, slots=True)
class GetResponse:
    status: int
    content_type: str
    body: bytes
    final_url: str


class ReadOnlyGetTransport(Protocol):
    def get(self, request: GetRequest) -> GetResponse: ...


class SourceHttpAdapter:
    def __init__(
        self,
        transport: ReadOnlyGetTransport,
        *,
        allowed_hosts: frozenset[str],
        allowed_content_types: frozenset[str],
        maximum_bytes: int = 2_000_000,
        timeout_seconds: int = 10,
    ) -> None:
        if not allowed_hosts or not allowed_content_types:
            raise ValueError("source adapter allowlists must be non-empty")
        self._transport = transport
        self._hosts = allowed_hosts
        self._types = allowed_content_types
        self._maximum = maximum_bytes
        self._timeout = timeout_seconds

    def fetch(self, url: str) -> GetResponse:
        self._validate_url(url)
        identity = hashlib.sha256(
            f"GET\0{url}\0{self._maximum}\0{self._timeout}".encode()
        ).hexdigest()
        try:
            response = self._transport.get(GetRequest(url, self._timeout, self._maximum, identity))
        except (TimeoutError, SourceTransportError) as error:
            raise SourceTransportError("source GET failed") from error
        if type(response) is not GetResponse:
            raise SourceTransportError("source GET returned malformed response")
        self._validate_url(response.final_url)
        if response.final_url != url:
            raise SourceTransportError("source redirect is not allowed")
        if response.status != 200:
            raise SourceTransportError("source GET returned a disallowed status")
        media_type = response.content_type.split(";", 1)[0].strip().lower()
        if media_type not in self._types:
            raise SourceTransportError("source GET returned a disallowed content type")
        if (
            type(response.body) is not bytes
            or not response.body
            or len(response.body) > self._maximum
        ):
            raise SourceTransportError("source GET returned malformed or oversized content")
        return response

    def _validate_url(self, url: str) -> None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (TypeError, ValueError) as error:
            raise SourceTransportError("source URL violates the allowlist policy") from error
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self._hosts
            or port is not None
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise SourceTransportError("source URL violates the allowlist policy")
