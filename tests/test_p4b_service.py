# mypy: ignore-errors
"""Public P4-B service entry tests: lineage, block ordering, and revalidation."""

from __future__ import annotations

import pytest

from seven_lens.application.ports.p4_source_records import AppendOutcome
from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.securities.contracts import (
    AssetClass,
    ListingExchange,
    SecurityId,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
    build_identity_record,
)
from seven_lens.securities.corporate_actions import (
    CorporateActionState,
    CorporateActionType,
    SplitRatio,
    build_corporate_action_record,
)
from seven_lens.securities.in_memory import InMemorySecurityMaster
from seven_lens.securities.quarantine import (
    QuarantineOutcome,
    QuarantinePurpose,
    QuarantineQuery,
    SourceObservation,
    master_version_for,
)
from seven_lens.securities.service import (
    SecurityMasterService,
    SecurityMasterServiceError,
    SourceLineageError,
)
from seven_lens.sources.adapters.in_memory_p4_records import InMemoryP4RecordLog
from seven_lens.sources.adapters.records import _build_normalized_record as build_normalized_record
from seven_lens.sources.roles import P4SourceFamily

_T0 = UtcTimestamp.from_isoformat("2026-01-01T00:00:00.000000Z")
_T1 = UtcTimestamp.from_isoformat("2026-01-05T15:00:00.000000Z")
_T2 = UtcTimestamp.from_isoformat("2026-01-06T12:00:00.000000Z")
_T3 = UtcTimestamp.from_isoformat("2026-01-07T12:00:00.000000Z")
_SEC = SecurityId("11111111-1111-4111-8111-111111111111")
_SCHEMA = SchemaVersion("1.0.0")
_EX_DATE = TradingDate.from_isoformat("2026-02-01")


def _source(
    record_id: str,
    family: P4SourceFamily,
    *,
    available_at: UtcTimestamp = _T0,
):
    endpoint_id = {
        P4SourceFamily.ALPACA_ASSETS: "asset_detail",
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS: "corporate_actions",
        P4SourceFamily.SEC_EDGAR: "submissions",
    }[family]
    return build_normalized_record(
        record_id=record_id,
        family=family,
        endpoint_id=endpoint_id,
        schema_version=_SCHEMA,
        content_hash=(record_id.encode().hex() + "0" * 64)[:64],
        retrieved_at=available_at,
        available_at=available_at,
        payload={"record_id": record_id},
        material_claim=False,
    )


def _setup(*, telemetry=None):
    source_log = InMemoryP4RecordLog()
    identity_source = _source("asset-1", P4SourceFamily.ALPACA_ASSETS)
    alpaca_source = _source("ca-1", P4SourceFamily.ALPACA_CORPORATE_ACTIONS)
    official_source = _source("sec-1", P4SourceFamily.SEC_EDGAR)
    for source in (identity_source, alpaca_source, official_source):
        assert source_log.append(source) is AppendOutcome.APPENDED
    identity = build_identity_record(
        security_id=_SEC,
        symbol=SecuritySymbol("ACME"),
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        valid_from=_T0,
        available_at=_T0,
        status=SecurityStatus.ACTIVE,
        source_refs=(
            SourceRef("asset-1", P4SourceFamily.ALPACA_ASSETS, identity_source.record_hash),
        ),
        schema_version=_SCHEMA,
    )
    event = build_corporate_action_record(
        event_id="event-1",
        security_id=_SEC,
        security_identity_hash=identity.identity_hash,
        action_type=CorporateActionType.FORWARD_SPLIT,
        ratio=SplitRatio.from_fraction(numerator=3, denominator=2),
        declared_at=_T1,
        ex_date=_EX_DATE,
        effective_date=_EX_DATE,
        available_at=_T1,
        state=CorporateActionState.DETECTED,
        source_refs=(
            SourceRef("ca-1", P4SourceFamily.ALPACA_CORPORATE_ACTIONS, alpaca_source.record_hash),
        ),
        schema_version=_SCHEMA,
    )
    repository = InMemorySecurityMaster()
    service = SecurityMasterService(repository, source_log, telemetry=telemetry)
    assert service.register_identity(identity) is AppendOutcome.APPENDED
    return service, repository, source_log, identity, event, alpaca_source, official_source


