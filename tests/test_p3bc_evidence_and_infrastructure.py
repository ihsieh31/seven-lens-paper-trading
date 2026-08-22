from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from seven_lens.infrastructure.content_store import ContentStoreError, FileContentStore
from seven_lens.infrastructure.source_http import (
    GetRequest,
    GetResponse,
    SourceHttpAdapter,
    SourceTransportError,
)
from seven_lens.market_data.events import (
    EventCandidate,
    EventKind,
    EventReason,
    MarketObservation,
    verify_event,
)
from seven_lens.sources.contracts import (
    SOURCE_SCHEMA_VERSION,
    AccessMethod,
    EvidenceClaim,
    EvidencePacket,
    EvidenceStatus,
    FreshnessStatus,
    RightsStatus,
    RobotsStatus,
    SourceFamily,
    SourceFragment,
    SourceKind,
    SourceRecord,
    build_evidence_packet,
)
from test_analysis_contracts import analysis_input, rid, timestamp


def source_record(
    *, available_minutes: int = 0, family: SourceFamily = SourceFamily.SEC
) -> SourceRecord:
    return SourceRecord(
        "source.1",
        "https://www.sec.gov/fixture",
        "SEC",
        family,
        SourceKind.FILING,
        AccessMethod.LICENSED_FIXTURE,
        timestamp(),
        timestamp(),
        timestamp(max(available_minutes, 0)),
        timestamp(available_minutes),
        "a" * 64,
        "text/plain",
        "en",
        RightsStatus.ALLOWED,
        RobotsStatus.NOT_APPLICABLE,
        True,
        ("MSFT",),
        ("claim.revenue",),
    )


def evidence_packet() -> EvidencePacket:
    inp = analysis_input()
    return build_evidence_packet(
        schema_version=SOURCE_SCHEMA_VERSION,
        packet_id=rid(50),
        as_of=inp.as_of,
        source_records=(source_record(),),
        fragments=(
            SourceFragment("evidence.1", "source.1", "a" * 64, "fixture", timestamp(), True),
        ),
        claims=(EvidenceClaim("claim.1", "Revenue increased", ("evidence.1",), True),),
        contradiction_claim_ids=(),
        missing_evidence=(),
        freshness_status=FreshnessStatus.FRESH,
        status=EvidenceStatus.VERIFIED,
        universe_hash=inp.universe_hash,
        portfolio_snapshot_hash=inp.portfolio_snapshot.content_hash,
        data_snapshot_refs=inp.data_snapshot_refs,
        producer_version="p3bc.1",
    )


def unsafe_packet(base: EvidencePacket, **changes: object) -> EvidencePacket:
    packet = object.__new__(EvidencePacket)
    for name in EvidencePacket.__slots__:
        object.__setattr__(packet, name, changes.get(name, getattr(base, name)))
    return packet


def test_point_in_time_packet_hash_citation_and_future_rejection() -> None:
    packet = evidence_packet()
    assert packet.packet_hash == packet.compute_hash()
    assert packet.citation_ids == {"evidence.1"}
    with pytest.raises(ValueError, match="point-in-time"):
        build_evidence_packet(
            schema_version=SOURCE_SCHEMA_VERSION,
            packet_id=rid(51),
            as_of=timestamp(),
            source_records=(source_record(available_minutes=1),),
            fragments=(
                SourceFragment("evidence.1", "source.1", "a" * 64, "fixture", timestamp(1), True),
            ),
            claims=(EvidenceClaim("claim.1", "Revenue increased", ("evidence.1",), True),),
            contradiction_claim_ids=(),
            missing_evidence=(),
            freshness_status=FreshnessStatus.FRESH,
            status=EvidenceStatus.VERIFIED,
            universe_hash="b" * 64,
            portfolio_snapshot_hash="c" * 64,
            data_snapshot_refs=("market.1",),
            producer_version="p3bc.1",
        )


