"""Exact Agnes HTTPS adapter with strict JSON and no implicit retry or redirect."""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import multiprocessing
import re
import socket
import ssl
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, cast

from seven_lens.application.ports.model_transport import (
    JsonModelRequest,
    JsonModelResponse,
    ModelTransportError,
    ModelTransportErrorCode,
)
from seven_lens.config.provider import AgnesProviderConfig
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.security.secret_values import SecretValue

_EXACT_SCHEME: Final = "https"
_EXACT_HOST: Final = "apihub.agnes-ai.com"
_EXACT_PATH: Final = "/v1/chat/completions"
_EXACT_MODEL: Final = "agnes-2.5-flash"
_EXACT_ENDPOINT: Final = f"{_EXACT_SCHEME}://{_EXACT_HOST}{_EXACT_PATH}"
_RESPONSE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_METADATA_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DNS_IPC_VERSION: Final = "seven-lens-agnes-dns-v1"
_DNS_CLEANUP_SECONDS: Final = 0.2
_MAX_DNS_ADDRESSES: Final = 16


class HttpExecutorErrorCode(StrEnum):
    DNS = "DNS"
    CONNECT = "CONNECT"
    READ_TIMEOUT = "READ_TIMEOUT"
    TOTAL_TIMEOUT = "TOTAL_TIMEOUT"
    TLS = "TLS"
    NETWORK = "NETWORK"
    OVERSIZE = "OVERSIZE"


_HTTP_ERROR_MESSAGES = {
    HttpExecutorErrorCode.DNS: "provider DNS lookup failed",
    HttpExecutorErrorCode.CONNECT: "provider connection failed",
    HttpExecutorErrorCode.READ_TIMEOUT: "provider response timed out",
    HttpExecutorErrorCode.TOTAL_TIMEOUT: "provider total timeout expired",
    HttpExecutorErrorCode.TLS: "provider TLS connection failed",
    HttpExecutorErrorCode.NETWORK: "provider network request failed",
    HttpExecutorErrorCode.OVERSIZE: "provider response exceeded the byte cap",
}


class HttpExecutorError(RuntimeError):
    __slots__ = ("code",)

    def __init__(self, code: HttpExecutorErrorCode) -> None:
        if type(code) is not HttpExecutorErrorCode:
            raise ValueError("HTTP executor error code is invalid")
        self.code = code
        super().__init__(_HTTP_ERROR_MESSAGES[code])

    def __repr__(self) -> str:
        return f"HttpExecutorError(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class RawHttpRequest:
    method: str
    scheme: str
    host: str
    path: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    connect_timeout_seconds: float
    read_timeout_seconds: float
    total_timeout_seconds: float
    maximum_response_bytes: int

    def __post_init__(self) -> None:
        if (
            self.method != "POST"
            or self.scheme != _EXACT_SCHEME
            or self.host != _EXACT_HOST
            or self.path != _EXACT_PATH
        ):
            raise ValueError("raw provider request route is invalid")
        if (
            type(self.headers) is not tuple
            or len(self.headers) != 3
            or any(
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not str
                or not item[0]
                or "\r" in item[0]
                or "\n" in item[0]
                or "\r" in item[1]
                or "\n" in item[1]
                for item in self.headers
            )
        ):
            raise ValueError("raw provider request headers are invalid")
        names = tuple(name.lower() for name, _ in self.headers)
        if names != ("authorization", "content-type", "accept"):
            raise ValueError("raw provider request header policy is invalid")
        if type(self.body) is not bytes or not self.body:
            raise ValueError("raw provider request body is invalid")
        for timeout in (
            self.connect_timeout_seconds,
            self.read_timeout_seconds,
            self.total_timeout_seconds,
        ):
            if type(timeout) is not float or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("raw provider request timeout is invalid")
        if (
            type(self.maximum_response_bytes) is not int
            or not 1 <= self.maximum_response_bytes <= 16_777_216
        ):
            raise ValueError("raw provider response cap is invalid")

    def __repr__(self) -> str:
        return (
            "RawHttpRequest(method='POST', route='agnes-policy', headers=[REDACTED], "
            f"body_bytes={len(self.body)}, maximum_response_bytes={self.maximum_response_bytes})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RawHttpResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    final_url: str

    def __post_init__(self) -> None:
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise ValueError("raw provider response status is invalid")
        if type(self.headers) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not str
            for item in self.headers
        ):
            raise ValueError("raw provider response headers are invalid")
        if type(self.body) is not bytes:
            raise ValueError("raw provider response body is invalid")
        if type(self.final_url) is not str:
            raise ValueError("raw provider response final route is invalid")

    def __repr__(self) -> str:
        return (
            f"RawHttpResponse(status={self.status}, headers=[REDACTED], "
            f"body_bytes={len(self.body)}, final_route='agnes-policy')"
        )


