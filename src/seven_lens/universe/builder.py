"""Deterministic universe hard-filter builder.

The builder applies every eligibility rule as an independent closed-reason
filter, in a fixed order, over the complete candidate set.  It never
truncates before filtering, never drops an exclusion reason, and orders the
result by canonical stable security id regardless of input order or DB row
order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.market_data.snapshots import (
    MIN_ADV20_USD,
    MIN_PRICE,
    MIN_TRADING_HISTORY_SESSIONS,
    Freshness,
    MarketSnapshot,
)
from seven_lens.screening.reasons import ClosedReason
from seven_lens.securities.contracts import (
    ListingExchange,
    SecurityId,
    SecurityIdentityRecord,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
)
from seven_lens.securities.identity import (
    IdentityQuery,
    IdentityResolutionStatus,
    resolve_identity,
)
from seven_lens.securities.quarantine import (
    QuarantineDecision,
    QuarantineOutcome,
    master_version_for,
)
from seven_lens.sources.adapters.records import NormalizedSourceRecord
from seven_lens.sources.roles import P4SourceFamily
from seven_lens.universe.contracts import (
    _UNIVERSE_SNAPSHOT_AUTHORITY,
    UniverseEntry,
    UniverseSnapshot,
    WholeShareFeasibility,
    _build_universe_snapshot,
)

_PRODUCER_VERSION: Final = "p4c.universe.v1"
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
# Asset observations and quarantine decisions are authorities that must be
# current, not merely visible: the composition refreshes them daily, so an
# observation older than this limit no longer proves tradability/quarantine
# state and the security is excluded with its typed reason.
_MAX_AUTHORITY_STALENESS: Final = timedelta(days=7)


class AssetKind(StrEnum):
    """Closed instrument kinds derived from Alpaca asset records.

    Only ``ORDINARY_COMMON_STOCK`` is eligible.  ETFs (including unlevered
    ETFs) and all other instrument types are fixed exclusions.
    """

    ORDINARY_COMMON_STOCK = "ordinary_common_stock"
    ETF = "etf"
    PREFERRED = "preferred"
    WARRANT = "warrant"
    UNIT = "unit"
    CLOSED_END_FUND = "closed_end_fund"
    ETN = "etn"
    LEVERAGED_INVERSE_ETF = "leveraged_inverse_etf"
    OTC = "otc"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class _AssetProjectionAuthority:
    """Opaque binding from one eligibility observation to both source authorities."""

    fingerprint: tuple[object, ...]
    asset_record_hash: str
    exchange_record_hash: str
    identity_hash: str


@dataclass(frozen=True, slots=True)
class AssetObservation:
    """One point-in-time Alpaca asset observation as seen by the builder."""

    security_id: SecurityId
    symbol: SecuritySymbol
    kind: AssetKind
    active: bool
    tradable: bool
    exchange: ListingExchange
    observed_at: UtcTimestamp
    halted: bool | None = None
    _authority: _AssetProjectionAuthority | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _AssetProjectionAuthority:
            raise ValueError("asset observations must be produced by the source-record factory")
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if type(self.symbol) is not SecuritySymbol:
            raise ValueError("symbol requires an exact SecuritySymbol")
        if type(self.kind) is not AssetKind:
            raise ValueError("kind requires an exact AssetKind")
        if type(self.active) is not bool or type(self.tradable) is not bool:
            raise ValueError("active and tradable require exact bool values")
        if type(self.exchange) is not ListingExchange:
            raise ValueError("exchange requires an exact ListingExchange")
        if type(self.observed_at) is not UtcTimestamp:
            raise ValueError("observed_at requires canonical UTC")
        if self.halted is not None and type(self.halted) is not bool:
            raise ValueError("halted requires an exact bool or None")
        self._verify_source_binding()

    def _verify_source_binding(self) -> None:
        authority = self._authority
        assert type(authority) is _AssetProjectionAuthority
        if authority.fingerprint != _asset_observation_fingerprint(self):
            raise ValueError("asset observation authority is not bound to frozen content")


def _asset_observation_fingerprint(value: AssetObservation) -> tuple[object, ...]:
    return (
        value.security_id,
        value.symbol,
        value.kind,
        value.active,
        value.tradable,
        value.exchange,
        value.observed_at,
        value.halted,
    )


def asset_observation_from_records(
    asset_record: NormalizedSourceRecord,
    exchange_record: NormalizedSourceRecord,
    *,
    identity: SecurityIdentityRecord,
    known_at: UtcTimestamp,
) -> AssetObservation:
    """Join Alpaca availability with official instrument/halt classification.

    Alpaca's assets endpoint is authoritative for stable provider id, listing,
    active status, and tradability.  A typed official-exchange observation is
    required separately for the closed instrument kind and current halt state.
    Both records must resolve through the exact P4-B identity lineage.
    """
    if type(asset_record) is not NormalizedSourceRecord:
        raise ValueError("asset source requires an exact NormalizedSourceRecord")
    if type(exchange_record) is not NormalizedSourceRecord:
        raise ValueError("exchange source requires an exact NormalizedSourceRecord")
    if type(identity) is not SecurityIdentityRecord:
        raise ValueError("identity requires an exact SecurityIdentityRecord")
    if type(known_at) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC")
    asset_record.verify_integrity()
    exchange_record.verify_integrity()
    identity.verify_integrity()
    if asset_record.family is not P4SourceFamily.ALPACA_ASSETS or asset_record.endpoint_id not in {
        "assets_list",
        "asset_detail",
    }:
        raise ValueError("asset status requires the Alpaca assets endpoint")
    if (
        exchange_record.family is not P4SourceFamily.EXCHANGE_OFFICIAL
        or exchange_record.endpoint_id != "exchange_notice"
    ):
        raise ValueError("instrument and halt status require an official exchange record")
    if (
        asset_record.available_at is None
        or exchange_record.available_at is None
        or asset_record.observation_at is None
        or exchange_record.observation_at is None
    ):
        raise ValueError("asset authorities require observation and availability timestamps")
    if (
        asset_record.available_at.value > known_at.value
        or exchange_record.available_at.value > known_at.value
    ):
        raise ValueError("asset authority is not available by known_at")

    asset_payload = asset_record.payload.to_dict()
    if set(asset_payload) != {
        "id",
        "symbol",
        "exchange",
        "asset_class",
        "status",
        "tradable",
    }:
        raise ValueError("Alpaca asset payload has an unexpected shape")
    exchange_payload = exchange_record.payload.to_dict()
    if set(exchange_payload) != {
        "exchange",
        "title",
        "url",
        "symbol",
        "instrument_kind",
        "halted",
        "observed_at",
    }:
        raise ValueError("official exchange status payload has an unexpected shape")

    security_id = SecurityId(str(asset_payload.get("id")))
    symbol = SecuritySymbol(str(asset_payload.get("symbol")))
    try:
        listing_exchange = ListingExchange(str(asset_payload.get("exchange")))
        kind = AssetKind(str(exchange_payload.get("instrument_kind")))
    except ValueError as error:
        raise ValueError("asset authority contains an unsupported closed enum") from error
    if asset_payload.get("asset_class") != "us_equity":
        raise ValueError("asset authority is not a US equity")
    status = asset_payload.get("status")
    tradable = asset_payload.get("tradable")
    halted = exchange_payload.get("halted")
    if status not in {"active", "inactive"} or type(tradable) is not bool:
        raise ValueError("Alpaca asset status values are invalid")
    if type(halted) is not bool:
        raise ValueError("official exchange halt status is invalid")
    if (
        exchange_payload.get("symbol") != symbol.value
        or exchange_payload.get("exchange") != listing_exchange.value
    ):
        raise ValueError("asset and exchange authorities disagree")
    if (
        identity.security_id != security_id
        or identity.symbol != symbol
        or identity.exchange is not listing_exchange
    ):
        raise ValueError("asset authorities do not bind to the supplied identity")
    asset_ref = SourceRef(asset_record.record_id, asset_record.family, asset_record.record_hash)
    if asset_ref not in identity.source_refs:
        raise ValueError("P4-B identity does not descend from the exact Alpaca asset record")
    for observed_at in (asset_record.observation_at, exchange_record.observation_at):
        if not identity.answers_as_of(as_of=observed_at, known_at=known_at):
            raise ValueError("asset authority is outside the identity validity window")

    values: dict[str, object] = {
        "security_id": security_id,
        "symbol": symbol,
        "kind": kind,
        "active": status == "active",
        "tradable": tradable,
        "exchange": listing_exchange,
        # Use the older observation so a stale half of the joined authority
        # cannot be hidden behind a newer counterpart.
        "observed_at": min(
            asset_record.observation_at,
            exchange_record.observation_at,
            key=lambda value: value.value,
        ),
        "halted": halted,
    }
    provisional = object.__new__(AssetObservation)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    authority = _AssetProjectionAuthority(
        fingerprint=_asset_observation_fingerprint(provisional),
        asset_record_hash=asset_record.record_hash,
        exchange_record_hash=exchange_record.record_hash,
        identity_hash=identity.identity_hash,
    )
    return AssetObservation(**values, _authority=authority)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class QuarantineView:
    """The security master's quarantine outcome for one security."""

    decision: QuarantineDecision

    def __post_init__(self) -> None:
        if type(self.decision) is not QuarantineDecision:
            raise ValueError("decision requires an exact QuarantineDecision")
        self.decision.verify_integrity()

    @property
    def security_id(self) -> SecurityId:
        return self.decision.security_id

    @property
    def outcome(self) -> QuarantineOutcome:
        return self.decision.outcome

    @property
    def event_ids(self) -> tuple[str, ...]:
        return self.decision.event_ids

    @property
    def decision_hash(self) -> str:
        return self.decision.decision_hash

    @property
    def symbol_as_of(self) -> SecuritySymbol:
        return self.decision.symbol_as_of

    @property
    def master_version(self) -> str:
        return self.decision.master_version


