"""Alpaca family adapters: assets, historical bars, IEX quotes, corporate actions.

Adapters validate provider schemas and build normalized records only; they never
confirm corporate actions, rank candidates, or make any trading decision.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Final

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.records import (
    NormalizedSourceRecord,
    ProviderTimestampError,
    SourceSchemaDriftError,
    build_normalized_record,
    content_hash_of,
    parse_provider_timestamp,
    require_date,
    require_decimal_text,
    require_keys,
    require_type,
    schema_version,
    strict_json_loads,
)
from seven_lens.sources.roles import P4SourceFamily

_ASSET_STATUS: Final = frozenset({"active", "inactive"})
_ASSET_CLASSES: Final = frozenset({"us_equity"})
_EXCHANGES: Final = frozenset({"AMEX", "ARCA", "BATS", "NASDAQ", "NYSE"})
_FEEDS: Final = frozenset({"iex", "sip"})
_SPLIT_TYPES: Final = frozenset({"forward", "reverse"})
_CA_TYPES: Final = frozenset({"split"})
_SYMBOL: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_CUSIP: Final = re.compile(r"^[0-9A-Z]{9}$")
_RAW_CA_TYPE: Final = re.compile(r"^[a-z][a-z_-]{0,31}$")
_MAX_BARS: Final = 10_000


class FeedEntitlementError(ValueError):
    """Raised when the effective feed differs from the requested entitlement."""


def parse_assets(
    payload: bytes, *, retrieved_at: UtcTimestamp
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate the Alpaca asset list and build one record per tradable asset."""
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, list) or not decoded or len(decoded) > 10_000:
        raise SourceSchemaDriftError("asset payload must be a non-empty bounded array")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for asset in decoded:
        if not isinstance(asset, dict):
            raise SourceSchemaDriftError("asset entries must be objects")
        require_keys(
            asset,
            required={"id", "symbol", "exchange", "asset_class", "status", "tradable"},
            allowed={"id", "symbol", "exchange", "asset_class", "status", "tradable"},
        )
        asset_id = require_type(asset["id"], str, "id")
        if type(asset_id) is not str or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", asset_id):
            raise SourceSchemaDriftError("asset id is not a provider identifier")
        symbol = require_type(asset["symbol"], str, "symbol")
        if type(symbol) is not str or _SYMBOL.fullmatch(symbol) is None:
            raise SourceSchemaDriftError("asset symbol is not canonical")
        exchange = asset["exchange"]
        if exchange not in _EXCHANGES:
            raise SourceSchemaDriftError("asset exchange enum is unknown; fail closed")
        if asset["asset_class"] not in _ASSET_CLASSES:
            raise SourceSchemaDriftError("asset class enum is unknown; fail closed")
        if asset["status"] not in _ASSET_STATUS:
            raise SourceSchemaDriftError("asset status enum is unknown; fail closed")
        if type(asset["tradable"]) is not bool:
            raise SourceSchemaDriftError("asset tradable must be a bool")
        records.append(
            build_normalized_record(
                record_id=f"alpaca-asset-{asset_id}",
                family=P4SourceFamily.ALPACA_ASSETS,
                endpoint_id="assets_list",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                payload={
                    "id": asset_id,
                    "symbol": symbol,
                    "exchange": exchange,
                    "asset_class": asset["asset_class"],
                    "status": asset["status"],
                    "tradable": asset["tradable"],
                },
                material_claim=False,
            )
        )
    return tuple(records)