class AgnesHttpExecutor(Protocol):
    def execute(self, request: RawHttpRequest) -> RawHttpResponse: ...


@dataclass(frozen=True, slots=True)
class ResolvedAddress:
    """One numeric TCP address produced before any credential-bearing request."""

    family: int
    socket_type: int
    protocol: int
    sockaddr: tuple[object, ...]

    def __post_init__(self) -> None:
        if self.family not in {socket.AF_INET, socket.AF_INET6}:
            raise ValueError("resolved address family is invalid")
        if self.socket_type != socket.SOCK_STREAM or self.protocol != socket.IPPROTO_TCP:
            raise ValueError("resolved address transport is invalid")
        expected_length = 2 if self.family == socket.AF_INET else 4
        if type(self.sockaddr) is not tuple or len(self.sockaddr) != expected_length:
            raise ValueError("resolved socket address is invalid")
        host, port = self.sockaddr[0], self.sockaddr[1]
        if type(host) is not str or type(port) is not int or port != 443:
            raise ValueError("resolved socket address is invalid")
        try:
            socket.inet_pton(self.family, host)
        except OSError:
            raise ValueError("resolved address must be numeric") from None
        if self.family == socket.AF_INET6 and any(
            type(value) is not int or value < 0 for value in self.sockaddr[2:]
        ):
            raise ValueError("resolved IPv6 scope is invalid")


class DnsResolver(Protocol):
    def resolve(self, host: str, timeout_seconds: float) -> tuple[ResolvedAddress, ...]: ...


class _DnsConnection(Protocol):
    def poll(self, timeout: float = 0.0) -> bool: ...

    def recv(self) -> object: ...

    def send(self, value: object) -> None: ...

    def close(self) -> None: ...


class _DnsProcess(Protocol):
    exitcode: int | None

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def close(self) -> None: ...


class _DnsProcessContext(Protocol):
    def Pipe(self, duplex: bool = True) -> tuple[_DnsConnection, _DnsConnection]: ...

    def Process(
        self,
        *,
        target: Callable[..., None],
        args: tuple[object, ...],
        daemon: bool = False,
    ) -> _DnsProcess: ...


