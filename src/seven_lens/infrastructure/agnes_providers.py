"""Thin exact-contract Agnes adapters for the P3-C and P3-D provider ports."""

from __future__ import annotations

from typing import Protocol, cast

from seven_lens.analysis.contracts import AnalystReport, ResearchConclusion, TraderPlan
from seven_lens.analysis.model_envelope import SanitizedProviderEnvelope
from seven_lens.analysis.ports import (
    DebateArgument,
    ProviderOutput,
    ProviderRequest,
    ProviderStage,
)
from seven_lens.analysis.prompt_builder import OutputContract
from seven_lens.analysis.proposal_contracts import PortfolioProposal, RiskArgument
from seven_lens.analysis.proposal_ports import (
    ProposalOutput,
    ProposalProviderStage,
    ProposalRequest,
)
from seven_lens.application.model_invoker import (
    ModelInvocationError,
    ModelOutput,
    validate_agnes_route,
)
from seven_lens.application.ports.model_transport import ModelTransportErrorCode


class _ModelInvoker(Protocol):
    def invoke(
        self,
        envelope: SanitizedProviderEnvelope,
        output_contract: OutputContract,
    ) -> ModelOutput: ...


_ANALYSIS_CONTRACTS = {
    ProviderStage.ANALYST: (OutputContract.ANALYST_REPORT, AnalystReport),
    ProviderStage.BULL: (OutputContract.DEBATE_ARGUMENT, DebateArgument),
    ProviderStage.BEAR: (OutputContract.DEBATE_ARGUMENT, DebateArgument),
    ProviderStage.RESEARCH_MANAGER: (
        OutputContract.RESEARCH_CONCLUSION,
        ResearchConclusion,
    ),
    ProviderStage.TRADER: (OutputContract.TRADER_PLAN, TraderPlan),
}

_PROPOSAL_CONTRACTS = {
    ProposalProviderStage.AGGRESSIVE: (OutputContract.RISK_ARGUMENT, RiskArgument),
    ProposalProviderStage.CONSERVATIVE: (OutputContract.RISK_ARGUMENT, RiskArgument),
    ProposalProviderStage.NEUTRAL: (OutputContract.RISK_ARGUMENT, RiskArgument),
    ProposalProviderStage.PORTFOLIO_MANAGER: (
        OutputContract.PORTFOLIO_PROPOSAL,
        PortfolioProposal,
    ),
    ProposalProviderStage.PORTFOLIO_MANAGER_RETRY: (
        OutputContract.PORTFOLIO_PROPOSAL,
        PortfolioProposal,
    ),
}


class AgnesAnalysisProvider:
    """Bind each P3-C stage to one exact invoker output contract."""

    def __init__(self, invoker: _ModelInvoker) -> None:
        if not callable(getattr(invoker, "invoke", None)):
            raise ValueError("Agnes analysis provider requires a model invoker")
        self._invoker = invoker

    def execute(self, request: ProviderRequest) -> ProviderOutput:
        if type(request) is not ProviderRequest:
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA)
        try:
            request.__post_init__()
            contract, expected_type = _ANALYSIS_CONTRACTS[request.stage]
            validate_agnes_route(request.envelope)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA) from None
        output = self._invoker.invoke(request.envelope, contract)
        if type(output) is not expected_type:
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA)
        return cast(ProviderOutput, output)


class AgnesProposalProvider:
    """Bind each P3-D stage to one exact invoker output contract."""

    def __init__(self, invoker: _ModelInvoker) -> None:
        if not callable(getattr(invoker, "invoke", None)):
            raise ValueError("Agnes proposal provider requires a model invoker")
        self._invoker = invoker

    def execute(self, request: ProposalRequest) -> ProposalOutput:
        if type(request) is not ProposalRequest:
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA)
        try:
            request.__post_init__()
            contract, expected_type = _PROPOSAL_CONTRACTS[request.stage]
            validate_agnes_route(request.envelope)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA) from None
        output = self._invoker.invoke(request.envelope, contract)
        if type(output) is not expected_type:
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA)
        return cast(ProposalOutput, output)
