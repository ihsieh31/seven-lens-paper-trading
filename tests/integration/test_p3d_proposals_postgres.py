# mypy: ignore-errors
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb
from test_postgres_runtime_role import runtime_postgres  # noqa: F401

from seven_lens.analysis.contracts import (
    SCHEMA_VERSION,
    AnalysisInput,
    AnalysisStatus,
    ContractMeta,
    PortfolioSnapshot,
    ProposalReasonCode,
    ResearchRating,
    RiskRejectionCode,
    RiskRejectionFeedback,
    TraderPlan,
    build_analysis_input,
    build_portfolio_snapshot,
    canonical_wire_json,
)
from seven_lens.analysis.proposal_contracts import (
    ProposalContext,
    ResearchBundle,
    RiskArgument,
    RiskViewpoint,
    build_proposal_context,
    build_research_bundle,
    build_risk_debate,
    derive_argument_id,
    derive_bundle_id,
    derive_context_id,
    derive_proposal_run_id,
)
from seven_lens.analysis.proposal_pipeline import ProposalPipeline, ProposalPipelineError
from seven_lens.application.ports.proposals import ProposalStage
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.postgres_proposals import (
    PostgresProposalError,
    PostgresProposalStateRepository,
)
from seven_lens.infrastructure.postgres_roles import PostgresRoleError, verify_runtime_role
from test_analysis_contracts import limits, rid
from test_p3d_proposal_contracts import item as fixture_item
from test_p3d_proposal_contracts import p3d_proposal as fixture_p3d_proposal
from test_p3d_proposal_contracts import parent_input as fixture_parent
from test_p3d_research_and_proposal_pipeline import (
    ProposalFakeProvider,
    versions,
)

pytestmark = pytest.mark.integration


@contextmanager
def connection(dsn: str) -> Iterator[Any]:
    value = psycopg.connect(dsn)
    try:
        yield value
    finally:
        value.close()


def _clock():
    return lambda: datetime.now(UTC)


def _live_parent(*, deadline_seconds: float = 600) -> AnalysisInput:
    base = fixture_parent()
    now = datetime.now(UTC)
    as_of = UtcTimestamp(now - timedelta(minutes=4))
    deadline = UtcTimestamp(now + timedelta(seconds=deadline_seconds))
    snapshot = build_portfolio_snapshot(
        as_of=as_of,
        nav=base.portfolio_snapshot.nav,
        cash=base.portfolio_snapshot.cash,
        buying_power=base.portfolio_snapshot.buying_power,
        positions=base.portfolio_snapshot.positions,
        open_orders=base.portfolio_snapshot.open_orders,
        same_day_fills=base.portfolio_snapshot.same_day_fills,
        borrow_statuses=base.portfolio_snapshot.borrow_statuses,
        remaining_limits=base.portfolio_snapshot.remaining_limits,
    )
    return build_analysis_input(
        meta=ContractMeta(SCHEMA_VERSION, base.meta.run_id, as_of, base.meta.producer_version),
        input_id=base.input_id,
        as_of=as_of,
        window=base.window,
        deadline=deadline,
        portfolio_snapshot=snapshot,
        holding_symbols=base.holding_symbols,
        candidate_symbols=base.candidate_symbols,
        focus_symbols=base.focus_symbols,
        evidence_refs=base.evidence_refs,
        data_snapshot_refs=base.data_snapshot_refs,
    )


def _live_bundle(parent: AnalysisInput) -> ResearchBundle:
    items = tuple(
        _live_bundle_item(parent, symbol, number) for symbol, number in (("MSFT", 71), ("NVDA", 72))
    )
    return build_research_bundle(
        meta=ContractMeta(
            SCHEMA_VERSION,
            derive_bundle_id(parent.input_id),
            parent.meta.created_at,
            parent.meta.producer_version,
        ),
        parent_input_id=parent.input_id,
        as_of=parent.as_of,
        window=parent.window,
        deadline=parent.deadline,
        universe_hash=parent.universe_hash,
        portfolio_snapshot_hash=parent.portfolio_snapshot.content_hash,
        data_snapshot_refs=parent.data_snapshot_refs,
        holding_symbols=parent.holding_symbols,
        candidate_symbols=parent.candidate_symbols,
        items=items,
    )


def _live_bundle_item(parent: AnalysisInput, symbol: str, number: int):
    base = fixture_item(symbol, number, parent)
    plan = TraderPlan(
        ContractMeta(
            SCHEMA_VERSION,
            base.analysis_run_id,
            parent.meta.created_at,
            parent.meta.producer_version,
        ),
        base.trader_plan_id,
        base.analysis_input_id,
        symbol,
        ResearchRating.BUY,
        (ProposalReasonCode.FUNDAMENTAL,),
        base.evidence_refs,
        Decimal("100.00"),
        Decimal("110.00"),
        Decimal("90.00"),
        AnalysisStatus.VALID,
    )
    payload = canonical_wire_json(plan)
    return replace(base, trader_plan_hash=_digest(payload))


def _trader_payload(parent: AnalysisInput, child: Any) -> str:
    return canonical_wire_json(
        TraderPlan(
            ContractMeta(
                SCHEMA_VERSION,
                child.analysis_run_id,
                parent.meta.created_at,
                parent.meta.producer_version,
            ),
            child.trader_plan_id,
            child.analysis_input_id,
            child.symbol,
            ResearchRating.BUY,
            (ProposalReasonCode.FUNDAMENTAL,),
            child.evidence_refs,
            Decimal("100.00"),
            Decimal("110.00"),
            Decimal("90.00"),
            AnalysisStatus.VALID,
        )
    )


def _live_snapshot(parent: AnalysisInput, minutes: int) -> PortfolioSnapshot:
    base = parent.portfolio_snapshot
    return build_portfolio_snapshot(
        as_of=UtcTimestamp(parent.as_of.value + timedelta(minutes=minutes)),
        nav=base.nav,
        cash=base.cash,
        buying_power=base.buying_power,
        positions=base.positions,
        open_orders=base.open_orders,
        same_day_fills=base.same_day_fills,
        borrow_statuses=base.borrow_statuses,
        remaining_limits=base.remaining_limits,
    )


def _live_feedback(
    proposal: Any, parent: AnalysisInput, *, reviewed_minutes: int = 1
) -> RiskRejectionFeedback:
    return RiskRejectionFeedback(
        ContractMeta(
            SCHEMA_VERSION, rid(901), parent.meta.created_at, parent.meta.producer_version
        ),
        proposal.proposal_id,
        1,
        (RiskRejectionCode.TURNOVER,),
        tuple(request.symbol for request in proposal.requests),
        limits(),
        "c" * 64,
        UtcTimestamp(parent.as_of.value + timedelta(minutes=reviewed_minutes)),
    )


def _live_context(
    parent: AnalysisInput,
    built: ResearchBundle,
    *,
    attempt: int = 1,
    snapshot: PortfolioSnapshot | None = None,
    previous_context_id: Any = None,
    superseded_proposal_id: Any = None,
    superseded_proposal_hash: str | None = None,
    feedback: RiskRejectionFeedback | None = None,
) -> ProposalContext:
    chosen_snapshot = snapshot or parent.portfolio_snapshot
    context_id = derive_context_id(
        built.bundle_id,
        attempt,
        chosen_snapshot.content_hash,
        superseded_proposal_id,
        superseded_proposal_hash,
    )
    return build_proposal_context(
        meta=ContractMeta(
            SCHEMA_VERSION,
            context_id,
            parent.meta.created_at,
            parent.meta.producer_version,
        ),
        attempt=attempt,
        bundle=built,
        snapshot=chosen_snapshot,
        allowed_symbols=(*parent.holding_symbols, *parent.candidate_symbols),
        graph_version="graph.1",
        prompt_version="prompt.1",
        model_version="model.1",
        provider_version="provider.1",
        data_version="data.1",
        memory_version="memory.1",
        previous_context_id=previous_context_id,
        superseded_proposal_id=superseded_proposal_id,
        superseded_proposal_hash=superseded_proposal_hash,
        feedback=feedback,
    )


def _debate_payload(context: ProposalContext, bundle: ResearchBundle) -> str:
    return canonical_wire_json(_live_debate(context, bundle))