class SpawnedDnsResolver:
    """Resolve in a terminable child so a DNS stall cannot outlive its budget."""

    def __init__(self, context: _DnsProcessContext | None = None) -> None:
        self._context = context or cast(_DnsProcessContext, multiprocessing.get_context("spawn"))

    def resolve(self, host: str, timeout_seconds: float) -> tuple[ResolvedAddress, ...]:
        if (
            host != _EXACT_HOST
            or type(timeout_seconds) is not float
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise HttpExecutorError(HttpExecutorErrorCode.DNS)
        receive_connection, send_connection = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_dns_worker_entry,
            args=(send_connection, host),
            daemon=True,
        )
        started = False
        timed_out = False
        try:
            process.start()
            started = True
            send_connection.close()
            if not receive_connection.poll(timeout_seconds):
                timed_out = True
                raise HttpExecutorError(HttpExecutorErrorCode.DNS)
            return _parse_dns_message(receive_connection.recv())
        except HttpExecutorError:
            raise
        except Exception:
            raise HttpExecutorError(HttpExecutorErrorCode.DNS) from None
        finally:
            receive_connection.close()
            if not started:
                send_connection.close()
                with suppress(ValueError):
                    process.close()
            else:
                if not timed_out:
                    process.join(_DNS_CLEANUP_SECONDS)
                if process.is_alive():
                    process.terminate()
                    process.join(_DNS_CLEANUP_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join(_DNS_CLEANUP_SECONDS)
                process.close()


def _dns_worker_entry(connection: _DnsConnection, host: str) -> None:
    try:
        records = socket.getaddrinfo(
            host,
            443,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        normalized: list[tuple[int, int, int, tuple[object, ...]]] = []
        seen: set[tuple[int, int, int, tuple[object, ...]]] = set()
        for family, socket_type, protocol, _canonical_name, sockaddr in records:
            if family not in {socket.AF_INET, socket.AF_INET6} or type(sockaddr) is not tuple:
                continue
            item = family, socket_type, protocol, tuple(sockaddr)
            if item not in seen:
                seen.add(item)
                normalized.append(item)
            if len(normalized) == _MAX_DNS_ADDRESSES:
                break
        outcome = "success" if normalized else "failure"
        connection.send((_DNS_IPC_VERSION, outcome, tuple(normalized)))
    except BaseException:
        with suppress(BaseException):
            connection.send((_DNS_IPC_VERSION, "failure", ()))
    finally:
        connection.close()


def _parse_dns_message(message: object) -> tuple[ResolvedAddress, ...]:
    if (
        type(message) is not tuple
        or len(message) != 3
        or message[0] != _DNS_IPC_VERSION
        or message[1] != "success"
        or type(message[2]) is not tuple
        or not 1 <= len(message[2]) <= _MAX_DNS_ADDRESSES
    ):
        raise HttpExecutorError(HttpExecutorErrorCode.DNS)
    try:
        return tuple(ResolvedAddress(*item) for item in message[2])
    except (TypeError, ValueError):
        raise HttpExecutorError(HttpExecutorErrorCode.DNS) from None


class _NativeResponse(Protocol):
    status: int

    def getheaders(self) -> list[tuple[str, str]]: ...

    def read(self, amount: int) -> bytes: ...


class _NativeSocket(Protocol):
    def settimeout(self, timeout: float) -> None: ...


class _NativeConnection(Protocol):
    sock: _NativeSocket | None

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> None: ...

    def getresponse(self) -> _NativeResponse: ...

    def close(self) -> None: ...


class _ConnectionFactory(Protocol):
    def __call__(
        self,
        host: str,
        address: ResolvedAddress,
        timeout: float,
        context: ssl.SSLContext,
    ) -> _NativeConnection: ...


class _PreResolvedHTTPSConnection(http.client.HTTPSConnection):
    """Connect only to a numeric preflight result while verifying the exact host."""

    def __init__(
        self,
        host: str,
        address: ResolvedAddress,
        timeout: float,
        context: ssl.SSLContext,
        *,
        socket_factory: Callable[[int, int, int], socket.socket] = socket.socket,
    ) -> None:
        super().__init__(host=host, port=443, timeout=timeout, context=context)
        self._resolved_address = address
        self._socket_factory = socket_factory
        self._ssl_context = context

    def connect(self) -> None:
        address = self._resolved_address
        raw_socket = self._socket_factory(address.family, address.socket_type, address.protocol)
        try:
            raw_socket.settimeout(self.timeout)
            raw_socket.connect(cast(tuple[str, int], address.sockaddr))
            self.sock = self._ssl_context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


def _default_connection_factory(
    host: str,
    address: ResolvedAddress,
    timeout: float,
    context: ssl.SSLContext,
) -> _NativeConnection:
    return cast(
        _NativeConnection,
        _PreResolvedHTTPSConnection(host, address, timeout, context),
    )


class StdlibAgnesHttpExecutor:
    """One direct verified-TLS origin request using no proxy or environment routing."""

    def __init__(
        self,
        *,
        resolver: DnsResolver | None = None,
        connection_factory: _ConnectionFactory | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolver = resolver or SpawnedDnsResolver()
        self._connection_factory = connection_factory or _default_connection_factory
        self._monotonic = monotonic

    def execute(self, request: RawHttpRequest) -> RawHttpResponse:
        if type(request) is not RawHttpRequest:
            raise HttpExecutorError(HttpExecutorErrorCode.NETWORK)
        started = self._safe_monotonic()
        connection: _NativeConnection | None = None
        try:
            context = ssl.create_default_context()
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            with suppress(NotImplementedError):
                context.set_alpn_protocols(["http/1.1"])
            addresses = self._resolver.resolve(
                request.host,
                min(request.connect_timeout_seconds, request.total_timeout_seconds),
            )
            elapsed = self._safe_monotonic() - started
            remaining_total = request.total_timeout_seconds - elapsed
            remaining_connect = request.connect_timeout_seconds - elapsed
            if remaining_total <= 0:
                raise HttpExecutorError(HttpExecutorErrorCode.TOTAL_TIMEOUT)
            if remaining_connect <= 0:
                raise HttpExecutorError(HttpExecutorErrorCode.CONNECT)
            try:
                connection = self._connection_factory(
                    request.host,
                    addresses[0],
                    min(remaining_connect, remaining_total),
                    context,
                )
                connection.request(
                    request.method,
                    request.path,
                    request.body,
                    dict(request.headers),
                )
            except socket.gaierror:
                raise HttpExecutorError(HttpExecutorErrorCode.CONNECT) from None
            except (ssl.SSLError, ssl.CertificateError):
                raise HttpExecutorError(HttpExecutorErrorCode.TLS) from None
            except TimeoutError:
                raise HttpExecutorError(HttpExecutorErrorCode.CONNECT) from None
            except OSError:
                raise HttpExecutorError(HttpExecutorErrorCode.CONNECT) from None

            remaining = request.total_timeout_seconds - (self._safe_monotonic() - started)
            if remaining <= 0:
                raise HttpExecutorError(HttpExecutorErrorCode.TOTAL_TIMEOUT)
            if connection.sock is None:
                raise HttpExecutorError(HttpExecutorErrorCode.NETWORK)
            connection.sock.settimeout(min(request.read_timeout_seconds, remaining))
            try:
                native_response = connection.getresponse()
            except TimeoutError:
                raise HttpExecutorError(HttpExecutorErrorCode.READ_TIMEOUT) from None
            except OSError:
                raise HttpExecutorError(HttpExecutorErrorCode.NETWORK) from None
            remaining = request.total_timeout_seconds - (self._safe_monotonic() - started)
            if remaining <= 0:
                raise HttpExecutorError(HttpExecutorErrorCode.TOTAL_TIMEOUT)
            if connection.sock is None:
                raise HttpExecutorError(HttpExecutorErrorCode.NETWORK)
            connection.sock.settimeout(min(request.read_timeout_seconds, remaining))
            try:
                headers = tuple(native_response.getheaders())
                body = native_response.read(request.maximum_response_bytes + 1)
            except TimeoutError:
                raise HttpExecutorError(HttpExecutorErrorCode.READ_TIMEOUT) from None
            except OSError:
                raise HttpExecutorError(HttpExecutorErrorCode.NETWORK) from None
            if self._safe_monotonic() - started > request.total_timeout_seconds:
                raise HttpExecutorError(HttpExecutorErrorCode.TOTAL_TIMEOUT)
            if type(body) is not bytes:
                raise HttpExecutorError(HttpExecutorErrorCode.NETWORK)
            if len(body) > request.maximum_response_bytes:
                raise HttpExecutorError(HttpExecutorErrorCode.OVERSIZE)
            return RawHttpResponse(
                status=native_response.status,
                headers=headers,
                body=body,
                final_url=_EXACT_ENDPOINT,
            )
        except HttpExecutorError:
            raise
        except Exception:
            raise HttpExecutorError(HttpExecutorErrorCode.NETWORK) from None
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()

    def _safe_monotonic(self) -> float:
        try:
            value = self._monotonic()
        except Exception:
            raise HttpExecutorError(HttpExecutorErrorCode.NETWORK) from None
        if type(value) not in {int, float} or not math.isfinite(value):
            raise HttpExecutorError(HttpExecutorErrorCode.NETWORK)
        return float(value)


class _DuplicateJsonKey(ValueError):
    pass


def build_agnes_request_body(config: AgnesProviderConfig, request: JsonModelRequest) -> bytes:
    """Canonical credential-free POST body shared by transport and evidence planning."""

    if type(config) is not AgnesProviderConfig or type(request) is not JsonModelRequest:
        raise ModelTransportError(ModelTransportErrorCode.CONFIG)
    if request.max_output_tokens > config.max_output_tokens:
        raise ModelTransportError(ModelTransportErrorCode.CONFIG)
    wire = {
        "max_tokens": request.max_output_tokens,
        "messages": [
            {"content": message.content, "role": message.role.value} for message in request.messages
        ],
        "model": config.model_id,
        "stream": False,
        "temperature": config.temperature,
    }
    if request.response_format is not None:
        wire["response_format"] = dict(request.response_format)
    try:
        body = json.dumps(
            wire,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ModelTransportError(ModelTransportErrorCode.SCHEMA) from None
    if not body or len(body) > config.request_byte_cap:
        raise ModelTransportError(ModelTransportErrorCode.OVERSIZE)
    return body


class AgnesJsonModelTransport:
    """Single-attempt exact Agnes adapter; outputs are not authority until audited."""

    def __init__(
        self,
        *,
        config: AgnesProviderConfig,
        api_key: SecretValue,
        executor: AgnesHttpExecutor,
        clock: Callable[[], UtcTimestamp],
    ) -> None:
        if type(config) is not AgnesProviderConfig or config.endpoint != _EXACT_ENDPOINT:
            raise ModelTransportError(ModelTransportErrorCode.CONFIG)
        if (
            config.model_id != _EXACT_MODEL
            or config.fallback_model_id is not None
            or config.fallback_attempts != 0
            or any(
                (
                    config.stream,
                    config.tools,
                    config.state,
                    config.files,
                    config.follow_redirects,
                    config.trust_env,
                    config.proxy,
                    config.automatic_retry,
                )
            )
        ):
            raise ModelTransportError(ModelTransportErrorCode.CONFIG)
        if type(api_key) is not SecretValue or not hasattr(executor, "execute"):
            raise ModelTransportError(ModelTransportErrorCode.CONFIG)
        self._config = config
        self._api_key = api_key
        self._executor = executor
        self._clock = clock

    def execute(self, request: JsonModelRequest) -> JsonModelResponse:
        if type(request) is not JsonModelRequest:
            raise ModelTransportError(ModelTransportErrorCode.CONFIG)
        remaining = self._remaining_seconds(request.deadline)
        if remaining <= 0:
            raise ModelTransportError(ModelTransportErrorCode.DEADLINE)
        if request.max_output_tokens > self._config.max_output_tokens:
            raise ModelTransportError(ModelTransportErrorCode.CONFIG)
        body = build_agnes_request_body(self._config, request)
        raw_request = RawHttpRequest(
            method="POST",
            scheme=self._config.scheme,
            host=self._config.host,
            path=self._config.path,
            headers=(
                ("Authorization", f"Bearer {self._api_key.reveal_text()}"),
                ("Content-Type", "application/json"),
                ("Accept", "application/json"),
            ),
            body=body,
            connect_timeout_seconds=min(self._config.connect_timeout_ms / 1000, remaining),
            read_timeout_seconds=min(self._config.read_timeout_ms / 1000, remaining),
            total_timeout_seconds=min(self._config.total_timeout_ms / 1000, remaining),
            maximum_response_bytes=self._config.response_byte_cap,
        )
        try:
            response = self._executor.execute(raw_request)
        except HttpExecutorError as error:
            raise ModelTransportError(_map_executor_error(error.code)) from None
        except TimeoutError:
            raise ModelTransportError(ModelTransportErrorCode.TIMEOUT) from None
        except Exception:
            raise ModelTransportError(ModelTransportErrorCode.TRANSIENT) from None
        if self._remaining_seconds(request.deadline) <= 0:
            raise ModelTransportError(ModelTransportErrorCode.DEADLINE)
        if type(response) is not RawHttpResponse:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if response.final_url != _EXACT_ENDPOINT:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        self._check_status(response.status)
        _validate_json_content_type(response.headers)
        if not response.body:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if len(response.body) > self._config.response_byte_cap:
            raise ModelTransportError(ModelTransportErrorCode.OVERSIZE)
        payload = _strict_json_object(response.body, ModelTransportErrorCode.PROTOCOL)
        return self._parse_response(payload, response.body)

    def _remaining_seconds(self, deadline: UtcTimestamp) -> float:
        try:
            now = self._clock()
        except Exception:
            raise ModelTransportError(ModelTransportErrorCode.CONFIG) from None
        if type(now) is not UtcTimestamp:
            raise ModelTransportError(ModelTransportErrorCode.CONFIG)
        return (deadline.value - now.value).total_seconds()

    def _check_status(self, status: int) -> None:
        if status == 200:
            return
        if status in {301, 302, 303, 307, 308}:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if status in {401, 403}:
            raise ModelTransportError(ModelTransportErrorCode.AUTH)
        if status == 408:
            raise ModelTransportError(ModelTransportErrorCode.TIMEOUT)
        if status == 429:
            raise ModelTransportError(ModelTransportErrorCode.RATE_LIMIT)
        if 500 <= status <= 599:
            raise ModelTransportError(ModelTransportErrorCode.TRANSIENT)
        if 400 <= status <= 499:
            raise ModelTransportError(ModelTransportErrorCode.PERMANENT)
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)

    def _parse_response(self, payload: dict[str, object], raw_body: bytes) -> JsonModelResponse:
        required_outer = {"id", "object", "created", "model", "choices", "usage"}
        if not required_outer.issubset(payload) or not set(payload).issubset(
            required_outer | {"metadata"}
        ):
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if "metadata" in payload:
            _validate_agnes_metadata(payload["metadata"])
        response_id = payload["id"]
        if type(response_id) is not str or _RESPONSE_ID.fullmatch(response_id) is None:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if payload["object"] != "chat.completion":
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if type(payload["created"]) is not int or payload["created"] < 0:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if payload["model"] != _EXACT_MODEL:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        choices = payload["choices"]
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        choice = choices[0]
        required_choice = {"index", "message", "finish_reason"}
        if not required_choice.issubset(choice) or not set(choice).issubset(
            required_choice | {"provider_specific_fields"}
        ):
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if "provider_specific_fields" in choice:
            _validate_provider_specific_fields(choice["provider_specific_fields"])
        if type(choice["index"]) is not int or choice["index"] != 0:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if choice["finish_reason"] != "stop":
            raise ModelTransportError(ModelTransportErrorCode.SCHEMA)
        message = choice["message"]
        if type(message) is not dict:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        required_message = {"role", "content"}
        if not required_message.issubset(message) or not set(message).issubset(
            required_message | {"reasoning_content"}
        ):
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if "reasoning_content" in message:
            reasoning_content = message["reasoning_content"]
            if (
                type(reasoning_content) is not str
                or len(reasoning_content.encode("utf-8")) > self._config.response_byte_cap
            ):
                raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        if message["role"] != "assistant" or type(message["content"]) is not str:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        content = _normalize_model_content(
            message["content"],
            self._config.response_byte_cap,
        )
        usage = payload["usage"]
        if type(usage) is not dict:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        required_usage = {"prompt_tokens", "completion_tokens", "total_tokens"}
        optional_usage = {"prompt_tokens_details", "completion_tokens_details"}
        if not required_usage.issubset(usage) or not set(usage).issubset(
            required_usage | optional_usage
        ):
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        for details_name in optional_usage & set(usage):
            _validate_token_details(usage[details_name])
        prompt_tokens = usage["prompt_tokens"]
        completion_tokens = usage["completion_tokens"]
        total_tokens = usage["total_tokens"]
        if (
            any(
                type(value) is not int or value < 0 or value > 10_000_000
                for value in (prompt_tokens, completion_tokens, total_tokens)
            )
            or total_tokens != prompt_tokens + completion_tokens
        ):
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
        return JsonModelResponse(
            provider_response_id=response_id,
            model_id=_EXACT_MODEL,
            content=content,
            response_hash=hashlib.sha256(raw_body).hexdigest(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )


def _validate_agnes_metadata(value: object) -> None:
    if type(value) is not dict or set(value) != {"weight_version"}:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    weight_version = value["weight_version"]
    if type(weight_version) is not str or _RESPONSE_ID.fullmatch(weight_version) is None:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)


def _validate_provider_specific_fields(value: object) -> None:
    if type(value) is not dict or set(value) != {"matched_stop"}:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    matched_stop = value["matched_stop"]
    if type(matched_stop) is not int or not -1 <= matched_stop <= 1_000_000:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)


def _validate_token_details(value: object) -> None:
    if type(value) is not dict or len(value) > 16:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    for key, count in value.items():
        if (
            type(key) is not str
            or _METADATA_KEY.fullmatch(key) is None
            or type(count) is not int
            or not 0 <= count <= 10_000_000
        ):
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)


def _normalize_model_content(content: str, byte_cap: int) -> str:
    if not content or len(content.encode("utf-8")) > byte_cap:
        raise ModelTransportError(ModelTransportErrorCode.SCHEMA)
    if "```" not in content:
        _strict_json_object(content.encode("utf-8"), ModelTransportErrorCode.SCHEMA)
        return content
    stripped = content.strip()
    prefix = "```json\n"
    suffix = "\n```"
    if (
        stripped.count("```") != 2
        or not stripped.startswith(prefix)
        or not stripped.endswith(suffix)
    ):
        raise ModelTransportError(ModelTransportErrorCode.SCHEMA)
    normalized = stripped[len(prefix) : -len(suffix)]
    if not normalized.startswith("{") or not normalized.endswith("}"):
        raise ModelTransportError(ModelTransportErrorCode.SCHEMA)
    _strict_json_object(normalized.encode("utf-8"), ModelTransportErrorCode.SCHEMA)
    return normalized


def _validate_json_content_type(headers: tuple[tuple[str, str], ...]) -> None:
    values = [value for name, value in headers if name.lower() == "content-type"]
    if len(values) != 1:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    parts = [part.strip().lower() for part in values[0].split(";")]
    if not parts or parts[0] != "application/json":
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    if len(parts) > 2 or (len(parts) == 2 and parts[1] != "charset=utf-8"):
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)


def _strict_json_object(body: bytes, code: ModelTransportErrorCode) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise _DuplicateJsonKey
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise ValueError

    try:
        text = body.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise ModelTransportError(code) from None
    if type(value) is not dict:
        raise ModelTransportError(code)
    return value


def _map_executor_error(code: HttpExecutorErrorCode) -> ModelTransportErrorCode:
    if code in {HttpExecutorErrorCode.READ_TIMEOUT, HttpExecutorErrorCode.TOTAL_TIMEOUT}:
        return ModelTransportErrorCode.TIMEOUT
    if code is HttpExecutorErrorCode.OVERSIZE:
        return ModelTransportErrorCode.OVERSIZE
    return ModelTransportErrorCode.TRANSIENT
