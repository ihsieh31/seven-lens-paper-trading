# mypy: ignore-errors
"""P4-A policy-bound GET-only transport: attack-surface and bounded-failure tests."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from seven_lens.config.errors import ConfigurationError
from seven_lens.security.secret_values import SecretValue
from seven_lens.sources.adapters.transport import (
    EXECUTOR_WIRE_CONTRACT,
    ExecutorResponse,
    FamilyNotExecutableError,
    InvalidEndpointError,
    InvalidParameterError,
    PolicyGetTransport,
    PreparedRequest,
    SourceAuditSink,
    SourceContentTypeError,
    SourceFetchAudit,
    SourceFetchRedirectError,
    SourceFetchTimeoutError,
    SourceMalformedResponseError,
    SourceRateLimitError,
    SourceStatusError,
    SourceTransportBudgetError,
)
from seven_lens.sources.roles import P4SourceFamily, p4_manifest_registry

_SECRET_TEXT = "fake-alpaca-secret-value-0001"


class RecordingExecutor:
    """Fake GET executor that records requests and returns a scripted response."""

    def __init__(
        self, response: ExecutorResponse | None = None, *, error: Exception | None = None
    ) -> None:
        self.calls: list[Any] = []
        self.response = response
        self.error = error

    def get(self, request: Any) -> ExecutorResponse:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        if self.response.final_url is None:
            return ExecutorResponse(
                status=self.response.status,
                content_type=self.response.content_type,
                body=self.response.body,
                final_url=request.url,
            )
        return self.response


class RecordingSink(SourceAuditSink):
    def __init__(self) -> None:
        self.events: list[SourceFetchAudit] = []

    def record(self, event: SourceFetchAudit) -> None:
        self.events.append(event)


def _ok_response(body: bytes = b'{"ok": true}', final_url: str | None = None) -> ExecutorResponse:
    return ExecutorResponse(
        status=200, content_type="application/json", body=body, final_url=final_url
    )


def _transport(
    response: ExecutorResponse | None = None,
    *,
    error: Exception | None = None,
    sink: RecordingSink | None = None,
) -> tuple[PolicyGetTransport, RecordingExecutor]:
    executor = RecordingExecutor(response if response is not None else _ok_response(), error=error)
    return PolicyGetTransport(
        p4_manifest_registry(), executor, audit_sink=sink or RecordingSink()
    ), executor


def test_prepare_builds_exact_https_url_from_policy_and_allowlist() -> None:
    transport, _ = _transport()

    prepared = transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")

    assert prepared.url == "https://paper-api.alpaca.markets/v2/assets"
    assert prepared.timeout_seconds == 15
    assert prepared.maximum_bytes == 1_000_000
    assert len(prepared.request_identity) == 64


def test_prepare_resolves_path_params_and_query_from_allowlist() -> None:
    transport, _ = _transport()

    prepared = transport.prepare(
        family=P4SourceFamily.ALPACA_HISTORICAL_BARS,
        endpoint_id="stock_bars",
        path_params={"symbol": "AAPL"},
        query={"feed": "sip", "limit": "100"},
    )

    assert prepared.url.startswith("https://data.alpaca.markets/v2/stocks/AAPL/bars?")
    assert "feed=sip" in prepared.url
    assert "limit=100" in prepared.url


def test_headers_are_restricted_to_manifest_allowlist_names() -> None:
    transport, executor = _transport()
    secret = SecretValue.from_bytes(_SECRET_TEXT.encode())

    prepared = transport.prepare(
        family=P4SourceFamily.ALPACA_ASSETS,
        endpoint_id="assets_list",
        headers={"APCA-API-KEY-ID": "fake-key-id", "APCA-API-SECRET-KEY": secret.reveal_text()},
    )
    transport.fetch(prepared)

    assert executor.calls[0].headers == {
        "APCA-API-KEY-ID": "fake-key-id",
        "APCA-API-SECRET-KEY": _SECRET_TEXT,
    }


def test_header_name_outside_allowlist_is_rejected_before_any_request() -> None:
    transport, executor = _transport()

    with pytest.raises(InvalidParameterError):
        transport.prepare(
            family=P4SourceFamily.ALPACA_ASSETS,
            endpoint_id="assets_list",
            headers={"X-Custom-Backdoor": "value"},
        )
    assert executor.calls == []


def test_header_value_injection_is_rejected() -> None:
    transport, executor = _transport()

    with pytest.raises(InvalidParameterError):
        transport.prepare(
            family=P4SourceFamily.ALPACA_ASSETS,
            endpoint_id="assets_list",
            headers={"APCA-API-KEY-ID": "value\r\nX-Evil: injected"},
        )
    assert executor.calls == []


def test_non_executable_family_never_reaches_the_executor() -> None:
    _transport_obj, executor = _transport()
    sink = RecordingSink()
    transport_audit = PolicyGetTransport(p4_manifest_registry(), executor, audit_sink=sink)

    with pytest.raises(FamilyNotExecutableError):
        transport_audit.prepare(family=P4SourceFamily.TAVILY, endpoint_id="tavily_search")
    assert executor.calls == []
    assert [event.status_class for event in sink.events] == ["NOT_EXECUTABLE"]


@pytest.mark.parametrize(
    ("family", "endpoint_id"),
    [
        (P4SourceFamily.ISSUER_IR, "issuer_press"),
        (P4SourceFamily.EXCHANGE_OFFICIAL, "exchange_notice"),
    ],
)
def test_registered_host_families_are_not_executable_and_never_reach_the_executor(
    family: P4SourceFamily, endpoint_id: str
) -> None:
    _transport_obj, executor = _transport()
    sink = RecordingSink()
    transport_audit = PolicyGetTransport(p4_manifest_registry(), executor, audit_sink=sink)

    with pytest.raises(FamilyNotExecutableError):
        transport_audit.prepare(family=family, endpoint_id=endpoint_id)
    assert executor.calls == []
    assert [event.status_class for event in sink.events] == ["NOT_EXECUTABLE"]


def test_unknown_endpoint_id_is_rejected() -> None:
    transport, executor = _transport()

    with pytest.raises(InvalidEndpointError):
        transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="mystery")
    assert executor.calls == []


def test_path_param_violating_pattern_is_rejected() -> None:
    transport, executor = _transport()

    with pytest.raises(InvalidParameterError):
        transport.prepare(
            family=P4SourceFamily.SEC_EDGAR,
            endpoint_id="submissions",
            path_params={"cik": "12"},
        )
    assert executor.calls == []


def test_required_query_missing_is_rejected() -> None:
    transport, executor = _transport()

    with pytest.raises(InvalidParameterError):
        transport.prepare(
            family=P4SourceFamily.GDELT,
            endpoint_id="gdelt_doc",
            query={"query": "alpaca", "mode": "artlist"},
        )
    prepared = transport.prepare(
        family=P4SourceFamily.GDELT,
        endpoint_id="gdelt_doc",
        query={"query": "alpaca", "mode": "artlist", "format": "json"},
    )
    assert "query=alpaca" in prepared.url
    assert executor.calls == []


def test_query_name_outside_allowlist_is_rejected() -> None:
    transport, executor = _transport()

    with pytest.raises(InvalidParameterError):
        transport.prepare(
            family=P4SourceFamily.GDELT,
            endpoint_id="gdelt_doc",
            query={"query": "x", "mode": "artlist", "format": "json", "raw": "1"},
        )
    assert executor.calls == []


def test_query_value_with_control_characters_is_rejected() -> None:
    transport, executor = _transport()

    with pytest.raises(InvalidParameterError):
        transport.prepare(
            family=P4SourceFamily.GDELT,
            endpoint_id="gdelt_doc",
            query={"query": "x\ny", "mode": "artlist", "format": "json"},
        )
    assert executor.calls == []


def test_redirect_response_is_refused_and_never_followed() -> None:
    redirect = ExecutorResponse(
        status=200,
        content_type="application/json",
        body=b"{}",
        final_url="https://evil.example.com/v2/assets",
    )
    transport, executor = _transport(redirect)

    prepared = transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
    with pytest.raises(SourceFetchRedirectError):
        transport.fetch(prepared)
    assert len(executor.calls) == 1


def test_3xx_status_is_refused_without_following() -> None:
    moved = ExecutorResponse(status=301, content_type="text/html", body=b"", final_url=None)
    transport, executor = _transport(moved)

    prepared = transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
    with pytest.raises(SourceFetchRedirectError):
        transport.fetch(prepared)
    assert len(executor.calls) == 1


@pytest.mark.parametrize(
    ("status", "status_class"),
    [
        (404, "CLIENT_ERROR"),
        (408, "CLIENT_ERROR"),
        (429, "RATE_LIMITED"),
        (500, "SERVER_ERROR"),
        (503, "SERVER_ERROR"),
    ],
)
def test_error_statuses_map_to_bounded_typed_failures(status: int, status_class: str) -> None:
    response = ExecutorResponse(
        status=status, content_type="application/json", body=b"{}", final_url=None
    )
    transport, executor = _transport(response)

    prepared = transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
    with pytest.raises(SourceStatusError) as error:
        transport.fetch(prepared)
    assert error.value.status_class == status_class
    assert len(executor.calls) == 1


def test_timeout_is_a_bounded_typed_failure_with_single_call() -> None:
    transport, executor = _transport(error=TimeoutError("connect timed out"))

    prepared = transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
    with pytest.raises(SourceFetchTimeoutError):
        transport.fetch(prepared)
    assert len(executor.calls) == 1


def test_wrong_mime_type_is_refused() -> None:
    html = ExecutorResponse(
        status=200, content_type="text/html; charset=utf-8", body=b"<html></html>", final_url=None
    )
    transport, executor = _transport(html)

    prepared = transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
    with pytest.raises(SourceContentTypeError):
        transport.fetch(prepared)
    assert len(executor.calls) == 1


def test_oversized_body_exceeding_decompressed_budget_is_refused() -> None:
    registry = p4_manifest_registry()
    iex_policy = registry.policy(P4SourceFamily.ALPACA_IEX_QUOTES)
    big_body = b"x" * (iex_policy.max_decompressed_bytes + 1)
    response = ExecutorResponse(
        status=200,
        content_type="application/json",
        body=big_body,
        final_url="https://data.alpaca.markets/v2/stocks/AAPL/quotes/latest",
    )
    transport, executor = _transport(response)

    prepared = transport.prepare(
        family=P4SourceFamily.ALPACA_IEX_QUOTES,
        endpoint_id="latest_quote",
        path_params={"symbol": "AAPL"},
    )
    with pytest.raises(SourceTransportBudgetError):
        transport.fetch(prepared)
    assert len(executor.calls) == 1


def test_empty_body_is_malformed() -> None:
    empty = ExecutorResponse(status=200, content_type="application/json", body=b"", final_url=None)
    transport, _ = _transport(empty)

    prepared = transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
    with pytest.raises(SourceMalformedResponseError):
        transport.fetch(prepared)


def test_malformed_executor_response_object_is_refused() -> None:
    transport, executor = _transport()
    executor.response = "not a response"

    prepared = transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
    with pytest.raises(SourceMalformedResponseError):
        transport.fetch(prepared)


def test_success_returns_body_with_content_hash_and_single_call() -> None:
    _transport_obj, executor = _transport(_ok_response(b'{"assets": []}'))
    sink = RecordingSink()
    audited = PolicyGetTransport(p4_manifest_registry(), executor, audit_sink=sink)

    prepared = audited.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
    result = audited.fetch(prepared)

    assert result.body == b'{"assets": []}'
    assert result.content_type == "application/json"
    assert len(result.content_hash) == 64
    assert len(executor.calls) == 1
    assert [event.status_class for event in sink.events] == ["OK"]


def test_audit_records_never_contain_urls_or_secret_material() -> None:
    _transport_obj, executor = _transport()
    secret = SecretValue.from_bytes(_SECRET_TEXT.encode())
    sink = RecordingSink()
    audited = PolicyGetTransport(p4_manifest_registry(), executor, audit_sink=sink)

    prepared = audited.prepare(
        family=P4SourceFamily.ALPACA_ASSETS,
        endpoint_id="assets_list",
        headers={"APCA-API-KEY-ID": "fake-key", "APCA-API-SECRET-KEY": secret.reveal_text()},
    )
    audited.fetch(prepared)

    flattened = "\n".join(str(field) for event in sink.events for field in event.wire())
    assert "https://" not in flattened
    assert _SECRET_TEXT not in flattened
    assert "APCA-API-SECRET-KEY=" not in flattened
    assert all(
        "\\" not in event.endpoint_id and "%" not in event.endpoint_id for event in sink.events
    )
    assert [event.status_class for event in sink.events] == ["OK"]


def test_prepared_request_repr_leaks_no_url_query_or_headers() -> None:
    transport, _ = _transport()
    secret = SecretValue.from_bytes(_SECRET_TEXT.encode())

    prepared = transport.prepare(
        family=P4SourceFamily.ALPACA_HISTORICAL_BARS,
        endpoint_id="stock_bars",
        path_params={"symbol": "AAPL"},
        query={"feed": "sip"},
        headers={"APCA-API-KEY-ID": "fake-key", "APCA-API-SECRET-KEY": secret.reveal_text()},
    )
    rendered = repr(prepared)

    assert "https://" not in rendered
    assert "feed=sip" not in rendered
    assert _SECRET_TEXT not in rendered
    assert "fake-key" not in rendered


def test_transport_exposes_no_arbitrary_url_entrypoint() -> None:
    public_methods = {
        name
        for name, value in vars(PolicyGetTransport).items()
        if not name.startswith("_") and callable(value)
    }

    assert "fetch" in public_methods and "prepare" in public_methods
    for name, value in vars(PolicyGetTransport).items():
        if not name.startswith("_") and callable(value):
            parameter_names = getattr(value, "__annotations__", {}).keys()
            assert "url" not in {parameter.lower() for parameter in parameter_names}, name


def test_executor_wire_contract_documented() -> None:
    assert "compressed" in EXECUTOR_WIRE_CONTRACT.lower()
    assert "retry" in EXECUTOR_WIRE_CONTRACT.lower()


def test_registry_hash_stable_and_policies_unchanged_between_transports() -> None:
    first, _ = _transport()
    second, _ = _transport()

    assert first.registry_hash == second.registry_hash


def test_transport_rejects_non_registry_and_non_executor_construction() -> None:
    with pytest.raises(ConfigurationError):
        PolicyGetTransport("not-a-registry", RecordingExecutor())  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        PolicyGetTransport(p4_manifest_registry(), "not-an-executor")  # type: ignore[arg-type]


def test_prepared_request_type_is_enforced_on_fetch() -> None:
    transport, executor = _transport()

    with pytest.raises(SourceMalformedResponseError):
        transport.fetch("not-a-prepared-request")  # type: ignore[arg-type]
    assert executor.calls == []


def test_forged_prepared_request_is_revalidated_before_executor() -> None:
    transport, executor = _transport()
    prepared = transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
    forged = replace(prepared, url="https://evil.example.com/v2/assets")

    with pytest.raises(SourceMalformedResponseError):
        transport.fetch(forged)
    assert executor.calls == []


def test_executor_response_fields_are_type_checked_before_access() -> None:
    transport, executor = _transport()
    executor.response = ExecutorResponse(
        status=200,
        content_type=None,
        body=b"{}",
        final_url=None,  # type: ignore[arg-type]
    )
    prepared = transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")

    with pytest.raises(SourceMalformedResponseError):
        transport.fetch(prepared)
    assert len(executor.calls) == 1


def test_manifest_rate_budget_is_enforced_before_the_sixth_sec_request() -> None:
    transport, executor = _transport()
    prepared = transport.prepare(
        family=P4SourceFamily.SEC_EDGAR,
        endpoint_id="submissions",
        path_params={"cik": "0000320193"},
    )

    for _ in range(5):
        transport.fetch(prepared)
    with pytest.raises(SourceRateLimitError):
        transport.fetch(prepared)
    assert len(executor.calls) == 5


def test_fetch_result_repr_leaks_no_body() -> None:
    transport, _ = _transport(_ok_response(b'{"secret-ish": "payload"}'))

    result = transport.fetch(
        transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
    )

    assert "payload" not in repr(result)
    assert PreparedRequest is not None