def _live_debate(context: ProposalContext, bundle: ResearchBundle):
    run_id = derive_proposal_run_id(context.context_id)
    meta = ContractMeta(
        SCHEMA_VERSION, run_id, context.meta.created_at, context.meta.producer_version
    )
    order = (
        (RiskViewpoint.AGGRESSIVE, 1),
        (RiskViewpoint.CONSERVATIVE, 1),
        (RiskViewpoint.NEUTRAL, 1),
        (RiskViewpoint.AGGRESSIVE, 2),
        (RiskViewpoint.CONSERVATIVE, 2),
        (RiskViewpoint.NEUTRAL, 2),
    )
    arguments = tuple(
        RiskArgument(
            meta=meta,
            argument_id=derive_argument_id(context.context_id, viewpoint, round_number),
            context_id=context.context_id,
            bundle_id=bundle.bundle_id,
            bundle_hash=bundle.bundle_hash,
            viewpoint=viewpoint,
            round_number=round_number,
            argument=f"{viewpoint.value} round {round_number}",
            evidence_refs=(bundle.citation_ids[0],),
            producer_version=context.meta.producer_version,
        )
        for viewpoint, round_number in order
    )
    return build_risk_debate(
        meta=meta,
        context_id=context.context_id,
        bundle=bundle,
        arguments=arguments,
    )


def _proposal_payload(context: ProposalContext, bundle: ResearchBundle) -> str:
    proposal = fixture_p3d_proposal(context)
    assert proposal.bundle_id == bundle.bundle_id
    return canonical_wire_json(proposal)


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def _seed_child_analysis_authority(
    database: Any, built: ResearchBundle, parent: AnalysisInput
) -> None:
    """Create the exact P3-C COMPLETE/TRADER rows consumed by one bundle."""

    packet_ids: dict[str, UUID] = {}
    with database.cursor() as cursor:
        for ordinal, child in enumerate(built.items, start=1):
            packet_id = packet_ids.setdefault(child.packet_hash, rid(800 + ordinal).value)
            cursor.execute(
                "SELECT public.register_evidence_packet(%s, %s, %s, %s, %s, %s)",
                (
                    packet_id,
                    child.packet_hash,
                    built.as_of.value,
                    built.universe_hash,
                    child.snapshot_hash,
                    child.producer_version,
                ),
            )
            cursor.execute(
                "SELECT public.create_analysis_run(%s, %s, %s, %s)",
                (
                    child.analysis_run_id.value,
                    child.analysis_input_id.value,
                    child.packet_hash,
                    child.snapshot_hash,
                ),
            )
            expected = "PLANNED"
            trader_payload = _trader_payload(parent, child)
            for stage, payload in (
                ("ANALYSTS", "seed:ANALYSTS"),
                ("DEBATE", "seed:DEBATE"),
                ("RESEARCH", "seed:RESEARCH"),
                ("TRADER", trader_payload),
                ("COMPLETE", "complete"),
            ):
                cursor.execute(
                    "SELECT public.advance_analysis_stage(%s, %s, %s, %s, %s)",
                    (
                        child.analysis_run_id.value,
                        expected,
                        stage,
                        _digest(payload),
                        payload,
                    ),
                )
                expected = stage


def _setup_authority(migrated_postgres: str) -> tuple[Any, ResearchBundle, ProposalContext]:
    parent = _live_parent()
    built = _live_bundle(parent)
    context = _live_context(parent, built)
    database = psycopg.connect(migrated_postgres)
    try:
        _seed_child_analysis_authority(database, built, parent)
        repository = PostgresProposalStateRepository(database)
        repository.register_bundle(built)
        repository.register_context(context)
        repository.create_run(
            str(derive_proposal_run_id(context.context_id)),
            str(context.context_id),
            str(built.bundle_id),
            built.bundle_hash,
        )
        database.commit()
    except Exception:
        database.close()
        raise
    return database, built, context


def _raw_advance(
    database: Any, run_id: str, expected: str, stage: str, digest: str, payload: str
) -> Any:
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT public.advance_proposal_stage(%s, %s, %s, %s, %s)",
            (UUID(run_id), expected, stage, digest, payload),
        )
        return cursor.fetchone()[0]