def test_public_discovery_durably_blocks_before_any_confirmation() -> None:
    service, repository, _, _, event, _, _ = _setup()

    head = service.discover_split(event)

    assert head.state is CorporateActionState.ENTRY_BLOCKED
    assert [row.state for row in repository.event_lineage(event.event_id)] == [
        CorporateActionState.DETECTED,
        CorporateActionState.ENTRY_BLOCKED,
    ]


def test_public_confirmation_requires_official_source_and_revalidates_hash_bound_refs() -> None:
    service, repository, _, identity, event, alpaca_source, official_source = _setup()
    service.discover_split(event)
    observations = (
        SourceObservation(
            source_ref=SourceRef(
                "ca-1", P4SourceFamily.ALPACA_CORPORATE_ACTIONS, alpaca_source.record_hash
            ),
            available_at=_T0,
            withdrawn=False,
            auditable=True,
        ),
        SourceObservation(
            source_ref=SourceRef("sec-1", P4SourceFamily.SEC_EDGAR, official_source.record_hash),
            available_at=_T0,
            withdrawn=False,
            auditable=True,
            claimed_type=CorporateActionType.FORWARD_SPLIT,
            claimed_ratio=SplitRatio.from_fraction(numerator=3, denominator=2),
            claimed_ex_date=_EX_DATE,
            claimed_effective_date=_EX_DATE,
        ),
    )

    head = service.confirm_split(event.event_id, observations, decision_at=_T2)

    assert head.state is CorporateActionState.CONFIRMED
    assert repository.event_lineage(event.event_id)[-1].source_refs == tuple(
        sorted(
            (event.source_refs[0], observations[1].source_ref),
            key=lambda ref: (ref.record_id, ref.family.value, ref.record_hash),
        )
    )
    query = QuarantineQuery(
        purpose=QuarantinePurpose.CANDIDATE_CREATION,
        security_id=_SEC,
        symbol_as_of=identity.symbol,
        decision_at=_T2,
        master_version=master_version_for(identity),
    )
    decision = service.candidate_creation_check(query)
    assert decision.outcome is QuarantineOutcome.ENTRY_BLOCKED


def test_three_public_seams_persist_canonical_identical_decisions() -> None:
    service, _, _, identity, _, _, _ = _setup()
    query_values = {
        "security_id": _SEC,
        "symbol_as_of": identity.symbol,
        "decision_at": _T1,
        "master_version": master_version_for(identity),
    }
    candidate = service.candidate_creation_check(
        QuarantineQuery(purpose=QuarantinePurpose.CANDIDATE_CREATION, **query_values)
    )
    risk = service.risk_approval_check(
        QuarantineQuery(purpose=QuarantinePurpose.RISK_APPROVAL, **query_values)
    )
    submit = service.submit_recheck(
        QuarantineQuery(purpose=QuarantinePurpose.SUBMIT_RECHECK, **query_values)
    )
    assert candidate.wire() == risk.wire() == submit.wire()
    assert candidate.decision_hash == risk.decision_hash == submit.decision_hash


def test_telemetry_failure_cannot_undo_durable_entry_block() -> None:
    def broken_telemetry(_event) -> None:
        raise RuntimeError("non-essential telemetry unavailable")

    service, repository, _, _, event, _, _ = _setup(telemetry=broken_telemetry)

    head = service.discover_split(event)

    assert head.state is CorporateActionState.ENTRY_BLOCKED
    assert repository.event_lineage(event.event_id)[-1].state is CorporateActionState.ENTRY_BLOCKED
    assert service.telemetry_failures == 2


