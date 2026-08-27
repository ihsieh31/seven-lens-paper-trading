"""Unit contracts for the P3-E authoritative model-call audit boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from seven_lens.analysis.contracts import AnalysisStatus
from seven_lens.analysis.model_audit import (
    CanonicalModelCallResult,
    ModelCallAuditRecord,
    ModelCallClaimDecision,
    ModelCallClaimResult,
    ModelCallErrorCode,
    ModelCallOutcome,
    ModelCallResultKind,
    ModelCallRole,
    ModelCallStage,
    ReasoningEffective,
    ReasoningRequested,
    StoredModelCallAttempt,
    derive_model_call_id,
)
from seven_lens.config.provider import ApiFlavor, ProviderKind
from seven_lens.domain.value_objects import RunId, UtcTimestamp
from test_analysis_contracts import report


def _rid(number: int) -> RunId:
    return RunId(UUID(int=number))


def audit_record() -> ModelCallAuditRecord:
    started = UtcTimestamp(datetime(2026, 8, 24, 1, 2, 3, tzinfo=UTC))
    return ModelCallAuditRecord(
        call_id=derive_model_call_id(
            _rid(2),
            _rid(4),
            ModelCallStage.ANALYST,
            ModelCallRole.TECHNICAL,
            0,
            1,
        ),
        run_id=_rid(1),
        input_id=_rid(2),
        context_id=_rid(4),
        stage=ModelCallStage.ANALYST,
        role=ModelCallRole.TECHNICAL,
        round_number=0,
        provider=ProviderKind.AGNES,
        model="agnes-2.5-flash",
        api_flavor=ApiFlavor.CHAT_COMPLETIONS,
        endpoint_policy_id="p3e-agnes-2.5-flash-only-v1",
        route_ordinal=1,
        prompt_template_hash="a" * 64,
        request_envelope_hash="b" * 64,
        response_hash="c" * 64,
        reasoning_requested=ReasoningRequested.MAX,
        reasoning_effective=ReasoningEffective.UNKNOWN,
        token_counts_trusted=True,
        input_tokens=120,
        output_tokens=40,
        latency_ms=250,
        started_at=started,
        completed_at=UtcTimestamp(started.value + timedelta(milliseconds=250)),
        outcome=ModelCallOutcome.SUCCESS,
        error_code=ModelCallErrorCode.NONE,
    )


def test_audit_record_is_exact_bounded_and_deterministically_identified() -> None:
    record = audit_record()
    assert record.call_id == derive_model_call_id(
        record.input_id,
        record.context_id,
        record.stage,
        record.role,
        record.round_number,
        record.route_ordinal,
    )
    assert record.to_metadata()["response_hash"] == "c" * 64
    assert record.to_claim().call_id == record.call_id


def test_model_call_identity_rejects_unsupported_second_route() -> None:
    with pytest.raises(ValueError, match="route ordinal"):
        derive_model_call_id(
            _rid(2),
            _rid(4),
            ModelCallStage.ANALYST,
            ModelCallRole.TECHNICAL,
            0,
            2,
        )


def test_claim_decision_requires_replay_authority_only_when_closed() -> None:
    assert ModelCallClaimResult(ModelCallClaimDecision.CLAIMED, None).attempt is None
    with pytest.raises(ValueError, match="replay authority"):
        ModelCallClaimResult(ModelCallClaimDecision.REPLAY, None)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("call_id", _rid(99), "call identity"),
        ("provider", "OPENAI", "provider"),
        ("model", "agnes-2.0-flash", "model"),
        ("api_flavor", "responses", "flavor"),
        ("endpoint_policy_id", "custom", "endpoint policy"),
        ("route_ordinal", True, "route ordinal"),
        ("latency_ms", True, "latency"),
        ("input_tokens", True, "token"),
        ("prompt_template_hash", "A" * 64, "hash"),
    ],
)
def test_audit_record_rejects_forged_or_unbounded_metadata(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(audit_record(), **{field: value})  # type: ignore[arg-type]


def test_audit_record_enforces_stage_role_round_closure() -> None:
    with pytest.raises(ValueError, match="stage role round"):
        replace(audit_record(), role=ModelCallRole.BULL)


def test_audit_record_enforces_success_failure_and_trusted_token_closure() -> None:
    with pytest.raises(ValueError, match="outcome"):
        replace(audit_record(), error_code=ModelCallErrorCode.TIMEOUT)
    with pytest.raises(ValueError, match="response hash"):
        replace(
            audit_record(),
            response_hash=None,
            outcome=ModelCallOutcome.SUCCESS,
            error_code=ModelCallErrorCode.NONE,
        )
    with pytest.raises(ValueError, match="trusted token"):
        replace(audit_record(), token_counts_trusted=False)


def test_audit_record_failure_may_omit_response_and_untrusted_counts() -> None:
    record = replace(
        audit_record(),
        response_hash=None,
        token_counts_trusted=False,
        input_tokens=None,
        output_tokens=None,
        outcome=ModelCallOutcome.FAILURE,
        error_code=ModelCallErrorCode.TIMEOUT,
    )
    assert record.response_hash is None


def test_audit_record_rejects_impossible_timestamps() -> None:
    with pytest.raises(ValueError, match="timestamps"):
        replace(audit_record(), completed_at=audit_record().started_at)
    with pytest.raises(ValueError, match="latency"):
        replace(audit_record(), latency_ms=248)


def canonical_result() -> CanonicalModelCallResult:
    record = audit_record()
    return CanonicalModelCallResult.from_contract(
        record.call_id,
        ModelCallResultKind.ANALYST_REPORT,
        report(AnalysisStatus.VALID),
    )


def test_successful_attempt_atomically_carries_only_canonical_parsed_result() -> None:
    stored = StoredModelCallAttempt(audit_record(), canonical_result())
    assert stored.result is not None
    assert "summary" in stored.result.payload.to_dict()


def test_attempt_rejects_missing_or_cross_call_result_authority() -> None:
    with pytest.raises(ValueError, match="requires canonical result"):
        StoredModelCallAttempt(audit_record(), None)
    with pytest.raises(ValueError, match="result identity"):
        StoredModelCallAttempt(
            audit_record(),
            replace(canonical_result(), call_id=_rid(88)),
        )