@dataclass(frozen=True, slots=True)
class _IdentityViewAuthority:
    """Opaque binding to one exact point-in-time P4-B resolution."""

    identity_hash: str
    as_of: UtcTimestamp
    known_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class IdentityView:
    """Closed identity resolution view for one security."""

    record: SecurityIdentityRecord
    _authority: _IdentityViewAuthority | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _IdentityViewAuthority:
            raise ValueError("identity views must be produced by the P4-B resolver")
        if type(self.record) is not SecurityIdentityRecord:
            raise ValueError("record requires an exact SecurityIdentityRecord")
        self.record.verify_integrity()
        if self._authority.identity_hash != self.record.identity_hash:
            raise ValueError("identity view authority is not bound to the resolved record")

    @property
    def security_id(self) -> SecurityId:
        return self.record.security_id

    @property
    def resolved(self) -> bool:
        return self.record.status is SecurityStatus.ACTIVE

    @property
    def symbol(self) -> SecuritySymbol:
        return self.record.symbol

    @property
    def identity_hash(self) -> str:
        return self.record.identity_hash

    @property
    def master_version(self) -> str:
        return master_version_for(self.record)


def identity_view_from_records(
    records: tuple[SecurityIdentityRecord, ...],
    *,
    security_id: SecurityId,
    as_of: UtcTimestamp,
    known_at: UtcTimestamp,
) -> IdentityView:
    """Resolve one exact P4-B identity view for universe composition."""
    resolution = resolve_identity(
        records,
        IdentityQuery(
            security_id=security_id,
            as_of=as_of,
            known_at=known_at,
        ),
    )
    if resolution.status is not IdentityResolutionStatus.RESOLVED or resolution.record is None:
        raise ValueError("identity view requires one unambiguous P4-B resolution")
    return IdentityView(
        record=resolution.record,
        _authority=_IdentityViewAuthority(
            identity_hash=resolution.record.identity_hash,
            as_of=as_of,
            known_at=known_at,
        ),
    )


