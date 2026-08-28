# mypy: ignore-errors
"""P4-A GDELT adapter tests: discovery articles with seen-time semantics."""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.gdelt import parse_doc_articles
from seven_lens.sources.adapters.records import SourceSchemaDriftError
from seven_lens.sources.roles import SourceRole

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_GDELT_JSON = b"""{
  "articles": [
    {"url": "https://news.example/article/2026/split",
     "title": "Filing hints at split",
     "seendate": "20260827T120000Z",
     "domain": "news.example",
     "language": "English",
     "sourcecountry": "United States"}
  ]
}"""


def test_parse_doc_articles_builds_discovery_records_with_seen_time() -> None:
    records = parse_doc_articles(_GDELT_JSON, retrieved_at=_RETRIEVED, query="split filing")

    assert len(records) == 1
    record = records[0]
    assert record.role is SourceRole.DISCOVERY
    assert record.material_claim is False
    assert str(record.observation_at) == "2026-08-27T12:00:00.000000Z"
    assert record.published_at is None
    payload = record.payload.to_dict()
    assert payload["domain"] == "news.example"
    assert record.record_id.startswith("gdelt-discovery-")


def test_parse_doc_articles_rejects_bad_seen_dates_or_urls() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_doc_articles(
            _GDELT_JSON.replace(b'"20260827T120000Z"', b'"2026-08-27 12:00:00"'),
            retrieved_at=_RETRIEVED,
            query="split filing",
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_doc_articles(
            _GDELT_JSON.replace(b"https://news.example", b"http://news.example"),
            retrieved_at=_RETRIEVED,
            query="split filing",
        )
