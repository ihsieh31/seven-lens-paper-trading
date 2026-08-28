# mypy: ignore-errors
"""P4-A Tavily adapter tests: discovery-only search results, scores never material."""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.records import SourceSchemaDriftError
from seven_lens.sources.adapters.tavily import parse_search_results
from seven_lens.sources.roles import SourceRole

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_TAVILY_JSON = b"""{
  "query": "AAPL reverse split announcement",
  "results": [
    {"title": "Apple announces reverse split",
     "url": "https://investor.example/news/0001",
     "content": "Apple today announced a reverse split pending approval.",
     "score": 0.91}
  ],
  "response_time": 0.42
}"""


def test_parse_search_results_builds_discovery_records_only() -> None:
    records = parse_search_results(
        _TAVILY_JSON, retrieved_at=_RETRIEVED, query="AAPL reverse split announcement"
    )

    assert len(records) == 1
    record = records[0]
    assert record.role is SourceRole.DISCOVERY
    assert record.material_claim is False
    payload = record.payload.to_dict()
    assert payload["score"] == 0.91
    assert payload["snippet"] == "Apple today announced a reverse split pending approval."
    assert record.record_id.startswith("tavily-discovery-")


def test_parse_search_results_rejects_query_mismatch_or_drift() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_search_results(_TAVILY_JSON, retrieved_at=_RETRIEVED, query="other query")
    with pytest.raises(SourceSchemaDriftError):
        parse_search_results(
            _TAVILY_JSON.replace(b'"score": 0.91', b'"score": "0.91"'),
            retrieved_at=_RETRIEVED,
            query="AAPL reverse split announcement",
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_search_results(
            _TAVILY_JSON.replace(b"https://investor.example", b"http://investor.example"),
            retrieved_at=_RETRIEVED,
            query="AAPL reverse split announcement",
        )
