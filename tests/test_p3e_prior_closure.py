from __future__ import annotations

import hashlib
import json

import pytest

from seven_lens.analysis.pipeline import AnalysisPipeline
from seven_lens.analysis.ports import (
    ProviderOutput,
    ProviderRequest,
    ScriptedAnalysisProvider,
)
from seven_lens.analysis.proposal_ports import ProposalOutput, ProposalRequest
from seven_lens.application.ports.analysis import InMemoryAnalysisStateRepository
from test_analysis_contracts import analysis_input, timestamp
from test_p3bc_analysis_pipeline import scripted_outputs
from test_p3bc_evidence_and_infrastructure import evidence_packet
from test_p3d_proposal_contracts import bundle, parent_input
from test_p3d_research_and_proposal_pipeline import (
    ProposalFakeProvider,
    make_proposal_pipeline,
)


def _rehash(envelope: object) -> None:
    material = json.dumps(
        envelope._material_wire(),  # type: ignore[attr-defined]
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


def test_p3c_round_two_rejects_reordered_round_one_prior_arguments() -> None:
    delegate = ScriptedAnalysisProvider(scripted_outputs())
    requests: dict[str, ProviderRequest] = {}

    class RecordingProvider:
        def execute(self, request: ProviderRequest) -> ProviderOutput:
            requests[request.key] = request
            return delegate.execute(request)

    AnalysisPipeline(
        RecordingProvider(),
        InMemoryAnalysisStateRepository(),
        now=lambda: timestamp().value,
    ).run(analysis_input(), evidence_packet(), "MSFT")
    envelope = requests["BULL::2"].envelope
    forged = (*envelope.prior_outputs[:4], envelope.prior_outputs[5], envelope.prior_outputs[4])
    object.__setattr__(envelope, "prior_outputs", forged)
    _rehash(envelope)

    with pytest.raises(ValueError, match="prior output"):
        envelope.validate_integrity()


def test_p3d_round_two_rejects_reordered_round_one_viewpoints() -> None:
    delegate = ProposalFakeProvider()
    requests: dict[str, ProposalRequest] = {}

    class RecordingProvider:
        calls = delegate.calls

        def execute(self, request: ProposalRequest) -> ProposalOutput:
            requests[request.key] = request
            return delegate.execute(request)

    pipeline, _ = make_proposal_pipeline(RecordingProvider())
    pipeline.run(bundle(), parent_input())
    envelope = requests["AGGRESSIVE:2"].envelope
    forged = (envelope.prior_outputs[1], envelope.prior_outputs[0], envelope.prior_outputs[2])
    object.__setattr__(envelope, "prior_outputs", forged)
    _rehash(envelope)

    with pytest.raises(ValueError, match="prior output"):
        envelope.validate_integrity()


def test_portfolio_manager_rejects_non_debate_prior_material() -> None:
    delegate = ProposalFakeProvider()
    requests: dict[str, ProposalRequest] = {}

    class RecordingProvider:
        calls = delegate.calls

        def execute(self, request: ProposalRequest) -> ProposalOutput:
            requests[request.key] = request
            return delegate.execute(request)

    pipeline, _ = make_proposal_pipeline(RecordingProvider())
    pipeline.run(bundle(), parent_input())
    manager = requests["PORTFOLIO_MANAGER:"].envelope
    risk_round_two = requests["AGGRESSIVE:2"].envelope
    object.__setattr__(manager, "prior_outputs", (risk_round_two.prior_outputs[0],))
    _rehash(manager)

    with pytest.raises(ValueError, match="prior output"):
        manager.validate_integrity()