@dataclass(frozen=True, slots=True)
class MarketView:
    """Market snapshot view used for liquidity/price gates."""

    snapshot: MarketSnapshot

    def __post_init__(self) -> None:
        if type(self.snapshot) is not MarketSnapshot:
            raise ValueError("snapshot requires an exact trusted MarketSnapshot")
        self.snapshot.verify_integrity()

    @property
    def security_id(self) -> SecurityId:
        """Return the identity bound to the trusted market snapshot."""
        return self.snapshot.security_id

    @property
    def trading_history_sessions(self) -> int:
        """Return the source-derived count of session dates in the snapshot."""
        return len(self.snapshot.bar_dates)

    @property
    def snapshot_hash(self) -> str:
        """Return the verified market snapshot hash."""
        return self.snapshot.snapshot_hash

    @property
    def last(self) -> Decimal | None:
        """Return the source-derived last price."""
        return self.snapshot.last

    @property
    def adv20_usd(self) -> Decimal | None:
        """Return the source-derived 20-session ADV."""
        return self.snapshot.adv20_usd

    @property
    def freshness_ok(self) -> bool:
        """Return freshness derived from the immutable market snapshot."""
        return self.snapshot.freshness is Freshness.FRESH

    @property
    def spread_ok(self) -> bool:
        """Return spread eligibility derived from the snapshot's exact reason set."""
        return ClosedReason.SPREAD_TOO_WIDE not in self.snapshot.reasons


