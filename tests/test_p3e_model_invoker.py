from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from seven_lens.analysis.contracts import AnalysisStatus, AnalystReport, AnalystRole, ContractMeta
from seven_lens.analysis.model_audit import (
    ModelCallAuditRecord,
    ModelCallClaim,
    ModelCallClaimDecision,
    ModelCallClaimResult,
    ModelCallErrorCode,
    ModelCallOutcome,
    StoredModelCallAttempt,
)
from seven_lens.analysis.model_material import evidence_packet_model_material
from seven_lens.analysis.prompt_builder import OutputContract
from seven_lens.application.model_invoker import (
    AuditedModelInvoker,
    ModelInvocationError,
)
from seven_lens.application.ports.model_audit import ModelCallAuditError
from seven_lens.application.ports.model_transport import (
    JsonModelRequest,
    JsonModelResponse,
    ModelTransportError,
    ModelTransportErrorCode,
)
from seven_lens.config.provider import agnes_25_flash_config
from test_analysis_contracts import analysis_input, report, rid, timestamp
from test_p3e_envelope_and_prompt import _envelope, _packet_with_excerpt


class FakeAuditPort:
    def __init__(self) -> None:
        self.claims: dict[object, ModelCallClaim] = {}
        self.attempts: dict[object, StoredModelCallAttempt] = {}
        self.events: list[str] = []
        self.fail_load = False
        self.fail_claim = False
        self.fail_persist = False

    def load(self, call_id: object) -> StoredModelCallAttempt | None:
        self.events.append("load")
        if self.fail_load:
            raise ModelCallAuditError("fake audit unavailable")
        return self.attempts.get(call_id)

    def claim(self, claim: ModelCallClaim) -> ModelCallClaimResult:
        self.events.append("claim")
        if self.fail_claim:
            raise ModelCallAuditError("fake audit unavailable")
        existing = self.claims.get(claim.call_id)
        if existing is not None and existing != claim:
            raise ModelCallAuditError("fake claim collision")
        self.claims.setdefault(claim.call_id, claim)
        attempt = self.attempts.get(claim.call_id)
        if attempt is not None:
            return ModelCallClaimResult(ModelCallClaimDecision.REPLAY, attempt)
        if existing is not None:
            return ModelCallClaimResult(ModelCallClaimDecision.IN_PROGRESS, None)
        return ModelCallClaimResult(ModelCallClaimDecision.CLAIMED, None)

    def persist(self, record: ModelCallAuditRecord, result: object | None) -> bool:
        self.events.append("persist")
        if self.fail_persist:
            raise ModelCallAuditError("fake audit unavailable")
        attempt = StoredModelCallAttempt(record, result)  # type: ignore[arg-type]
        existing = self.attempts.get(record.call_id)
        if existing is not None:
            if existing != attempt:
                raise ModelCallAuditError("fake attempt collision")
            return False
        self.attempts[record.call_id] = attempt
        return True


class FakeTransport:
    def __init__(self, result: JsonModelResponse | ModelTransportError) -> None:
        self.result = result
        self.requests: list[JsonModelRequest] = []
        self.events: list[str] = []

    def execute(self, request: JsonModelRequest) -> JsonModelResponse:
        self.requests.append(request)
        self.events.append("network")
        if type(self.result) is ModelTransportError:
            raise self.result
        return cast(JsonModelResponse, self.result)


def _valid_report(**changes: object) -> AnalystReport:
    envelope = _envelope()
    base = report(AnalysisStatus.VALID)
    values: dict[str, object] = {
        "meta": ContractMeta(
            base.meta.schema_version,
            envelope.run_id,
            base.meta.created_at,
            envelope.producer_version,
        ),
        "report_id": envelope.output_id,
        "input_id": envelope.input_id,
        "role": AnalystRole.TECHNICAL,
        "symbol": envelope.symbol,
    }
    values.update(changes)
    return replace(base, **cast(Any, values))


