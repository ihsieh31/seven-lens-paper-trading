# mypy: ignore-errors
"""P4-A yfinance adapter tests: supplement-grade chart data, rights UNKNOWN."""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.records import SourceSchemaDriftError
from seven_lens.sources.adapters.yfinance import parse_chart_quote
from seven_lens.sources.contracts import RightsStatus
from seven_lens.sources.roles import SourceRole

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_YFINANCE_JSON = b"""{
  "chart": {
    "result": [
      {"meta": {"symbol": "AAPL", "regularMarketPrice": 250.87,
       "regularMarketTime": 1787839200, "exchangeName": "NMS",
       "currency": "USD"}}
    ],
    "error": null
  }
}"""


def test_parse_chart_quote_builds_supplement_records_with_unknown_rights() -> None:
    records = parse_chart_quote(_YFINANCE_JSON, retrieved_at=_RETRIEVED, symbol="AAPL")

    assert len(records) == 1
    record = records[0]
    assert record.role is SourceRole.RESEARCH_SUPPLEMENT
    assert record.rights is RightsStatus.UNKNOWN
    assert record.material_claim is False
    payload = record.payload.to_dict()
    assert payload["symbol"] == "AAPL"
    assert payload["regular_market_price"] == 250.87
    assert record.record_id.startswith("yfinance-chart-AAPL-")


def test_parse_chart_quote_rejects_error_payloads_or_symbol_mismatch() -> None:
    error_payload = b'{"chart": {"result": null, "error": {"code": "Bad Request"}}}'
    with pytest.raises(SourceSchemaDriftError):
        parse_chart_quote(error_payload, retrieved_at=_RETRIEVED, symbol="AAPL")
    with pytest.raises(SourceSchemaDriftError):
        parse_chart_quote(_YFINANCE_JSON, retrieved_at=_RETRIEVED, symbol="MSFT")
