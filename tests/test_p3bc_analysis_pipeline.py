from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal

import pytest

from seven_lens.analysis.contracts import (
    AnalysisStatus,
    AnalystReport,
    AnalystRole,
    ContractMeta,
    InvestmentDebateState,
    PortfolioSnapshot,
    ProposalReasonCode,
    ResearchConclusion,
    ResearchRating,
    TraderPlan,
    build_portfolio_snapshot,
)
from seven_lens.analysis.model_envelope import (
    EnvelopeRole,
    EnvelopeStage,
    derive_provider_output_id,
)
from seven_lens.analysis.pipeline import ROLE_ORDER, AnalysisPipeline, AnalysisPipelineError
from seven_lens.analysis.ports import (
    DebateArgument,
    ProviderOutput,
    ProviderRequest,
    ProviderStage,
    ScriptedAnalysisProvider,
)
from seven_lens.application.ports.analysis import (
    AnalysisStage,
    InMemoryAnalysisStateRepository,
    StoredStageResult,
)
from seven_lens.domain.json_values import JsonObject
from seven_lens.sources.contracts import EvidencePacket, build_evidence_packet
from test_analysis_contracts import analysis_input, meta, report, rid, timestamp
from test_p3bc_evidence_and_infrastructure import evidence_packet


def portfolio_snapshot_at(minutes: int) -> PortfolioSnapshot:
    base = analysis_input().portfolio_snapshot
    return build_portfolio_snapshot(
        as_of=timestamp(minutes),
        nav=base.nav,
        cash=base.cash,
        buying_power=base.buying_power,
        positions=base.positions,
        open_orders=base.open_orders,
        same_day_fills=base.same_day_fills,
        borrow_statuses=base.borrow_statuses,
        remaining_limits=base.remaining_limits,
    )


def packet_with_data_snapshot_refs(refs: tuple[str, ...]) -> EvidencePacket:
    base = evidence_packet()
    return build_evidence_packet(
        schema_version=base.schema_version,
        packet_id=base.packet_id,
        as_of=base.as_of,
        source_records=base.source_records,
        fragments=base.fragments,
        claims=base.claims,
        contradiction_claim_ids=base.contradiction_claim_ids,
        missing_evidence=base.missing_evidence,
        freshness_status=base.freshness_status,
        status=base.status,
        universe_hash=base.universe_hash,
        portfolio_snapshot_hash=base.portfolio_snapshot_hash,
        data_snapshot_refs=refs,
        producer_version=base.producer_version,
    )


def scripted_outputs() -> dict[str, ProviderOutput | BaseException]:
    inp = analysis_input()
    packet = evidence_packet()
    outputs: dict[str, ProviderOutput | BaseException] = {}
    for role in ROLE_ORDER:
        outputs[f"ANALYST:{role.value}:"] = replace(
            report(AnalysisStatus.VALID),
            report_id=derive_provider_output_id(
                inp.meta.run_id,
                inp.input_id,
                EnvelopeStage.ANALYST,
                EnvelopeRole(role.value),
                None,
            ),
            role=role,
        )
    for round_number in (1, 2):
        for side in (ProviderStage.BULL, ProviderStage.BEAR):
            outputs[f"{side.value}::{round_number}"] = DebateArgument(
                inp.input_id,
                packet.packet_hash,
                "MSFT",
                side,
                round_number,
                f"{side.value.lower()} argument {round_number}",
                ("evidence.1",),
            )
    outputs["RESEARCH_MANAGER::"] = ResearchConclusion(
        meta(),
        derive_provider_output_id(
            inp.meta.run_id,
            inp.input_id,
            EnvelopeStage.RESEARCH_MANAGER,
            EnvelopeRole.RESEARCH_MANAGER,
            None,
        ),
        inp.input_id,
        "MSFT",
        ResearchRating.BUY,
        "conclusion",
        ("driver",),
        ("risk",),
        ("invalidator",),
        ("evidence.1",),
        Decimal("0.8000"),
        AnalysisStatus.VALID,
    )
    outputs["TRADER::"] = TraderPlan(
        meta(),
        derive_provider_output_id(
            inp.meta.run_id,
            inp.input_id,
            EnvelopeStage.TRADER,
            EnvelopeRole.TRADER,
            None,
        ),
        inp.input_id,
        "MSFT",
        ResearchRating.BUY,
        (ProposalReasonCode.FUNDAMENTAL,),
        ("evidence.1",),
        Decimal("100.00"),
        Decimal("110.00"),
        Decimal("90.00"),
        AnalysisStatus.VALID,
    )
    return outputs


