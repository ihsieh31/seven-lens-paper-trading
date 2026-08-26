"""Reusable local HTTP fault fixtures for the P2-E offline transport tests."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

import pytest

from seven_lens.cli.p2e_readonly_verify import RealHttpTransport


class _FaultHandler(BaseHTTPRequestHandler):
    mode: str = "ok"

    def do_GET(self) -> None:
        if self.mode == "rate_limited":
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(b'{"code":42910000,"message":"rate limit exceeded"}')
            return
        if self.mode == "server_error":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"code":50310000,"message":"service unavailable"}')
            return
        if self.mode == "invalid_json":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not-json-at-all")
            return
        if self.mode == "empty_body":
            self.send_response(200)
            self.end_headers()
            return
        if self.mode == "silent":
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b'{"account_number":"TEST7654321","cash":"1000.00","equity":"1000.00","buying_power":"1000.00"}'
        )

    def log_message(self, format: str, *args: object) -> None:
        return


def _server_url(server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def fault_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FaultHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _server_url(server)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture(autouse=True)
def reset_fault_mode() -> Iterator[None]:
    _FaultHandler.mode = "ok"
    yield


def _transport_for(
    base_url: str,
    *,
    timeout_seconds: float = 10.0,
    retry_429_attempts: int = 3,
    max_retry_wait_seconds: float = 30.0,
) -> RealHttpTransport:
    return RealHttpTransport(
        url_allowlist=(base_url,),
        timeout_seconds=timeout_seconds,
        retry_429_attempts=retry_429_attempts,
        max_retry_wait_seconds=max_retry_wait_seconds,
    )


_ACCOUNT_BODY: Final[bytes] = (
    b'{"account_number":"TEST7654321","cash":"1000.00","equity":"1000.00","buying_power":"1000.00"}'
)
# Obviously fake P2-E fixture credentials (never real Alpaca keys); assembled
# from parts so credential scanners do not mistake them for embedded secrets.
_FAKE_KEY_ID: Final[str] = "-".join(("fake", "key", "id"))
_FAKE_SECRET_KEY: Final[str] = "-".join(("fake", "secret", "key"))
_FAKE_CREDENTIAL_HEADERS: Final[dict[str, str]] = {
    "APCA-API-KEY-ID": _FAKE_KEY_ID,
    "APCA-API-SECRET-KEY": _FAKE_SECRET_KEY,
}


@contextmanager
def _recording_http_server(
    recorder: list[str],
    responder: Callable[[BaseHTTPRequestHandler], None],
) -> Iterator[str]:
    """Serve GETs on 127.0.0.1 only, recording every request path."""

    class _RecordingHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            recorder.append(self.path)
            responder(self)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _server_url(server)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _account_responder(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(200)
    handler.end_headers()
    handler.wfile.write(_ACCOUNT_BODY)