def test_quarantine_replays_source_heads_at_the_decision_cutoff() -> None:
    service, _, source_log, identity, event, _, official_source = _setup()
    service.discover_split(event)
    service.confirm_split(
        event.event_id,
        (
            SourceObservation(
                source_ref=event.source_refs[0],
                available_at=_T0,
                withdrawn=False,
                auditable=True,
            ),
            SourceObservation(
                source_ref=SourceRef(
                    "sec-1", P4SourceFamily.SEC_EDGAR, official_source.record_hash
                ),
                available_at=_T0,
                withdrawn=False,
                auditable=True,
                claimed_type=CorporateActionType.FORWARD_SPLIT,
                claimed_ratio=SplitRatio.from_fraction(numerator=3, denominator=2),
                claimed_ex_date=_EX_DATE,
                claimed_effective_date=_EX_DATE,
            ),
        ),
        decision_at=_T2,
    )
    corrected = build_normalized_record(
        record_id="sec-1",
        family=P4SourceFamily.SEC_EDGAR,
        endpoint_id="submissions",
        schema_version=_SCHEMA,
        content_hash="f" * 64,
        retrieved_at=_T3,
        available_at=_T3,
        payload={"record_id": "sec-1", "version": "corrected"},
        material_claim=False,
        supersedes_content_hash=official_source.content_hash,
    )
    assert source_log.append(corrected) is AppendOutcome.APPENDED

    historical = service.submit_recheck(
        QuarantineQuery(
            purpose=QuarantinePurpose.SUBMIT_RECHECK,
            security_id=_SEC,
            symbol_as_of=identity.symbol,
            decision_at=_T2,
            master_version=master_version_for(identity),
        )
    )
    current = service.submit_recheck(
        QuarantineQuery(
            purpose=QuarantinePurpose.SUBMIT_RECHECK,
            security_id=_SEC,
            symbol_as_of=identity.symbol,
            decision_at=_T3,
            master_version=master_version_for(identity),
        )
    )

    assert historical.outcome is QuarantineOutcome.ENTRY_BLOCKED
    assert current.outcome is QuarantineOutcome.REVIEW_REQUIRED


def test_unknown_source_reference_fails_closed_before_identity_append() -> None:
    source_log = InMemoryP4RecordLog()
    repository = InMemorySecurityMaster()
    service = SecurityMasterService(repository, source_log)
    identity = build_identity_record(
        security_id=_SEC,
        symbol=SecuritySymbol("ACME"),
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        valid_from=_T0,
        available_at=_T0,
        status=SecurityStatus.ACTIVE,
        source_refs=(SourceRef("missing", P4SourceFamily.ALPACA_ASSETS, "a" * 64),),
        schema_version=_SCHEMA,
    )

    with pytest.raises(SourceLineageError):
        service.register_identity(identity)
    assert repository.identity_records(security_id=_SEC) == ()


def test_identity_append_readback_failure_rolls_back_the_append() -> None:
    source_log = InMemoryP4RecordLog()
    source = _source("asset-readback", P4SourceFamily.ALPACA_ASSETS)
    assert source_log.append(source) is AppendOutcome.APPENDED
    identity = build_identity_record(
        security_id=_SEC,
        symbol=SecuritySymbol("ACME"),
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        valid_from=_T0,
        available_at=_T0,
        status=SecurityStatus.ACTIVE,
        source_refs=(SourceRef(source.record_id, source.family, source.record_hash),),
        schema_version=_SCHEMA,
    )

    class ReadbackFailureRepository(InMemorySecurityMaster):
        drop_once = True

        def identity_records(self, **kwargs):  # type: ignore[no-untyped-def]
            if self.drop_once:
                self.drop_once = False
                return ()
            return super().identity_records(**kwargs)

    repository = ReadbackFailureRepository()
    service = SecurityMasterService(repository, source_log)
    with pytest.raises(SecurityMasterServiceError, match="readback"):
        service.register_identity(identity)

    assert repository.identity_records(security_id=_SEC) == ()