def valid_reports() -> tuple[AnalystReport, ...]:
    inp = analysis_input()
    return tuple(
        replace(
            report(AnalysisStatus.VALID),
            report_id=derive_provider_output_id(
                inp.meta.run_id,
                inp.input_id,
                EnvelopeStage.ANALYST,
                EnvelopeRole(role.value),
                None,
            ),
            role=role,
        )
        for role in ROLE_ORDER
    )


def persisted_stage(run_id: str, stage: AnalysisStage, payload: str) -> StoredStageResult:
    return StoredStageResult(run_id, stage, hashlib.sha256(payload.encode()).hexdigest(), payload)


def persisted_analysts_stage(reports: tuple[AnalystReport, ...]) -> StoredStageResult:
    payload = JsonObject.from_value({"reports": [item.to_wire() for item in reports]}).to_json()
    return persisted_stage(str(meta().run_id), AnalysisStage.ANALYSTS, payload)


def valid_debate() -> InvestmentDebateState:
    return InvestmentDebateState(
        meta(),
        rid(30),
        analysis_input().input_id,
        "MSFT",
        ("bull",),
        ("bear",),
        ("evidence.1",),
        (),
        (),
        2,
        True,
    )


def test_pipeline_has_fixed_role_join_two_rounds_and_trader_boundary() -> None:
    provider = ScriptedAnalysisProvider(scripted_outputs())
    repository = InMemoryAnalysisStateRepository()
    result = AnalysisPipeline(provider, repository, now=lambda: timestamp().value).run(
        analysis_input(), evidence_packet(), "MSFT"
    )
    assert tuple(item.role for item in result.reports) == ROLE_ORDER
    assert result.debate.round_count == 2
    assert result.debate.complete is True
    assert result.trader_plan.status is AnalysisStatus.VALID
    assert repository.current_stage(str(meta().run_id)) is AnalysisStage.COMPLETE
    assert set(provider.calls[:4]) == {
        "ANALYST:TECHNICAL:",
        "ANALYST:FUNDAMENTALS:",
        "ANALYST:NEWS:",
        "ANALYST:SENTIMENT:",
    }
    assert set(provider.calls[4:6]) == {"BULL::1", "BEAR::1"}
    assert set(provider.calls[6:8]) == {"BULL::2", "BEAR::2"}
    assert provider.calls[8:] == ["RESEARCH_MANAGER::", "TRADER::"]


def test_pipeline_fails_closed_on_provider_identity_and_exception() -> None:
    outputs = scripted_outputs()
    outputs["ANALYST:TECHNICAL:"] = replace(report(AnalysisStatus.VALID), role=AnalystRole.NEWS)
    with pytest.raises(AnalysisPipelineError, match="identity"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(outputs),
            InMemoryAnalysisStateRepository(),
            now=lambda: timestamp().value,
        ).run(analysis_input(), evidence_packet(), "MSFT")

    outputs = scripted_outputs()
    outputs["ANALYST:TECHNICAL:"] = TimeoutError("marker")
    with pytest.raises(AnalysisPipelineError, match="failed closed") as caught:
        AnalysisPipeline(
            ScriptedAnalysisProvider(outputs),
            InMemoryAnalysisStateRepository(),
            now=lambda: timestamp().value,
        ).run(analysis_input(), evidence_packet(), "MSFT")
    assert "marker" not in str(caught.value)


