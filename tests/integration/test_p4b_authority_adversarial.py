# mypy: ignore-errors
"""Adversarial PostgreSQL boundary probes for the P4-B authority contract."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_p4b_security_master_postgres import (
    _DECISION_HASH_DOMAIN,
    _SCHEMA,
    _SECURITY_ID,
    _SOURCE_RECORD_HASH_DOMAIN,
    _T0,
    _T1,
    _canonical_hash,
    _source,
)
from test_p4b_security_master_postgres import (
    p4b_runtime_postgres as _p4b_runtime_postgres_fixture,
)

from seven_lens.infrastructure.postgres_securities import (
    PostgresP4RecordLog,
    PostgresSecurityMaster,
)
from seven_lens.securities.contracts import (
    AssetClass,
    ListingExchange,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
    build_identity_record,
)
from seven_lens.securities.quarantine import master_version_for
from seven_lens.sources.roles import P4SourceFamily

p4b_runtime_postgres = _p4b_runtime_postgres_fixture

pytestmark = pytest.mark.integration


def _decision_wire(
    *,
    reasons: list[str],
    event_ids: list[str],
    source_refs: list[dict[str, str]],
    security_id: str = _SECURITY_ID.value,
) -> dict[str, object]:
    return {
        "security_id": security_id,
        "symbol_as_of": "ACME",
        "master_version": "p4b.securities.v1:" + ("a" * 64),
        "decision_at": str(_T1),
        "outcome": "ENTRY_BLOCKED",
        "reasons": reasons,
        "event_ids": event_ids,
        "source_refs": source_refs,
        "producer_version": "p4b.quarantine.v1",
    }


def _identity_with_sources(first, second):
    refs = tuple(
        sorted(
            (
                SourceRef(first.record_id, first.family, first.record_hash),
                SourceRef(second.record_id, second.family, second.record_hash),
            ),
            key=lambda ref: (ref.record_id, ref.family.value, ref.record_hash),
        )
    )
    return build_identity_record(
        security_id=_SECURITY_ID,
        symbol=SecuritySymbol("ACME"),
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        valid_from=_T0,
        available_at=_T0,
        status=SecurityStatus.ACTIVE,
        source_refs=refs,
        schema_version=_SCHEMA,
    )


def test_runtime_eligible_requires_complete_identity_source_closure(
    migrated_postgres: str,
    p4b_runtime_postgres: tuple[str, object],
) -> None:
    """H1: one matching identity source must not be enough for ELIGIBLE."""
    runtime_dsn, _ = p4b_runtime_postgres
    first = _source("asset-closure-a", P4SourceFamily.ALPACA_ASSETS)
    second = _source("asset-closure-b", P4SourceFamily.ALPACA_ASSETS)
    identity = _identity_with_sources(first, second)
    refs = [
        {
            "record_id": identity.source_refs[0].record_id,
            "record_hash": identity.source_refs[0].record_hash,
            "family": identity.source_refs[0].family.value,
        }
    ]
    wire = {
        "security_id": identity.security_id.value,
        "symbol_as_of": identity.symbol.value,
        "master_version": master_version_for(identity),
        "decision_at": str(_T1),
        "outcome": "ELIGIBLE",
        "reasons": [],
        "event_ids": [],
        "source_refs": refs,
        "producer_version": "p4b.quarantine.v1",
    }
    decision_hash = _canonical_hash(_DECISION_HASH_DOMAIN, wire)

    with psycopg.connect(migrated_postgres) as owner:
        source_log = PostgresP4RecordLog(owner)
        repository = PostgresSecurityMaster(owner)
        assert source_log.append(first).value == "APPENDED"
        assert source_log.append(second).value == "APPENDED"
        assert repository.append_identity(identity).value == "APPENDED"
        owner.commit()

    with psycopg.connect(runtime_dsn) as runtime:
        try:
            result = runtime.execute(
                "SELECT public.record_quarantine_decision(%s, %s)",
                (decision_hash, Jsonb(wire)),
            ).fetchone()
        except psycopg.errors.CheckViolation as failure:
            assert failure.sqlstate == "23514"
        else:
            in_tx_rows = runtime.execute(
                "SELECT count(*) FROM public.security_quarantine_decisions "
                "WHERE decision_hash = %s",
                (decision_hash,),
            ).fetchone()[0]
            runtime.rollback()
            pytest.fail(f"H1 reproduced: result={result!r}, in_transaction_rows={in_tx_rows}")
        runtime.rollback()

    with psycopg.connect(migrated_postgres) as owner:
        assert owner.execute(
            "SELECT count(*) FROM public.security_quarantine_decisions WHERE decision_hash = %s",
            (decision_hash,),
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("reasons", "event_ids"),
    (
        (["FORMAL_CONFIRMATION_MISSING", "SPLIT_DETECTED"], []),
        (["SPLIT_DETECTED"], ["event-duplicate", "event-duplicate"]),
    ),
    ids=("reasons-not-enum-order", "duplicate-event-ids"),
)
def test_runtime_quarantine_rejects_noncanonical_reason_or_event_arrays(
    p4b_runtime_postgres: tuple[str, object],
    reasons: list[str],
    event_ids: list[str],
) -> None:
    """M1: persisted decision arrays must match the Python canonical contract."""
    runtime_dsn, _ = p4b_runtime_postgres
    wire = _decision_wire(reasons=reasons, event_ids=event_ids, source_refs=[])
    decision_hash = _canonical_hash(_DECISION_HASH_DOMAIN, wire)

    with psycopg.connect(runtime_dsn) as runtime:
        try:
            result = runtime.execute(
                "SELECT public.record_quarantine_decision(%s, %s)",
                (decision_hash, Jsonb(wire)),
            ).fetchone()
        except psycopg.errors.CheckViolation as failure:
            assert failure.sqlstate == "23514"
        else:
            in_tx_rows = runtime.execute(
                "SELECT count(*) FROM public.security_quarantine_decisions "
                "WHERE decision_hash = %s",
                (decision_hash,),
            ).fetchone()[0]
            runtime.rollback()
            pytest.fail(f"M1 reproduced: result={result!r}, in_transaction_rows={in_tx_rows}")
        runtime.rollback()
        assert runtime.execute(
            "SELECT count(*) FROM public.security_quarantine_decisions WHERE decision_hash = %s",
            (decision_hash,),
        ).fetchone() == (0,)


def test_runtime_source_record_rejects_alpaca_scalar_type_drift(
    p4b_runtime_postgres: tuple[str, object],
) -> None:
    """M2: a recomputed hash must not make a semantically invalid payload valid."""
    runtime_dsn, _ = p4b_runtime_postgres
    source = _source("source-bad-alpaca-type", P4SourceFamily.ALPACA_ASSETS)
    payload = source.wire()["payload"]
    assert isinstance(payload, dict)
    forged_wire = source.wire() | {"payload": {**payload, "tradable": "true"}}
    forged_hash = _canonical_hash(_SOURCE_RECORD_HASH_DOMAIN, forged_wire)

    with psycopg.connect(runtime_dsn) as runtime:
        try:
            result = runtime.execute(
                "SELECT public.append_p4_source_record(%s, %s, %s, %s)",
                (
                    source.record_id,
                    forged_hash,
                    source.content_hash,
                    Jsonb(forged_wire),
                ),
            ).fetchone()
        except psycopg.errors.CheckViolation as failure:
            assert failure.sqlstate == "23514"
        else:
            in_tx_rows = runtime.execute(
                "SELECT count(*) FROM public.p4_source_records WHERE record_id = %s",
                (source.record_id,),
            ).fetchone()[0]
            runtime.rollback()
            pytest.fail(f"M2 reproduced: result={result!r}, in_transaction_rows={in_tx_rows}")
        runtime.rollback()
        assert runtime.execute(
            "SELECT count(*) FROM public.p4_source_records WHERE record_id = %s",
            (source.record_id,),
        ).fetchone() == (0,)