def _response(value: AnalystReport | None = None) -> JsonModelResponse:
    content = json.dumps(
        (value or _valid_report()).to_wire(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return JsonModelResponse(
        provider_response_id="response.1",
        model_id="agnes-2.5-flash",
        content=content,
        response_hash=hashlib.sha256(b"provider-raw-response").hexdigest(),
        prompt_tokens=120,
        completion_tokens=40,
        total_tokens=160,
    )


def _clock(*times: datetime) -> Callable[[], datetime]:
    values = iter(times)
    return lambda: next(values)


def _invoker(
    audit: FakeAuditPort,
    transport: FakeTransport,
    *,
    clock: Callable[[], datetime] | None = None,
) -> AuditedModelInvoker:
    return AuditedModelInvoker(
        config=agnes_25_flash_config(),
        transport=transport,
        audit=audit,
        clock=clock
        or _clock(
            datetime(2026, 8, 21, 14, 31, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 14, 31, 0, 250_000, tzinfo=UTC),
        ),
    )


def test_success_is_claimed_once_audited_before_return_and_uses_fixed_three_messages() -> None:
    audit = FakeAuditPort()
    transport = FakeTransport(_response())
    output = _invoker(audit, transport).invoke(_envelope(), OutputContract.ANALYST_REPORT)

    assert output == _valid_report()
    assert audit.events == ["load", "claim", "persist"]
    assert transport.events == ["network"]
    request = transport.requests[0]
    assert tuple(message.role.value for message in request.messages) == (
        "system",
        "developer",
        "user",
    )
    attempt = next(iter(audit.attempts.values()))
    assert attempt.record.outcome is ModelCallOutcome.SUCCESS
    assert attempt.record.error_code is ModelCallErrorCode.NONE
    assert attempt.record.reasoning_effective.value == "UNKNOWN"
    assert attempt.record.token_counts_trusted is True
    metadata = repr(attempt.record.to_metadata())
    assert _response().content not in metadata
    assert "UNTRUSTED_DATA" not in metadata


def test_closed_success_replays_without_prompt_network_or_second_claim() -> None:
    audit = FakeAuditPort()
    first_transport = FakeTransport(_response())
    first = _invoker(audit, first_transport).invoke(_envelope(), OutputContract.ANALYST_REPORT)

    second_transport = FakeTransport(ModelTransportError(ModelTransportErrorCode.TRANSIENT))
    second = _invoker(
        audit,
        second_transport,
        clock=_clock(datetime(2026, 8, 21, 14, 31, 1, tzinfo=UTC)),
    ).invoke(_envelope(), OutputContract.ANALYST_REPORT)
    assert second == first
    assert second_transport.requests == []
    assert audit.events[-1] == "load"


@pytest.mark.parametrize("failure_point", ["load", "claim", "persist"])
def test_audit_failure_never_returns_output_or_retries_provider(failure_point: str) -> None:
    audit = FakeAuditPort()
    setattr(audit, f"fail_{failure_point}", True)
    transport = FakeTransport(_response())
    with pytest.raises(ModelInvocationError) as excinfo:
        _invoker(audit, transport).invoke(_envelope(), OutputContract.ANALYST_REPORT)
    assert excinfo.value.code is ModelTransportErrorCode.AUDIT
    assert len(transport.requests) == (1 if failure_point == "persist" else 0)
    assert "provider-raw-response" not in repr(excinfo.value)


def test_unclosed_claim_is_not_retried_and_different_envelope_collides() -> None:
    audit = FakeAuditPort()
    first = _invoker(audit, FakeTransport(_response()))
    # Create only the durable claim to simulate a crash with unknown provider outcome.
    claim = first.claim_for(_envelope(), OutputContract.ANALYST_REPORT)
    assert audit.claim(claim).decision is ModelCallClaimDecision.CLAIMED

    blocked_transport = FakeTransport(_response())
    with pytest.raises(ModelInvocationError) as excinfo:
        _invoker(
            audit,
            blocked_transport,
            clock=_clock(datetime(2026, 8, 21, 14, 31, 1, tzinfo=UTC)),
        ).invoke(_envelope(), OutputContract.ANALYST_REPORT)
    assert excinfo.value.code is ModelTransportErrorCode.AUDIT
    assert blocked_transport.requests == []

    with pytest.raises(ModelInvocationError) as collision:
        different_packet = _packet_with_excerpt("different verified excerpt")
        _invoker(
            audit,
            FakeTransport(_response()),
            clock=_clock(datetime(2026, 8, 21, 14, 31, 1, tzinfo=UTC)),
        ).invoke(
            _envelope(
                packet_hash=different_packet.packet_hash,
                source_material=(analysis_input(), different_packet, "AAPL"),
                untrusted_data=evidence_packet_model_material(different_packet),
            ),
            OutputContract.ANALYST_REPORT,
        )
    assert collision.value.code is ModelTransportErrorCode.AUDIT


@pytest.mark.parametrize(
    "content",
    [
        '{"meta":{},"meta":{}}',
        '{"confidence":NaN}',
        '{"unknown":true}',
        "```json\n{}\n```",
    ],
)
def test_inner_json_is_strict_and_malformed_output_closes_schema_failure(content: str) -> None:
    response = replace(_response(), content=content)
    audit = FakeAuditPort()
    transport = FakeTransport(response)
    with pytest.raises(ModelInvocationError) as excinfo:
        _invoker(audit, transport).invoke(_envelope(), OutputContract.ANALYST_REPORT)
    assert excinfo.value.code is ModelTransportErrorCode.SCHEMA
    attempt = next(iter(audit.attempts.values()))
    assert attempt.record.outcome is ModelCallOutcome.FAILURE
    assert attempt.record.error_code is ModelCallErrorCode.SCHEMA
    assert attempt.result is None
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"report_id": rid(99)},
        {"input_id": rid(99)},
        {"symbol": "MSFT"},
        {"role": AnalystRole.NEWS},
        {"evidence_refs": ("foreign.1",)},
        {"meta": ContractMeta(_valid_report().meta.schema_version, rid(99), timestamp(), "p3a.1")},
        {
            "meta": ContractMeta(
                _valid_report().meta.schema_version,
                rid(1),
                timestamp(),
                "foreign.1",
            )
        },
        {
            "meta": ContractMeta(
                _valid_report().meta.schema_version,
                rid(1),
                timestamp(1),
                "p3a.1",
            )
        },
    ],
)
def test_output_identity_version_and_citation_drift_has_zero_authority(
    changed: dict[str, object],
) -> None:
    audit = FakeAuditPort()
    transport = FakeTransport(_response(_valid_report(**changed)))
    with pytest.raises(ModelInvocationError) as excinfo:
        _invoker(audit, transport).invoke(_envelope(), OutputContract.ANALYST_REPORT)
    assert excinfo.value.code is ModelTransportErrorCode.SCHEMA
    assert next(iter(audit.attempts.values())).result is None