def parse_bars(
    payload: bytes,
    *,
    retrieved_at: UtcTimestamp,
    requested_feed: str,
    effective_feed: str,
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one delayed-SIP historical bar page with explicit feed identity."""
    if requested_feed not in _FEEDS or effective_feed not in _FEEDS:
        raise SourceSchemaDriftError("bar feeds must be iex or sip")
    if requested_feed != effective_feed:
        raise FeedEntitlementError(
            "effective feed differs from the requested entitlement; no silent fallback"
        )
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("bars payload must be an object")
    require_keys(
        decoded,
        required={"symbol", "bars"},
        allowed={"symbol", "bars", "next_page_token"},
    )
    symbol = require_type(decoded["symbol"], str, "symbol")
    if type(symbol) is not str or _SYMBOL.fullmatch(symbol) is None:
        raise SourceSchemaDriftError("bars symbol is not canonical")
    bars = require_type(decoded["bars"], list, "bars")
    if type(bars) is not list or len(bars) > _MAX_BARS:
        raise SourceSchemaDriftError("bars array is missing or oversized")
    normalized_bars: list[dict[str, object]] = []
    latest: UtcTimestamp | None = None
    for bar in bars:
        if not isinstance(bar, dict):
            raise SourceSchemaDriftError("bar entries must be objects")
        require_keys(
            bar,
            required={"t", "o", "h", "l", "c", "v"},
            allowed={"t", "o", "h", "l", "c", "v", "n"},
        )
        stamp = bar["t"] if type(bar["t"]) is str else None
        if stamp is None:
            raise SourceSchemaDriftError("bar timestamp must be text")
        try:
            bar_time = parse_provider_timestamp(stamp)
        except ProviderTimestampError as error:
            raise SourceSchemaDriftError("bar timestamp is not canonical") from error
        for price_field in ("o", "h", "l", "c"):
            price_text = require_decimal_text(bar[price_field], f"bar {price_field}")
            if Decimal(price_text) <= 0:
                raise SourceSchemaDriftError("bar prices must be positive")
        volume = require_type(bar["v"], int, "bar v")
        if type(volume) is not int or volume < 0:
            raise SourceSchemaDriftError("bar volume must be a non-negative integer")
        if latest is None or bar_time.value > latest.value:
            latest = bar_time
        normalized_bars.append(
            {
                "t": stamp,
                "o": bar["o"],
                "h": bar["h"],
                "l": bar["l"],
                "c": bar["c"],
                "v": volume,
            }
        )
    content_hash = content_hash_of(payload)
    next_page_token = decoded.get("next_page_token")
    if next_page_token is not None and type(next_page_token) is not str:
        raise SourceSchemaDriftError("next_page_token must be text or null")
    return (
        build_normalized_record(
            record_id=f"alpaca-bars-{symbol}-{content_hash[:16]}",
            family=P4SourceFamily.ALPACA_HISTORICAL_BARS,
            endpoint_id="stock_bars",
            schema_version=schema_version("1.0.0"),
            content_hash=content_hash,
            retrieved_at=retrieved_at,
            observation_at=latest,
            payload={
                "symbol": symbol,
                "feed": requested_feed,
                "bars": normalized_bars,
                "next_page_token": next_page_token,
            },
            material_claim=False,
        ),
    )


def parse_iex_quote(
    payload: bytes, *, retrieved_at: UtcTimestamp, symbol: str
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one latest IEX quote; records always carry the coverage warning."""
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("quote payload must be an object")
    require_keys(
        decoded,
        required={"symbol", "bid_price", "ask_price", "timestamp"},
        allowed={"symbol", "bid_price", "ask_price", "bid_size", "ask_size", "timestamp"},
    )
    if decoded["symbol"] != symbol:
        raise SourceSchemaDriftError("quote symbol does not match the request")
    bid, ask = decoded["bid_price"], decoded["ask_price"]
    for price in (bid, ask):
        if price is not None:
            require_decimal_text(price, "quote price")
    if bid is None and ask is None:
        raise SourceSchemaDriftError("quote without bid and ask is malformed")
    stamp = require_timestamp_text(decoded["timestamp"])
    content_hash = content_hash_of(payload)
    sizes: dict[str, int] = {}
    for size_field in ("bid_size", "ask_size"):
        if size_field in decoded:
            value = require_type(decoded[size_field], int, size_field)
            if type(value) is not int or value < 0:
                raise SourceSchemaDriftError("quote sizes must be non-negative integers")
            sizes[size_field] = value
    payload_obj: dict[str, object] = {
        "symbol": symbol,
        "bid_price": bid,
        "ask_price": ask,
        "timestamp": decoded["timestamp"],
        "feed": "iex",
    }
    payload_obj.update(sizes)
    return (
        build_normalized_record(
            record_id=f"alpaca-iex-quote-{symbol}-{content_hash[:16]}",
            family=P4SourceFamily.ALPACA_IEX_QUOTES,
            endpoint_id="latest_quote",
            schema_version=schema_version("1.0.0"),
            content_hash=content_hash,
            retrieved_at=retrieved_at,
            observation_at=stamp,
            payload=payload_obj,
            material_claim=False,
            coverage_warning="IEX feed only; not full NBBO/SIP market coverage",
        ),
    )


def parse_corporate_actions(
    payload: bytes, *, retrieved_at: UtcTimestamp
) -> tuple[NormalizedSourceRecord, ...]:
    """Parse split notices as detection-only records; P4-A never confirms them."""
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("corporate actions payload must be an object")
    require_keys(
        decoded,
        required={"corporate_actions"},
        allowed={"corporate_actions", "next_page_token"},
    )
    entries = require_type(decoded["corporate_actions"], list, "corporate_actions")
    if type(entries) is not list or len(entries) > 1_000:
        raise SourceSchemaDriftError("corporate action entries are missing or oversized")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise SourceSchemaDriftError("corporate action entries must be objects")
        require_keys(
            entry,
            required={"type"},
            allowed={
                "type",
                "split_type",
                "cusip",
                "symbol",
                "ex_date",
                "record_date",
                "payment_date",
                "ratio",
            },
        )
        raw_type = entry["type"]
        if type(raw_type) is not str or _RAW_CA_TYPE.fullmatch(raw_type) is None:
            raise SourceSchemaDriftError("corporate action type must be bounded text")
        supported = raw_type in _CA_TYPES
        split_type: object = None
        identity: object = None
        ex_date: object = None
        ratio: object = None
        if supported:
            if "split_type" not in entry or entry["split_type"] not in _SPLIT_TYPES:
                raise SourceSchemaDriftError("split entries require a forward/reverse type")
            split_type = entry["split_type"]
            if entry.get("cusip") is not None:
                if type(entry["cusip"]) is not str or _CUSIP.fullmatch(entry["cusip"]) is None:
                    raise SourceSchemaDriftError("corporate action cusip is not canonical")
                identity = entry["cusip"]
            elif entry.get("symbol") is not None:
                if type(entry["symbol"]) is not str or _SYMBOL.fullmatch(entry["symbol"]) is None:
                    raise SourceSchemaDriftError("corporate action symbol is not canonical")
                identity = entry["symbol"]
            else:
                raise SourceSchemaDriftError("corporate action entries require an identity")
            if entry.get("ex_date") is not None:
                ex_date = str(require_date(entry["ex_date"], "ex_date"))
            if entry.get("ratio") is not None:
                ratio = require_decimal_text(entry["ratio"], "ratio")
        else:
            identity = entry.get("symbol") if isinstance(entry.get("symbol"), str) else None
        complete = bool(supported and split_type and identity and ratio and ex_date)
        record_identity = identity if isinstance(identity, str) else raw_type
        payload_obj: dict[str, object] = {
            "type": raw_type,
            "split_type": split_type,
            "cusip": entry.get("cusip"),
            "symbol": entry.get("symbol"),
            "ex_date": ex_date,
            "record_date": entry.get("record_date"),
            "payment_date": entry.get("payment_date"),
            "ratio": ratio,
            "supported": supported,
            "complete": complete,
            "detection_only": True,
        }
        records.append(
            build_normalized_record(
                record_id=f"alpaca-ca-{record_identity}-{content_hash[:12]}-{len(records)}",
                family=P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
                endpoint_id="corporate_actions",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                payload=payload_obj,
                material_claim=False,
            )
        )
    return tuple(records)


def require_timestamp_text(value: object) -> UtcTimestamp:
    if type(value) is not str:
        raise SourceSchemaDriftError("timestamp must be text")
    try:
        return parse_provider_timestamp(value)
    except ProviderTimestampError as error:
        raise SourceSchemaDriftError("timestamp is not a bounded provider stamp") from error
