# mypy: ignore-errors
"""P4-B PostgreSQL authority tests: hash binding, ACLs, CAS, and rollback."""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Barrier

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.infrastructure.migrations import current_version, migrate, rollback, verify_schema
from seven_lens.infrastructure.postgres_roles import provision_runtime_role
from seven_lens.infrastructure.postgres_securities import (
    PostgresP4RecordLog,
    PostgresSecuritiesError,
    PostgresSecurityMaster,
)
from seven_lens.securities.contracts import (
    AssetClass,
    ListingExchange,
    SecurityId,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
    build_identity_record,
)
from seven_lens.securities.corporate_actions import (
    CorporateActionState,
    CorporateActionType,
    SplitRatio,
    build_corporate_action_record,
)
from seven_lens.securities.quarantine import (
    QuarantineOutcome,
    QuarantinePurpose,
    QuarantineQuery,
    SourceObservation,
    master_version_for,
)
from seven_lens.securities.service import SecurityMasterService
from seven_lens.sources.adapters.records import NormalizedSourceRecord, build_normalized_record
from seven_lens.sources.roles import P4SourceFamily

pytestmark = pytest.mark.integration

_T0 = UtcTimestamp.from_isoformat("2026-01-01T00:00:00.000000Z")
_T1 = UtcTimestamp.from_isoformat("2026-01-05T15:00:00.000000Z")
_T2 = UtcTimestamp.from_isoformat("2026-01-06T12:00:00.000000Z")
_SECURITY_ID = SecurityId("11111111-1111-4111-8111-111111111111")
_SCHEMA = SchemaVersion("1.0.0")
_EX_DATE = TradingDate.from_isoformat("2026-02-01")
_RUNTIME_ROLE = "seven_lens_p4b_runtime"
_RUNTIME_PASSWORD = "p4b-disposable-runtime-only"
_SOURCE_RECORD_HASH_DOMAIN = b"seven-lens.p4.source-record.v1\x00"
_DECISION_HASH_DOMAIN = b"seven-lens.p4b.quarantine-decision.v1\x00"


@pytest.fixture
def p4b_runtime_postgres(migrated_postgres: str) -> Iterator[tuple[str, object]]:
    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(_RUNTIME_ROLE)))
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(_RUNTIME_ROLE), sql.Literal(_RUNTIME_PASSWORD))
        )
    provision_runtime_role(migrated_postgres, _RUNTIME_ROLE)
    runtime_dsn = make_conninfo(
        migrated_postgres,
        user=_RUNTIME_ROLE,
        password=_RUNTIME_PASSWORD,
    )
    try:
        yield runtime_dsn, object()
    finally:
        with psycopg.connect(migrated_postgres, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(_RUNTIME_ROLE)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(_RUNTIME_ROLE)))


def _source(
    record_id: str,
    family: P4SourceFamily,
    *,
    available_at: UtcTimestamp = _T0,
    version: str = "v1",
    supersedes_content_hash: str | None = None,
) -> NormalizedSourceRecord:
    endpoint_id = {
        P4SourceFamily.ALPACA_ASSETS: "asset_detail",
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS: "corporate_actions",
        P4SourceFamily.SEC_EDGAR: "submissions",
    }[family]
    payload = {
        P4SourceFamily.ALPACA_ASSETS: {
            "id": "90927a3c-0b6a-4d5a-bd31-4d45a26b7cc8",
            "symbol": "ACME",
            "exchange": "NYSE",
            "asset_class": "us_equity",
            "status": "active",
            "tradable": True,
        },
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS: {
            "type": "split",
            "split_type": "forward",
            "cusip": "037833100",
            "symbol": "ACME",
            "ex_date": "2026-02-01",
            "record_date": None,
            "payment_date": None,
            "ratio": "1.5",
            "supported": True,
            "complete": True,
            "detection_only": True,
        },
        P4SourceFamily.SEC_EDGAR: {
            "cik_padded": "0000320193",
            "accession_number": "0000320193-26-000001",
            "form": "10-Q",
            "primary_document": "acme-10q.htm",
            "filing_date": "2026-01-01",
        },
    }[family]
    return build_normalized_record(
        record_id=record_id,
        family=family,
        endpoint_id=endpoint_id,
        schema_version=_SCHEMA,
        content_hash=sha256(f"{record_id}:{version}".encode()).hexdigest(),
        retrieved_at=available_at,
        available_at=available_at,
        payload=payload,
        material_claim=False,
        supersedes_content_hash=supersedes_content_hash,
    )