@pytest.mark.parametrize("timestamp_field", ["retrieved_at", "published_at"])
def test_verified_packet_rejects_future_source_timestamps(timestamp_field: str) -> None:
    base = evidence_packet()
    if timestamp_field == "retrieved_at":
        source = replace(base.source_records[0], retrieved_at=timestamp(1))
    else:
        source = replace(base.source_records[0], published_at=timestamp(1))
    with pytest.raises(ValueError, match="point-in-time"):
        build_evidence_packet(
            schema_version=base.schema_version,
            packet_id=base.packet_id,
            as_of=base.as_of,
            source_records=(source,),
            fragments=base.fragments,
            claims=base.claims,
            contradiction_claim_ids=base.contradiction_claim_ids,
            missing_evidence=base.missing_evidence,
            freshness_status=base.freshness_status,
            status=base.status,
            universe_hash=base.universe_hash,
            portfolio_snapshot_hash=base.portfolio_snapshot_hash,
            data_snapshot_refs=base.data_snapshot_refs,
            producer_version=base.producer_version,
        )


def test_packet_hash_commits_every_nested_source_fragment_and_claim_field() -> None:
    packet = evidence_packet()
    source = packet.source_records[0]
    fragment = packet.fragments[0]
    claim = packet.claims[0]
    source_variants = (
        replace(source, source_id="source.2"),
        replace(source, canonical_url="https://www.sec.gov/other"),
        replace(source, publisher="SEC fixture"),
        replace(source, source_family=SourceFamily.ISSUER),
        replace(source, source_kind=SourceKind.ISSUER_RELEASE),
        replace(source, access_method=AccessMethod.HTTPS_GET),
        replace(source, published_at=None),
        replace(source, discovered_at=timestamp(-1)),
        replace(source, retrieved_at=timestamp(1)),
        replace(source, available_at=timestamp(-1)),
        replace(source, content_hash="b" * 64),
        replace(source, content_type="text/html"),
        replace(source, language="zh-TW"),
        replace(source, rights_status=RightsStatus.METADATA_ONLY),
        replace(source, robots_status=RobotsStatus.ALLOWED),
        replace(source, primary_source=False),
        replace(source, ticker_tags=("AAPL",)),
        replace(source, claim_tags=("claim.margin",)),
        replace(source, supersedes="source.0"),
        replace(source, tombstone=True),
    )
    fragment_variants = (
        replace(fragment, fragment_id="evidence.2"),
        replace(fragment, source_id="source.2"),
        replace(fragment, content_hash="b" * 64),
        replace(fragment, excerpt="different fixture"),
        replace(fragment, available_at=timestamp(-1)),
        replace(fragment, verified=False),
        replace(fragment, prompt_injection_flags=("instruction.override",)),
    )
    claim_variants = (
        replace(claim, claim_id="claim.2"),
        replace(claim, statement="Revenue decreased"),
        replace(claim, fragment_refs=("evidence.2",)),
        replace(claim, material=False),
    )
    assert len(source_variants) == len(SourceRecord.__slots__)
    assert len(fragment_variants) == len(SourceFragment.__slots__)
    assert len(claim_variants) == len(EvidenceClaim.__slots__)
    for source_variant in source_variants:
        assert (
            unsafe_packet(packet, source_records=(source_variant,)).compute_hash()
            != packet.packet_hash
        )
    for fragment_variant in fragment_variants:
        assert (
            unsafe_packet(packet, fragments=(fragment_variant,)).compute_hash()
            != packet.packet_hash
        )
    for claim_variant in claim_variants:
        assert unsafe_packet(packet, claims=(claim_variant,)).compute_hash() != packet.packet_hash


