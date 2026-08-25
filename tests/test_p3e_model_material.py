from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from seven_lens.analysis.model_material import (
    evidence_packet_model_material,
    research_bundle_model_material,
)
from test_p3bc_evidence_and_infrastructure import evidence_packet
from test_p3d_proposal_contracts import bundle


def test_evidence_projection_has_verified_material_and_no_source_url() -> None:
    material = evidence_packet_model_material(evidence_packet())
    view = cast(dict[str, Any], material)
    assert view["verified_claims"][0]["statement"] == "Revenue increased"
    assert view["approved_fragments"][0]["excerpt"] == "fixture"
    rendered = repr(material).lower()
    assert "https://" not in rendered
    assert "canonical_url" not in rendered
    assert "credential" not in rendered


def test_research_projection_carries_exact_full_trader_plan_material() -> None:
    built = bundle()
    material = research_bundle_model_material(built)
    view = cast(dict[str, Any], material)
    items = view["research_bundle"]["items"]
    assert items[0]["trader_plan"] == built.items[0].trader_plan.to_wire()
    assert items[0]["trader_plan_hash"] == built.items[0].trader_plan_hash


def test_material_projection_revalidates_tampered_authority() -> None:
    packet = evidence_packet()
    forged = object.__new__(type(packet))
    for field in packet.__slots__:
        object.__setattr__(forged, field, getattr(packet, field))
    object.__setattr__(forged, "packet_hash", "0" * 64)
    with pytest.raises(ValueError, match="invalid"):
        evidence_packet_model_material(forged)

    built = bundle()
    with pytest.raises(ValueError, match=r"invalid|does not match"):
        research_bundle_model_material(replace(built, bundle_hash="0" * 64))