def _identity(source: NormalizedSourceRecord):
    return build_identity_record(
        security_id=_SECURITY_ID,
        symbol=SecuritySymbol("ACME"),
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        valid_from=_T0,
        available_at=_T0,
        status=SecurityStatus.ACTIVE,
        source_refs=(SourceRef(source.record_id, source.family, source.record_hash),),
        schema_version=_SCHEMA,
    )


def _event(identity, source: NormalizedSourceRecord):
    return build_corporate_action_record(
        event_id="event-1",
        security_id=_SECURITY_ID,
        security_identity_hash=identity.identity_hash,
        action_type=CorporateActionType.FORWARD_SPLIT,
        ratio=SplitRatio.from_fraction(numerator=3, denominator=2),
        declared_at=_T1,
        ex_date=_EX_DATE,
        effective_date=_EX_DATE,
        available_at=_T1,
        state=CorporateActionState.DETECTED,
        source_refs=(SourceRef(source.record_id, source.family, source.record_hash),),
        schema_version=_SCHEMA,
    )


def _observation(
    source: NormalizedSourceRecord,
    *,
    auditable: bool = True,
    withdrawn: bool = False,
    claims: bool = False,
) -> SourceObservation:
    return SourceObservation(
        source_ref=SourceRef(source.record_id, source.family, source.record_hash),
        available_at=source.available_at or source.retrieved_at,
        withdrawn=withdrawn,
        auditable=auditable,
        claimed_type=CorporateActionType.FORWARD_SPLIT if claims else None,
        claimed_ratio=SplitRatio.from_fraction(numerator=3, denominator=2) if claims else None,
        claimed_ex_date=_EX_DATE if claims else None,
        claimed_effective_date=_EX_DATE if claims else None,
    )


def _canonical_hash(domain: bytes, wire: dict[str, object]) -> str:
    canonical = json.dumps(
        wire, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return sha256(domain + canonical).hexdigest()


def _setup_service(connection: psycopg.Connection[object]):
    source_log = PostgresP4RecordLog(connection)
    repository = PostgresSecurityMaster(connection)
    identity_source = _source("asset-1", P4SourceFamily.ALPACA_ASSETS)
    event_source = _source("ca-1", P4SourceFamily.ALPACA_CORPORATE_ACTIONS)
    official_source = _source("sec-1", P4SourceFamily.SEC_EDGAR)
    for source in (identity_source, event_source, official_source):
        source_log.append(source)
    identity = _identity(identity_source)
    event = _event(identity, event_source)
    service = SecurityMasterService(repository, source_log)
    service.register_identity(identity)
    return service, repository, source_log, identity, event, event_source, official_source


def test_p4b_postgres_source_log_keeps_exact_versions_and_rejects_forged_hash(
    migrated_postgres: str,
) -> None:
    base = _source("source-1", P4SourceFamily.ALPACA_CORPORATE_ACTIONS)
    corrected = _source(
        "source-1",
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
        available_at=_T2,
        version="v2",
        supersedes_content_hash=base.content_hash,
    )
    with psycopg.connect(migrated_postgres) as connection:
        records = PostgresP4RecordLog(connection)
        assert records.append(base).value == "APPENDED"
        assert records.append(corrected).value == "APPENDED"
        assert records.append(base).value == "IDEMPOTENT_DUPLICATE"
        assert records.get("source-1") == corrected
        assert records.get_version("source-1", base.record_hash) == base
        assert records.count() == 1

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "SELECT public.append_p4_source_record(%s, %s, %s, %s)",
                (
                    "source-2",
                    "0" * 64,
                    base.content_hash,
                    Jsonb(base.wire() | {"record_id": "source-2"}),
                ),
            )
        connection.rollback()


def test_p4b_postgres_source_log_rejects_forged_payload_even_with_recomputed_hash(
    migrated_postgres: str,
) -> None:
    source = _source("source-forged-payload", P4SourceFamily.ALPACA_ASSETS)
    forged_wire = source.wire() | {"payload": {"forged": True}}
    forged_hash = _canonical_hash(_SOURCE_RECORD_HASH_DOMAIN, forged_wire)

    with psycopg.connect(migrated_postgres) as connection:
        with pytest.raises(psycopg.errors.CheckViolation) as failure:
            connection.execute(
                "SELECT public.append_p4_source_record(%s, %s, %s, %s)",
                (
                    source.record_id,
                    forged_hash,
                    source.content_hash,
                    Jsonb(forged_wire),
                ),
            )
        assert failure.value.sqlstate == "23514"
        connection.rollback()
        assert connection.execute(
            "SELECT count(*) FROM public.p4_source_records WHERE record_id = %s",
            (source.record_id,),
        ).fetchone() == (0,)