@pytest.mark.parametrize(
    ("freshness", "contradictions", "missing"),
    [
        (FreshnessStatus.STALE, (), ()),
        (FreshnessStatus.FRESH, ("claim.1",), ()),
        (FreshnessStatus.FRESH, (), ("missing.1",)),
    ],
)
def test_verified_packet_requires_fresh_complete_contradiction_free_evidence(
    freshness: FreshnessStatus,
    contradictions: tuple[str, ...],
    missing: tuple[str, ...],
) -> None:
    base = evidence_packet()
    with pytest.raises(ValueError, match="fresh, complete"):
        build_evidence_packet(
            schema_version=base.schema_version,
            packet_id=base.packet_id,
            as_of=base.as_of,
            source_records=base.source_records,
            fragments=base.fragments,
            claims=base.claims,
            contradiction_claim_ids=contradictions,
            missing_evidence=missing,
            freshness_status=freshness,
            status=EvidenceStatus.VERIFIED,
            universe_hash=base.universe_hash,
            portfolio_snapshot_hash=base.portfolio_snapshot_hash,
            data_snapshot_refs=base.data_snapshot_refs,
            producer_version=base.producer_version,
        )


def test_source_record_rejects_explicit_https_port() -> None:
    with pytest.raises(ValueError, match="canonical HTTPS"):
        replace(source_record(), canonical_url="https://www.sec.gov:444/fixture")


def test_packet_rejects_fragment_predating_its_source_availability() -> None:
    inp = analysis_input()
    with pytest.raises(ValueError, match="predates"):
        build_evidence_packet(
            schema_version=SOURCE_SCHEMA_VERSION,
            packet_id=rid(52),
            as_of=inp.as_of,
            source_records=(source_record(),),
            fragments=(
                # The source is available at as_of; a fragment claiming earlier
                # availability than its own source is point-in-time infeasible.
                SourceFragment("evidence.1", "source.1", "a" * 64, "fixture", timestamp(-1), True),
            ),
            claims=(EvidenceClaim("claim.1", "Revenue increased", ("evidence.1",), True),),
            contradiction_claim_ids=(),
            missing_evidence=(),
            freshness_status=FreshnessStatus.FRESH,
            status=EvidenceStatus.VERIFIED,
            universe_hash=inp.universe_hash,
            portfolio_snapshot_hash=inp.portfolio_snapshot.content_hash,
            data_snapshot_refs=inp.data_snapshot_refs,
            producer_version="p3bc.1",
        )


def test_content_store_recomputes_hash_is_atomic_and_rejects_escape(tmp_path: Path) -> None:
    store = FileContentStore(tmp_path / "cas", maximum_bytes=16)
    stored = store.put(b"fixture")
    assert store.get(stored.content_hash) == b"fixture"
    assert store.put(b"fixture", declared_hash=stored.content_hash) == stored
    with pytest.raises(ContentStoreError, match="declared"):
        store.put(b"fixture", declared_hash="0" * 64)
    with pytest.raises(ContentStoreError, match="oversized"):
        store.put(b"x" * 17)
    with pytest.raises(ContentStoreError, match="hash"):
        store.get("../escape")

    target = tmp_path / "cas" / stored.content_hash[:2] / stored.content_hash
    target.write_bytes(b"corrupt")
    with pytest.raises(ContentStoreError, match="collision"):
        store.put(b"fixture")

    real_root = tmp_path / "real"
    real_root.mkdir()
    symlink_root = tmp_path / "link"
    symlink_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(ContentStoreError, match="symlink"):
        FileContentStore(symlink_root)