def test_state_is_monotonic_idempotent_and_rejects_changed_retry() -> None:
    repository = InMemoryAnalysisStateRepository()
    repository.create_run("run", "input", "a" * 64, "b" * 64)
    result = StoredStageResult("run", AnalysisStage.ANALYSTS, "c" * 64, "payload")
    assert repository.advance(result, AnalysisStage.PLANNED) is True
    assert repository.advance(result, AnalysisStage.PLANNED) is False
    # Remediation R1 Fix-2: retries keep using the legal (PLANNED, ANALYSTS) pair;
    # self-transitions such as (ANALYSTS, ANALYSTS) are now rejected by the whitelist.
    with pytest.raises(ValueError, match="not legal"):
        repository.advance(
            replace(result, result_hash="d" * 64),
            AnalysisStage.ANALYSTS,
        )
    with pytest.raises(ValueError, match="changed"):
        repository.advance(
            replace(result, result_hash="d" * 64),
            AnalysisStage.PLANNED,
        )
    with pytest.raises(ValueError, match="not legal"):
        repository.advance(
            StoredStageResult("run", AnalysisStage.TRADER, "e" * 64, "payload"),
            AnalysisStage.DEBATE,
        )
    with pytest.raises(ValueError, match="out of order"):
        repository.advance(
            StoredStageResult("run", AnalysisStage.RESEARCH, "e" * 64, "payload"),
            AnalysisStage.DEBATE,
        )


def test_in_memory_repository_rejects_run_identity_collision() -> None:
    repository = InMemoryAnalysisStateRepository()
    repository.create_run("run", "input", "a" * 64, "b" * 64)
    repository.create_run("run", "input", "a" * 64, "b" * 64)
    for identity in (
        ("other-input", "a" * 64, "b" * 64),
        ("input", "c" * 64, "b" * 64),
        ("input", "a" * 64, "d" * 64),
    ):
        with pytest.raises(ValueError, match="identity collision"):
            repository.create_run("run", *identity)


@pytest.mark.parametrize(
    ("packet_hash", "snapshot_hash"),
    [("a" * 64, "b" * 64), ("c" * 64, "d" * 64)],
    ids=["same-packet-snapshot", "different-packet-snapshot"],
)
def test_in_memory_repository_rejects_second_run_for_same_input(
    packet_hash: str, snapshot_hash: str
) -> None:
    repository = InMemoryAnalysisStateRepository()
    repository.create_run("run-1", "input-1", "a" * 64, "b" * 64)
    with pytest.raises(ValueError, match="input already has"):
        repository.create_run("run-2", "input-1", packet_hash, snapshot_hash)
    assert repository.current_stage("run-1") is AnalysisStage.PLANNED
    with pytest.raises(KeyError):
        repository.current_stage("run-2")


def test_repository_whitelist_blocks_skip_regression_revival_and_runtime_jump() -> None:
    repository = InMemoryAnalysisStateRepository()
    repository.create_run("run", "input", "a" * 64, "b" * 64)
    for expected, stage in (
        (AnalysisStage.PLANNED, AnalysisStage.TRADER),
        (AnalysisStage.PLANNED, AnalysisStage.COMPLETE),
        (AnalysisStage.ANALYSTS, AnalysisStage.ANALYSTS),
        (AnalysisStage.DEBATE, AnalysisStage.ANALYSTS),
    ):
        with pytest.raises(ValueError, match="not legal"):
            repository.advance(StoredStageResult("run", stage, "c" * 64, "payload"), expected)
    assert (
        repository.advance(
            StoredStageResult("run", AnalysisStage.ANALYSTS, "d" * 64, "payload"),
            AnalysisStage.PLANNED,
        )
        is True
    )
    assert (
        repository.advance(
            StoredStageResult("run", AnalysisStage.INVALID, "e" * 64, "invalid"),
            AnalysisStage.ANALYSTS,
        )
        is True
    )
    with pytest.raises(ValueError, match="not legal"):
        repository.advance(
            StoredStageResult("run", AnalysisStage.DEBATE, "f" * 64, "payload"),
            AnalysisStage.INVALID,
        )


