"""Deterministic P3-C analysts-to-Trader orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import cast
from uuid import UUID

from seven_lens.analysis.concurrency import run_bounded_group
from seven_lens.analysis.contracts import (
    AnalysisInput,
    AnalysisStatus,
    AnalystReport,
    AnalystRole,
    InvestmentDebateState,
    ResearchConclusion,
    TraderPlan,
    canonical_wire_json,
)
from seven_lens.analysis.model_envelope import (
    EnvelopeRole,
    EnvelopeStage,
    EnvelopeVersions,
    SanitizedProviderEnvelope,
    derive_provider_output_id,
)
from seven_lens.analysis.model_material import evidence_packet_model_material
from seven_lens.analysis.ports import (
    AnalysisProvider,
    DebateArgument,
    ProviderRequest,
    ProviderStage,
)
from seven_lens.analysis.prompt_builder import (
    APPROVED_PROMPT_TEMPLATE_HASH,
    APPROVED_PROMPT_TEMPLATE_ID,
)
from seven_lens.application.ports.analysis import (
    AnalysisStage,
    AnalysisStateRepository,
    StoredStageResult,
)
from seven_lens.domain.json_values import JsonObject
from seven_lens.domain.value_objects import RunId
from seven_lens.sources.contracts import EvidencePacket, EvidenceStatus, FreshnessStatus

ROLE_ORDER = (
    AnalystRole.TECHNICAL,
    AnalystRole.FUNDAMENTALS,
    AnalystRole.NEWS,
    AnalystRole.SENTIMENT,
)


class AnalysisPipelineError(RuntimeError):
    pass


def _envelope_identity(
    stage: ProviderStage,
    role: AnalystRole | None,
) -> tuple[EnvelopeStage, EnvelopeRole]:
    if stage is ProviderStage.ANALYST:
        if type(role) is not AnalystRole:
            raise AnalysisPipelineError("analysis provider role is invalid")
        return EnvelopeStage.ANALYST, EnvelopeRole(role.value)
    if stage is ProviderStage.BULL:
        return EnvelopeStage.INVESTMENT_DEBATE, EnvelopeRole.BULL
    if stage is ProviderStage.BEAR:
        return EnvelopeStage.INVESTMENT_DEBATE, EnvelopeRole.BEAR
    if stage is ProviderStage.RESEARCH_MANAGER:
        return EnvelopeStage.RESEARCH_MANAGER, EnvelopeRole.RESEARCH_MANAGER
    return EnvelopeStage.TRADER, EnvelopeRole.TRADER


def _debate_argument_wire(argument: DebateArgument) -> dict[str, object]:
    return {
        "input_id": str(argument.input_id),
        "packet_hash": argument.packet_hash,
        "symbol": argument.symbol,
        "side": argument.side.value,
        "round_number": argument.round_number,
        "argument": argument.argument,
        "evidence_refs": list(argument.evidence_refs),
    }


def _strict_json_loads(payload: str) -> object:
    if type(payload) is not str or not 1 <= len(payload.encode("utf-8")) <= 262_144:
        raise ValueError("persisted analysis payload is outside its byte bound")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key is rejected")
            result[key] = value
        return result

    def reject_constant(_: str) -> object:
        raise ValueError("non-finite JSON numbers are rejected")

    return json.loads(
        payload,
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_constant,
    )


@dataclass(frozen=True, slots=True)
class PipelineResult:
    reports: tuple[AnalystReport, ...]
    debate: InvestmentDebateState
    conclusion: ResearchConclusion
    trader_plan: TraderPlan


class AnalysisPipeline:
    def __init__(
        self,
        provider: AnalysisProvider,
        repository: AnalysisStateRepository,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._now = now or (lambda: datetime.now(UTC))

    def run(
        self, analysis_input: AnalysisInput, packet: EvidencePacket, symbol: str
    ) -> PipelineResult:
        self._validate_frozen_input(analysis_input, packet, symbol)
        self._check_deadline(analysis_input)
        run_id = str(analysis_input.meta.run_id)
        self._repository.create_run(
            run_id,
            str(analysis_input.input_id),
            packet.packet_hash,
            analysis_input.portfolio_snapshot.content_hash,
        )
        current = self._repository.current_stage(run_id)
        if current is AnalysisStage.COMPLETE:
            terminal = self._load_stored(run_id, AnalysisStage.COMPLETE)
            if terminal.payload != "complete":
                raise AnalysisPipelineError("persisted terminal stage is malformed")
        if self._at_least(current, AnalysisStage.ANALYSTS):
            reports = self._load_reports(analysis_input, packet, symbol, run_id)
        else:
            reports = run_bounded_group(
                tuple(
                    partial(self._analyst, analysis_input, packet, symbol, role)
                    for role in ROLE_ORDER
                ),
                max_workers=4,
            )
            self._persist(
                run_id,
                analysis_input,
                AnalysisStage.PLANNED,
                AnalysisStage.ANALYSTS,
                {"reports": [item.to_wire() for item in reports]},
            )
        current = self._repository.current_stage(run_id)
        if self._at_least(current, AnalysisStage.DEBATE):
            debate = cast(
                InvestmentDebateState,
                self._load_contract(run_id, AnalysisStage.DEBATE, InvestmentDebateState),
            )
            assert type(debate) is InvestmentDebateState
            if (
                debate.input_id != analysis_input.input_id
                or debate.meta.run_id != analysis_input.meta.run_id
                or debate.meta.producer_version != analysis_input.meta.producer_version
                or debate.symbol != symbol
                or debate.round_count != 2
                or debate.complete is not True
                or not set(debate.verified_claims) <= packet.citation_ids
            ):
                raise AnalysisPipelineError(
                    "persisted debate identity, evidence, or completion is invalid"
                )
        else:
            round_arguments: list[DebateArgument] = []
            for round_number in (1, 2):
                prior = tuple(reports) + tuple(round_arguments)
                round_arguments.extend(
                    run_bounded_group(
                        tuple(
                            partial(
                                self._argument,
                                analysis_input,
                                packet,
                                symbol,
                                side,
                                round_number,
                                prior,
                            )
                            for side in (ProviderStage.BULL, ProviderStage.BEAR)
                        ),
                        max_workers=2,
                    )
                )
            arguments = tuple(round_arguments)
            debate = self._build_debate(analysis_input, symbol, arguments)
            self._persist(
                run_id,
                analysis_input,
                AnalysisStage.ANALYSTS,
                AnalysisStage.DEBATE,
                canonical_wire_json(debate),
            )
        current = self._repository.current_stage(run_id)
        if self._at_least(current, AnalysisStage.RESEARCH):
            conclusion = cast(
                ResearchConclusion,
                self._load_contract(run_id, AnalysisStage.RESEARCH, ResearchConclusion),
            )
        else:
            conclusion = cast(
                ResearchConclusion,
                self._typed_output(
                    analysis_input,
                    packet,
                    symbol,
                    ProviderStage.RESEARCH_MANAGER,
                    ResearchConclusion,
                    prior_outputs=(*reports, debate),
                ),
            )
        assert type(conclusion) is ResearchConclusion
        if (
            conclusion.input_id != analysis_input.input_id
            or conclusion.conclusion_id
            != derive_provider_output_id(
                analysis_input.meta.run_id,
                analysis_input.input_id,
                EnvelopeStage.RESEARCH_MANAGER,
                EnvelopeRole.RESEARCH_MANAGER,
                None,
            )
            or conclusion.symbol != symbol
            or conclusion.status is not AnalysisStatus.VALID
            or conclusion.meta.run_id != analysis_input.meta.run_id
            or conclusion.meta.producer_version != analysis_input.meta.producer_version
        ):
            raise AnalysisPipelineError("research conclusion identity or status is invalid")
        if not set(conclusion.evidence_refs) <= packet.citation_ids:
            raise AnalysisPipelineError("research conclusion cites evidence outside frozen packet")
        if current is AnalysisStage.DEBATE:
            self._persist(
                run_id,
                analysis_input,
                AnalysisStage.DEBATE,
                AnalysisStage.RESEARCH,
                canonical_wire_json(conclusion),
            )
        current = self._repository.current_stage(run_id)
        if self._at_least(current, AnalysisStage.TRADER):
            plan = cast(TraderPlan, self._load_contract(run_id, AnalysisStage.TRADER, TraderPlan))
        else:
            plan = cast(
                TraderPlan,
                self._typed_output(
                    analysis_input,
                    packet,
                    symbol,
                    ProviderStage.TRADER,
                    TraderPlan,
                    prior_outputs=(*reports, debate, conclusion),
                ),
            )
        assert type(plan) is TraderPlan
        if (
            plan.input_id != analysis_input.input_id
            or plan.plan_id
            != derive_provider_output_id(
                analysis_input.meta.run_id,
                analysis_input.input_id,
                EnvelopeStage.TRADER,
                EnvelopeRole.TRADER,
                None,
            )
            or plan.symbol != symbol
            or plan.status is not AnalysisStatus.VALID
            or plan.meta.run_id != analysis_input.meta.run_id
            or plan.meta.producer_version != analysis_input.meta.producer_version
        ):
            raise AnalysisPipelineError("trader plan identity or status is invalid")
        if not set(plan.evidence_refs) <= packet.citation_ids:
            raise AnalysisPipelineError("trader plan cites evidence outside frozen packet")
        if current is AnalysisStage.RESEARCH:
            self._persist(
                run_id,
                analysis_input,
                AnalysisStage.RESEARCH,
                AnalysisStage.TRADER,
                canonical_wire_json(plan),
            )
        if self._repository.current_stage(run_id) is AnalysisStage.TRADER:
            self._persist(
                run_id,
                analysis_input,
                AnalysisStage.TRADER,
                AnalysisStage.COMPLETE,
                "complete",
            )
        return PipelineResult(reports, debate, conclusion, plan)

    @staticmethod
    def _at_least(current: AnalysisStage, required: AnalysisStage) -> bool:
        order = (
            AnalysisStage.PLANNED,
            AnalysisStage.ANALYSTS,
            AnalysisStage.DEBATE,
            AnalysisStage.RESEARCH,
            AnalysisStage.TRADER,
            AnalysisStage.COMPLETE,
        )
        if current in {AnalysisStage.INVALID, AnalysisStage.EXPIRED}:
            raise AnalysisPipelineError("terminal analysis run cannot resume")
        return order.index(current) >= order.index(required)

    def _load_reports(
        self,
        analysis_input: AnalysisInput,
        packet: EvidencePacket,
        symbol: str,
        run_id: str,
    ) -> tuple[AnalystReport, ...]:
        stored = self._load_stored(run_id, AnalysisStage.ANALYSTS)
        try:
            value = _strict_json_loads(stored.payload)
            if type(value) is not dict or set(value) != {"reports"}:
                raise ValueError("analyst result requires the exact wrapper")
            raw_reports = value["reports"]
            if type(raw_reports) is not list:
                raise ValueError("analyst result reports must be a list")
            reports = tuple(AnalystReport.from_wire(item) for item in raw_reports)
        except (KeyError, RecursionError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AnalysisPipelineError("persisted analyst stage is malformed") from error
        canonical = JsonObject.from_value(
            {"reports": [item.to_wire() for item in reports]}
        ).to_json()
        if canonical != stored.payload:
            raise AnalysisPipelineError("persisted analyst stage is not canonical")
        if tuple(item.role for item in reports) != ROLE_ORDER:
            raise AnalysisPipelineError("persisted analyst join order is invalid")
        for index, item in enumerate(reports):
            self._check_analyst_report(item, analysis_input, packet, symbol, ROLE_ORDER[index])
        return reports

    def _load_contract(
        self,
        run_id: str,
        stage: AnalysisStage,
        contract_type: type[InvestmentDebateState] | type[ResearchConclusion] | type[TraderPlan],
    ) -> InvestmentDebateState | ResearchConclusion | TraderPlan:
        stored = self._load_stored(run_id, stage)
        try:
            contract = contract_type.from_wire(_strict_json_loads(stored.payload))
        except (RecursionError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AnalysisPipelineError("persisted stage result is malformed") from error
        if canonical_wire_json(contract) != stored.payload:
            raise AnalysisPipelineError("persisted stage result is not canonical")
        return contract

    def _load_stored(self, run_id: str, stage: AnalysisStage) -> StoredStageResult:
        stored = self._repository.load(run_id, stage)
        if stored is None:
            raise AnalysisPipelineError("persisted stage result is missing")
        if (
            type(stored) is not StoredStageResult
            or stored.run_id != run_id
            or stored.stage is not stage
            or type(stored.result_hash) is not str
            or len(stored.result_hash) != 64
            or any(character not in "0123456789abcdef" for character in stored.result_hash)
            or type(stored.payload) is not str
            or not 1 <= len(stored.payload.encode("utf-8")) <= 262_144
            or hashlib.sha256(stored.payload.encode("utf-8")).hexdigest() != stored.result_hash
        ):
            raise AnalysisPipelineError("persisted stage result identity is invalid")
        return stored

    def _analyst(
        self,
        analysis_input: AnalysisInput,
        packet: EvidencePacket,
        symbol: str,
        role: AnalystRole,
    ) -> AnalystReport:
        output = self._typed_output(
            analysis_input, packet, symbol, ProviderStage.ANALYST, AnalystReport, role=role
        )
        assert type(output) is AnalystReport
        self._check_analyst_report(output, analysis_input, packet, symbol, role)
        return output

    @staticmethod
    def _check_analyst_report(
        report: AnalystReport,
        analysis_input: AnalysisInput,
        packet: EvidencePacket,
        symbol: str,
        role: AnalystRole,
    ) -> None:
        """Apply the exact same authority checks to fresh and persisted reports."""
        if (
            report.input_id != analysis_input.input_id
            or report.report_id
            != derive_provider_output_id(
                analysis_input.meta.run_id,
                analysis_input.input_id,
                EnvelopeStage.ANALYST,
                EnvelopeRole(role.value),
                None,
            )
            or report.role is not role
            or report.symbol != symbol
            or report.meta.producer_version != analysis_input.meta.producer_version
        ):
            raise AnalysisPipelineError("analyst output identity is invalid")
        if (
            report.meta.run_id != analysis_input.meta.run_id
            or report.status is not AnalysisStatus.VALID
        ):
            raise AnalysisPipelineError("analyst output run or status is invalid")
        if (
            not report.material_claims
            or not report.evidence_refs
            or not set(report.evidence_refs) <= packet.citation_ids
            or not set(report.counterevidence_refs) <= packet.citation_ids
        ):
            raise AnalysisPipelineError("analyst report evidence is invalid")

    def _argument(
        self,
        analysis_input: AnalysisInput,
        packet: EvidencePacket,
        symbol: str,
        side: ProviderStage,
        round_number: int,
        prior_outputs: tuple[object, ...],
    ) -> DebateArgument:
        output = self._typed_output(
            analysis_input,
            packet,
            symbol,
            side,
            DebateArgument,
            round_number=round_number,
            prior_outputs=prior_outputs,
        )
        assert type(output) is DebateArgument
        if (
            output.input_id != analysis_input.input_id
            or output.packet_hash != packet.packet_hash
            or output.symbol != symbol
            or output.side is not side
            or output.round_number != round_number
        ):
            raise AnalysisPipelineError("debate output identity is invalid")
        if not set(output.evidence_refs) <= packet.citation_ids:
            raise AnalysisPipelineError("debate cites evidence outside frozen packet")
        return output

    def _typed_output(
        self,
        analysis_input: AnalysisInput,
        packet: EvidencePacket,
        symbol: str,
        stage: ProviderStage,
        expected: type[object],
        *,
        role: AnalystRole | None = None,
        round_number: int | None = None,
        prior_outputs: tuple[object, ...] = (),
    ) -> object:
        self._check_deadline(analysis_input)
        envelope_stage, envelope_role = _envelope_identity(stage, role)
        envelope = SanitizedProviderEnvelope.build(
            stage=envelope_stage,
            role=envelope_role,
            round_number=round_number,
            run_id=analysis_input.meta.run_id,
            input_id=analysis_input.input_id,
            output_id=derive_provider_output_id(
                analysis_input.meta.run_id,
                analysis_input.input_id,
                envelope_stage,
                envelope_role,
                round_number,
            ),
            producer_version=analysis_input.meta.producer_version,
            symbol=symbol,
            attempt=None,
            superseded_proposal_id=None,
            superseded_proposal_hash=None,
            context_id=None,
            previous_context_id=None,
            bundle_id=None,
            packet_hash=packet.packet_hash,
            snapshot_hash=analysis_input.portfolio_snapshot.content_hash,
            context_hash=None,
            bundle_hash=None,
            universe_hash=analysis_input.universe_hash,
            created_at=analysis_input.meta.created_at,
            deadline=analysis_input.deadline,
            window=analysis_input.window,
            allowed_symbols=(*analysis_input.holding_symbols, *analysis_input.candidate_symbols),
            citation_ids=tuple(sorted(packet.citation_ids)),
            portfolio_snapshot=analysis_input.portfolio_snapshot,
            source_material=(analysis_input, packet, symbol),
            untrusted_data=evidence_packet_model_material(packet),
            prior_outputs=prior_outputs,
            feedback=None,
            versions=EnvelopeVersions(
                graph="tradingagents.1",
                prompt="p3e.1",
                model="agnes-2.5-flash",
                provider="agnes.1",
                data=packet.producer_version,
                memory="none.1",
            ),
            prompt_template_id=APPROVED_PROMPT_TEMPLATE_ID,
            prompt_template_hash=APPROVED_PROMPT_TEMPLATE_HASH,
        )
        request = ProviderRequest(
            stage,
            analysis_input.meta.run_id,
            analysis_input.input_id,
            packet.packet_hash,
            analysis_input.portfolio_snapshot.content_hash,
            symbol,
            analysis_input.deadline,
            tuple(sorted(packet.citation_ids)),
            envelope,
            role,
            round_number,
        )
        try:
            output = self._provider.execute(request)
        except Exception as error:
            raise AnalysisPipelineError("analysis provider failed closed") from error
        self._check_deadline(analysis_input)
        if type(output) is not expected:
            raise AnalysisPipelineError("analysis provider returned an invalid result type")
        return output

    def _build_debate(
        self,
        analysis_input: AnalysisInput,
        symbol: str,
        arguments: tuple[DebateArgument, ...],
    ) -> InvestmentDebateState:
        if len(arguments) != 4:
            raise AnalysisPipelineError("investment debate requires two complete rounds")
        return InvestmentDebateState(
            analysis_input.meta,
            RunId(
                UUID(
                    bytes=hashlib.sha256(f"{analysis_input.input_id}:debate".encode()).digest()[
                        :16
                    ],
                    version=4,
                )
            ),
            analysis_input.input_id,
            symbol,
            tuple(item.argument for item in arguments if item.side is ProviderStage.BULL),
            tuple(item.argument for item in arguments if item.side is ProviderStage.BEAR),
            tuple(dict.fromkeys(ref for item in arguments for ref in item.evidence_refs)),
            (),
            (),
            2,
            True,
        )

    def _validate_frozen_input(
        self, analysis_input: AnalysisInput, packet: EvidencePacket, symbol: str
    ) -> None:
        if type(analysis_input) is not AnalysisInput:
            raise AnalysisPipelineError("analysis input integrity is invalid")
        try:
            analysis_input.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise AnalysisPipelineError("analysis input integrity is invalid") from error
        if type(packet) is not EvidencePacket:
            raise AnalysisPipelineError("evidence packet integrity is invalid")
        try:
            packet.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise AnalysisPipelineError("evidence packet integrity is invalid") from error
        if (
            packet.status is not EvidenceStatus.VERIFIED
            or packet.freshness_status is not FreshnessStatus.FRESH
            or packet.contradiction_claim_ids
            or packet.missing_evidence
            or packet.as_of != analysis_input.as_of
        ):
            raise AnalysisPipelineError("evidence packet is not verified for the analysis time")
        if (
            packet.universe_hash != analysis_input.universe_hash
            or packet.portfolio_snapshot_hash != analysis_input.portfolio_snapshot.content_hash
            or packet.data_snapshot_refs != analysis_input.data_snapshot_refs
        ):
            raise AnalysisPipelineError("frozen input identity mismatch")
        if (
            set(analysis_input.evidence_refs) != packet.citation_ids
            or symbol not in analysis_input.focus_symbols
        ):
            raise AnalysisPipelineError("analysis evidence or symbol view is invalid")

    def _check_deadline(self, analysis_input: AnalysisInput) -> None:
        if self._now() > analysis_input.deadline.value:
            raise AnalysisPipelineError("analysis deadline expired")

    def _persist(
        self,
        run_id: str,
        analysis_input: AnalysisInput,
        expected: AnalysisStage,
        stage: AnalysisStage,
        payload: object,
    ) -> None:
        self._check_deadline(analysis_input)
        rendered = payload if type(payload) is str else JsonObject.from_value(payload).to_json()
        assert type(rendered) is str
        digest = hashlib.sha256(rendered.encode()).hexdigest()
        current = self._repository.current_stage(run_id)
        if current is stage:
            existing = self._load_stored(run_id, stage)
            if existing.result_hash != digest or existing.payload != rendered:
                raise AnalysisPipelineError("persisted stage result identity mismatch")
            return
        self._check_deadline(analysis_input)
        self._repository.advance(StoredStageResult(run_id, stage, digest, rendered), expected)