class FakeTransport:
    def __init__(self, response: GetResponse | BaseException) -> None:
        self.response = response
        self.requests: list[GetRequest] = []

    def get(self, request: GetRequest) -> GetResponse:
        self.requests.append(request)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def test_source_adapter_is_deterministic_get_only_and_non_echo() -> None:
    url = "https://www.sec.gov/fixture"
    transport = FakeTransport(GetResponse(200, "text/plain; charset=utf-8", b"ok", url))
    adapter = SourceHttpAdapter(
        transport,
        allowed_hosts=frozenset({"www.sec.gov"}),
        allowed_content_types=frozenset({"text/plain"}),
    )
    assert adapter.fetch(url).body == b"ok"
    assert adapter.fetch(url).body == b"ok"
    assert transport.requests[0].request_id == transport.requests[1].request_id

    marker = "SECRET-RESPONSE-BODY"
    denied = SourceHttpAdapter(
        FakeTransport(GetResponse(429, "text/plain", marker.encode(), url)),
        allowed_hosts=frozenset({"www.sec.gov"}),
        allowed_content_types=frozenset({"text/plain"}),
    )
    with pytest.raises(SourceTransportError) as caught:
        denied.fetch(url)
    assert marker not in str(caught.value)

    with pytest.raises(SourceTransportError, match="allowlist"):
        adapter.fetch("https://www.sec.gov:444/fixture")


@pytest.mark.parametrize(
    "response",
    [
        GetResponse(200, "text/plain", b"ok", "https://www.sec.gov/redirected"),
        GetResponse(200, "application/octet-stream", b"ok", "https://www.sec.gov/fixture"),
        GetResponse(200, "text/plain", b"x" * 11, "https://www.sec.gov/fixture"),
        TimeoutError("secret timeout detail"),
    ],
)
def test_source_adapter_rejects_redirect_type_oversize_and_timeout(
    response: GetResponse | BaseException,
) -> None:
    adapter = SourceHttpAdapter(
        FakeTransport(response),
        allowed_hosts=frozenset({"www.sec.gov"}),
        allowed_content_types=frozenset({"text/plain"}),
        maximum_bytes=10,
    )
    with pytest.raises(SourceTransportError) as caught:
        adapter.fetch("https://www.sec.gov/fixture")
    assert "secret timeout detail" not in str(caught.value)


def observations(
    family: SourceFamily, prices: tuple[str, str, str]
) -> tuple[MarketObservation, ...]:
    return tuple(
        MarketObservation(
            f"{family.value}.{index}",
            "MSFT",
            family,
            timestamp(index - 3),
            Decimal(price),
            100 + index,
            timestamp(1),
        )
        for index, price in enumerate(prices, 1)
    )


def test_event_verifier_requires_two_families_three_fresh_samples() -> None:
    first = observations(SourceFamily.MARKET_VENDOR, ("100", "101", "102"))
    second = observations(SourceFamily.EXCHANGE, ("100", "101", "102"))
    candidate = EventCandidate(
        "event.1", EventKind.PRICE_VOLUME, "MSFT", timestamp(), first + second
    )
    assert verify_event(candidate).reason is EventReason.VERIFIED
    assert (
        verify_event(replace(candidate, observations=first)).reason
        is EventReason.SOURCE_FAMILY_COLLISION
    )
    assert (
        verify_event(replace(candidate, observations=first + second[:2])).reason
        is EventReason.INSUFFICIENT_SAMPLES
    )
    assert verify_event(replace(candidate, as_of=timestamp(2))).reason is EventReason.STALE
    assert verify_event(replace(candidate, as_of=timestamp(-3))).reason is EventReason.FUTURE
    conflicting = observations(SourceFamily.EXCHANGE, ("110", "111", "112"))
    assert (
        verify_event(replace(candidate, observations=first + conflicting)).reason
        is EventReason.DATA_CONFLICT
    )
    assert (
        verify_event(replace(candidate, observations=tuple(reversed(first)) + second)).reason
        is EventReason.OUT_OF_ORDER
    )


def test_official_primary_news_is_single_source_verified() -> None:
    candidate = EventCandidate(
        "event.2",
        EventKind.NEWS,
        "MSFT",
        timestamp(),
        news_source_families=(SourceFamily.SEC,),
        news_source_kind=SourceKind.FILING,
        news_primary=True,
    )
    assert verify_event(candidate).reason is EventReason.PRIMARY_OFFICIAL
    spoofed = replace(candidate, news_source_families=(SourceFamily.SEARCH,))
    assert verify_event(spoofed).reason is EventReason.UNVERIFIED