def test_repository_counts_real_attempts_and_bounds_same_hash_retries() -> None:
    repository = InMemoryAnalysisStateRepository()
    repository.create_run("run", "input", "a" * 64, "b" * 64)
    result = StoredStageResult("run", AnalysisStage.ANALYSTS, "d" * 64, "payload")
    assert repository.advance(result, AnalysisStage.PLANNED) is True
    assert repository.attempt_count("run", AnalysisStage.ANALYSTS) == 1
    for expected in range(2, 9):
        assert repository.advance(result, AnalysisStage.PLANNED) is False
        assert repository.attempt_count("run", AnalysisStage.ANALYSTS) == expected
    with pytest.raises(ValueError, match="budget"):
        repository.advance(result, AnalysisStage.PLANNED)


def test_complete_run_resumes_only_from_persisted_results() -> None:
    repository = InMemoryAnalysisStateRepository()
    first = AnalysisPipeline(
        ScriptedAnalysisProvider(scripted_outputs()),
        repository,
        now=lambda: timestamp().value,
    ).run(analysis_input(), evidence_packet(), "MSFT")
    empty_provider = ScriptedAnalysisProvider({})
    resumed = AnalysisPipeline(
        empty_provider,
        repository,
        now=lambda: timestamp().value,
    ).run(analysis_input(), evidence_packet(), "MSFT")
    assert resumed == first
    assert empty_provider.calls == []
    for stage in (
        AnalysisStage.ANALYSTS,
        AnalysisStage.DEBATE,
        AnalysisStage.RESEARCH,
        AnalysisStage.TRADER,
        AnalysisStage.COMPLETE,
    ):
        assert repository.attempt_count(str(meta().run_id), stage) == 1


@pytest.mark.parametrize(
    "tampered",
    [
        replace(
            report(AnalysisStatus.VALID),
            report_id=valid_reports()[0].report_id,
            role=AnalystRole.TECHNICAL,
            meta=replace(meta(), run_id=rid(99)),
        ),
        replace(
            report(AnalysisStatus.VALID),
            report_id=valid_reports()[0].report_id,
            role=AnalystRole.TECHNICAL,
            input_id=rid(99),
        ),
        replace(
            report(AnalysisStatus.VALID),
            report_id=valid_reports()[0].report_id,
            role=AnalystRole.TECHNICAL,
            status=AnalysisStatus.ABSTAIN,
            confidence=Decimal("0.0000"),
            material_claims=(),
        ),
        replace(
            report(AnalysisStatus.VALID),
            report_id=valid_reports()[0].report_id,
            role=AnalystRole.TECHNICAL,
            evidence_refs=("evidence.9",),
        ),
        replace(
            report(AnalysisStatus.VALID),
            report_id=valid_reports()[0].report_id,
            role=AnalystRole.TECHNICAL,
            counterevidence_refs=("foreign.evidence",),
        ),
    ],
    ids=[
        "foreign-run-id",
        "foreign-input-id",
        "abstain-status",
        "citation-outside-packet",
        "counterevidence-outside-packet",
    ],
)
def test_resume_rejects_drifted_persisted_analyst_payload(tampered: AnalystReport) -> None:
    repository = InMemoryAnalysisStateRepository()
    run_id = str(meta().run_id)
    repository.create_run(
        run_id,
        str(analysis_input().input_id),
        evidence_packet().packet_hash,
        analysis_input().portfolio_snapshot.content_hash,
    )
    reports = list(valid_reports())
    reports[0] = tampered
    repository.advance(persisted_analysts_stage(tuple(reports)), AnalysisStage.PLANNED)
    with pytest.raises(AnalysisPipelineError):
        AnalysisPipeline(
            ScriptedAnalysisProvider(scripted_outputs()),
            repository,
            now=lambda: timestamp().value,
        ).run(analysis_input(), evidence_packet(), "MSFT")


