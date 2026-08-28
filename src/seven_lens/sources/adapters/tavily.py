"""Tavily adapter: parse-only search results; discovery records, never material."""

from __future__ import annotations

import re
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

_HTTPS_URL: Final = re.compile(
    r"^https://[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9](:\d{2,5})?(/[^\s]*)?$"
)
_MAX_RESULTS: Final = 100


def parse_search_results(
    payload: bytes, *, retrieved_at: UtcTimestamp, query: str
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one Tavily search payload; snippet and score stay discovery-grade."""
    if type(query) is not str or not 1 <= len(query) <= 512:
        raise SourceSchemaDriftError("search query must be bounded text")
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("tavily payload must be an object")
    require_keys(
        decoded,
        required={"query", "results"},
        allowed={"query", "results", "response_time"},
    )
    if decoded["query"] != query:
        raise SourceSchemaDriftError("search query does not match the request")
    results = require_type(decoded["results"], list, "results")
    if type(results) is not list or len(results) > _MAX_RESULTS:
        raise SourceSchemaDriftError("results must be a bounded array")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for result in results:
        if not isinstance(result, dict):
            raise SourceSchemaDriftError("result entries must be objects")
        require_keys(
            result,
            required={"title", "url", "content", "score"},
            allowed={"title", "url", "content", "score"},
        )
        title = result["title"]
        if type(title) is not str or not 1 <= len(title) <= 512:
            raise SourceSchemaDriftError("result title must be bounded text")
        url = result["url"]
        if type(url) is not str or _HTTPS_URL.fullmatch(url) is None or len(url) > 2048:
            raise SourceSchemaDriftError("result url must be canonical HTTPS")
        snippet = result["content"]
        if type(snippet) is not str or not 1 <= len(snippet) <= 4096:
            raise SourceSchemaDriftError("result snippet must be bounded text")
        score = result["score"]
        if type(score) is not int and type(score) is not float:
            raise SourceSchemaDriftError("result score must be a number")
        if not 0 <= float(score) <= 1:
            raise SourceSchemaDriftError("result score must be normalized")
        records.append(
            build_normalized_record(
                record_id=f"tavily-discovery-{content_hash[:16]}-{len(records)}",
                family=P4SourceFamily.TAVILY,
                endpoint_id="tavily_search",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                payload=canonical_payload(
                    {
                        "query": query,
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "score": score,
                    }
                ),
                material_claim=False,
            )
        )
    return tuple(records)
