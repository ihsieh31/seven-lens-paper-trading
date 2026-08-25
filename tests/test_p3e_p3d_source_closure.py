from __future__ import annotations

import pytest

from seven_lens.analysis.contracts import ContractMeta
from seven_lens.analysis.proposal_contracts import build_proposal_context
from test_analysis_contracts import rid, timestamp
from test_p3d_proposal_contracts import (
    bundle,
    context,
    refreshed_snapshot,
    rejection,
)
from test_p3e_envelope_and_prompt import _p3d_envelope


def test_context_versions_must_match_the_frozen_bundle_producer_versions() -> None:
    foreign = context(graph_version="foreign.1")

    with pytest.raises(ValueError, match="foreign or stale"):
        _p3d_envelope(source_context=foreign)


def test_attempt_one_context_must_use_the_bundle_portfolio_snapshot() -> None:
    refreshed = context(snapshot=refreshed_snapshot())

    with pytest.raises(ValueError, match="foreign or stale"):
        _p3d_envelope(source_context=refreshed)


def test_retry_previous_context_must_be_the_exact_attempt_one_derivation() -> None:
    superseded = rid(11)
    foreign = context(
        2,
        previous_context_id=rid(99),
        superseded_proposal_id=superseded,
        feedback=rejection(superseded),
    )

    with pytest.raises(ValueError, match="foreign or stale"):
        _p3d_envelope(source_context=foreign)


def test_context_created_at_must_match_bundle() -> None:
    built_bundle = bundle()
    base = context(bundle=built_bundle)
    foreign = build_proposal_context(
        meta=ContractMeta(
            base.meta.schema_version,
            base.context_id,
            timestamp(1),
            base.meta.producer_version,
        ),
        attempt=base.attempt,
        bundle=built_bundle,
        snapshot=base.snapshot,
        allowed_symbols=base.allowed_symbols,
        graph_version=base.graph_version,
        prompt_version=base.prompt_version,
        model_version=base.model_version,
        provider_version=base.provider_version,
        data_version=base.data_version,
        memory_version=base.memory_version,
    )

    with pytest.raises(ValueError, match="foreign or stale"):
        _p3d_envelope(source_context=foreign)