def test_fresh_analyst_rejects_counterevidence_outside_packet() -> None:
    outputs = scripted_outputs()
    outputs["ANALYST:TECHNICAL:"] = replace(
        valid_reports()[0], counterevidence_refs=("foreign.evidence",)
    )
    with pytest.raises(AnalysisPipelineError, match="evidence"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(outputs),
            InMemoryAnalysisStateRepository(),
            now=lambda: timestamp().value,
        ).run(analysis_input(), evidence_packet(), "MSFT")


def test_resume_rejects_drifted_persisted_debate_payload() -> None:
    repository = InMemoryAnalysisStateRepository()
    run_id = str(meta().run_id)
    repository.create_run(
        run_id,
        str(analysis_input().input_id),
        evidence_packet().packet_hash,
        analysis_input().portfolio_snapshot.content_hash,
    )
    repository.advance(persisted_analysts_stage(valid_reports()), AnalysisStage.PLANNED)
    drifted = replace(valid_debate(), input_id=rid(99))
    repository.advance(
        persisted_stage(
            run_id,
            AnalysisStage.DEBATE,
            JsonObject.from_value(drifted.to_wire()).to_json(),
        ),
        AnalysisStage.ANALYSTS,
    )
    with pytest.raises(AnalysisPipelineError, match="debate"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(scripted_outputs()),
            repository,
            now=lambda: timestamp().value,
        ).run(analysis_input(), evidence_packet(), "MSFT")


def test_resume_rejects_persisted_debate_evidence_outside_packet() -> None:
    repository = InMemoryAnalysisStateRepository()
    run_id = str(meta().run_id)
    repository.create_run(
        run_id,
        str(analysis_input().input_id),
        evidence_packet().packet_hash,
        analysis_input().portfolio_snapshot.content_hash,
    )
    repository.advance(persisted_analysts_stage(valid_reports()), AnalysisStage.PLANNED)
    drifted = replace(valid_debate(), verified_claims=("foreign.evidence",))
    repository.advance(
        persisted_stage(
            run_id,
            AnalysisStage.DEBATE,
            JsonObject.from_value(drifted.to_wire()).to_json(),
        ),
        AnalysisStage.ANALYSTS,
    )
    with pytest.raises(AnalysisPipelineError, match=r"debate.*evidence"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(scripted_outputs()),
            repository,
            now=lambda: timestamp().value,
        ).run(analysis_input(), evidence_packet(), "MSFT")


def test_resume_from_persisted_analysts_is_pure_replay() -> None:
    repository = InMemoryAnalysisStateRepository()
    run_id = str(meta().run_id)
    repository.create_run(
        run_id,
        str(analysis_input().input_id),
        evidence_packet().packet_hash,
        analysis_input().portfolio_snapshot.content_hash,
    )
    repository.advance(persisted_analysts_stage(valid_reports()), AnalysisStage.PLANNED)
    provider = ScriptedAnalysisProvider(scripted_outputs())
    resumed = AnalysisPipeline(provider, repository, now=lambda: timestamp().value).run(
        analysis_input(), evidence_packet(), "MSFT"
    )
    assert len(provider.calls) == 6
    assert set(provider.calls[:2]) == {"BULL::1", "BEAR::1"}
    assert set(provider.calls[2:4]) == {"BULL::2", "BEAR::2"}
    assert tuple(provider.calls[4:]) == ("RESEARCH_MANAGER::", "TRADER::")
    fresh = AnalysisPipeline(
        ScriptedAnalysisProvider(scripted_outputs()),
        InMemoryAnalysisStateRepository(),
        now=lambda: timestamp().value,
    ).run(analysis_input(), evidence_packet(), "MSFT")
    assert resumed == fresh