def _raw_register_bundle(
    database: Any,
    built: ResearchBundle,
    items: list[dict[str, object]],
    wire: dict[str, object],
) -> None:
    without_hash = {key: value for key, value in wire.items() if key != "bundle_hash"}
    bundle_hash = _digest(
        json.dumps(without_hash, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    wire["bundle_hash"] = bundle_hash
    payload = json.dumps(wire, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    database.execute(
        "SELECT public.register_research_bundle(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            built.bundle_id.value,
            built.parent_input_id.value,
            bundle_hash,
            built.as_of.value,
            built.window.value,
            built.deadline.value,
            built.universe_hash,
            built.portfolio_snapshot_hash,
            Jsonb(items),
            _digest(payload),
            payload,
        ),
    )


def test_p3d_sql_identity_golden_vectors_and_duplicate_json_rejection(
    migrated_postgres: str,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    context = _live_context(parent, built)
    with psycopg.connect(migrated_postgres, autocommit=True) as database:
        row = database.execute(
            "SELECT public.p3d_derive_run_id(%s, %s, %s), "
            "public.p3d_derive_run_id(%s, %s, %s, %s, %s, %s)",
            (
                "seven-lens.p3d.child-run.v1",
                str(parent.input_id),
                "MSFT",
                "seven-lens.p3d.context.v1",
                str(built.bundle_id),
                "1",
                parent.portfolio_snapshot.content_hash,
                "",
                "",
            ),
        ).fetchone()
        assert row == (built.items[0].analysis_run_id.value, context.context_id.value)
        with pytest.raises(psycopg.errors.CheckViolation, match="duplicate object keys"):
            database.execute("SELECT public.p3d_canonical_json(%s::json)", ('{"a":1,"a":1}',))


def test_p3d_bundle_registration_is_idempotent_and_bounds_items(migrated_postgres) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with connection(migrated_postgres) as database:
        _seed_child_analysis_authority(database, built, parent)
        repository = PostgresProposalStateRepository(database)
        repository.register_bundle(built)
        repository.register_bundle(built)
        tampered = object.__new__(ResearchBundle)
        for name in ResearchBundle.__slots__:
            object.__setattr__(tampered, name, getattr(built, name))
        object.__setattr__(tampered, "bundle_hash", "0" * 64)
        with pytest.raises(PostgresProposalError, match="integrity is invalid"):
            repository.register_bundle(tampered)
        database.commit()

        with database.cursor() as cursor:
            valid_trader = _trader_payload(parent, built.items[0])
            duplicate_trader = valid_trader.replace(
                '"status":"VALID"',
                '"status":"VALID","status":"VALID"',
                1,
            )
            cursor.execute(
                "UPDATE public.analysis_stage_results SET payload = %s, result_hash = %s "
                "WHERE run_id = %s AND stage = 'TRADER'",
                (
                    duplicate_trader,
                    _digest(duplicate_trader),
                    built.items[0].analysis_run_id.value,
                ),
            )
        forged_items = [
            {"ordinal": ordinal, **child.to_wire()}
            for ordinal, child in enumerate(built.items, start=1)
        ]
        forged_items[0]["trader_plan_hash"] = _digest(duplicate_trader)
        forged_wire = built.to_wire()
        forged_wire["items"][0]["trader_plan_hash"] = _digest(duplicate_trader)
        forged_without_hash = {
            key: value for key, value in forged_wire.items() if key != "bundle_hash"
        }
        forged_bundle_hash = _digest(
            json.dumps(
                forged_without_hash,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        forged_wire["bundle_hash"] = forged_bundle_hash
        forged_payload = json.dumps(
            forged_wire,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match=r"duplicate object keys|not canonical JSON",
        ):
            database.execute(
                "SELECT public.register_research_bundle("
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    built.bundle_id.value,
                    built.parent_input_id.value,
                    forged_bundle_hash,
                    built.as_of.value,
                    built.window.value,
                    built.deadline.value,
                    built.universe_hash,
                    built.portfolio_snapshot_hash,
                    Jsonb(forged_items),
                    _digest(forged_payload),
                    forged_payload,
                ),
            )
        database.rollback()

        items = [
            {"ordinal": ordinal, **child.to_wire()}
            for ordinal, child in enumerate(built.items, start=1)
        ]
        collision_wire = built.to_wire()
        collision_wire["bundle_id"] = str(rid(300))
        collision_wire["parent_input_id"] = str(rid(301))
        collision_wire["bundle_hash"] = "e" * 64
        collision_payload = json.dumps(
            collision_wire, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        with (
            database.cursor() as cursor,
            pytest.raises(psycopg.errors.CheckViolation, match="identity is invalid"),
        ):
            cursor.execute(
                "SELECT public.register_research_bundle("
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    str(rid(300)),
                    str(rid(301)),
                    "e" * 64,
                    built.as_of.value,
                    "PRIMARY",
                    built.deadline.value,
                    built.universe_hash,
                    built.portfolio_snapshot_hash,
                    Jsonb(items),
                    _digest(collision_payload),
                    collision_payload,
                ),
            )
        database.rollback()

        oversized = [
            {
                **items[0],
                "ordinal": index + 1,
                "symbol": f"S{index:02d}",
                "analysis_run_id": str(rid(400 + index)),
            }
            for index in range(28)
        ]
        with (
            database.cursor() as cursor,
            pytest.raises(psycopg.errors.CheckViolation, match="item count"),
        ):
            cursor.execute(
                "SELECT public.register_research_bundle("
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    str(built.bundle_id),
                    str(built.parent_input_id),
                    built.bundle_hash,
                    built.as_of.value,
                    "PRIMARY",
                    built.deadline.value,
                    built.universe_hash,
                    built.portfolio_snapshot_hash,
                    Jsonb(oversized),
                    "f" * 64,
                    collision_payload,
                ),
            )
        database.rollback()


@pytest.mark.parametrize("field", ["as_of", "universe_hash", "producer_version"])
def test_p3d_bundle_revalidates_persisted_evidence_packet_authority(
    migrated_postgres: str,
    field: str,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with connection(migrated_postgres) as database:
        _seed_child_analysis_authority(database, built, parent)
        if field == "as_of":
            value: object = parent.as_of.value - timedelta(microseconds=1)
        elif field == "universe_hash":
            value = "0" * 64
        else:
            value = "foreign.1"
        database.execute(
            sql.SQL("UPDATE public.evidence_packets SET {} = %s WHERE packet_hash = %s").format(
                sql.Identifier(field)
            ),
            (value, built.items[0].packet_hash),
        )
        database.commit()

        with pytest.raises(psycopg.errors.CheckViolation, match="evidence authority"):
            PostgresProposalStateRepository(database).register_bundle(built)
        database.rollback()
        assert (
            database.execute(
                "SELECT count(*) FROM public.research_bundles WHERE bundle_id = %s",
                (built.bundle_id.value,),
            ).fetchone()[0]
            == 0
        )
        assert (
            database.execute(
                "SELECT count(*) FROM public.research_bundle_items WHERE bundle_id = %s",
                (built.bundle_id.value,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("uuid_text", ["not-a-uuid", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"])
def test_p3d_raw_bundle_rejects_noncanonical_uuid_text(
    migrated_postgres: str,
    uuid_text: str,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with connection(migrated_postgres) as database:
        _seed_child_analysis_authority(database, built, parent)
        items = [
            {"ordinal": ordinal, **child.to_wire()}
            for ordinal, child in enumerate(built.items, start=1)
        ]
        wire = built.to_wire()
        items[0]["analysis_run_id"] = uuid_text
        wire["items"][0]["analysis_run_id"] = uuid_text
        with pytest.raises(psycopg.errors.CheckViolation, match="item UUID text is invalid"):
            _raw_register_bundle(database, built, items, wire)
        database.rollback()
        assert (
            database.execute(
                "SELECT count(*) FROM public.research_bundles WHERE bundle_id = %s",
                (built.bundle_id.value,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("variant", ["invalid_complete", "null_rating", "invalid_json"])
def test_p3d_bundle_revalidates_complete_and_trader_contracts(
    migrated_postgres: str,
    variant: str,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with connection(migrated_postgres) as database:
        _seed_child_analysis_authority(database, built, parent)
        child = built.items[0]
        if variant == "invalid_complete":
            database.execute(
                "UPDATE public.analysis_stage_results SET payload = %s, result_hash = %s "
                "WHERE run_id = %s AND stage = 'COMPLETE'",
                ("not-complete", _digest("not-complete"), child.analysis_run_id.value),
            )
            with pytest.raises(psycopg.errors.CheckViolation, match="COMPLETE authority"):
                PostgresProposalStateRepository(database).register_bundle(built)
        else:
            if variant == "null_rating":
                trader_wire = json.loads(_trader_payload(parent, child))
                trader_wire["rating"] = None
                trader_payload = json.dumps(
                    trader_wire,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            else:
                trader_payload = "{not-json"
            trader_hash = _digest(trader_payload)
            database.execute(
                "UPDATE public.analysis_stage_results SET payload = %s, result_hash = %s "
                "WHERE run_id = %s AND stage = 'TRADER'",
                (trader_payload, trader_hash, child.analysis_run_id.value),
            )
            items = [
                {"ordinal": ordinal, **item.to_wire()}
                for ordinal, item in enumerate(built.items, start=1)
            ]
            wire = built.to_wire()
            items[0]["trader_plan_hash"] = trader_hash
            wire["items"][0]["trader_plan_hash"] = trader_hash
            expected = "payload is invalid" if variant == "null_rating" else "not valid JSON"
            with pytest.raises(psycopg.errors.CheckViolation, match=expected):
                _raw_register_bundle(database, built, items, wire)
        database.rollback()
        assert (
            database.execute(
                "SELECT count(*) FROM public.research_bundles WHERE bundle_id = %s",
                (built.bundle_id.value,),
            ).fetchone()[0]
            == 0
        )


def test_p3d_context_and_run_lineage_enforced_by_postgres(migrated_postgres) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with connection(migrated_postgres) as database:
        _seed_child_analysis_authority(database, built, parent)
        repository = PostgresProposalStateRepository(database)
        repository.register_bundle(built)
        first = _live_context(parent, built)
        repository.register_context(first)
        repository.register_context(first)
        database.commit()

        with pytest.raises(psycopg.errors.CheckViolation, match="frozen bundle snapshot"):
            repository.register_context(
                _live_context(parent, built, snapshot=_live_snapshot(parent, 1))
            )
        database.rollback()

        orphan_retry = _live_context(
            parent,
            built,
            attempt=2,
            superseded_proposal_id=rid(11),
            superseded_proposal_hash="d" * 64,
            previous_context_id=first.context_id,
            feedback=RiskRejectionFeedback(
                ContractMeta(
                    SCHEMA_VERSION,
                    rid(902),
                    parent.meta.created_at,
                    parent.meta.producer_version,
                ),
                rid(11),
                1,
                (RiskRejectionCode.TURNOVER,),
                ("MSFT",),
                limits(),
                "c" * 64,
                UtcTimestamp(parent.as_of.value + timedelta(minutes=1)),
            ),
            snapshot=_live_snapshot(parent, 2),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="supersedes an unknown"):
            repository.register_context(orphan_retry)
        database.rollback()

        run_id = str(derive_proposal_run_id(first.context_id))
        repository.create_run(
            run_id, str(first.context_id), str(built.bundle_id), built.bundle_hash
        )
        repository.create_run(
            run_id, str(first.context_id), str(built.bundle_id), built.bundle_hash
        )
        database.commit()
        with pytest.raises(psycopg.errors.CheckViolation, match="run identity is invalid"):
            repository.create_run(
                str(rid(21)), str(first.context_id), str(built.bundle_id), built.bundle_hash
            )
        database.rollback()
        with pytest.raises(psycopg.errors.ForeignKeyViolation, match="unavailable"):
            repository.create_run(run_id, str(first.context_id), str(built.bundle_id), "0" * 64)
        database.rollback()
        with pytest.raises(psycopg.errors.ForeignKeyViolation, match="unavailable"):
            repository.create_run(
                str(rid(22)), str(rid(23)), str(built.bundle_id), built.bundle_hash
            )
        database.rollback()
        assert repository.current_stage(run_id) is ProposalStage.PLANNED


@pytest.mark.parametrize(
    "variant",
    [
        "flat_position",
        "float_remaining_slots",
        "future_as_of",
        "retrograde_as_of",
        "secret_order_ref",
        "secret_fill_ref",
        "attempt_secret_text",
    ],
)
def test_p3d_raw_context_rejects_hash_consistent_malformed_nested_snapshot(
    migrated_postgres: str,
    variant: str,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with connection(migrated_postgres) as database:
        _seed_child_analysis_authority(database, built, parent)
        repository = PostgresProposalStateRepository(database)
        first = ProposalPipeline(ProposalFakeProvider(), repository, versions(), now=_clock()).run(
            built, parent
        )
        feedback = _live_feedback(first, parent)
        repository.register_feedback(feedback)
        database.commit()

        first_context = _live_context(parent, built)
        superseded_hash = _digest(canonical_wire_json(first))
        valid_context = _live_context(
            parent,
            built,
            attempt=2,
            snapshot=_live_snapshot(parent, 2),
            previous_context_id=first_context.context_id,
            superseded_proposal_id=first.proposal_id,
            superseded_proposal_hash=superseded_hash,
            feedback=feedback,
        )
        wire = valid_context.to_wire()
        snapshot_wire = wire["snapshot"]
        if variant == "flat_position":
            snapshot_wire["positions"][0]["side"] = "FLAT"
        elif variant == "float_remaining_slots":
            snapshot_wire["remaining_limits"]["remaining_slots"] = 13.0
        elif variant == "future_as_of":
            snapshot_wire["as_of"] = (datetime.now(UTC) + timedelta(seconds=60)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
        elif variant == "retrograde_as_of":
            snapshot_wire["as_of"] = (parent.as_of.value - timedelta(microseconds=1)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
        elif variant == "secret_order_ref":
            snapshot_wire["open_orders"][0]["reference_id"] = "api_key:abc"
        elif variant == "secret_fill_ref":
            snapshot_wire["same_day_fills"][0]["reference_id"] = "credential:abc"
        snapshot_without_hash = {
            key: value for key, value in snapshot_wire.items() if key != "content_hash"
        }
        snapshot_hash = _digest(
            json.dumps(
                snapshot_without_hash,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        snapshot_wire["content_hash"] = snapshot_hash
        wire["snapshot_hash"] = snapshot_hash
        context_id = derive_context_id(
            built.bundle_id,
            2,
            snapshot_hash,
            first.proposal_id,
            superseded_hash,
        )
        wire["context_id"] = str(context_id)
        wire["meta"]["run_id"] = str(context_id)
        if variant == "attempt_secret_text":
            wire["attempt"] = "authorization: secret"
        context_without_hash = {key: value for key, value in wire.items() if key != "context_hash"}
        context_hash = _digest(
            json.dumps(
                context_without_hash,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        wire["context_hash"] = context_hash
        payload = json.dumps(
            wire,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match=r"snapshot is malformed|does not match the research bundle|attempt is invalid",
        ) as captured:
            database.execute(
                "SELECT public.register_proposal_context("
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    context_id.value,
                    built.bundle_id.value,
                    2,
                    snapshot_hash,
                    first_context.context_id.value,
                    first.proposal_id.value,
                    superseded_hash,
                    feedback.meta.run_id.value,
                    context_hash,
                    _digest(payload),
                    payload,
                ),
            )
        if variant == "attempt_secret_text":
            assert "authorization" not in str(captured.value).lower()
            assert "secret" not in str(captured.value).lower()
        database.rollback()
        assert (
            database.execute(
                "SELECT count(*) FROM public.proposal_contexts "
                "WHERE bundle_id = %s AND attempt = 2",
                (built.bundle_id.value,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("variant", ["float_remaining_slots", "retrograde_review"])
def test_p3d_raw_feedback_rejects_contract_drift_with_zero_authority(
    migrated_postgres: str,
    variant: str,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with connection(migrated_postgres) as database:
        _seed_child_analysis_authority(database, built, parent)
        repository = PostgresProposalStateRepository(database)
        first = ProposalPipeline(ProposalFakeProvider(), repository, versions(), now=_clock()).run(
            built, parent
        )
        database.commit()

        feedback = _live_feedback(first, parent)
        wire = feedback.to_wire()
        if variant == "float_remaining_slots":
            wire["remaining_limits"]["remaining_slots"] = 13.0
        else:
            wire["reviewed_at"] = str(parent.meta.created_at)
        payload = json.dumps(
            wire,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            database.execute(
                "SELECT public.register_risk_feedback(%s, %s, %s, %s)",
                (
                    feedback.meta.run_id.value,
                    first.proposal_id.value,
                    _digest(payload),
                    payload,
                ),
            )
        database.rollback()
        assert (
            database.execute(
                "SELECT count(*) FROM public.risk_rejection_feedback WHERE feedback_id = %s",
                (feedback.meta.run_id.value,),
            ).fetchone()[0]
            == 0
        )


def test_p3d_full_initial_retry_and_third_proposal_never_becomes_authority(
    migrated_postgres,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with connection(migrated_postgres) as database:
        _seed_child_analysis_authority(database, built, parent)
        repository = PostgresProposalStateRepository(database)
        provider = ProposalFakeProvider()
        pipeline = ProposalPipeline(provider, repository, versions(), now=_clock())
        first = pipeline.run(built, parent)
        database.commit()

        replay_provider = ProposalFakeProvider()
        replay = ProposalPipeline(replay_provider, repository, versions(), now=_clock())
        assert replay.run(built, parent) == first
        assert replay_provider.calls == []
        database.commit()

        retry_provider = ProposalFakeProvider()
        retry = ProposalPipeline(retry_provider, repository, versions(), now=_clock())
        refreshed = _live_snapshot(parent, 2)
        feedback = _live_feedback(first, parent)
        second = retry.retry(built, parent, refreshed, feedback, first)
        database.commit()
        assert retry_provider.calls == ["PORTFOLIO_MANAGER_RETRY:"]
        assert second.attempt == 2
        assert second.superseded_proposal_id == first.proposal_id

        same_replay = ProposalPipeline(ProposalFakeProvider(), repository, versions(), now=_clock())
        assert same_replay.retry(built, parent, refreshed, feedback, first) == second
        database.commit()

        third = ProposalPipeline(ProposalFakeProvider(), repository, versions(), now=_clock())
        # The fast-fail gate rejects the third attempt before any new authority
        # row is written, so no rollback is needed to keep the database clean.
        with pytest.raises(ProposalPipelineError, match="already has an attempt 2"):
            third.retry(
                built,
                parent,
                _live_snapshot(parent, 3),
                _live_feedback(first, parent),
                first,
            )
        database.commit()

        first_run = str(
            derive_proposal_run_id(
                derive_context_id(built.bundle_id, 1, parent.portfolio_snapshot.content_hash, None)
            )
        )
        second_run = str(
            derive_proposal_run_id(
                derive_context_id(
                    built.bundle_id,
                    2,
                    refreshed.content_hash,
                    first.proposal_id,
                    _digest(canonical_wire_json(first)),
                )
            )
        )
        third_run = str(
            derive_proposal_run_id(
                derive_context_id(
                    built.bundle_id,
                    2,
                    _live_snapshot(parent, 3).content_hash,
                    first.proposal_id,
                    _digest(canonical_wire_json(first)),
                )
            )
        )
        assert repository.current_stage(first_run) is ProposalStage.COMPLETE
        assert repository.current_stage(second_run) is ProposalStage.COMPLETE
        with pytest.raises(PostgresProposalError, match="unavailable"):
            repository.current_stage(third_run)
        with database.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.portfolio_proposals WHERE bundle_id = %s",
                (built.bundle_id.value,),
            )
            assert cursor.fetchone()[0] == 2


def test_p3d_two_attempt_two_contexts_linearize_without_orphans(
    migrated_postgres,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with connection(migrated_postgres) as database:
        _seed_child_analysis_authority(database, built, parent)
        repository = PostgresProposalStateRepository(database)
        first = ProposalPipeline(ProposalFakeProvider(), repository, versions(), now=_clock()).run(
            built, parent
        )
        feedback = _live_feedback(first, parent)
        repository.register_feedback(feedback)
        database.commit()

    first_context = _live_context(parent, built)
    superseded_hash = _digest(canonical_wire_json(first))
    contexts = tuple(
        _live_context(
            parent,
            built,
            attempt=2,
            snapshot=_live_snapshot(parent, minutes),
            previous_context_id=first_context.context_id,
            superseded_proposal_id=first.proposal_id,
            superseded_proposal_hash=superseded_hash,
            feedback=feedback,
        )
        for minutes in (2, 3)
    )
    barrier = Barrier(2)

    def register(context: ProposalContext) -> str:
        with psycopg.connect(migrated_postgres) as worker:
            barrier.wait()
            try:
                PostgresProposalStateRepository(worker).register_context(context)
                worker.commit()
                return "ok"
            except psycopg.Error as error:
                worker.rollback()
                return str(error.sqlstate)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(register, contexts))
    assert sorted(outcomes) == ["23505", "ok"]
    with psycopg.connect(migrated_postgres, autocommit=True) as authority:
        assert (
            authority.execute(
                "SELECT count(*) FROM public.proposal_contexts "
                "WHERE bundle_id = %s AND attempt = 2",
                (built.bundle_id.value,),
            ).fetchone()[0]
            == 1
        )
        assert (
            authority.execute(
                "SELECT count(*) FROM public.proposal_runs AS run "
                "JOIN public.proposal_contexts AS context ON context.context_id = run.context_id "
                "WHERE context.bundle_id = %s AND context.attempt = 2",
                (built.bundle_id.value,),
            ).fetchone()[0]
            == 0
        )
        assert (
            authority.execute(
                "SELECT count(*) FROM public.risk_rejection_feedback WHERE feedback_id = %s",
                (feedback.meta.run_id.value,),
            ).fetchone()[0]
            == 1
        )


def test_p3d_transition_whitelist_and_payload_rules(migrated_postgres) -> None:
    database, built, context = _setup_authority(migrated_postgres)
    run_id = str(derive_proposal_run_id(context.context_id))
    try:
        for expected, stage in (
            ("PLANNED", "PROPOSAL"),
            ("PLANNED", "COMPLETE"),
            ("RISK_DEBATE", "RISK_DEBATE"),
            ("COMPLETE", "RISK_DEBATE"),
        ):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="not legal"):
                _raw_advance(database, run_id, expected, stage, "f" * 64, "abuse")
            database.rollback()

        debate = _debate_payload(context, built)
        duplicate_debate = (
            '{"meta":'
            + json.dumps(
                json.loads(debate)["meta"],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + ","
            + debate[1:]
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="duplicate object keys"):
            _raw_advance(
                database,
                run_id,
                "PLANNED",
                "RISK_DEBATE",
                _digest(duplicate_debate),
                duplicate_debate,
            )
        database.rollback()
        assert (
            PostgresProposalStateRepository(database).current_stage(run_id) is ProposalStage.PLANNED
        )
        assert _raw_advance(database, run_id, "PLANNED", "RISK_DEBATE", _digest(debate), debate)
        database.commit()
        with pytest.raises(psycopg.errors.CheckViolation, match="does not match payload"):
            _raw_advance(database, run_id, "PLANNED", "RISK_DEBATE", "0" * 64, debate)
        database.rollback()

        with pytest.raises(psycopg.errors.CheckViolation, match="payload is not valid JSON"):
            _raw_advance(
                database,
                run_id,
                "RISK_DEBATE",
                "PROPOSAL",
                _digest("not-json"),
                "not-json",
            )
        database.rollback()

        string_attempt = json.loads(_proposal_payload(context, built))
        string_attempt["attempt"] = "1"
        string_attempt_payload = json.dumps(
            string_attempt, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="payload is malformed"):
            _raw_advance(
                database,
                run_id,
                "RISK_DEBATE",
                "PROPOSAL",
                _digest(string_attempt_payload),
                string_attempt_payload,
            )
        database.rollback()

        foreign = json.loads(_proposal_payload(context, built))
        foreign["context_id"] = str(rid(41))
        foreign_payload = json.dumps(
            foreign, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="proposal identity is invalid"):
            _raw_advance(
                database,
                run_id,
                "RISK_DEBATE",
                "PROPOSAL",
                _digest(foreign_payload),
                foreign_payload,
            )
        database.rollback()

        proposal = _proposal_payload(context, built)
        assert _raw_advance(
            database, run_id, "RISK_DEBATE", "PROPOSAL", _digest(proposal), proposal
        )
        assert _raw_advance(
            database, run_id, "PROPOSAL", "COMPLETE", _digest("complete"), "complete"
        )
        database.commit()
        with pytest.raises(psycopg.errors.CheckViolation, match="immutable result changed"):
            _raw_advance(database, run_id, "PROPOSAL", "COMPLETE", _digest("again"), "again")
        database.rollback()
        with database.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.portfolio_proposals WHERE bundle_id = %s",
                (built.bundle_id.value,),
            )
            assert cursor.fetchone()[0] == 1
    finally:
        database.close()


def test_p3d_same_hash_retry_budget_in_postgres(migrated_postgres) -> None:
    database, built, context = _setup_authority(migrated_postgres)
    try:
        run_id = str(derive_proposal_run_id(context.context_id))
        debate = _debate_payload(context, built)
        assert _raw_advance(database, run_id, "PLANNED", "RISK_DEBATE", _digest(debate), debate)
        for _ in range(7):
            assert (
                _raw_advance(database, run_id, "PLANNED", "RISK_DEBATE", _digest(debate), debate)
                is False
            )
            database.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            _raw_advance(database, run_id, "PLANNED", "RISK_DEBATE", _digest(debate), debate)
        database.rollback()
    finally:
        database.close()


def test_p3d_owner_cannot_rewrite_append_only_or_stage_identity(migrated_postgres) -> None:
    database, built, context = _setup_authority(migrated_postgres)
    try:
        run_id = str(derive_proposal_run_id(context.context_id))
        debate = _debate_payload(context, built)
        assert _raw_advance(database, run_id, "PLANNED", "RISK_DEBATE", _digest(debate), debate)
        database.commit()

        with (
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="append-only"),
            database.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE public.research_bundles SET bundle_hash = %s WHERE bundle_id = %s",
                ("0" * 64, built.bundle_id.value),
            )
        database.rollback()

        with (
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="not legal"),
            database.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE public.proposal_runs SET current_stage = 'COMPLETE' WHERE run_id = %s",
                (UUID(run_id),),
            )
        database.rollback()

        with (
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="not legal"),
            database.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE public.proposal_stage_results SET result_hash = %s "
                "WHERE run_id = %s AND stage = 'RISK_DEBATE'",
                ("0" * 64, UUID(run_id)),
            )
        database.rollback()
        with database.cursor() as cursor:
            cursor.execute(
                "SELECT result_hash, attempt FROM public.proposal_stage_results "
                "WHERE run_id = %s AND stage = 'RISK_DEBATE'",
                (UUID(run_id),),
            )
            assert cursor.fetchone() == (_digest(debate), 1)
    finally:
        database.close()


def test_p3d_terminal_sink_is_enforced_in_postgres(migrated_postgres) -> None:
    database, _, context = _setup_authority(migrated_postgres)
    try:
        run_id = str(derive_proposal_run_id(context.context_id))
        assert _raw_advance(database, run_id, "PLANNED", "INVALID", _digest("invalid"), "invalid")
        database.commit()
        for target in ("RISK_DEBATE", "PROPOSAL", "COMPLETE"):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="not legal"):
                _raw_advance(database, run_id, "INVALID", target, _digest("revive"), "revive")
            database.rollback()
    finally:
        database.close()


@pytest.mark.parametrize(
    "variant", ["null_expiry", "invalid_uuid", "uppercase_uuid", "negative_zero"]
)
def test_p3d_raw_proposal_rejects_noncanonical_wire_values(
    migrated_postgres: str,
    variant: str,
) -> None:
    database, built, context = _setup_authority(migrated_postgres)
    run_id = str(derive_proposal_run_id(context.context_id))
    try:
        debate = _debate_payload(context, built)
        assert _raw_advance(
            database,
            run_id,
            "PLANNED",
            "RISK_DEBATE",
            _digest(debate),
            debate,
        )
        database.commit()
        wire = json.loads(_proposal_payload(context, built))
        if variant == "null_expiry":
            wire["expiration_at"] = None
            expected = "payload is malformed"
        elif variant == "invalid_uuid":
            wire["proposal_id"] = "not-a-uuid"
            expected = "UUID text is invalid"
        elif variant == "uppercase_uuid":
            wire["proposal_id"] = wire["proposal_id"].upper()
            expected = "UUID text is invalid"
        else:
            wire["requests"][0]["target_weight"] = "-0.000000"
            expected = "proposal request is outside the frozen context"
        payload = json.dumps(wire, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with pytest.raises(psycopg.errors.CheckViolation, match=expected):
            _raw_advance(
                database,
                run_id,
                "RISK_DEBATE",
                "PROPOSAL",
                _digest(payload),
                payload,
            )
        database.rollback()
        repository = PostgresProposalStateRepository(database)
        assert repository.current_stage(run_id) is ProposalStage.RISK_DEBATE
        assert repository.load(run_id, ProposalStage.PROPOSAL) is None
        assert (
            database.execute("SELECT count(*) FROM public.portfolio_proposals").fetchone()[0] == 0
        )
    finally:
        database.close()


@pytest.mark.parametrize("uuid_text", ["not-a-uuid", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"])
def test_p3d_raw_debate_rejects_noncanonical_uuid_text(
    migrated_postgres: str,
    uuid_text: str,
) -> None:
    database, built, context = _setup_authority(migrated_postgres)
    run_id = str(derive_proposal_run_id(context.context_id))
    try:
        wire = json.loads(_debate_payload(context, built))
        wire["debate_id"] = uuid_text
        without_hash = {key: value for key, value in wire.items() if key != "debate_hash"}
        wire["debate_hash"] = _digest(
            json.dumps(
                without_hash,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        payload = json.dumps(wire, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with pytest.raises(psycopg.errors.CheckViolation, match="UUID text is invalid"):
            _raw_advance(
                database,
                run_id,
                "PLANNED",
                "RISK_DEBATE",
                _digest(payload),
                payload,
            )
        database.rollback()
        repository = PostgresProposalStateRepository(database)
        assert repository.current_stage(run_id) is ProposalStage.PLANNED
        assert repository.load(run_id, ProposalStage.RISK_DEBATE) is None
        assert database.execute("SELECT count(*) FROM public.risk_debates").fetchone()[0] == 0
    finally:
        database.close()


def test_p3d_database_deadline_blocks_bundle_and_late_stage(migrated_postgres) -> None:
    with connection(migrated_postgres) as database:
        expired_parent = _live_parent(deadline_seconds=-1)
        expired_bundle = _live_bundle(expired_parent)
        _seed_child_analysis_authority(database, expired_bundle, expired_parent)
        with pytest.raises(psycopg.errors.QueryCanceled, match="deadline expired"):
            PostgresProposalStateRepository(database).register_bundle(expired_bundle)
        database.rollback()
        with database.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.research_bundles WHERE bundle_id = %s",
                (expired_bundle.bundle_id.value,),
            )
            assert cursor.fetchone()[0] == 0

    parent = _live_parent(deadline_seconds=3)
    built = _live_bundle(parent)
    context = _live_context(parent, built)
    with connection(migrated_postgres) as database:
        _seed_child_analysis_authority(database, built, parent)
        repository = PostgresProposalStateRepository(database)
        repository.register_bundle(built)
        repository.register_context(context)
        run_id = str(derive_proposal_run_id(context.context_id))
        repository.create_run(
            run_id, str(context.context_id), str(built.bundle_id), built.bundle_hash
        )
        database.commit()
        remaining = max(
            0.0,
            (built.deadline.value - datetime.now(UTC)).total_seconds() + 0.2,
        )
        with database.cursor() as cursor:
            cursor.execute("SELECT pg_catalog.pg_sleep(%s)", (remaining,))
        debate = _debate_payload(context, built)
        with pytest.raises(psycopg.errors.QueryCanceled, match="deadline expired"):
            _raw_advance(
                database,
                run_id,
                "PLANNED",
                "RISK_DEBATE",
                _digest(debate),
                debate,
            )
        database.rollback()
        assert repository.current_stage(run_id) is ProposalStage.PLANNED
        assert repository.load(run_id, ProposalStage.RISK_DEBATE) is None


def test_p3d_concurrent_different_debate_results_leave_one_authority(
    migrated_postgres,
) -> None:
    database, built, context = _setup_authority(migrated_postgres)
    database.close()
    run_id = str(derive_proposal_run_id(context.context_id))
    barrier = Barrier(2)

    first_debate = _live_debate(context, built)
    changed_argument = replace(first_debate.arguments[0], argument="changed but valid")
    second_debate = build_risk_debate(
        meta=first_debate.meta,
        context_id=context.context_id,
        bundle=built,
        arguments=(changed_argument, *first_debate.arguments[1:]),
    )
    payloads = (canonical_wire_json(first_debate), canonical_wire_json(second_debate))

    def advance(payload: str) -> str:
        with psycopg.connect(migrated_postgres) as worker:
            barrier.wait()
            try:
                _raw_advance(worker, run_id, "PLANNED", "RISK_DEBATE", _digest(payload), payload)
            except psycopg.Error as error:
                worker.rollback()
                return str(error.sqlstate)
            worker.commit()
            return "ok"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(advance, payloads))
    assert sorted(outcomes) == ["23514", "ok"]
    with psycopg.connect(migrated_postgres, autocommit=True) as authority:
        rows = authority.execute(
            "SELECT result_hash FROM public.proposal_stage_results "
            "WHERE run_id = %s AND stage = 'RISK_DEBATE'",
            (UUID(run_id),),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] in {_digest(payload) for payload in payloads}
        debates = authority.execute("SELECT count(*) FROM public.risk_debates").fetchone()[0]
        assert debates == 1


def test_p3d_concurrent_same_debate_is_idempotent(migrated_postgres: str) -> None:
    database, built, context = _setup_authority(migrated_postgres)
    database.close()
    run_id = str(derive_proposal_run_id(context.context_id))
    payload = _debate_payload(context, built)
    barrier = Barrier(2)

    def advance(_: int) -> str:
        with psycopg.connect(migrated_postgres) as worker:
            barrier.wait()
            inserted = _raw_advance(
                worker,
                run_id,
                "PLANNED",
                "RISK_DEBATE",
                _digest(payload),
                payload,
            )
            worker.commit()
            return str(inserted).lower()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(advance, (1, 2)))
    assert sorted(outcomes) == ["false", "true"]
    with psycopg.connect(migrated_postgres, autocommit=True) as authority:
        assert (
            authority.execute(
                "SELECT attempt FROM public.proposal_stage_results "
                "WHERE run_id = %s AND stage = 'RISK_DEBATE'",
                (UUID(run_id),),
            ).fetchone()[0]
            == 2
        )
        assert authority.execute("SELECT count(*) FROM public.risk_debates").fetchone()[0] == 1


def test_p3d_concurrent_duplicate_bundle_and_children_are_idempotent(
    migrated_postgres: str,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with psycopg.connect(migrated_postgres) as seed:
        _seed_child_analysis_authority(seed, built, parent)
        seed.commit()
    barrier = Barrier(2)

    def register(_: int) -> str:
        with psycopg.connect(migrated_postgres) as worker:
            barrier.wait()
            PostgresProposalStateRepository(worker).register_bundle(built)
            worker.commit()
            return "ok"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(register, (1, 2)))
    assert outcomes == ["ok", "ok"]
    with psycopg.connect(migrated_postgres, autocommit=True) as authority:
        assert (
            authority.execute(
                "SELECT count(*) FROM public.research_bundles WHERE bundle_id = %s",
                (built.bundle_id.value,),
            ).fetchone()[0]
            == 1
        )
        assert authority.execute(
            "SELECT count(*) FROM public.research_bundle_items WHERE bundle_id = %s",
            (built.bundle_id.value,),
        ).fetchone()[0] == len(built.items)


def test_p3d_bundle_lock_wait_rechecks_wall_clock_deadline(migrated_postgres: str) -> None:
    parent = _live_parent(deadline_seconds=2)
    built = _live_bundle(parent)
    with psycopg.connect(migrated_postgres) as seed:
        _seed_child_analysis_authority(seed, built, parent)
        seed.commit()
    barrier = Barrier(2)

    def register() -> str:
        with psycopg.connect(migrated_postgres) as worker:
            barrier.wait()
            try:
                PostgresProposalStateRepository(worker).register_bundle(built)
                worker.commit()
                return "ok"
            except psycopg.Error as error:
                worker.rollback()
                return str(error.sqlstate)

    with psycopg.connect(migrated_postgres) as blocker:
        blocker.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s, 0))",
            (str(parent.input_id),),
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(register)
            barrier.wait()
            remaining = max(0.0, (built.deadline.value - datetime.now(UTC)).total_seconds() + 0.2)
            blocker.execute("SELECT pg_catalog.pg_sleep(%s)", (remaining,))
            blocker.commit()
            assert future.result() == "57014"
    with psycopg.connect(migrated_postgres, autocommit=True) as authority:
        assert (
            authority.execute(
                "SELECT count(*) FROM public.research_bundles WHERE bundle_id = %s",
                (built.bundle_id.value,),
            ).fetchone()[0]
            == 0
        )
        assert (
            authority.execute(
                "SELECT count(*) FROM public.research_bundle_items WHERE bundle_id = %s",
                (built.bundle_id.value,),
            ).fetchone()[0]
            == 0
        )


def test_p3d_concurrent_complete_and_invalid_terminal_transitions_linearize(
    migrated_postgres: str,
) -> None:
    database, built, context = _setup_authority(migrated_postgres)
    run_id = str(derive_proposal_run_id(context.context_id))
    debate = _debate_payload(context, built)
    proposal = _proposal_payload(context, built)
    assert _raw_advance(database, run_id, "PLANNED", "RISK_DEBATE", _digest(debate), debate)
    assert _raw_advance(database, run_id, "RISK_DEBATE", "PROPOSAL", _digest(proposal), proposal)
    database.commit()
    database.close()
    barrier = Barrier(2)

    def finish(stage: str) -> str:
        payload = stage.lower()
        with psycopg.connect(migrated_postgres) as worker:
            barrier.wait()
            try:
                _raw_advance(
                    worker,
                    run_id,
                    "PROPOSAL",
                    stage,
                    _digest(payload),
                    payload,
                )
                worker.commit()
                return "ok"
            except psycopg.Error as error:
                worker.rollback()
                return str(error.sqlstate)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(finish, ("COMPLETE", "INVALID")))
    assert sorted(outcomes) == ["55000", "ok"]
    with psycopg.connect(migrated_postgres, autocommit=True) as authority:
        final_stage = authority.execute(
            "SELECT current_stage FROM public.proposal_runs WHERE run_id = %s",
            (UUID(run_id),),
        ).fetchone()[0]
        assert final_stage in {"COMPLETE", "INVALID"}
        assert (
            authority.execute(
                "SELECT count(*) FROM public.proposal_stage_results "
                "WHERE run_id = %s AND stage IN ('COMPLETE', 'INVALID')",
                (UUID(run_id),),
            ).fetchone()[0]
            == 1
        )
        assert (
            authority.execute("SELECT count(*) FROM public.portfolio_proposals").fetchone()[0] == 1
        )


def test_p3d_two_concurrent_attempt_two_pipelines_leave_one_proposal_authority(
    migrated_postgres: str,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    with psycopg.connect(migrated_postgres) as seed:
        _seed_child_analysis_authority(seed, built, parent)
        repository = PostgresProposalStateRepository(seed)
        first = ProposalPipeline(ProposalFakeProvider(), repository, versions(), now=_clock()).run(
            built, parent
        )
        feedback = _live_feedback(first, parent)
        repository.register_feedback(feedback)
        seed.commit()
    refreshed = _live_snapshot(parent, 2)
    barrier = Barrier(2)

    def retry(_: int) -> str:
        with psycopg.connect(migrated_postgres) as worker:
            barrier.wait()
            proposal = ProposalPipeline(
                ProposalFakeProvider(),
                PostgresProposalStateRepository(worker),
                versions(),
                now=_clock(),
            ).retry(built, parent, refreshed, feedback, first)
            worker.commit()
            return _digest(canonical_wire_json(proposal))

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(retry, (1, 2)))
    assert len(set(outcomes)) == 1
    context_id = derive_context_id(
        built.bundle_id,
        2,
        refreshed.content_hash,
        first.proposal_id,
        _digest(canonical_wire_json(first)),
    )
    run_id = derive_proposal_run_id(context_id)
    with psycopg.connect(migrated_postgres, autocommit=True) as authority:
        assert (
            authority.execute(
                "SELECT count(*) FROM public.proposal_contexts WHERE context_id = %s",
                (context_id.value,),
            ).fetchone()[0]
            == 1
        )
        assert (
            authority.execute(
                "SELECT count(*) FROM public.proposal_runs WHERE run_id = %s",
                (run_id.value,),
            ).fetchone()[0]
            == 1
        )
        assert (
            authority.execute(
                "SELECT count(*) FROM public.portfolio_proposals WHERE context_id = %s",
                (context_id.value,),
            ).fetchone()[0]
            == 1
        )
        rows = authority.execute(
            "SELECT stage, attempt FROM public.proposal_stage_results "
            "WHERE run_id = %s ORDER BY stage",
            (run_id.value,),
        ).fetchall()
        assert rows == [("COMPLETE", 1), ("PROPOSAL", 1), ("RISK_DEBATE", 1)]


def test_p3d_two_different_attempt_two_proposals_linearize_without_orphans(
    migrated_postgres: str,
) -> None:
    parent = _live_parent()
    built = _live_bundle(parent)
    refreshed = _live_snapshot(parent, 2)
    with psycopg.connect(migrated_postgres) as seed:
        _seed_child_analysis_authority(seed, built, parent)
        repository = PostgresProposalStateRepository(seed)
        first = ProposalPipeline(ProposalFakeProvider(), repository, versions(), now=_clock()).run(
            built, parent
        )
        feedback = _live_feedback(first, parent)
        repository.register_feedback(feedback)
        first_context = _live_context(parent, built)
        context = _live_context(
            parent,
            built,
            attempt=2,
            snapshot=refreshed,
            previous_context_id=first_context.context_id,
            superseded_proposal_id=first.proposal_id,
            superseded_proposal_hash=_digest(canonical_wire_json(first)),
            feedback=feedback,
        )
        repository.register_context(context)
        run_id = derive_proposal_run_id(context.context_id)
        repository.create_run(
            str(run_id), str(context.context_id), str(built.bundle_id), built.bundle_hash
        )
        inherited_debate = _debate_payload(first_context, built)
        assert _raw_advance(
            seed,
            str(run_id),
            "PLANNED",
            "RISK_DEBATE",
            _digest(inherited_debate),
            inherited_debate,
        )
        seed.commit()

    first_payload = _proposal_payload(context, built)
    changed_wire = json.loads(first_payload)
    changed_wire["requests"][0]["confidence"] = "0.9100"
    second_payload = json.dumps(
        changed_wire,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    payloads = (first_payload, second_payload)
    barrier = Barrier(2)

    def propose(payload: str) -> str:
        with psycopg.connect(migrated_postgres) as worker:
            barrier.wait()
            try:
                _raw_advance(
                    worker,
                    str(run_id),
                    "RISK_DEBATE",
                    "PROPOSAL",
                    _digest(payload),
                    payload,
                )
                worker.commit()
                return "ok"
            except psycopg.Error as error:
                worker.rollback()
                return str(error.sqlstate)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(propose, payloads))
    assert sorted(outcomes) == ["23514", "ok"]
    with psycopg.connect(migrated_postgres, autocommit=True) as authority:
        result_hash = authority.execute(
            "SELECT result_hash FROM public.proposal_stage_results "
            "WHERE run_id = %s AND stage = 'PROPOSAL'",
            (run_id.value,),
        ).fetchone()[0]
        assert result_hash in {_digest(payload) for payload in payloads}
        assert (
            authority.execute(
                "SELECT count(*) FROM public.portfolio_proposals "
                "WHERE context_id = %s AND attempt = 2",
                (context.context_id.value,),
            ).fetchone()[0]
            == 1
        )
        assert (
            authority.execute(
                "SELECT current_stage FROM public.proposal_runs WHERE run_id = %s",
                (run_id.value,),
            ).fetchone()[0]
            == "PROPOSAL"
        )
        assert (
            authority.execute(
                "SELECT count(*) FROM public.portfolio_proposals AS proposal "
                "LEFT JOIN public.proposal_runs AS run ON run.context_id = proposal.context_id "
                "LEFT JOIN public.proposal_stage_results AS result "
                "ON result.run_id = run.run_id AND result.stage = 'PROPOSAL' "
                "WHERE run.run_id IS NULL OR result.run_id IS NULL"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.usefixtures("runtime_postgres")
def test_p3d_runtime_role_cannot_mutate_proposal_authority(
    request: pytest.FixtureRequest,
) -> None:
    runtime_dsn = request.getfixturevalue("runtime_postgres")[0]
    with connection(runtime_dsn) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), runtime.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.proposal_stage_results "
                "(run_id, stage, result_hash, payload) VALUES (%s, %s, %s, %s)",
                ("00000000-0000-4000-8000-000000000001", "RISK_DEBATE", "a" * 64, "x"),
            )
        runtime.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege), runtime.cursor() as cursor:
            cursor.execute("UPDATE public.proposal_runs SET current_stage = 'COMPLETE'")
        runtime.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege), runtime.cursor() as cursor:
            cursor.execute("DELETE FROM public.portfolio_proposals")
        runtime.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege), runtime.cursor() as cursor:
            cursor.execute(
                "CREATE OR REPLACE FUNCTION public.advance_proposal_stage("
                "uuid, text, text, text, text) RETURNS boolean "
                "LANGUAGE sql AS 'SELECT true'"
            )
        runtime.rollback()


@pytest.mark.usefixtures("runtime_postgres")
def test_p3d_role_verifier_detects_proposal_table_privilege_drift(
    migrated_postgres: str,
    request: pytest.FixtureRequest,
) -> None:
    evidence = request.getfixturevalue("runtime_postgres")[1]
    assert evidence.runtime_role == "seven_lens_runtime_test"
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        owner.execute("GRANT INSERT ON public.portfolio_proposals TO seven_lens_runtime_test")
        try:
            with pytest.raises(PostgresRoleError, match="P3 table privileges"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(
                "REVOKE INSERT ON public.portfolio_proposals FROM seven_lens_runtime_test"
            )
        owner.execute("GRANT UPDATE ON public.portfolio_proposals TO seven_lens_runtime_test")
        try:
            with pytest.raises(PostgresRoleError, match="P3 table privileges"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(
                "REVOKE UPDATE ON public.portfolio_proposals FROM seven_lens_runtime_test"
            )
        owner.execute("GRANT DELETE ON public.portfolio_proposals TO seven_lens_runtime_test")
        try:
            with pytest.raises(PostgresRoleError, match="P3 table privileges"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(
                "REVOKE DELETE ON public.portfolio_proposals FROM seven_lens_runtime_test"
            )
        owner.execute("GRANT TRUNCATE ON public.portfolio_proposals TO seven_lens_runtime_test")
        try:
            with pytest.raises(PostgresRoleError, match="P3 table privileges"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(
                "REVOKE TRUNCATE ON public.portfolio_proposals FROM seven_lens_runtime_test"
            )
        owner.execute("GRANT REFERENCES ON public.portfolio_proposals TO seven_lens_runtime_test")
        try:
            with pytest.raises(PostgresRoleError, match="P3 table privileges"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(
                "REVOKE REFERENCES ON public.portfolio_proposals FROM seven_lens_runtime_test"
            )
        owner.execute("GRANT TRIGGER ON public.portfolio_proposals TO seven_lens_runtime_test")
        try:
            with pytest.raises(PostgresRoleError, match="P3 table privileges"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(
                "REVOKE TRIGGER ON public.portfolio_proposals FROM seven_lens_runtime_test"
            )
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence


@pytest.mark.usefixtures("runtime_postgres")
def test_p3d_role_verifier_detects_proposal_function_drift(
    migrated_postgres: str,
    request: pytest.FixtureRequest,
) -> None:
    evidence = request.getfixturevalue("runtime_postgres")[1]
    assert evidence.runtime_role == "seven_lens_runtime_test"
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        owner.execute(
            "REVOKE EXECUTE ON FUNCTION public.register_risk_feedback("
            "uuid, uuid, text, text) FROM seven_lens_runtime_test"
        )
        try:
            with pytest.raises(PostgresRoleError, match="P3 function privileges"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(
                "GRANT EXECUTE ON FUNCTION public.register_risk_feedback("
                "uuid, uuid, text, text) TO seven_lens_runtime_test"
            )
        owner.execute(
            "GRANT EXECUTE ON FUNCTION public.digest(text, text) TO seven_lens_runtime_test"
        )
        try:
            with pytest.raises(PostgresRoleError, match="function privileges"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(
                "REVOKE EXECUTE ON FUNCTION public.digest(text, text) FROM seven_lens_runtime_test"
            )
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence


@pytest.mark.usefixtures("runtime_postgres")
def test_p3d_role_verifier_detects_nonruntime_owner_drift(
    migrated_postgres: str,
    request: pytest.FixtureRequest,
) -> None:
    evidence = request.getfixturevalue("runtime_postgres")[1]
    drift_role = "p3d_owner_drift"
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        owner.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(drift_role)))
        try:
            owner.execute(
                sql.SQL("ALTER TABLE public.proposal_contexts OWNER TO {}").format(
                    sql.Identifier(drift_role)
                )
            )
            with pytest.raises(PostgresRoleError, match="own every authoritative object"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
            owner.execute(
                sql.SQL("ALTER TABLE public.proposal_contexts OWNER TO {}").format(
                    sql.Identifier(evidence.owner_role)
                )
            )

            owner.execute(
                sql.SQL(
                    "ALTER FUNCTION public.register_risk_feedback(uuid, uuid, text, text) "
                    "OWNER TO {}"
                ).format(sql.Identifier(drift_role))
            )
            with pytest.raises(PostgresRoleError, match="own every authoritative object"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
            owner.execute(
                sql.SQL(
                    "ALTER FUNCTION public.register_risk_feedback(uuid, uuid, text, text) "
                    "OWNER TO {}"
                ).format(sql.Identifier(evidence.owner_role))
            )
        finally:
            owner.execute(
                sql.SQL("ALTER TABLE public.proposal_contexts OWNER TO {}").format(
                    sql.Identifier(evidence.owner_role)
                )
            )
            owner.execute(
                sql.SQL(
                    "ALTER FUNCTION public.register_risk_feedback(uuid, uuid, text, text) "
                    "OWNER TO {}"
                ).format(sql.Identifier(evidence.owner_role))
            )
            owner.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(drift_role)))
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence


@pytest.mark.usefixtures("runtime_postgres")
def test_p3d_role_verifier_detects_public_execute_grant(
    migrated_postgres: str,
    request: pytest.FixtureRequest,
) -> None:
    evidence = request.getfixturevalue("runtime_postgres")[1]
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence


@pytest.mark.usefixtures("runtime_postgres")
def test_p3d_role_verifier_detects_function_security_configuration_drift(
    migrated_postgres: str,
    request: pytest.FixtureRequest,
) -> None:
    evidence = request.getfixturevalue("runtime_postgres")[1]
    signature = "public.register_risk_feedback(uuid, uuid, text, text)"
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        owner.execute(f"ALTER FUNCTION {signature} SECURITY INVOKER")
        try:
            with pytest.raises(PostgresRoleError, match="security configuration"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(f"ALTER FUNCTION {signature} SECURITY DEFINER")

        owner.execute(f"ALTER FUNCTION {signature} SET search_path = public")
        try:
            with pytest.raises(PostgresRoleError, match="security configuration"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(
                f"ALTER FUNCTION {signature} SET search_path = pg_catalog, public, pg_temp"
            )
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        owner.execute(
            "GRANT EXECUTE ON FUNCTION public.register_risk_feedback("
            "uuid, uuid, text, text) TO PUBLIC"
        )
        try:
            with pytest.raises(PostgresRoleError, match="PUBLIC"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute(
                "REVOKE EXECUTE ON FUNCTION public.register_risk_feedback("
                "uuid, uuid, text, text) FROM PUBLIC"
            )
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence


@pytest.mark.usefixtures("runtime_postgres")
def test_p3d_role_verifier_detects_extra_public_objects(
    migrated_postgres: str,
    request: pytest.FixtureRequest,
) -> None:
    evidence = request.getfixturevalue("runtime_postgres")[1]
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        owner.execute(
            "CREATE FUNCTION public.rogue_p3d() RETURNS void LANGUAGE sql AS 'SELECT 1' "
            "SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp"
        )
        try:
            with pytest.raises(PostgresRoleError, match="inventory"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute("DROP FUNCTION public.rogue_p3d()")
        # An extra overload of an approved P3 name must also fail closed: it
        # carries default PUBLIC EXECUTE, so either the PUBLIC check or the
        # inventory check rejects it depending on evaluation order.
        owner.execute(
            "CREATE FUNCTION public.register_risk_feedback(uuid) RETURNS void LANGUAGE sql "
            "AS 'SELECT 1' SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp"
        )
        try:
            with pytest.raises(PostgresRoleError, match=r"PUBLIC|inventory"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute("DROP FUNCTION public.register_risk_feedback(uuid)")
        owner.execute("CREATE TABLE public.rogue_p3d_table (payload TEXT)")
        try:
            with pytest.raises(PostgresRoleError, match="inventory"):
                verify_runtime_role(migrated_postgres, evidence.runtime_role)
        finally:
            owner.execute("DROP TABLE public.rogue_p3d_table")
    assert verify_runtime_role(migrated_postgres, evidence.runtime_role) == evidence