def test_p4b_postgres_source_log_rejects_unknown_payload_fields_from_runtime(
    p4b_runtime_postgres: tuple[str, object],
) -> None:
    runtime_dsn, _ = p4b_runtime_postgres
    source = _source("source-extra-field", P4SourceFamily.ALPACA_ASSETS)
    forged_wire = source.wire()
    payload = forged_wire["payload"]
    assert isinstance(payload, dict)
    forged_wire["payload"] = {**payload, "evil_extra_field": "must-be-rejected"}
    forged_hash = _canonical_hash(_SOURCE_RECORD_HASH_DOMAIN, forged_wire)

    with psycopg.connect(runtime_dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation) as failure:
            connection.execute(
                "SELECT public.append_p4_source_record(%s, %s, %s, %s)",
                (
                    source.record_id,
                    forged_hash,
                    source.content_hash,
                    Jsonb(forged_wire),
                ),
            )
        assert failure.value.sqlstate == "23514"
        connection.rollback()
        assert connection.execute(
            "SELECT count(*) FROM public.p4_source_records WHERE record_id = %s",
            (source.record_id,),
        ).fetchone() == (0,)


def test_p4b_postgres_source_log_rejects_backdated_supersession(
    migrated_postgres: str,
) -> None:
    base = _source(
        "source-backdated",
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
        available_at=_T1,
    )
    backdated = _source(
        "source-backdated",
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
        available_at=_T0,
        version="v2",
        supersedes_content_hash=base.content_hash,
    )

    with psycopg.connect(migrated_postgres) as connection:
        records = PostgresP4RecordLog(connection)
        assert records.append(base).value == "APPENDED"
        connection.commit()
        with pytest.raises(PostgresSecuritiesError) as failure:
            records.append(backdated)
        assert failure.value.sqlstate == "23514"
        connection.rollback()
        assert records.get("source-backdated") == base


def test_p4b_postgres_quarantine_rejects_forged_eligible_without_identity_closure(
    migrated_postgres: str,
) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        source_log = PostgresP4RecordLog(connection)
        repository = PostgresSecurityMaster(connection)
        source = _source("source-eligible", P4SourceFamily.ALPACA_ASSETS)
        assert source_log.append(source).value == "APPENDED"
        identity = _identity(source)
        assert repository.append_identity(identity).value == "APPENDED"
        service = SecurityMasterService(repository, source_log)
        valid = service.candidate_creation_check(
            QuarantineQuery(
                purpose=QuarantinePurpose.CANDIDATE_CREATION,
                security_id=_SECURITY_ID,
                symbol_as_of=identity.symbol,
                decision_at=_T1,
                master_version=master_version_for(identity),
            )
        )
        assert valid.outcome is QuarantineOutcome.ELIGIBLE

        forged_wire = valid.wire() | {
            "master_version": "p4b.securities.v1:" + ("0" * 64),
        }
        forged_hash = _canonical_hash(_DECISION_HASH_DOMAIN, forged_wire)
        with pytest.raises(psycopg.errors.CheckViolation) as failure:
            connection.execute(
                "SELECT public.record_quarantine_decision(%s, %s)",
                (forged_hash, Jsonb(forged_wire)),
            )
        assert failure.value.sqlstate == "23514"
        connection.rollback()
        assert connection.execute(
            "SELECT count(*) FROM public.security_quarantine_decisions WHERE decision_hash = %s",
            (forged_hash,),
        ).fetchone() == (0,)