def test_provider_request_requires_canonical_hex_digests() -> None:
    inp = analysis_input()
    packet = evidence_packet()
    with pytest.raises(ValueError, match="packet hash"):
        ProviderRequest(
            ProviderStage.ANALYST,
            inp.meta.run_id,
            inp.input_id,
            "z" * 64,
            inp.portfolio_snapshot.content_hash,
            "MSFT",
            inp.deadline,
            ("evidence.1",),
            None,  # type: ignore[arg-type]
            AnalystRole.TECHNICAL,
            None,
        )
    with pytest.raises(ValueError, match="snapshot hash"):
        ProviderRequest(
            ProviderStage.ANALYST,
            inp.meta.run_id,
            inp.input_id,
            packet.packet_hash,
            "G" * 64,
            "MSFT",
            inp.deadline,
            ("evidence.1",),
            None,  # type: ignore[arg-type]
            AnalystRole.TECHNICAL,
            None,
        )


def test_pipeline_rejects_producer_version_drift_on_every_role_output() -> None:
    inp = analysis_input()
    drifted_meta = ContractMeta(meta().schema_version, meta().run_id, meta().created_at, "rogue.1")

    analyst_drift = scripted_outputs()
    analyst_drift["ANALYST:TECHNICAL:"] = replace(valid_reports()[0], meta=drifted_meta)
    with pytest.raises(AnalysisPipelineError, match="identity"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(analyst_drift),
            InMemoryAnalysisStateRepository(),
            now=lambda: timestamp().value,
        ).run(inp, evidence_packet(), "MSFT")
    conclusion_drift = scripted_outputs()
    conclusion_drift["RESEARCH_MANAGER::"] = ResearchConclusion(
        drifted_meta,
        derive_provider_output_id(
            inp.meta.run_id,
            inp.input_id,
            EnvelopeStage.RESEARCH_MANAGER,
            EnvelopeRole.RESEARCH_MANAGER,
            None,
        ),
        inp.input_id,
        "MSFT",
        ResearchRating.BUY,
        "conclusion",
        ("driver",),
        ("risk",),
        ("invalidator",),
        ("evidence.1",),
        Decimal("0.8000"),
        AnalysisStatus.VALID,
    )
    with pytest.raises(AnalysisPipelineError, match="conclusion"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(conclusion_drift),
            InMemoryAnalysisStateRepository(),
            now=lambda: timestamp().value,
        ).run(inp, evidence_packet(), "MSFT")

    plan_drift = scripted_outputs()
    plan_drift["TRADER::"] = TraderPlan(
        drifted_meta,
        derive_provider_output_id(
            inp.meta.run_id,
            inp.input_id,
            EnvelopeStage.TRADER,
            EnvelopeRole.TRADER,
            None,
        ),
        inp.input_id,
        "MSFT",
        ResearchRating.BUY,
        (ProposalReasonCode.FUNDAMENTAL,),
        ("evidence.1",),
        Decimal("100.00"),
        Decimal("110.00"),
        Decimal("90.00"),
        AnalysisStatus.VALID,
    )
    with pytest.raises(AnalysisPipelineError, match="trader plan"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(plan_drift),
            InMemoryAnalysisStateRepository(),
            now=lambda: timestamp().value,
        ).run(inp, evidence_packet(), "MSFT")


def test_pipeline_rechecks_deadline_after_provider_before_persisting() -> None:
    clock = [timestamp().value]
    delegate = ScriptedAnalysisProvider(scripted_outputs())

    class DeadlineCrossingProvider:
        def execute(self, request: ProviderRequest) -> ProviderOutput:
            output = delegate.execute(request)
            if request.stage is ProviderStage.TRADER:
                clock[0] = timestamp(16).value
            return output

    repository = InMemoryAnalysisStateRepository()
    with pytest.raises(AnalysisPipelineError, match="deadline expired"):
        AnalysisPipeline(DeadlineCrossingProvider(), repository, now=lambda: clock[0]).run(
            analysis_input(), evidence_packet(), "MSFT"
        )
    assert repository.current_stage(str(meta().run_id)) is AnalysisStage.RESEARCH