def test_late_valid_response_is_audited_as_deadline_and_never_returned() -> None:
    audit = FakeAuditPort()
    transport = FakeTransport(_response())
    with pytest.raises(ModelInvocationError) as excinfo:
        _invoker(
            audit,
            transport,
            clock=_clock(
                datetime(2026, 8, 21, 14, 31, tzinfo=UTC),
                _envelope().deadline.value + timedelta(microseconds=1),
            ),
        ).invoke(_envelope(), OutputContract.ANALYST_REPORT)
    assert excinfo.value.code is ModelTransportErrorCode.DEADLINE
    assert next(iter(audit.attempts.values())).record.error_code is ModelCallErrorCode.DEADLINE


def test_transport_error_is_closed_once_without_fallback() -> None:
    audit = FakeAuditPort()
    transport = FakeTransport(ModelTransportError(ModelTransportErrorCode.RATE_LIMIT))
    with pytest.raises(ModelInvocationError) as excinfo:
        _invoker(audit, transport).invoke(_envelope(), OutputContract.ANALYST_REPORT)
    assert excinfo.value.code is ModelTransportErrorCode.RATE_LIMIT
    attempt = next(iter(audit.attempts.values()))
    assert attempt.record.error_code is ModelCallErrorCode.RATE_LIMIT
    assert attempt.result is None
    assert len(transport.requests) == 1


def test_wrong_contract_model_or_expired_replay_is_rejected_without_network() -> None:
    with pytest.raises(ModelInvocationError) as wrong_contract:
        _invoker(FakeAuditPort(), FakeTransport(_response())).invoke(
            _envelope(), OutputContract.TRADER_PLAN
        )
    assert wrong_contract.value.code is ModelTransportErrorCode.SCHEMA

    wrong_model = FakeTransport(replace(_response(), model_id="agnes-2.0-flash"))
    with pytest.raises(ModelInvocationError) as model_error:
        _invoker(FakeAuditPort(), wrong_model).invoke(_envelope(), OutputContract.ANALYST_REPORT)
    assert model_error.value.code is ModelTransportErrorCode.PROTOCOL

    audit = FakeAuditPort()
    _invoker(audit, FakeTransport(_response())).invoke(_envelope(), OutputContract.ANALYST_REPORT)
    with pytest.raises(ModelInvocationError) as expired:
        _invoker(
            audit,
            FakeTransport(_response()),
            clock=_clock(_envelope().deadline.value + timedelta(microseconds=1)),
        ).invoke(_envelope(), OutputContract.ANALYST_REPORT)
    assert expired.value.code is ModelTransportErrorCode.DEADLINE


def test_invocation_error_is_fixed_and_does_not_expose_exception_or_prompt() -> None:
    error = ModelInvocationError(ModelTransportErrorCode.AUDIT)
    assert str(error) == "model call audit failed"
    assert repr(error) == "ModelInvocationError(code='AUDIT')"
    assert not hasattr(error, "body")
    assert not hasattr(error, "headers")