def test_p4b_postgres_runtime_cannot_record_eligible_while_event_is_blocked(
    migrated_postgres: str,
    p4b_runtime_postgres: tuple[str, object],
) -> None:
    runtime_dsn, _ = p4b_runtime_postgres
    with psycopg.connect(migrated_postgres) as owner:
        service, _, _, identity, event, _, _ = _setup_service(owner)
        blocked = service.discover_split(event)
        assert blocked.state is CorporateActionState.ENTRY_BLOCKED
        owner.commit()

    wire = {
        "security_id": identity.security_id.value,
        "symbol_as_of": identity.symbol.value,
        "master_version": master_version_for(identity),
        "decision_at": str(_T2),
        "outcome": "ELIGIBLE",
        "reasons": [],
        "event_ids": [],
        "source_refs": [
            {
                "record_id": ref.record_id,
                "record_hash": ref.record_hash,
                "family": ref.family.value,
            }
            for ref in identity.source_refs
        ],
        "producer_version": "p4b.quarantine.v1",
    }
    decision_hash = _canonical_hash(_DECISION_HASH_DOMAIN, wire)

    with psycopg.connect(runtime_dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation) as failure:
            connection.execute(
                "SELECT public.record_quarantine_decision(%s, %s)",
                (decision_hash, Jsonb(wire)),
            )
        assert failure.value.sqlstate == "23514"
        connection.rollback()

    with psycopg.connect(migrated_postgres) as owner:
        assert owner.execute(
            "SELECT state FROM public.corporate_action_event_head WHERE event_id = %s",
            (event.event_id,),
        ).fetchone() == (CorporateActionState.ENTRY_BLOCKED.value,)
        assert owner.execute(
            "SELECT count(*) FROM public.security_quarantine_decisions WHERE decision_hash = %s",
            (decision_hash,),
        ).fetchone() == (0,)


def test_p4b_postgres_public_service_persists_block_confirmation_and_identical_seams(
    migrated_postgres: str,
) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        service, repository, _, identity, event, event_source, official_source = _setup_service(
            connection
        )
        blocked = service.discover_split(event)
        assert blocked.state is CorporateActionState.ENTRY_BLOCKED

        confirmed = service.confirm_split(
            event.event_id,
            (_observation(event_source), _observation(official_source, claims=True)),
            decision_at=_T2,
        )
        assert confirmed.state is CorporateActionState.CONFIRMED
        assert [row.state for row in repository.event_lineage(event.event_id)] == [
            CorporateActionState.DETECTED,
            CorporateActionState.ENTRY_BLOCKED,
            CorporateActionState.CONFIRMED,
        ]

        values = {
            "security_id": _SECURITY_ID,
            "symbol_as_of": identity.symbol,
            "decision_at": _T2,
            "master_version": master_version_for(identity),
        }
        decisions = (
            service.candidate_creation_check(
                QuarantineQuery(purpose=QuarantinePurpose.CANDIDATE_CREATION, **values)
            ),
            service.risk_approval_check(
                QuarantineQuery(purpose=QuarantinePurpose.RISK_APPROVAL, **values)
            ),
            service.submit_recheck(
                QuarantineQuery(purpose=QuarantinePurpose.SUBMIT_RECHECK, **values)
            ),
        )
        assert decisions[0].wire() == decisions[1].wire() == decisions[2].wire()
        assert decisions[0].outcome is QuarantineOutcome.ENTRY_BLOCKED
        assert repository.latest_decision(_SECURITY_ID) == decisions[-1]

        assert connection.execute(
            "SELECT count(*) FROM public.security_identity_heads"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM public.corporate_action_event_head"
        ).fetchone() == (1,)


def test_p4b_postgres_source_correction_forces_review_and_telemetry_failure_keeps_block(
    migrated_postgres: str,
) -> None:
    captured: list[object] = []

    def broken_telemetry(event: object) -> None:
        captured.append(event)
        raise RuntimeError("telemetry unavailable")

    with psycopg.connect(migrated_postgres) as connection:
        source_log = PostgresP4RecordLog(connection)
        repository = PostgresSecurityMaster(connection)
        identity_source = _source("asset-1", P4SourceFamily.ALPACA_ASSETS)
        event_source = _source("ca-1", P4SourceFamily.ALPACA_CORPORATE_ACTIONS)
        official_source = _source("sec-1", P4SourceFamily.SEC_EDGAR)
        for source in (identity_source, event_source, official_source):
            source_log.append(source)
        identity = _identity(identity_source)
        event = _event(identity, event_source)
        service = SecurityMasterService(repository, source_log, telemetry=broken_telemetry)
        service.register_identity(identity)
        service.discover_split(event)

        corrected_official = _source(
            "sec-1",
            P4SourceFamily.SEC_EDGAR,
            available_at=_T2,
            version="v2",
            supersedes_content_hash=official_source.content_hash,
        )
        assert source_log.append(corrected_official).value == "APPENDED"
        reviewed = service.confirm_split(
            event.event_id,
            (_observation(event_source), _observation(official_source, claims=True)),
            decision_at=_T2,
        )
        assert reviewed.state is CorporateActionState.REVIEW_REQUIRED
        assert service.telemetry_failures == 3
        assert len(captured) == 3


