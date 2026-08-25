from __future__ import annotations

from seven_lens.analysis.contracts import AnalystRole
from seven_lens.analysis.model_envelope import CanonicalEnvelopeSection
from seven_lens.analysis.model_material import evidence_packet_model_material
from seven_lens.analysis.ports import ProviderRequest, ProviderStage
from seven_lens.analysis.proposal_ports import ProposalOutput, ProposalRequest
from test_analysis_contracts import analysis_input
from test_p3d_proposal_contracts import bundle, parent_input
from test_p3d_research_and_proposal_pipeline import (
    ProposalFakeProvider,
    make_proposal_pipeline,
)
from test_p3e_envelope_and_prompt import _envelope, _packet_with_excerpt


def test_envelope_section_and_analysis_request_repr_never_render_raw_material() -> None:
    marker = "PRIVATE-MARKER-9Z"
    packet = _packet_with_excerpt(marker)
    analysis = analysis_input()
    envelope = _envelope(
        packet_hash=packet.packet_hash,
        source_material=(analysis, packet, "AAPL"),
        untrusted_data=evidence_packet_model_material(packet),
    )
    request = ProviderRequest(
        ProviderStage.ANALYST,
        envelope.run_id,
        envelope.input_id,
        packet.packet_hash,
        envelope.snapshot_hash,
        "AAPL",
        envelope.deadline,
        envelope.citation_ids,
        envelope,
        AnalystRole.TECHNICAL,
        None,
    )

    assert marker not in repr(envelope.untrusted_data)
    assert marker not in repr(envelope)
    assert marker not in repr(request)
    assert "[REDACTED]" in repr(envelope)
    assert repr(request) == "ProviderRequest(<redacted>)"


def test_standalone_section_and_proposal_request_repr_are_redacted() -> None:
    marker = "PRIVATE-PROPOSAL-MARKER-8Q"
    section = CanonicalEnvelopeSection.from_value({"summary": marker})
    delegate = ProposalFakeProvider()

    class CaptureProvider:
        def __init__(self) -> None:
            self.request: ProposalRequest | None = None

        def execute(self, request: ProposalRequest) -> ProposalOutput:
            self.request = request
            return delegate.execute(request)

    capture = CaptureProvider()
    pipeline, _ = make_proposal_pipeline(capture)
    pipeline.run(bundle(), parent_input())

    assert marker not in repr(section)
    assert capture.request is not None
    assert repr(capture.request) == "ProposalRequest(<redacted>)"
