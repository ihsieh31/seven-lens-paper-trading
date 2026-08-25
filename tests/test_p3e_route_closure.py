from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from seven_lens.analysis.contracts import AnalysisWindow
from seven_lens.analysis.model_envelope import EnvelopeVersions
from seven_lens.analysis.prompt_builder import OutputContract
from seven_lens.application.model_invoker import ModelInvocationError
from seven_lens.application.ports.model_transport import ModelTransportErrorCode
from seven_lens.infrastructure.agnes_providers import (
    AgnesAnalysisProvider,
    AgnesProposalProvider,
)
from test_analysis_contracts import rid, timestamp
from test_p3d_proposal_contracts import bundle, parent_input
from test_p3d_research_and_proposal_pipeline import make_proposal_pipeline
from test_p3e_agnes_providers import (
    CapturedRequest,
    CaptureProposalRequest,
    DynamicAnalysisInvoker,
    DynamicProposalInvoker,
    _analyst_request,
)
from test_p3e_model_invoker import FakeAuditPort, FakeTransport, _invoker, _response


def _forge_route(request: object, **changes: str) -> None:
    envelope = request.envelope  # type: ignore[attr-defined]
    versions = replace(envelope.versions, **changes)
    object.__setattr__(envelope, "versions", versions)
    material = json.dumps(
        envelope._material_wire(),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    object.__setattr__(
        envelope,
        "envelope_hash",
        hashlib.sha256(material.encode("utf-8")).hexdigest(),
    )
    envelope.validate_integrity()


@pytest.mark.parametrize(
    "changes",
    (
        {"prompt": "foreign.1"},
        {"model": "agnes-2.0-flash"},
        {"provider": "foreign.1"},
    ),
)
def test_agnes_provider_edge_rejects_forged_route_before_invoker(
    changes: dict[str, str],
) -> None:
    request = _analyst_request()
    _forge_route(request, **changes)
    invoker = DynamicAnalysisInvoker()

    with pytest.raises(ModelInvocationError) as error:
        AgnesAnalysisProvider(invoker).execute(request)  # type: ignore[arg-type]

    assert error.value.code is ModelTransportErrorCode.SCHEMA
    assert invoker.calls == []


@pytest.mark.parametrize(
    "versions",
    (
        EnvelopeVersions(
            "tradingagents.1", "foreign.1", "agnes-2.5-flash", "agnes.1", "p3b.1", "none.1"
        ),
        EnvelopeVersions(
            "tradingagents.1", "p3e.1", "agnes-2.0-flash", "agnes.1", "p3b.1", "none.1"
        ),
        EnvelopeVersions(
            "tradingagents.1", "p3e.1", "agnes-2.5-flash", "foreign.1", "p3b.1", "none.1"
        ),
    ),
)
def test_audited_invoker_rejects_route_before_audit_or_network(
    versions: EnvelopeVersions,
) -> None:
    request = _analyst_request()
    _forge_route(
        request,
        prompt=versions.prompt,
        model=versions.model,
        provider=versions.provider,
    )
    audit = FakeAuditPort()
    transport = FakeTransport(_response())

    with pytest.raises(ModelInvocationError) as error:
        _invoker(audit, transport).invoke(request.envelope, OutputContract.ANALYST_REPORT)

    assert error.value.code is ModelTransportErrorCode.SCHEMA
    assert audit.events == []
    assert transport.requests == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("window", AnalysisWindow.SECONDARY),
        ("created_at", timestamp(1)),
        ("attempt", 2),
        ("superseded_proposal_id", rid(99)),
        ("output_id", rid(98)),
    ),
)
def test_proposal_request_drift_is_rejected_before_invoker(field: str, value: object) -> None:
    capture = CaptureProposalRequest()
    pipeline, _ = make_proposal_pipeline(capture)  # type: ignore[arg-type]
    with pytest.raises(CapturedRequest):
        pipeline.run(bundle(), parent_input())
    request = capture.request
    assert request is not None
    object.__setattr__(request, field, value)
    invoker = DynamicProposalInvoker()

    with pytest.raises(ModelInvocationError) as error:
        AgnesProposalProvider(invoker).execute(request)  # type: ignore[arg-type]

    assert error.value.code is ModelTransportErrorCode.SCHEMA
    assert invoker.calls == []