def test_p4b_postgres_event_cas_rejects_the_loser_on_two_connections(
    migrated_postgres: str,
) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        service, repository, _, _identity_record, event, _event_source, official_source = (
            _setup_service(connection)
        )
        service.discover_split(event)
        blocked = repository.event_lineage(event.event_id)[-1]
        review = build_corporate_action_record(
            event_id=event.event_id,
            security_id=event.security_id,
            security_identity_hash=event.security_identity_hash,
            action_type=event.action_type,
            ratio=event.ratio,
            declared_at=event.declared_at,
            ex_date=event.ex_date,
            effective_date=event.effective_date,
            available_at=_T2,
            state=CorporateActionState.REVIEW_REQUIRED,
            source_refs=blocked.source_refs,
            schema_version=event.schema_version,
        )
        confirmed = build_corporate_action_record(
            event_id=event.event_id,
            security_id=event.security_id,
            security_identity_hash=event.security_identity_hash,
            action_type=event.action_type,
            ratio=event.ratio,
            declared_at=event.declared_at,
            ex_date=event.ex_date,
            effective_date=event.effective_date,
            available_at=_T2,
            state=CorporateActionState.CONFIRMED,
            source_refs=tuple(
                sorted(
                    (*blocked.source_refs, _observation(official_source).source_ref),
                    key=lambda ref: (ref.record_id, ref.family.value, ref.record_hash),
                )
            ),
            schema_version=event.schema_version,
        )
        previous_hash = blocked.record_hash

    first = psycopg.connect(migrated_postgres)
    second = psycopg.connect(migrated_postgres)
    barrier = Barrier(2)

    def append(connection: psycopg.Connection[object], candidate) -> tuple[str, str | None]:
        barrier.wait()
        try:
            with connection.transaction():
                PostgresSecurityMaster(connection).append_event(
                    candidate, previous_record_hash=previous_hash
                )
            return "success", None
        except PostgresSecuritiesError as error:
            return "error", error.sqlstate

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                executor.map(
                    append,
                    (first, second),
                    (review, confirmed),
                )
            )
    finally:
        first.close()
        second.close()
    assert sorted(outcomes) == [("error", "40001"), ("success", None)]


def test_p4b_postgres_confirm_and_withdraw_race_replays_to_review(
    migrated_postgres: str,
) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        _, repository, _, identity, event, event_source, official_source = _setup_service(
            connection
        )
        service = SecurityMasterService(repository, PostgresP4RecordLog(connection))
        service.discover_split(event)

    first = psycopg.connect(migrated_postgres)
    second = psycopg.connect(migrated_postgres)
    first_service = SecurityMasterService(PostgresSecurityMaster(first), PostgresP4RecordLog(first))
    second_records = PostgresP4RecordLog(second)
    corrected_official = _source(
        "sec-1",
        P4SourceFamily.SEC_EDGAR,
        available_at=_T2,
        version="v2",
        supersedes_content_hash=official_source.content_hash,
    )
    observations = (_observation(event_source), _observation(official_source, claims=True))
    barrier = Barrier(2)

    def confirm() -> tuple[str, str]:
        barrier.wait()
        try:
            head = first_service.confirm_split(event.event_id, observations, decision_at=_T2)
            first.commit()
            return "confirm", head.state.value
        except Exception:
            first.rollback()
            raise

    def withdraw() -> tuple[str, str]:
        barrier.wait()
        try:
            with second.transaction():
                second_records.append(corrected_official)
            return "withdraw", "committed"
        except Exception:
            second.rollback()
            raise

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(lambda task: task(), (confirm, withdraw)))
        assert sorted(outcomes)[0][0] == "confirm"
        assert sorted(outcomes)[1] == ("withdraw", "committed")

        query = QuarantineQuery(
            purpose=QuarantinePurpose.SUBMIT_RECHECK,
            security_id=_SECURITY_ID,
            symbol_as_of=identity.symbol,
            decision_at=_T2,
            master_version=master_version_for(identity),
        )
        decision = SecurityMasterService(
            PostgresSecurityMaster(second), second_records
        ).submit_recheck(query)
        assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED
        assert "SOURCE_WITHDRAWN_OR_CORRECTED" in {reason.value for reason in decision.reasons}
    finally:
        first.close()
        second.close()


