# mypy: ignore-errors
"""Fake end-to-end tests for Agnes analysis/proposal adapters and composition."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from fakes.secrets import FakeSecretProvider
from seven_lens.analysis.contracts import (
    SCHEMA_VERSION,
    AnalysisStatus,
    AnalystReport,
    AnalystRole,
    ContractMeta,
    PortfolioRequest,
    PositionSide,
    ProposalAction,
    ProposalReasonCode,
    ResearchConclusion,
    ResearchRating,
    TraderPlan,
)
from seven_lens.analysis.model_envelope import (
    EnvelopeRole,
    EnvelopeStage,
    SanitizedProviderEnvelope,
    derive_provider_output_id,
)
from seven_lens.analysis.pipeline import AnalysisPipeline
from seven_lens.analysis.ports import DebateArgument, ProviderRequest, ProviderStage
from seven_lens.analysis.prompt_builder import OutputContract
from seven_lens.analysis.proposal_contracts import (
    PortfolioProposal,
    RiskArgument,
    RiskViewpoint,
)
from seven_lens.analysis.proposal_pipeline import ProposalProducerVersions
from seven_lens.analysis.proposal_ports import ProposalRequest
from seven_lens.application.model_invoker import ModelInvocationError
from seven_lens.application.p3e_composition import (
    AgnesProviderStack,
    build_agnes_provider_stack,
)
from seven_lens.application.ports.analysis import InMemoryAnalysisStateRepository
from seven_lens.application.ports.model_transport import ModelTransportErrorCode
from seven_lens.infrastructure.agnes_providers import (
    AgnesAnalysisProvider,
    AgnesProposalProvider,
)
from seven_lens.infrastructure.agnes_transport import RawHttpRequest, RawHttpResponse
from seven_lens.security.secret_values import SecretKind, SecretRef, SecretValue
from test_analysis_contracts import analysis_input, timestamp
from test_p3bc_evidence_and_infrastructure import evidence_packet
from test_p3d_research_and_proposal_pipeline import (
    fixture_bundle,
    fixture_item,
    fixture_parent,
    make_proposal_pipeline,
)
from test_p3e_envelope_and_prompt import _envelope
from test_p3e_model_invoker import FakeAuditPort


class DynamicAnalysisInvoker:
    def __init__(self) -> None:
        self.calls: list[tuple[SanitizedProviderEnvelope, OutputContract]] = []

    def invoke(self, envelope: SanitizedProviderEnvelope, contract: OutputContract) -> object:
        self.calls.append((envelope, contract))
        meta = ContractMeta(
            SCHEMA_VERSION,
            envelope.run_id,
            envelope.created_at,
            envelope.producer_version,
        )
        if contract is OutputContract.ANALYST_REPORT:
            return AnalystReport(
                meta,
                envelope.output_id,
                envelope.input_id,
                AnalystRole(envelope.role.value),
                envelope.symbol,
                AnalysisStatus.VALID,
                "summary",
                ("observation",),
                ("claim",),
                (envelope.citation_ids[0],),
                (),
                ("missing",),
                ("risk",),
                ("catalyst",),
                ("invalidator",),
                Decimal("0.8000"),
            )
        if contract is OutputContract.DEBATE_ARGUMENT:
            return DebateArgument(
                envelope.input_id,
                envelope.packet_hash,
                envelope.symbol,
                ProviderStage(envelope.role.value),
                envelope.round_number,
                f"bounded {envelope.role.value} round {envelope.round_number}",
                (envelope.citation_ids[0],),
            )
        if contract is OutputContract.RESEARCH_CONCLUSION:
            return ResearchConclusion(
                meta,
                envelope.output_id,
                envelope.input_id,
                envelope.symbol,
                ResearchRating.BUY,
                "conclusion",
                ("driver",),
                ("risk",),
                ("invalidator",),
                (envelope.citation_ids[0],),
                Decimal("0.8000"),
                AnalysisStatus.VALID,
            )
        return TraderPlan(
            meta,
            envelope.output_id,
            envelope.input_id,
            envelope.symbol,
            ResearchRating.BUY,
            (ProposalReasonCode.FUNDAMENTAL,),
            (envelope.citation_ids[0],),
            Decimal("100.00"),
            Decimal("110.00"),
            Decimal("90.00"),
            AnalysisStatus.VALID,
        )


class DynamicProposalInvoker:
    def __init__(self) -> None:
        self.calls: list[tuple[SanitizedProviderEnvelope, OutputContract]] = []

    def invoke(self, envelope: SanitizedProviderEnvelope, contract: OutputContract) -> object:
        self.calls.append((envelope, contract))
        if contract is OutputContract.RISK_ARGUMENT:
            return RiskArgument(
                meta=ContractMeta(
                    SCHEMA_VERSION,
                    envelope.run_id,
                    envelope.created_at,
                    envelope.producer_version,
                ),
                argument_id=envelope.output_id,
                context_id=envelope.context_id,
                bundle_id=envelope.bundle_id,
                bundle_hash=envelope.bundle_hash,
                viewpoint=RiskViewpoint(envelope.role.value),
                round_number=envelope.round_number,
                argument="bounded risk argument",
                evidence_refs=(envelope.citation_ids[0],),
                producer_version=envelope.producer_version,
            )
        attempt = 2 if envelope.role is EnvelopeRole.PORTFOLIO_MANAGER_RETRY else 1
        request = PortfolioRequest(
            envelope.allowed_symbols[-1],
            ProposalAction.OPEN,
            PositionSide.LONG,
            Decimal("0.050000"),
            Decimal("0.8000"),
            (envelope.citation_ids[0],),
            (ProposalReasonCode.FUNDAMENTAL,),
            ("margin compression",),
        )
        return PortfolioProposal(
            meta=ContractMeta(
                SCHEMA_VERSION,
                envelope.output_id,
                envelope.created_at,
                envelope.producer_version,
            ),
            proposal_id=envelope.output_id,
            attempt=attempt,
            context_id=envelope.context_id,
            context_hash=envelope.context_hash,
            bundle_id=envelope.bundle_id,
            bundle_hash=envelope.bundle_hash,
            superseded_proposal_id=None,
            universe_hash=envelope.universe_hash,
            snapshot_hash=envelope.snapshot_hash,
            window=envelope.window,
            requests=(request,),
            graph_version=envelope.versions.graph,
            prompt_version=envelope.versions.prompt,
            model_version=envelope.versions.model,
            provider_version=envelope.versions.provider,
            data_version=envelope.versions.data,
            memory_version=envelope.versions.memory,
            expiration_at=envelope.deadline,
            status=AnalysisStatus.VALID,
        )


def _analyst_request() -> ProviderRequest:
    base = _envelope()
    envelope = _envelope(
        output_id=derive_provider_output_id(
            base.run_id,
            base.input_id,
            EnvelopeStage.ANALYST,
            EnvelopeRole.TECHNICAL,
            None,
        )
    )
    return ProviderRequest(
        stage=ProviderStage.ANALYST,
        run_id=envelope.run_id,
        input_id=envelope.input_id,
        packet_hash=envelope.packet_hash,
        snapshot_hash=envelope.snapshot_hash,
        symbol=envelope.symbol,
        deadline=envelope.deadline,
        evidence_refs=envelope.citation_ids,
        envelope=envelope,
        role=AnalystRole.TECHNICAL,
    )


def test_analysis_adapter_runs_full_fake_p3c_and_uses_exact_contract_per_stage() -> None:
    invoker = DynamicAnalysisInvoker()
    provider = AgnesAnalysisProvider(invoker)
    result = AnalysisPipeline(
        provider,
        InMemoryAnalysisStateRepository(),
        now=lambda: timestamp().value,
    ).run(analysis_input(), evidence_packet(), "MSFT")

    assert type(result.trader_plan) is TraderPlan
    contracts = [contract for _envelope_value, contract in invoker.calls]
    assert contracts.count(OutputContract.ANALYST_REPORT) == 4
    assert contracts.count(OutputContract.DEBATE_ARGUMENT) == 4
    assert contracts.count(OutputContract.RESEARCH_CONCLUSION) == 1
    assert contracts.count(OutputContract.TRADER_PLAN) == 1


def test_proposal_adapter_runs_full_fake_p3d_and_uses_exact_contract_per_stage() -> None:
    invoker = DynamicProposalInvoker()
    provider = AgnesProposalProvider(invoker)
    parent = fixture_parent()
    bundle = fixture_bundle(
        parent,
        (
            replace(fixture_item("MSFT", 71, parent), prompt_version="p3e.1"),
            replace(fixture_item("NVDA", 72, parent), prompt_version="p3e.1"),
        ),
    )
    pipeline, _ = make_proposal_pipeline(
        provider,
        producer_versions=ProposalProducerVersions(
            "graph.1",
            "p3e.1",
            "agnes-2.5-flash",
            "agnes.1",
            "data.1",
            "memory.1",
        ),
    )

    proposal = pipeline.run(bundle, parent)

    assert type(proposal) is PortfolioProposal
    contracts = [contract for _envelope_value, contract in invoker.calls]
    assert contracts.count(OutputContract.RISK_ARGUMENT) == 6
    assert contracts.count(OutputContract.PORTFOLIO_PROPOSAL) == 1


class DriftingInvoker:
    def invoke(self, envelope: object, contract: OutputContract) -> object:
        del envelope, contract
        return object()


@pytest.mark.parametrize(
    "provider",
    [
        AgnesAnalysisProvider(DriftingInvoker()),
        AgnesProposalProvider(DriftingInvoker()),
    ],
)
def test_adapter_rejects_invoker_output_type_drift(provider: object) -> None:
    if type(provider) is AgnesAnalysisProvider:
        request = _analyst_request()
    else:
        capture = CaptureProposalRequest()
        pipeline, _ = make_proposal_pipeline(capture)
        with pytest.raises(CapturedRequest):
            pipeline.run(fixture_bundle(), fixture_parent())
        request = capture.request

    with pytest.raises(ModelInvocationError) as caught:
        provider.execute(request)  # type: ignore[attr-defined]
    assert caught.value.code is ModelTransportErrorCode.SCHEMA


class CapturedRequest(BaseException):
    pass


class CaptureProposalRequest:
    def __init__(self) -> None:
        self.request: ProposalRequest | None = None

    def execute(self, request: ProposalRequest) -> object:
        self.request = request
        raise CapturedRequest


class OneResponseExecutor:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[RawHttpRequest] = []

    def execute(self, request: RawHttpRequest) -> RawHttpResponse:
        self.requests.append(request)
        outer = {
            "id": "response.1",
            "object": "chat.completion",
            "created": 1_777_000_000,
            "model": "agnes-2.5-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self.content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }
        return RawHttpResponse(
            200,
            (("Content-Type", "application/json"),),
            json.dumps(outer, separators=(",", ":")).encode(),
            "https://apihub.agnes-ai.com/v1/chat/completions",
        )


def test_composition_builds_sealed_fake_end_to_end_without_keychain_or_network() -> None:
    request = _analyst_request()
    output = DynamicAnalysisInvoker().invoke(request.envelope, OutputContract.ANALYST_REPORT)
    assert type(output) is AnalystReport
    executor = OneResponseExecutor(
        json.dumps(output.to_wire(), separators=(",", ":"), sort_keys=True)
    )
    ref = SecretRef.primary(SecretKind.AGNES_API_KEY)
    backend = FakeSecretProvider({ref: SecretValue.from_bytes(b"fake-composition-agnes-key")})
    audit = FakeAuditPort()
    stack = build_agnes_provider_stack(
        secret_provider=backend,
        audit=audit,
        executor=executor,
        clock=lambda: timestamp(),
    )

    result = stack.analysis_provider.execute(request)

    assert type(stack) is AgnesProviderStack
    assert result == output
    assert backend.calls == [ref]
    assert len(executor.requests) == 1
    assert len(audit.attempts) == 1
    evidence = repr((stack, audit.attempts, executor.requests))
    assert "fake-composition-agnes-key" not in evidence


def test_public_stack_cannot_bypass_audit_through_raw_capabilities() -> None:
    ref = SecretRef.primary(SecretKind.AGNES_API_KEY)
    audit = FakeAuditPort()
    executor = OneResponseExecutor("{}")
    stack = build_agnes_provider_stack(
        secret_provider=FakeSecretProvider(
            {ref: SecretValue.from_bytes(b"fake-bypass-regression-key")}
        ),
        audit=audit,
        executor=executor,
        clock=lambda: timestamp(),
    )

    assert set(stack.__slots__) == {"analysis_provider", "proposal_provider"}
    for bypass_name in ("config", "transport", "invoker", "secret", "api_key", "execute"):
        assert not hasattr(stack, bypass_name)
        with pytest.raises(AttributeError):
            getattr(stack, bypass_name)
    assert callable(stack.analysis_provider.execute)
    assert callable(stack.proposal_provider.execute)
    assert audit.attempts == {}
    assert executor.requests == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"audit": object()},
        {"executor": object()},
        {"clock": object()},
    ],
)
def test_composition_rejects_invalid_capabilities_before_secret_lookup(
    overrides: dict[str, object],
) -> None:
    ref = SecretRef.primary(SecretKind.AGNES_API_KEY)
    backend = FakeSecretProvider({ref: SecretValue.from_bytes(b"fake-unused-key")})
    values: dict[str, object] = {
        "secret_provider": backend,
        "audit": FakeAuditPort(),
        "executor": OneResponseExecutor("{}"),
        "clock": lambda: timestamp(),
    }
    values.update(overrides)

    with pytest.raises(ValueError, match="capability"):
        build_agnes_provider_stack(**values)  # type: ignore[arg-type]
    assert backend.calls == []