def _kind_reason(kind: AssetKind) -> ClosedReason | None:
    """Map one instrument kind to its closed exclusion reason."""
    if kind is AssetKind.ORDINARY_COMMON_STOCK:
        return None
    if kind is AssetKind.OTC:
        return ClosedReason.OTC_OR_EXCLUDED_INSTRUMENT
    if kind is AssetKind.ETF:
        return ClosedReason.UNSUPPORTED_ASSET_CLASS
    return ClosedReason.UNSUPPORTED_ASSET_CLASS


def _evaluate_entry(
    *,
    asset: AssetObservation,
    identity: IdentityView | None,
    quarantine: QuarantineView | None,
    market: MarketView | None,
    known_at: UtcTimestamp,
) -> UniverseEntry:
    """Evaluate one security against every hard filter in fixed order."""
    reason = _kind_reason(asset.kind)
    if reason is not None:
        return _entry(asset, False, reason, identity, quarantine, market)

    if not asset.active or not asset.tradable:
        return _entry(
            asset, False, ClosedReason.NOT_ACTIVE_OR_TRADABLE, identity, quarantine, market
        )

    if asset.halted is not False:
        return _entry(
            asset, False, ClosedReason.NOT_ACTIVE_OR_TRADABLE, identity, quarantine, market
        )

    if asset.observed_at.value < known_at.value - _MAX_AUTHORITY_STALENESS:
        # A stale tradability observation proves nothing about the current
        # state; unknown tradability can never become eligible.
        return _entry(
            asset, False, ClosedReason.NOT_ACTIVE_OR_TRADABLE, identity, quarantine, market
        )

    if (
        identity is None
        or not identity.resolved
        or identity.symbol != asset.symbol
        or identity.record.exchange is not asset.exchange
        or asset._authority is None
        or asset._authority.identity_hash != identity.identity_hash
    ):
        return _entry(asset, False, ClosedReason.IDENTITY_NOT_CLOSED, identity, quarantine, market)

    if (
        quarantine is None
        or quarantine.security_id != asset.security_id
        or quarantine.symbol_as_of != asset.symbol
        or identity is None
        or quarantine.master_version != identity.master_version
        or quarantine.outcome is not QuarantineOutcome.ELIGIBLE
        or quarantine.decision.decision_at.value < known_at.value - _MAX_AUTHORITY_STALENESS
    ):
        return _entry(
            asset,
            False,
            ClosedReason.CORPORATE_ACTION_QUARANTINE,
            identity,
            quarantine,
            market,
        )

    if market is None:
        return _entry(
            asset, False, ClosedReason.QUOTE_MISSING_OR_STALE, identity, quarantine, market
        )

    if market.snapshot.symbol != asset.symbol:
        return _entry(asset, False, ClosedReason.MARKET_DATA_CONFLICT, identity, quarantine, market)

    if ClosedReason.MARKET_DATA_CONFLICT in market.snapshot.reasons:
        return _entry(asset, False, ClosedReason.MARKET_DATA_CONFLICT, identity, quarantine, market)

    if market.last is None or market.last < MIN_PRICE:
        return _entry(asset, False, ClosedReason.PRICE_BELOW_MINIMUM, identity, quarantine, market)

    if market.adv20_usd is None or market.adv20_usd < MIN_ADV20_USD:
        return _entry(asset, False, ClosedReason.ADV_BELOW_MINIMUM, identity, quarantine, market)

    if market.trading_history_sessions < MIN_TRADING_HISTORY_SESSIONS:
        return _entry(
            asset,
            False,
            ClosedReason.INSUFFICIENT_TRADING_HISTORY,
            identity,
            quarantine,
            market,
        )

    if not market.freshness_ok:
        return _entry(
            asset, False, ClosedReason.QUOTE_MISSING_OR_STALE, identity, quarantine, market
        )

    if not market.spread_ok:
        return _entry(asset, False, ClosedReason.SPREAD_TOO_WIDE, identity, quarantine, market)

    return _entry(asset, True, None, identity, quarantine, market)


