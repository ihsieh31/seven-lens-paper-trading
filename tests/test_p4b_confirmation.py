# mypy: ignore-errors
"""P4-B confirmation evaluator: block on any discovery, confirm only on proof.

Any qualified discovery blocks entry immediately.  Auto-confirmation requires
all six §4 prerequisites at once: identity closure, complete exact facts, at
least one SEC/issuer-IR/listing-exchange official announcement, every read
source available by decision time, no contradictions or withdrawals, and
auditable source identity.  Conflicts are never resolved by voting; absence of
Alpaca data is never counter-evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
from seven_lens.securities.quarantine import (
    ConfirmationOutcome,
    QuarantineReason,
    SourceObservation,
    evaluate_confirmation,
)
from seven_lens.sources.roles import P4SourceFamily

_T_DECL = UtcTimestamp(datetime(2026, 1, 5, 14, 30, 0, tzinfo=UTC))
_T_AVAIL = UtcTimestamp(datetime(2026, 1, 5, 15, 0, 0, tzinfo=UTC))
_T_DECISION = UtcTimestamp(datetime(2026, 1, 10, 0, 0, 0, tzinfo=UTC))
_EX_DATE = TradingDate.from_isoformat("2026-02-01")
_EFFECTIVE_DATE = TradingDate.from_isoformat("2026-02-01")
_SEC = SecurityId("0d96f15b-8b11-4f84-8c2c-6f6f6f6f6f6f")
_SCHEMA = SchemaVersion("1.0.0")
_ALPACA_HASH = "b" * 64
_OFFICIAL_HASH = "c" * 64

_IDENTITY = build_identity_record(
    security_id=_SEC,
    symbol=SecuritySymbol("TEST"),
    exchange=ListingExchange.NASDAQ,
    asset_class=AssetClass.US_EQUITY,
    valid_from=UtcTimestamp(datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)),
    available_at=UtcTimestamp(datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)),
    status=SecurityStatus.ACTIVE,
    source_refs=(
        SourceRef(
            record_id="alpaca-asset-1",
            family=P4SourceFamily.ALPACA_ASSETS,
            record_hash="d" * 64,
        ),
    ),
    schema_version=_SCHEMA,
)

_ALPACA_REF = SourceRef(
    record_id="alpaca-ca-1",
    family=P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
    record_hash=_ALPACA_HASH,
)

_EVENT = build_corporate_action_record(
    event_id="evt-split-0001",
    security_id=_SEC,
    security_identity_hash=_IDENTITY.identity_hash,
    action_type=CorporateActionType.FORWARD_SPLIT,
    ratio=SplitRatio.from_fraction(numerator=3, denominator=2),
    declared_at=_T_DECL,
    ex_date=_EX_DATE,
    effective_date=_EFFECTIVE_DATE,
    available_at=_T_AVAIL,
    state=CorporateActionState.DETECTED,
    source_refs=(_ALPACA_REF,),
    schema_version=_SCHEMA,
)


def _alpaca_obs(**overrides: object) -> SourceObservation:
    values: dict[str, object] = {
        "source_ref": _ALPACA_REF,
        "available_at": _T_AVAIL,
        "withdrawn": False,
        "auditable": True,
        "claimed_type": CorporateActionType.FORWARD_SPLIT,
        "claimed_ratio": SplitRatio.from_fraction(numerator=3, denominator=2),
        "claimed_ex_date": _EX_DATE,
        "claimed_effective_date": _EFFECTIVE_DATE,
    }
    values.update(overrides)
    return SourceObservation(**values)


def _official_obs(
    family: P4SourceFamily = P4SourceFamily.SEC_EDGAR, **overrides: object
) -> SourceObservation:
    values: dict[str, object] = {
        "source_ref": SourceRef(
            record_id=f"official-{family.value.lower()}-1",
            family=family,
            record_hash=_OFFICIAL_HASH,
        ),
        "available_at": UtcTimestamp(datetime(2026, 1, 6, 12, 0, 0, tzinfo=UTC)),
        "withdrawn": False,
        "auditable": True,
        "claimed_type": CorporateActionType.FORWARD_SPLIT,
        "claimed_ratio": SplitRatio.from_fraction(numerator=3, denominator=2),
        "claimed_ex_date": _EX_DATE,
        "claimed_effective_date": _EFFECTIVE_DATE,
    }
    values.update(overrides)
    return SourceObservation(**values)


def _evaluate(observations: tuple, decision_at: UtcTimestamp = _T_DECISION, **kwargs):
    return evaluate_confirmation(
        event=kwargs.get("event", _EVENT),
        identity=kwargs.get("identity", _IDENTITY),
        observations=observations,
        decision_at=decision_at,
    )


# --- closed enums -----------------------------------------------------------


def test_quarantine_reasons_are_the_closed_fifteen() -> None:
    assert [reason.name for reason in QuarantineReason] == [
        "UNKNOWN_SECURITY",
        "AMBIGUOUS_IDENTITY",
        "SYMBOL_AS_OF_MISMATCH",
        "IDENTITY_INTERVAL_CONFLICT",
        "SOURCE_NOT_YET_AVAILABLE",
        "STALE_SECURITY_MASTER",
        "SPLIT_DETECTED",
        "FORMAL_CONFIRMATION_MISSING",
        "SPLIT_RATIO_CONFLICT",
        "SPLIT_DATE_CONFLICT",
        "SPLIT_IDENTITY_CONFLICT",
        "SOURCE_WITHDRAWN_OR_CORRECTED",
        "UNSUPPORTED_CORPORATE_ACTION",
        "EFFECTIVE_OR_LATE_EVENT_REVIEW",
        "SPLIT_TYPE_CONFLICT",
    ]


def test_confirmation_outcome_is_closed() -> None:
    assert set(ConfirmationOutcome) == {
        ConfirmationOutcome.ENTRY_BLOCKED,
        ConfirmationOutcome.CONFIRMED,
        ConfirmationOutcome.REVIEW_REQUIRED,
    }


# --- block on any discovery -------------------------------------------------


def test_alpaca_only_detection_blocks_without_confirming() -> None:
    evaluation = _evaluate((_alpaca_obs(),))
    assert evaluation.outcome is ConfirmationOutcome.ENTRY_BLOCKED
    assert evaluation.reasons == (
        QuarantineReason.SPLIT_DETECTED,
        QuarantineReason.FORMAL_CONFIRMATION_MISSING,
    )


def test_discovery_only_sources_can_never_confirm() -> None:
    for family in (P4SourceFamily.TAVILY, P4SourceFamily.GDELT, P4SourceFamily.YFINANCE):
        observation = _official_obs(family=family)
        evaluation = _evaluate((_alpaca_obs(), observation))
        assert evaluation.outcome is ConfirmationOutcome.ENTRY_BLOCKED
        assert QuarantineReason.FORMAL_CONFIRMATION_MISSING in evaluation.reasons


def test_alpaca_absence_is_not_counter_evidence() -> None:
    # The Alpaca detection source states nothing beyond its existence; that
    # silence must never contradict an official announcement.
    silent = _alpaca_obs(
        claimed_type=None, claimed_ratio=None, claimed_ex_date=None, claimed_effective_date=None
    )
    evaluation = _evaluate((silent, _official_obs()))
    assert evaluation.outcome is ConfirmationOutcome.CONFIRMED
    assert evaluation.reasons == ()


# --- official confirmation --------------------------------------------------


def test_single_official_announcement_confirms() -> None:
    evaluation = _evaluate((_alpaca_obs(), _official_obs()))
    assert evaluation.outcome is ConfirmationOutcome.CONFIRMED
    assert evaluation.reasons == ()


@pytest.mark.parametrize(
    "family",
    [P4SourceFamily.SEC_EDGAR, P4SourceFamily.ISSUER_IR, P4SourceFamily.EXCHANGE_OFFICIAL],
)
def test_each_official_family_can_confirm(family: P4SourceFamily) -> None:
    evaluation = _evaluate((_alpaca_obs(), _official_obs(family=family)))
    assert evaluation.outcome is ConfirmationOutcome.CONFIRMED


def test_official_announcement_must_be_auditable_to_confirm() -> None:
    evaluation = _evaluate((_alpaca_obs(), _official_obs(auditable=False)))
    assert evaluation.outcome is ConfirmationOutcome.ENTRY_BLOCKED
    assert evaluation.reasons == (
        QuarantineReason.SPLIT_DETECTED,
        QuarantineReason.FORMAL_CONFIRMATION_MISSING,
    )


def test_official_announcement_must_be_available_by_decision_time() -> None:
    late = _official_obs(available_at=UtcTimestamp(datetime(2026, 1, 11, 0, 0, 0, tzinfo=UTC)))
    evaluation = _evaluate((_alpaca_obs(), late))
    assert evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED
    assert QuarantineReason.SOURCE_NOT_YET_AVAILABLE in evaluation.reasons


# --- withdrawals and conflicts ----------------------------------------------


def test_withdrawn_official_announcement_forces_review() -> None:
    evaluation = _evaluate((_alpaca_obs(), _official_obs(withdrawn=True)))
    assert evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED
    assert evaluation.reasons == (
        QuarantineReason.SPLIT_DETECTED,
        QuarantineReason.SOURCE_WITHDRAWN_OR_CORRECTED,
    )


def test_ratio_conflict_between_sources_forces_review_without_voting() -> None:
    dissenting = _official_obs(claimed_ratio=SplitRatio.from_fraction(numerator=2, denominator=1))
    evaluation = _evaluate((_alpaca_obs(), dissenting))
    assert evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED
    assert evaluation.reasons == (
        QuarantineReason.SPLIT_DETECTED,
        QuarantineReason.SPLIT_RATIO_CONFLICT,
    )


def test_ratio_conflict_between_two_discovery_sources_forces_review() -> None:
    other = SourceObservation(
        source_ref=SourceRef(
            record_id="tavily-1", family=P4SourceFamily.TAVILY, record_hash="e" * 64
        ),
        available_at=_T_AVAIL,
        withdrawn=False,
        auditable=True,
        claimed_type=CorporateActionType.FORWARD_SPLIT,
        claimed_ratio=SplitRatio.from_fraction(numerator=2, denominator=1),
        claimed_ex_date=_EX_DATE,
        claimed_effective_date=_EFFECTIVE_DATE,
    )
    evaluation = _evaluate((_alpaca_obs(), other))
    assert evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED
    assert QuarantineReason.SPLIT_RATIO_CONFLICT in evaluation.reasons


def test_date_conflicts_force_review() -> None:
    ex_conflict = _official_obs(claimed_ex_date=TradingDate.from_isoformat("2026-02-02"))
    evaluation = _evaluate((_alpaca_obs(), ex_conflict))
    assert evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED
    assert QuarantineReason.SPLIT_DATE_CONFLICT in evaluation.reasons

    effective_conflict = _official_obs(
        claimed_effective_date=TradingDate.from_isoformat("2026-02-03")
    )
    evaluation = _evaluate((_alpaca_obs(), effective_conflict))
    assert evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED
    assert QuarantineReason.SPLIT_DATE_CONFLICT in evaluation.reasons


def test_type_conflict_forces_review() -> None:
    dissenting = _official_obs(claimed_type=CorporateActionType.REVERSE_SPLIT)
    evaluation = _evaluate((_alpaca_obs(), dissenting))
    assert evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED
    assert evaluation.reasons == (
        QuarantineReason.SPLIT_DETECTED,
        QuarantineReason.SPLIT_TYPE_CONFLICT,
    )


def test_identity_version_drift_forces_review() -> None:
    stranger = build_identity_record(
        security_id=_SEC,
        symbol=SecuritySymbol("TEST"),
        exchange=ListingExchange.NASDAQ,
        asset_class=AssetClass.US_EQUITY,
        valid_from=UtcTimestamp(datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)),
        available_at=UtcTimestamp(datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)),
        status=SecurityStatus.ACTIVE,
        source_refs=(
            SourceRef(
                record_id="alpaca-asset-2",
                family=P4SourceFamily.ALPACA_ASSETS,
                record_hash="f" * 64,
            ),
        ),
        schema_version=_SCHEMA,
    )
    assert stranger.identity_hash != _IDENTITY.identity_hash
    evaluation = _evaluate((_alpaca_obs(), _official_obs()), identity=stranger)
    assert evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED
    assert QuarantineReason.SPLIT_IDENTITY_CONFLICT in evaluation.reasons


# --- late or already-effective events ----------------------------------------


def test_already_effective_event_forces_review_even_when_officially_confirmed() -> None:
    at_ex = UtcTimestamp(datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC))
    evaluation = _evaluate((_alpaca_obs(), _official_obs()), decision_at=at_ex)
    assert evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED
    assert evaluation.reasons == (
        QuarantineReason.SPLIT_DETECTED,
        QuarantineReason.EFFECTIVE_OR_LATE_EVENT_REVIEW,
    )


def test_decision_one_day_before_ex_date_can_still_confirm() -> None:
    before_ex = UtcTimestamp(datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC))
    evaluation = _evaluate((_alpaca_obs(), _official_obs()), decision_at=before_ex)
    assert evaluation.outcome is ConfirmationOutcome.CONFIRMED


def test_combined_findings_report_every_reason_sorted_and_unique() -> None:
    withdrawn = _official_obs(withdrawn=True)
    late = SourceObservation(
        source_ref=SourceRef(
            record_id="gdelt-1", family=P4SourceFamily.GDELT, record_hash="e" * 64
        ),
        available_at=UtcTimestamp(datetime(2026, 1, 12, 0, 0, 0, tzinfo=UTC)),
        withdrawn=False,
        auditable=True,
        claimed_type=CorporateActionType.FORWARD_SPLIT,
        claimed_ratio=SplitRatio.from_fraction(numerator=2, denominator=1),
        claimed_ex_date=None,
        claimed_effective_date=None,
    )
    evaluation = _evaluate((_alpaca_obs(), withdrawn, late))
    assert evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED
    assert evaluation.reasons == (
        QuarantineReason.SOURCE_NOT_YET_AVAILABLE,
        QuarantineReason.SPLIT_DETECTED,
        QuarantineReason.SPLIT_RATIO_CONFLICT,
        QuarantineReason.SOURCE_WITHDRAWN_OR_CORRECTED,
    )


# --- input contract ----------------------------------------------------------


def test_observations_must_cover_every_event_source_ref() -> None:
    with pytest.raises(ValueError, match="cover"):
        _evaluate((_official_obs(),))


def test_observations_must_be_a_bounded_unique_tuple() -> None:
    with pytest.raises(ValueError, match="tuple"):
        _evaluate(())
    with pytest.raises(ValueError, match="tuple"):
        _evaluate([_alpaca_obs(), _official_obs()])
    with pytest.raises(ValueError, match="unique"):
        _evaluate((_alpaca_obs(), _alpaca_obs()))


def test_event_must_be_pre_confirmation_state() -> None:
    confirmed_event = build_corporate_action_record(
        event_id=_EVENT.event_id,
        security_id=_SEC,
        security_identity_hash=_IDENTITY.identity_hash,
        action_type=CorporateActionType.FORWARD_SPLIT,
        ratio=SplitRatio.from_fraction(numerator=3, denominator=2),
        declared_at=_T_DECL,
        ex_date=_EX_DATE,
        effective_date=_EFFECTIVE_DATE,
        available_at=_T_AVAIL,
        state=CorporateActionState.CONFIRMED,
        source_refs=(_ALPACA_REF,),
        schema_version=_SCHEMA,
    )
    with pytest.raises(ValueError, match="pre-confirmation"):
        _evaluate((_alpaca_obs(), _official_obs()), event=confirmed_event)


def test_tampered_event_or_identity_is_rejected() -> None:
    tampered = object.__new__(type(_EVENT))
    for field in (
        "event_id",
        "security_id",
        "security_identity_hash",
        "action_type",
        "ratio",
        "declared_at",
        "ex_date",
        "effective_date",
        "available_at",
        "state",
        "source_refs",
        "schema_version",
        "record_hash",
    ):
        object.__setattr__(tampered, field, getattr(_EVENT, field))
    object.__setattr__(tampered, "ratio", SplitRatio.from_fraction(numerator=9, denominator=1))
    with pytest.raises(ValueError, match="hash"):
        _evaluate((_alpaca_obs(), _official_obs()), event=tampered)


def test_observation_fields_are_exact_typed() -> None:
    with pytest.raises(ValueError, match="withdrawn"):
        _alpaca_obs(withdrawn="no")
    with pytest.raises(ValueError, match="auditable"):
        _alpaca_obs(auditable=1)
    with pytest.raises(ValueError, match="available_at"):
        _alpaca_obs(available_at="2026-01-05T15:00:00.000000Z")
    with pytest.raises(ValueError, match="claimed_ratio"):
        _alpaca_obs(claimed_ratio=1.5)


def test_decision_at_must_be_canonical_utc() -> None:
    with pytest.raises(ValueError, match="decision_at"):
        evaluate_confirmation(
            event=_EVENT,
            identity=_IDENTITY,
            observations=(_alpaca_obs(), _official_obs()),
            decision_at="2026-01-10T00:00:00.000000Z",
        )


def test_evaluation_is_deterministic() -> None:
    first = _evaluate((_alpaca_obs(), _official_obs()))
    second = _evaluate((_alpaca_obs(), _official_obs()))
    assert first == second
