from __future__ import annotations

import hashlib
import json

import pytest

from seven_lens.analysis.model_audit import ModelCallErrorCode, ModelCallOutcome
from seven_lens.analysis.model_envelope import CanonicalEnvelopeSection
from seven_lens.analysis.prompt_builder import OutputContract
from seven_lens.application.model_invoker import ModelInvocationError
from seven_lens.application.ports.model_transport import (
    JsonModelResponse,
    ModelTransportErrorCode,
)
from seven_lens.security.sanitized_text import validate_sanitized_text
from test_p3e_envelope_and_prompt import _envelope
from test_p3e_model_invoker import FakeAuditPort, FakeTransport, _invoker, _valid_report

PROHIBITED_TEXT = (
    "Bearer DEMO-NON-SECRET",
    "contains api_key material",
    "api key is DEMO",
    "account id ACC-DEMO",
    "broker order id ORD-DEMO",
    "password DEMO",
    "token DEMO",
    "authorization DEMO",
    "file:/etc/passwd",
    "javascript:alert(1)",
    "tel:+123456789",
    "192.168.1.10/private",
    "[2001:db8::1]/private",
    "../private/key",
)


@pytest.mark.parametrize("text", PROHIBITED_TEXT)
def test_shared_sanitizer_and_canonical_envelope_reject_capability_text(text: str) -> None:
    with pytest.raises(ValueError, match="prohibited"):
        validate_sanitized_text(text, "test text", maximum=8_192)
    with pytest.raises(ValueError, match="prohibited"):
        CanonicalEnvelopeSection.from_value({"note": text})


@pytest.mark.parametrize(
    "key",
    (
        "name",
        "fullName",
        "firstName",
        "lastName",
        "customerId",
        "clientId",
        "portfolioOwner",
        "email",
        "phone",
        "address",
    ),
)
def test_identity_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="prohibited"):
        CanonicalEnvelopeSection.from_value({key: "DEMO-NON-SECRET"})


def test_ordinary_financial_prose_and_nonidentity_names_remain_valid() -> None:
    section = CanonicalEnvelopeSection.from_value(
        {
            "publisher": "A bounded issuer disclosure",
            "single_name_room": "Exposure remains below the configured limit",
            "summary": "Revenue accelerated while leverage declined",
        }
    )
    assert section.to_dict()["publisher"] == "A bounded issuer disclosure"


@pytest.mark.parametrize("text", PROHIBITED_TEXT)
def test_sensitive_model_output_closes_schema_failure_without_authority(text: str) -> None:
    wire = _valid_report().to_wire()
    wire["summary"] = text
    content = json.dumps(wire, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    response = JsonModelResponse(
        provider_response_id="response.1",
        model_id="agnes-2.5-flash",
        content=content,
        response_hash=hashlib.sha256(content.encode()).hexdigest(),
        prompt_tokens=120,
        completion_tokens=40,
        total_tokens=160,
    )
    audit = FakeAuditPort()
    transport = FakeTransport(response)

    with pytest.raises(ModelInvocationError) as error:
        _invoker(audit, transport).invoke(_envelope(), OutputContract.ANALYST_REPORT)

    assert error.value.code is ModelTransportErrorCode.SCHEMA
    attempt = next(iter(audit.attempts.values()))
    assert attempt.record.outcome is ModelCallOutcome.FAILURE
    assert attempt.record.error_code is ModelCallErrorCode.SCHEMA
    assert attempt.result is None
    assert len(transport.requests) == 1