def _entry(
    asset: AssetObservation,
    eligible: bool,
    reason: ClosedReason | None,
    identity: IdentityView | None,
    quarantine: QuarantineView | None,
    market: MarketView | None,
) -> UniverseEntry:
    bound_identity = (
        identity
        if identity is not None
        and identity.security_id == asset.security_id
        and identity.symbol == asset.symbol
        else None
    )
    bound_market = (
        market
        if market is not None
        and market.security_id == asset.security_id
        and market.snapshot.symbol == asset.symbol
        else None
    )
    bound_quarantine = (
        quarantine
        if bound_identity is not None
        and quarantine is not None
        and quarantine.security_id == asset.security_id
        and quarantine.symbol_as_of == asset.symbol
        and quarantine.master_version == bound_identity.master_version
        else None
    )
    return UniverseEntry(
        security_id=asset.security_id,
        symbol=asset.symbol,
        eligible=eligible,
        reason=reason,
        identity_hash=None if bound_identity is None else bound_identity.identity_hash,
        master_version=None if bound_identity is None else bound_identity.master_version,
        market_snapshot_hash=None if bound_market is None else bound_market.snapshot_hash,
        # P4-C has no portfolio/quantity capability; feasibility is typed
        # NOT_EVALUATED and resolved by P4-D/E.
        whole_share_feasibility=WholeShareFeasibility.NOT_EVALUATED,
        quarantine_decision_hash=(
            None if bound_quarantine is None else bound_quarantine.decision_hash
        ),
        quarantine_event_ids=() if bound_quarantine is None else bound_quarantine.event_ids,
    )


