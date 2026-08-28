"""yfinance adapter: research-supplement chart quotes with unverified rights."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.records import (
    NormalizedSourceRecord,
    SourceSchemaDriftError,
    build_normalized_record,
    canonical_payload,
    content_hash_of,
    require_keys,
    require_type,
    schema_version,
    strict_json_loads,
)
from seven_lens.sources.roles import P4SourceFamily

_MAX_MARKET_TIME_SECONDS: Final = 4_102_444_800  # 2100-01-01; guards epoch-text confusion


def parse_chart_quote(
    payload: bytes, *, retrieved_at: UtcTimestamp, symbol: str
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one Yahoo chart payload; it never fills an authority gap."""
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("yfinance payload must be an object")
    require_keys(decoded, required={"chart"}, allowed={"chart"})
    chart = require_type(decoded["chart"], dict, "chart")
    if type(chart) is not dict:
        raise SourceSchemaDriftError("chart must be an object")
    require_keys(chart, required={"result", "error"}, allowed={"result", "error"})
    if chart["error"] is not None:
        raise SourceSchemaDriftError("yfinance response carried an error payload")
    results = require_type(chart["result"], list, "result")
    if type(results) is not list or len(results) != 1:
        raise SourceSchemaDriftError("chart result must carry exactly one entry")
    result = results[0]
    if not isinstance(result, dict):
        raise SourceSchemaDriftError("chart result entries must be objects")
    require_keys(result, required={"meta"}, allowed={"meta"})
    meta = require_type(result["meta"], dict, "meta")
    if type(meta) is not dict:
        raise SourceSchemaDriftError("meta must be an object")
    require_keys(
        meta,
        required={"symbol", "regularMarketPrice", "regularMarketTime"},
        allowed={
            "symbol",
            "regularMarketPrice",
            "regularMarketTime",
            "exchangeName",
            "currency",
        },
    )
    if meta["symbol"] != symbol:
        raise SourceSchemaDriftError("chart symbol does not match the request")
    price = meta["regularMarketPrice"]
    if type(price) is not int and type(price) is not float:
        raise SourceSchemaDriftError("regular market price must be a number")
    market_time = meta["regularMarketTime"]
    if type(market_time) is not int or not 0 < market_time < _MAX_MARKET_TIME_SECONDS:
        raise SourceSchemaDriftError("regular market time must be bounded epoch seconds")
    observation_at = UtcTimestamp(datetime.fromtimestamp(market_time, tz=UTC))
    content_hash = content_hash_of(payload)
    return (
        build_normalized_record(
            record_id=f"yfinance-chart-{symbol}-{content_hash[:16]}",
            family=P4SourceFamily.YFINANCE,
            endpoint_id="yahoo_chart",
            schema_version=schema_version("1.0.0"),
            content_hash=content_hash,
            retrieved_at=retrieved_at,
            observation_at=observation_at,
            payload=canonical_payload(
                {
                    "symbol": symbol,
                    "regular_market_price": price,
                    "regular_market_time": market_time,
                    "exchange_name": meta.get("exchangeName"),
                    "currency": meta.get("currency"),
                    "supplement_only": True,
                }
            ),
            material_claim=False,
        ),
    )
