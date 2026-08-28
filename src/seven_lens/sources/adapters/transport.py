"""Policy-bound GET-only source transport; this module performs no network itself.

Every request is constructed from an immutable ``SourceFamilyPolicy``; callers can
only supply endpoint identifiers, path/query parameters, and allowlisted header
values.  There is no entrypoint accepting an arbitrary URL, method, or header set.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from typing import Final, Protocol
from urllib.parse import quote, unquote, urlsplit

from seven_lens.config.errors import ConfigurationError
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.roles import (
    P4_CANONICAL_REGISTRY_HASH,
    NonExecutableReason,
    P4SourceFamily,
    SourceFamilyPolicy,
    SourceManifestRegistry,
)

_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_ENDPOINT_ID: Final = re.compile(r"^[a-z0-9_]{1,64}$")
_PLACEHOLDER: Final = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_CONTROL_TEXT: Final = re.compile(r"[\x00-\x1f\x7f]")
_MAX_PARAM_BYTES: Final = 512
_MAX_HEADER_VALUE_BYTES: Final = 256

EXECUTOR_WIRE_CONTRACT: Final = (
    "The injected executor performs exactly one HTTP GET per call with no retry, "
    "no redirect following, and no fallback host.  It must abort once the "
    "compressed wire byte budget is reached and return the exact retrieved "
    "bytes; if it decompresses, the returned body is the post-decompression "
    "payload bounded by the family decompression budget."
)


class SourceRequestError(RuntimeError):
    """Bounded base type for typed transport failures."""


class FamilyNotExecutableError(SourceRequestError):
    def __init__(self, family: str, reason: NonExecutableReason) -> None:
        self.family = family
        self.reason = reason
        super().__init__(f"source family is not executable: {reason.value}")


class InvalidEndpointError(SourceRequestError):
    def __init__(self) -> None:
        super().__init__("endpoint identifier is unknown for this family")


class InvalidParameterError(SourceRequestError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class SourceFetchTimeoutError(SourceRequestError):
    def __init__(self) -> None:
        super().__init__("source GET timed out")


class SourceStatusError(SourceRequestError):
    def __init__(self, status_class: str) -> None:
        self.status_class = status_class
        super().__init__(f"source GET returned {status_class}")


class SourceFetchRedirectError(SourceRequestError):
    def __init__(self) -> None:
        super().__init__("source GET redirect is not allowed")


class SourceContentTypeError(SourceRequestError):
    def __init__(self) -> None:
        super().__init__("source GET returned a disallowed content type")


class SourceTransportBudgetError(SourceRequestError):
    def __init__(self) -> None:
        super().__init__("source GET exceeded its byte budget")


class SourceMalformedResponseError(SourceRequestError):
    def __init__(self) -> None:
        super().__init__("source GET returned a malformed response")


class SourceRateLimitError(SourceRequestError):
    def __init__(self) -> None:
        super().__init__("source GET rate budget is exhausted")


@dataclass(frozen=True, slots=True)
class ExecutorRequest:
    """The only request shape an executor accepts; headers are value-only."""

    url: str
    timeout_seconds: int
    maximum_bytes: int
    request_id: str
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ExecutorResponse:
    status: int
    content_type: str
    body: bytes
    final_url: str


class SourceGetExecutor(Protocol):
    """Injected transport seam implementing ``EXECUTOR_WIRE_CONTRACT``."""

    def get(self, request: ExecutorRequest) -> ExecutorResponse: ...


class SourceAuditSink(Protocol):
    def record(self, event: SourceFetchAudit) -> None: ...


@dataclass(frozen=True, slots=True)
class SourceFetchAudit:
    """Sanitized audit event: family, endpoint id, status class, bounded metrics."""

    family: str
    endpoint_id: str
    status_class: str
    latency_ms: int
    byte_count: int
    content_hash: str
    error_code: str | None
    occurred_at: UtcTimestamp

    def wire(self) -> dict[str, str | int | None]:
        return {
            "family": self.family,
            "endpoint_id": self.endpoint_id,
            "status_class": self.status_class,
            "latency_ms": self.latency_ms,
            "byte_count": self.byte_count,
            "content_hash": self.content_hash,
            "error_code": self.error_code,
            "occurred_at": str(self.occurred_at),
        }


@dataclass(frozen=True, slots=True, repr=False)
class PreparedRequest:
    """A fully validated GET; the URL is internal and never re-entered by callers."""

    family: P4SourceFamily
    endpoint_id: str
    url: str
    headers: tuple[tuple[str, str], ...]
    timeout_seconds: int
    maximum_bytes: int
    maximum_decompressed_bytes: int
    request_identity: str

    def __repr__(self) -> str:
        return (
            f"PreparedRequest(family={self.family.value}, "
            f"endpoint_id={self.endpoint_id}, request_identity={self.request_identity})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class FetchResult:
    body: bytes
    content_type: str
    content_hash: str
    request_identity: str
    audit: SourceFetchAudit

    def __repr__(self) -> str:
        return (
            f"FetchResult(content_type={self.content_type}, "
            f"content_hash={self.content_hash}, "
            f"request_identity={self.request_identity})"
        )


def _clean_text(value: object, field_name: str, maximum: int) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8", "strict")) > maximum:
        raise InvalidParameterError(f"{field_name} must be bounded text")
    if _CONTROL_TEXT.search(value) is not None:
        raise InvalidParameterError(f"{field_name} must not contain control characters")
    return value


class PolicyGetTransport:
    """Constructs policy-exact GETs and maps every failure to a bounded type."""

    def __init__(
        self,
        registry: SourceManifestRegistry,
        executor: SourceGetExecutor,
        *,
        audit_sink: SourceAuditSink | None = None,
    ) -> None:
        if type(registry) is not SourceManifestRegistry:
            raise ConfigurationError("transport requires an exact SourceManifestRegistry")
        if registry.registry_hash != P4_CANONICAL_REGISTRY_HASH:
            raise ConfigurationError("transport requires the canonical P4 source registry")
        if not callable(getattr(executor, "get", None)):
            raise ConfigurationError("transport requires an injected GET executor")
        self._registry = registry
        self._executor = executor
        self._sink = audit_sink
        self._rate_lock = Lock()
        self._rate_history: dict[P4SourceFamily, list[float]] = {
            family: [] for family in P4SourceFamily
        }

    @property
    def registry_hash(self) -> str:
        return self._registry.registry_hash

    def prepare(
        self,
        *,
        family: P4SourceFamily,
        endpoint_id: str,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> PreparedRequest:
        """Validate every input against the family manifest and build the URL."""
        if type(family) is not P4SourceFamily:
            raise InvalidParameterError("family requires an exact P4SourceFamily")
        _clean_text(endpoint_id, "endpoint_id", 64)
        if _ENDPOINT_ID.fullmatch(endpoint_id) is None:
            raise InvalidEndpointError()
        policy = self._registry.policy(family)
        if not policy.executable:
            reason = policy.non_executable_reason
            assert reason is not None
            self._emit(
                family.value, endpoint_id, "NOT_EXECUTABLE", 0, 0, FamilyNotExecutableError.__name__
            )
            raise FamilyNotExecutableError(family.value, reason)
        try:
            prepared = self._build(family, policy, endpoint_id, path_params, query, headers)
        except SourceRequestError as error:
            self._emit(family.value, endpoint_id, "INVALID_REQUEST", 0, 0, type(error).__name__)
            raise
        return prepared

    def _build(
        self,
        family: P4SourceFamily,
        policy: SourceFamilyPolicy,
        endpoint_id: str,
        path_params: Mapping[str, str] | None,
        query: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
    ) -> PreparedRequest:
        endpoint = next(
            (spec for spec in policy.endpoints if spec.endpoint_id == endpoint_id), None
        )
        if endpoint is None:
            raise InvalidEndpointError()
        placeholders = set(_PLACEHOLDER.findall(endpoint.path_template))
        raw_params = path_params or {}
        if set(raw_params) != placeholders:
            raise InvalidParameterError("path parameters do not match the template")
        resolved = endpoint.path_template
        for name, value in raw_params.items():
            text = _clean_text(value, f"path parameter {name}", _MAX_PARAM_BYTES)
            if "%" in text or "\\" in text or ".." in text:
                raise InvalidParameterError(f"path parameter {name} contains path syntax")
            if "/" in text and not name.endswith("_path"):
                raise InvalidParameterError(f"path parameter {name} must not span segments")
            if "//" in text:
                raise InvalidParameterError(
                    f"path parameter {name} must not contain empty segments"
                )
            resolved = resolved.replace("{" + name + "}", quote(text, safe=""))
        if re.fullmatch(endpoint.path_pattern, resolved) is None:
            raise InvalidParameterError("resolved path violates the endpoint pattern")

        raw_query = query or {}
        unknown = set(raw_query) - set(policy.query_allowlist)
        if unknown:
            raise InvalidParameterError("query contains a name outside the allowlist")
        missing = set(policy.required_query) - set(raw_query)
        if missing:
            raise InvalidParameterError("query is missing a required parameter")
        encoded_pairs: list[tuple[str, str]] = []
        for name in sorted(raw_query):
            text = _clean_text(raw_query[name], f"query parameter {name}", _MAX_PARAM_BYTES)
            encoded_pairs.append((name, quote(text, safe="-.~_")))
        encoded_query = "&".join(f"{name}={value}" for name, value in encoded_pairs)

        raw_headers = headers or {}
        unknown_headers = set(raw_headers) - set(policy.header_allowlist)
        if unknown_headers:
            raise InvalidParameterError("header contains a name outside the allowlist")
        header_pairs: list[tuple[str, str]] = []
        for name in policy.header_allowlist:
            if name not in raw_headers:
                continue
            header_pairs.append(
                (name, _clean_text(raw_headers[name], f"header {name}", _MAX_HEADER_VALUE_BYTES))
            )

        url = f"https://{policy.host}{resolved}"
        if encoded_query:
            url = f"{url}?{encoded_query}"
        self._validate_final_shape(url, policy)
        if len(url.encode("utf-8", "strict")) > policy.max_request_bytes:
            raise InvalidParameterError("request exceeds the family request byte budget")
        identity = sha256(
            "\0".join(
                (
                    "GET",
                    family.value,
                    endpoint_id,
                    resolved,
                    encoded_query,
                    str(policy.timeout_seconds),
                    str(policy.max_response_bytes),
                )
            ).encode("utf-8")
        ).hexdigest()
        return PreparedRequest(
            family=family,
            endpoint_id=endpoint_id,
            url=url,
            headers=tuple(header_pairs),
            timeout_seconds=policy.timeout_seconds,
            maximum_bytes=policy.max_response_bytes,
            maximum_decompressed_bytes=policy.max_decompressed_bytes,
            request_identity=identity,
        )

    def _validate_final_shape(self, url: str, policy: SourceFamilyPolicy) -> None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (TypeError, ValueError) as error:
            raise InvalidParameterError("constructed URL is malformed") from error
        if (
            parsed.scheme != "https"
            or parsed.netloc != policy.host
            or parsed.hostname != policy.host
            or port is not None
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise InvalidParameterError("constructed URL violates the family policy")
        if "%" in parsed.path or "//" in parsed.path.lstrip("/") or "/../" in parsed.path:
            raise InvalidParameterError("constructed path contains encoded or traversal syntax")

    @staticmethod
    def _request_identity(
        family: P4SourceFamily,
        endpoint_id: str,
        resolved_path: str,
        encoded_query: str,
        policy: SourceFamilyPolicy,
    ) -> str:
        return sha256(
            "\0".join(
                (
                    "GET",
                    family.value,
                    endpoint_id,
                    resolved_path,
                    encoded_query,
                    str(policy.timeout_seconds),
                    str(policy.max_response_bytes),
                )
            ).encode("utf-8")
        ).hexdigest()

    def _validate_prepared(self, prepared: PreparedRequest) -> SourceFamilyPolicy:
        """Revalidate the complete immutable request before crossing the executor seam."""
        try:
            if self._registry.registry_hash != P4_CANONICAL_REGISTRY_HASH:
                raise SourceMalformedResponseError()
            if (
                type(prepared.family) is not P4SourceFamily
                or type(prepared.endpoint_id) is not str
                or _ENDPOINT_ID.fullmatch(prepared.endpoint_id) is None
                or type(prepared.url) is not str
                or type(prepared.headers) is not tuple
                or type(prepared.request_identity) is not str
                or _HASH_TEXT.fullmatch(prepared.request_identity) is None
                or type(prepared.timeout_seconds) is not int
                or type(prepared.maximum_bytes) is not int
                or type(prepared.maximum_decompressed_bytes) is not int
            ):
                raise SourceMalformedResponseError()
            policy = self._registry.policy(prepared.family)
            if not policy.executable:
                raise SourceMalformedResponseError()
            endpoint = next(
                (spec for spec in policy.endpoints if spec.endpoint_id == prepared.endpoint_id),
                None,
            )
            if endpoint is None:
                raise SourceMalformedResponseError()
            self._validate_final_shape(prepared.url, policy)
            parsed = urlsplit(prepared.url)
            if re.fullmatch(endpoint.path_pattern, parsed.path) is None:
                raise SourceMalformedResponseError()
            if len(prepared.url.encode("utf-8", "strict")) > policy.max_request_bytes:
                raise SourceMalformedResponseError()
            if (
                prepared.timeout_seconds != policy.timeout_seconds
                or prepared.maximum_bytes != policy.max_response_bytes
                or prepared.maximum_decompressed_bytes != policy.max_decompressed_bytes
            ):
                raise SourceMalformedResponseError()

            query_names: list[str] = []
            if parsed.query:
                for pair in parsed.query.split("&"):
                    if pair.count("=") != 1:
                        raise SourceMalformedResponseError()
                    name, encoded_value = pair.split("=", 1)
                    if name not in policy.query_allowlist or name in query_names:
                        raise SourceMalformedResponseError()
                    if not encoded_value:
                        raise SourceMalformedResponseError()
                    decoded_value = unquote(encoded_value)
                    _clean_text(decoded_value, f"query parameter {name}", _MAX_PARAM_BYTES)
                    if quote(decoded_value, safe="-.~_") != encoded_value:
                        raise SourceMalformedResponseError()
                    query_names.append(name)
            if query_names != sorted(query_names) or not set(policy.required_query).issubset(
                query_names
            ):
                raise SourceMalformedResponseError()

            header_names: list[str] = []
            for header_pair in prepared.headers:
                if type(header_pair) is not tuple or len(header_pair) != 2:
                    raise SourceMalformedResponseError()
                name, value = header_pair
                if (
                    type(name) is not str
                    or type(value) is not str
                    or name not in policy.header_allowlist
                    or name in header_names
                ):
                    raise SourceMalformedResponseError()
                _clean_text(value, f"header {name}", _MAX_HEADER_VALUE_BYTES)
                header_names.append(name)
            expected_header_names = [
                name for name in policy.header_allowlist if name in header_names
            ]
            if header_names != expected_header_names:
                raise SourceMalformedResponseError()

            canonical_url = f"https://{policy.host}{parsed.path}"
            if parsed.query:
                canonical_url = f"{canonical_url}?{parsed.query}"
            if prepared.url != canonical_url:
                raise SourceMalformedResponseError()
            if prepared.request_identity != self._request_identity(
                prepared.family, prepared.endpoint_id, parsed.path, parsed.query, policy
            ):
                raise SourceMalformedResponseError()
            return policy
        except SourceMalformedResponseError:
            raise
        except SourceRequestError:
            raise SourceMalformedResponseError() from None
        except (KeyError, TypeError, UnicodeError, ValueError):
            raise SourceMalformedResponseError() from None

    def _reserve_rate(self, family: P4SourceFamily, now: float) -> bool:
        policy = self._registry.policy(family)
        with self._rate_lock:
            history = [
                stamp for stamp in self._rate_history[family] if now - stamp < policy.window_seconds
            ]
            burst_count = sum(1 for stamp in history if now - stamp < 1.0)
            if len(history) >= policy.requests_per_window or burst_count >= policy.burst_limit:
                self._rate_history[family] = history
                return False
            history.append(now)
            self._rate_history[family] = history
            return True

    def fetch(self, prepared: PreparedRequest) -> FetchResult:
        """Execute exactly one GET and map the outcome to a bounded result type."""
        if type(prepared) is not PreparedRequest:
            raise SourceMalformedResponseError()
        policy = self._validate_prepared(prepared)
        started = time.perf_counter_ns()
        if not self._reserve_rate(prepared.family, time.monotonic()):
            latency_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
            self._emit_latency(prepared, "RATE_LIMITED", latency_ms, SourceRateLimitError.__name__)
            raise SourceRateLimitError()
        try:
            response = self._executor.get(
                ExecutorRequest(
                    url=prepared.url,
                    timeout_seconds=prepared.timeout_seconds,
                    maximum_bytes=prepared.maximum_bytes,
                    request_id=prepared.request_identity,
                    headers=dict(prepared.headers),
                )
            )
        except TimeoutError:
            self._emit_latency(prepared, "TIMEOUT", 0, SourceFetchTimeoutError.__name__)
            raise SourceFetchTimeoutError() from None
        except SourceRequestError:
            raise
        except Exception:
            self._emit_latency(prepared, "MALFORMED", 0, SourceMalformedResponseError.__name__)
            raise SourceMalformedResponseError() from None
        latency_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
        if (
            type(response) is not ExecutorResponse
            or type(response.status) is not int
            or type(response.content_type) is not str
            or not response.content_type
            or type(response.body) is not bytes
            or type(response.final_url) is not str
        ):
            self._emit_latency(
                prepared, "MALFORMED", latency_ms, SourceMalformedResponseError.__name__
            )
            raise SourceMalformedResponseError()
        status_class = self._status_class(response.status)
        if status_class == "REDIRECT":
            self._emit_latency(prepared, "REDIRECT", latency_ms, SourceFetchRedirectError.__name__)
            raise SourceFetchRedirectError()
        if status_class != "OK":
            error = SourceStatusError(status_class)
            self._emit_latency(prepared, status_class, latency_ms, type(error).__name__)
            raise error
        if response.final_url != prepared.url:
            self._emit_latency(prepared, "REDIRECT", latency_ms, SourceFetchRedirectError.__name__)
            raise SourceFetchRedirectError()
        media_type = response.content_type.split(";", 1)[0].strip().lower()
        if media_type not in policy.allowed_media_types:
            self._emit_latency(
                prepared, "CONTENT_TYPE", latency_ms, SourceContentTypeError.__name__
            )
            raise SourceContentTypeError()
        if type(response.body) is not bytes or not response.body:
            self._emit_latency(
                prepared, "MALFORMED", latency_ms, SourceMalformedResponseError.__name__
            )
            raise SourceMalformedResponseError()
        if len(response.body) > prepared.maximum_decompressed_bytes:
            self._emit_latency(prepared, "BUDGET", latency_ms, SourceTransportBudgetError.__name__)
            raise SourceTransportBudgetError()
        content_hash = sha256(response.body).hexdigest()
        audit = self._emit_latency(
            prepared,
            "OK",
            latency_ms,
            None,
            byte_count=len(response.body),
            content_hash=content_hash,
        )
        return FetchResult(
            body=response.body,
            content_type=media_type,
            content_hash=content_hash,
            request_identity=prepared.request_identity,
            audit=audit,
        )

    @staticmethod
    def _status_class(status: int) -> str:
        if status == 200:
            return "OK"
        if 300 <= status < 400:
            return "REDIRECT"
        if status == 429:
            return "RATE_LIMITED"
        if 400 <= status < 500:
            return "CLIENT_ERROR"
        if status >= 500:
            return "SERVER_ERROR"
        return "MALFORMED"

    def _emit(
        self,
        family: str,
        endpoint_id: str,
        status_class: str,
        latency_ms: int,
        byte_count: int,
        error_code: str | None,
        content_hash: str = "0" * 64,
    ) -> None:
        if self._sink is None:
            return
        self._sink.record(
            SourceFetchAudit(
                family=family,
                endpoint_id=endpoint_id,
                status_class=status_class,
                latency_ms=latency_ms,
                byte_count=byte_count,
                content_hash=content_hash,
                error_code=error_code,
                occurred_at=UtcTimestamp.now(),
            )
        )

    def _emit_latency(
        self,
        prepared: PreparedRequest,
        status_class: str,
        latency_ms: int,
        error_code: str | None,
        byte_count: int = 0,
        content_hash: str = "0" * 64,
    ) -> SourceFetchAudit:
        event = SourceFetchAudit(
            family=prepared.family.value,
            endpoint_id=prepared.endpoint_id,
            status_class=status_class,
            latency_ms=latency_ms,
            byte_count=byte_count,
            content_hash=content_hash,
            error_code=error_code,
            occurred_at=UtcTimestamp.now(),
        )
        if self._sink is not None:
            self._sink.record(event)
        return event