def test_p4b_postgres_runtime_role_can_only_use_definer_authorities(
    p4b_runtime_postgres: tuple[str, object],
) -> None:
    runtime_dsn, _ = p4b_runtime_postgres
    source = _source("asset-1", P4SourceFamily.ALPACA_ASSETS)
    identity = _identity(source)
    with psycopg.connect(runtime_dsn) as connection:
        records = PostgresP4RecordLog(connection)
        repository = PostgresSecurityMaster(connection)
        assert records.append(source).value == "APPENDED"
        assert repository.append_identity(identity).value == "APPENDED"
        connection.commit()

        with pytest.raises(psycopg.errors.InsufficientPrivilege) as failure:
            connection.execute(
                "INSERT INTO public.p4_source_records "
                "(record_id, record_hash, content_hash, family, retrieved_at, wire) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("direct", "0" * 64, "0" * 64, "ALPACA_ASSETS", _T0.value, Jsonb({})),
            )
        assert failure.value.sqlstate == "42501"
        connection.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as failure:
            connection.execute(
                "UPDATE public.security_identity_heads SET available_at = available_at"
            )
        assert failure.value.sqlstate == "42501"
        connection.rollback()


def test_p4b_postgres_runtime_cannot_replay_transitions_to_confirmed(
    p4b_runtime_postgres: tuple[str, object],
) -> None:
    runtime_dsn, _ = p4b_runtime_postgres
    identity_source = _source("asset-1", P4SourceFamily.ALPACA_ASSETS)
    event_source = _source("ca-1", P4SourceFamily.ALPACA_CORPORATE_ACTIONS)
    official_source = _source("sec-1", P4SourceFamily.SEC_EDGAR)
    identity = _identity(identity_source)
    event = _event(identity, event_source)
    official_ref = SourceRef(
        official_source.record_id, official_source.family, official_source.record_hash
    )
    blocked = build_corporate_action_record(
        event_id=event.event_id,
        security_id=event.security_id,
        security_identity_hash=event.security_identity_hash,
        action_type=event.action_type,
        ratio=event.ratio,
        declared_at=event.declared_at,
        ex_date=event.ex_date,
        effective_date=event.effective_date,
        available_at=event.available_at,
        state=CorporateActionState.ENTRY_BLOCKED,
        source_refs=event.source_refs,
        schema_version=event.schema_version,
    )
    confirmed = build_corporate_action_record(
        event_id=event.event_id,
        security_id=event.security_id,
        security_identity_hash=event.security_identity_hash,
        action_type=event.action_type,
        ratio=event.ratio,
        declared_at=event.declared_at,
        ex_date=event.ex_date,
        effective_date=event.effective_date,
        available_at=_T2,
        state=CorporateActionState.CONFIRMED,
        source_refs=tuple(
            sorted(
                (*blocked.source_refs, official_ref),
                key=lambda ref: (ref.record_id, ref.family.value, ref.record_hash),
            )
        ),
        schema_version=event.schema_version,
    )

    with psycopg.connect(runtime_dsn) as connection:
        records = PostgresP4RecordLog(connection)
        repository = PostgresSecurityMaster(connection)
        for source in (identity_source, event_source, official_source):
            assert records.append(source).value == "APPENDED"
        assert repository.append_identity(identity).value == "APPENDED"
        assert repository.append_event(event, previous_record_hash=None).value == "APPENDED"
        assert (
            repository.append_event(blocked, previous_record_hash=event.record_hash).value
            == "APPENDED"
        )
        connection.commit()

        with pytest.raises(psycopg.errors.InsufficientPrivilege) as failure:
            connection.execute(
                "SELECT public.append_corporate_action_event(%s, %s, %s)",
                (confirmed.record_hash, blocked.record_hash, Jsonb(confirmed.wire())),
            )
        assert failure.value.sqlstate == "42501"
        connection.rollback()
        assert (
            repository.event_lineage(event.event_id)[-1].state is CorporateActionState.ENTRY_BLOCKED
        )


def test_p4b_rollback_and_reapply_removes_and_rebuilds_the_authority(
    migrated_postgres: str,
) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        records = PostgresP4RecordLog(connection)
        records.append(_source("source-1", P4SourceFamily.ALPACA_ASSETS))
    assert current_version(migrated_postgres) == 23
    assert rollback(migrated_postgres) == 22
    with psycopg.connect(migrated_postgres) as connection:
        assert connection.execute(
            "SELECT to_regclass('public.p4_source_records'), "
            "to_regclass('public.security_identities'), "
            "to_regclass('public.corporate_action_events')"
        ).fetchone() == (
            "p4_source_records",
            "security_identities",
            "corporate_action_events",
        )
    assert migrate(migrated_postgres) == 23
    assert verify_schema(migrated_postgres) == 23
