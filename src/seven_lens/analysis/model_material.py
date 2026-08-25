"""Pure projections of accepted domain facts into P3-E untrusted model data."""

from __future__ import annotations

from seven_lens.analysis.proposal_contracts import ResearchBundle
from seven_lens.domain.json_values import JsonObject, JsonValue
from seven_lens.sources.contracts import EvidencePacket


def evidence_packet_model_material(packet: EvidencePacket) -> dict[str, JsonValue]:
    """Project verified evidence without URLs, credentials, or storage capabilities."""

    if type(packet) is not EvidencePacket:
        raise ValueError("model evidence material requires an exact EvidencePacket")
    try:
        packet.validate_integrity()
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("model evidence material is invalid") from error
    material: dict[str, JsonValue] = {
        "packet": {
            "schema_version": str(packet.schema_version),
            "packet_id": str(packet.packet_id),
            "as_of": str(packet.as_of),
            "packet_hash": packet.packet_hash,
            "universe_hash": packet.universe_hash,
            "portfolio_snapshot_hash": packet.portfolio_snapshot_hash,
            "data_snapshot_refs": list(packet.data_snapshot_refs),
            "producer_version": packet.producer_version,
            "freshness_status": packet.freshness_status.value,
            "status": packet.status.value,
        },
        "source_summaries": [
            {
                "source_id": source.source_id,
                "publisher": source.publisher,
                "source_family": source.source_family.value,
                "source_kind": source.source_kind.value,
                "published_at": None if source.published_at is None else str(source.published_at),
                "available_at": None if source.available_at is None else str(source.available_at),
                "content_hash": source.content_hash,
                "language": source.language,
                "primary_source": source.primary_source,
                "ticker_tags": list(source.ticker_tags),
                "claim_tags": list(source.claim_tags),
            }
            for source in packet.source_records
        ],
        "approved_fragments": [
            {
                "fragment_id": fragment.fragment_id,
                "source_id": fragment.source_id,
                "content_hash": fragment.content_hash,
                "excerpt": fragment.excerpt,
                "available_at": str(fragment.available_at),
                "verified": fragment.verified,
                "prompt_injection_flags": list(fragment.prompt_injection_flags),
            }
            for fragment in packet.fragments
        ],
        "verified_claims": [
            {
                "claim_id": claim.claim_id,
                "statement": claim.statement,
                "fragment_refs": list(claim.fragment_refs),
                "material": claim.material,
            }
            for claim in packet.claims
        ],
    }
    # Normalize now so unsupported values or accidental structural drift fail before
    # any envelope/transport boundary is reached.
    return JsonObject.from_value(material).to_dict()


def research_bundle_model_material(bundle: ResearchBundle) -> dict[str, JsonValue]:
    """Expose exact completed TraderPlan material without repository lookup capability."""

    if type(bundle) is not ResearchBundle:
        raise ValueError("model research material requires an exact ResearchBundle")
    try:
        bundle.validate_integrity()
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("model research material is invalid") from error
    return JsonObject.from_value({"research_bundle": bundle.to_wire()}).to_dict()