def build_universe(
    *,
    as_of: TradingDate,
    known_at: UtcTimestamp,
    security_master_version: str,
    assets: tuple[AssetObservation, ...],
    identities: tuple[IdentityView, ...],
    quarantines: tuple[QuarantineView, ...],
    markets: tuple[MarketView, ...],
    policy_hash: str,
    schema_version: SchemaVersion,
) -> UniverseSnapshot:
    """Build the deterministic monthly universe snapshot.

    Failure ordering is fixed: validate inputs, build the complete candidate
    set from every asset observation, apply every hard filter independently,
    sort by canonical security id, then publish.  No dict/DB ordering decides
    any outcome, and no security is dropped without a typed reason.
    """
    if type(as_of) is not TradingDate:
        raise ValueError("as_of requires an exact TradingDate")
    if as_of.value.day != 1:
        raise ValueError("universe as_of must be the first day of a calendar month")
    if type(known_at) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC")
    if type(security_master_version) is not str or not security_master_version:
        raise ValueError("security_master_version requires non-empty text")
    if type(assets) is not tuple or any(type(a) is not AssetObservation for a in assets):
        raise ValueError("assets must be a tuple of AssetObservation values")
    if type(identities) is not tuple or any(type(i) is not IdentityView for i in identities):
        raise ValueError("identities must be a tuple of IdentityView values")
    if type(quarantines) is not tuple or any(type(q) is not QuarantineView for q in quarantines):
        raise ValueError("quarantines must be a tuple of QuarantineView values")
    if type(markets) is not tuple or any(type(m) is not MarketView for m in markets):
        raise ValueError("markets must be a tuple of MarketView values")
    if type(policy_hash) is not str or len(policy_hash) != 64:
        raise ValueError("policy_hash must be a SHA-256 digest")
    if _HASH_TEXT.fullmatch(policy_hash) is None:
        raise ValueError("policy_hash must be a SHA-256 digest")
    if type(schema_version) is not SchemaVersion:
        raise ValueError("schema_version requires an exact SchemaVersion")

    for asset in assets:
        asset._verify_source_binding()
    if any(asset.observed_at.value > known_at.value for asset in assets):
        raise ValueError("asset observations after known_at are not admissible")

    def _record_security_id(value: object) -> str:
        if type(value) is AssetObservation:
            return value.security_id.value
        if type(value) is IdentityView:
            return value.security_id.value
        if type(value) is QuarantineView:
            return value.security_id.value
        if type(value) is MarketView:
            return value.security_id.value
        raise ValueError("unknown security record type")

    def _assert_unique(label: str, values: tuple[object, ...]) -> None:
        ids = [_record_security_id(value) for value in values]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{label} must not repeat a security")

    _assert_unique("assets", assets)
    _assert_unique("identities", identities)
    _assert_unique("quarantines", quarantines)
    _assert_unique("markets", markets)
    asset_ids = {asset.security_id.value for asset in assets}
    for label, values in (
        ("identities", identities),
        ("quarantines", quarantines),
        ("markets", markets),
    ):
        if any(_record_security_id(value) not in asset_ids for value in values):
            raise ValueError(f"{label} contains a security outside the asset candidate set")

    identity_by_id = {i.security_id.value: i for i in identities}
    quarantine_by_id = {q.security_id.value: q for q in quarantines}
    market_by_id = {m.security_id.value: m for m in markets}

    as_of_instant = UtcTimestamp(datetime.combine(as_of.value, datetime.min.time(), tzinfo=UTC))
    for identity in identities:
        identity.record.verify_integrity()
        if (
            identity._authority is None
            or identity._authority.as_of != as_of_instant
            or identity._authority.known_at != known_at
        ):
            raise ValueError("identity view does not match the universe point-in-time query")
        if identity.record.status is SecurityStatus.ACTIVE and not identity.record.answers_as_of(
            as_of=as_of_instant, known_at=known_at
        ):
            raise ValueError("active identity is not point-in-time visible")
        if identity.master_version != master_version_for(identity.record):
            raise ValueError("identity master version is not derived from the identity record")

    for quarantine in quarantines:
        quarantine.decision.verify_integrity()
        if quarantine.decision.decision_at.value > known_at.value:
            raise ValueError("quarantine decisions after known_at are not admissible")
        quarantine_identity = identity_by_id.get(quarantine.security_id.value)
        if (
            quarantine_identity is not None
            and quarantine.decision.master_version != quarantine_identity.master_version
        ):
            raise ValueError("quarantine decision master version does not match the identity")

    for market in markets:
        market.snapshot.verify_integrity()
        if market.snapshot.as_of.value.date() != as_of.value:
            raise ValueError("market snapshots must match the universe as_of date")
        if market.snapshot.known_at.value > known_at.value:
            raise ValueError("market snapshots after known_at are not admissible")
        if market.snapshot.security_id != market.security_id:
            raise ValueError("market snapshot security identity is inconsistent")

    entries = [
        _evaluate_entry(
            asset=asset,
            identity=identity_by_id.get(asset.security_id.value),
            quarantine=quarantine_by_id.get(asset.security_id.value),
            market=market_by_id.get(asset.security_id.value),
            known_at=known_at,
        )
        for asset in assets
    ]
    entries.sort(key=lambda e: e.security_id.value)
    entries_tuple = tuple(entries)

    market_snapshot_refs = tuple(sorted({m.snapshot_hash for m in markets}))

    return _build_universe_snapshot(
        authority=_UNIVERSE_SNAPSHOT_AUTHORITY,
        as_of=as_of,
        known_at=known_at,
        security_master_version=security_master_version,
        market_snapshot_refs=market_snapshot_refs,
        entries=entries_tuple,
        policy_hash=policy_hash,
        schema_version=schema_version,
        producer_version=_PRODUCER_VERSION,
    )
