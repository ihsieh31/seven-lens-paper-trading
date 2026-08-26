"""Offline P2-E transport safety tests; these never require PostgreSQL or credentials."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

import pytest

from p2e_readonly_offline_support import (
    _FAKE_CREDENTIAL_HEADERS,
    _FAKE_KEY_ID,
    _FAKE_SECRET_KEY,
    _account_responder,
    _FaultHandler,
    _recording_http_server,
    _transport_for,
)
from seven_lens.application.ports.broker import BrokerTransportError

pytest_plugins = ("p2e_readonly_offline_fixtures",)


def test_transport_rejects_post_and_bodies(fault_server: str) -> None:
    transport = _transport_for(fault_server)
    with pytest.raises(ValueError, match="GET"):
        transport.request("POST", f"{fault_server}/v2/orders", {}, {"symbol": "AAPL"})
    with pytest.raises(ValueError, match="body"):
        transport.request("GET", f"{fault_server}/v2/account", {}, {"symbol": "AAPL"})
    assert transport.request_log == ()


def test_transport_rejects_url_outside_allowlist(fault_server: str) -> None:
    transport = _transport_for(fault_server)
    with pytest.raises(BrokerTransportError, match="allowlist"):
        transport.request("GET", "https://api.alpaca.markets/v2/account", {}, None)
    with pytest.raises(BrokerTransportError, match="API path"):
        transport.request("GET", f"{fault_server}/other/path", {}, None)
    assert transport.request_log == ()


def test_transport_fails_closed_on_rate_limit_and_server_error(
    fault_server: str,
) -> None:
    rate_limited = _transport_for(
        fault_server,
        timeout_seconds=2.0,
        retry_429_attempts=1,
        max_retry_wait_seconds=0.25,
    )
    _FaultHandler.mode = "rate_limited"
    response = rate_limited.request("GET", f"{fault_server}/v2/account", {}, None)
    assert response.status == 429
    assert type(response.body) is dict

    server_error = _transport_for(
        fault_server,
        timeout_seconds=2.0,
        retry_429_attempts=1,
        max_retry_wait_seconds=0.25,
    )
    _FaultHandler.mode = "server_error"
    response = server_error.request("GET", f"{fault_server}/v2/account", {}, None)
    assert response.status == 503
    assert type(response.body) is dict


def test_transport_retries_rate_limit_once_then_recovers(fault_server: str) -> None:
    transport = _transport_for(
        fault_server,
        timeout_seconds=2.0,
        retry_429_attempts=3,
        max_retry_wait_seconds=0.25,
    )
    attempts: list[int] = []

    def recording_do_get(self: _FaultHandler) -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(b'{"message":"rate limit"}')
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b'{"account_number":"TEST1","cash":"1.00","equity":"1.00","buying_power":"1.00"}'
        )

    original = _FaultHandler.do_GET
    try:
        _FaultHandler.do_GET = recording_do_get  # type: ignore[method-assign]
        response = transport.request("GET", f"{fault_server}/v2/account", {}, None)
    finally:
        _FaultHandler.do_GET = original  # type: ignore[method-assign]
    assert response.status == 200
    assert len(attempts) == 2
    assert [method for method, _ in transport.request_log] == ["GET"]


def test_transport_fails_closed_on_malformed_bodies(fault_server: str) -> None:
    transport = _transport_for(
        fault_server, timeout_seconds=2.0, retry_429_attempts=1, max_retry_wait_seconds=0.25
    )
    _FaultHandler.mode = "invalid_json"
    with pytest.raises(BrokerTransportError, match="unparsable"):
        transport.request("GET", f"{fault_server}/v2/account", {}, None)
    _FaultHandler.mode = "empty_body"
    with pytest.raises(BrokerTransportError, match="empty"):
        transport.request("GET", f"{fault_server}/v2/account", {}, None)


def test_transport_fails_closed_on_timeout(fault_server: str) -> None:
    transport = _transport_for(
        fault_server,
        timeout_seconds=0.2,
        retry_429_attempts=1,
        max_retry_wait_seconds=0.25,
    )
    _FaultHandler.mode = "silent"
    with pytest.raises(BrokerTransportError, match="timed out"):
        transport.request("GET", f"{fault_server}/v2/account", {}, None)


def test_transport_fails_closed_on_silent_drop(fault_server: str) -> None:
    transport = _transport_for(
        fault_server,
        timeout_seconds=2.0,
        retry_429_attempts=1,
        max_retry_wait_seconds=0.25,
    )
    _FaultHandler.mode = "silent"
    with pytest.raises(BrokerTransportError):
        transport.request("GET", f"{fault_server}/v2/account", {}, None)


@pytest.mark.parametrize("redirect_status", [301, 302, 303, 307, 308])
def test_transport_never_follows_a_redirect_or_forwards_credentials(
    redirect_status: int,
) -> None:
    source_seen: list[str] = []
    target_seen: list[str] = []
    location_holder: list[str] = []

    def source_redirects(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(redirect_status)
        handler.send_header("Location", location_holder[0])
        handler.end_headers()

    with (
        _recording_http_server(target_seen, _account_responder) as target_url,
        _recording_http_server(source_seen, source_redirects) as source_url,
    ):
        location = f"{target_url}/v2/account"
        location_holder.append(location)
        transport = _transport_for(
            source_url,
            timeout_seconds=2.0,
            retry_429_attempts=1,
            max_retry_wait_seconds=0.25,
        )
        with pytest.raises(BrokerTransportError) as refused:
            transport.request("GET", f"{source_url}/v2/account", _FAKE_CREDENTIAL_HEADERS, None)

    message = str(refused.value)
    assert _FAKE_KEY_ID not in message
    assert _FAKE_SECRET_KEY not in message
    assert location not in message
    assert source_seen == ["/v2/account"]
    assert target_seen == []
    assert transport.request_log == (("GET", "/v2/account"),)
    assert all(method == "GET" for method, _ in transport.request_log)


def test_transport_still_completes_a_normal_allowlisted_get() -> None:
    seen: list[str] = []
    with _recording_http_server(seen, _account_responder) as url:
        transport = _transport_for(url, timeout_seconds=2.0, max_retry_wait_seconds=0.25)
        response = transport.request("GET", f"{url}/v2/account", _FAKE_CREDENTIAL_HEADERS, None)

    assert response.status == 200
    assert type(response.body) is dict
    assert seen == ["/v2/account"]
    assert transport.request_log == (("GET", "/v2/account"),)