def test_pipeline_rejects_expired_input_before_creating_run_authority() -> None:
    repository = InMemoryAnalysisStateRepository()
    with pytest.raises(AnalysisPipelineError, match="deadline expired"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(scripted_outputs()),
            repository,
            now=lambda: timestamp(16).value,
        ).run(analysis_input(), evidence_packet(), "MSFT")
    with pytest.raises(KeyError):
        repository.current_stage(str(meta().run_id))


@pytest.mark.parametrize("snapshot_minutes", [-10, 10], ids=["stale", "future"])
def test_pipeline_rejects_snapshot_time_drift_before_creating_run(
    snapshot_minutes: int,
) -> None:
    inp = analysis_input()
    object.__setattr__(inp, "portfolio_snapshot", portfolio_snapshot_at(snapshot_minutes))
    repository = InMemoryAnalysisStateRepository()
    with pytest.raises(AnalysisPipelineError, match="analysis input integrity"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(scripted_outputs()),
            repository,
            now=lambda: timestamp().value,
        ).run(inp, evidence_packet(), "MSFT")
    with pytest.raises(KeyError):
        repository.current_stage(str(meta().run_id))


@pytest.mark.parametrize(
    ("input_refs", "packet_refs"),
    [
        (("foreign.snapshot",), ("market.1",)),
        ((), ("market.1",)),
        (("market.2", "market.1"), ("market.1", "market.2")),
    ],
    ids=["foreign", "missing", "reordered"],
)
def test_pipeline_rejects_data_snapshot_ref_drift_before_creating_run(
    input_refs: tuple[str, ...], packet_refs: tuple[str, ...]
) -> None:
    inp = replace(analysis_input(), data_snapshot_refs=input_refs)
    packet = packet_with_data_snapshot_refs(packet_refs)
    repository = InMemoryAnalysisStateRepository()
    with pytest.raises(AnalysisPipelineError, match="frozen input identity mismatch"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(scripted_outputs()),
            repository,
            now=lambda: timestamp().value,
        ).run(inp, packet, "MSFT")
    with pytest.raises(KeyError):
        repository.current_stage(str(meta().run_id))


@pytest.mark.parametrize("tamper", ["focus-symbols", "nested-position"])
def test_pipeline_revalidates_analysis_input_before_creating_run(tamper: str) -> None:
    inp = analysis_input()
    if tamper == "focus-symbols":
        object.__setattr__(inp, "focus_symbols", ("SPY",))
        symbol = "SPY"
    else:
        object.__setattr__(inp.portfolio_snapshot.positions[0], "quantity", Decimal("-1.000000"))
        symbol = "MSFT"
    repository = InMemoryAnalysisStateRepository()
    with pytest.raises(AnalysisPipelineError, match="analysis input integrity"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(scripted_outputs()),
            repository,
            now=lambda: timestamp().value,
        ).run(inp, evidence_packet(), symbol)
    with pytest.raises(KeyError):
        repository.current_stage(str(meta().run_id))


@pytest.mark.parametrize("tamper", ["packet-hash", "future-source"])
def test_pipeline_revalidates_packet_integrity_before_creating_run(tamper: str) -> None:
    packet = evidence_packet()
    if tamper == "packet-hash":
        object.__setattr__(packet, "packet_hash", "f" * 64)
    else:
        future_source = replace(
            packet.source_records[0],
            retrieved_at=timestamp(1),
            available_at=timestamp(1),
        )
        object.__setattr__(packet, "source_records", (future_source,))
    repository = InMemoryAnalysisStateRepository()
    with pytest.raises(AnalysisPipelineError, match="packet integrity"):
        AnalysisPipeline(
            ScriptedAnalysisProvider(scripted_outputs()),
            repository,
            now=lambda: timestamp().value,
        ).run(analysis_input(), packet, "MSFT")
    with pytest.raises(KeyError):
        repository.current_stage(str(meta().run_id))
