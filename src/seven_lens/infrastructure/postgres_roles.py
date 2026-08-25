"""Least-privilege grants for an externally created PostgreSQL runtime login role."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, cast

import psycopg
from psycopg import sql

_ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_AUTHORITATIVE_VIEW_NAMES: Final[frozenset[str]] = frozenset(
    {"approved_reflection_records", "approved_reflection_sources"}
)

# The exact table inventory of the authoritative public schema after migrations
# 0001-0013.  Any extra or missing table is privilege-surface drift.
_AUTHORITATIVE_TABLE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "account_baseline_revisions",
        "account_baselines",
        "analysis_runs",
        "analysis_stage_results",
        "audit_events",
        "broker_orders",
        "control_commands",
        "control_state",
        "domain_events",
        "evidence_packets",
        "fills",
        "job_instances",
        "job_leases",
        "model_call_audits",
        "model_call_claims",
        "memory_artifact_sources",
        "memory_artifact_state_events",
        "memory_artifacts",
        "memory_current_pointer",
        "memory_curation_audits",
        "memory_promotion_history",
        "order_intents",
        "portfolio_proposals",
        "proposal_contexts",
        "proposal_runs",
        "proposal_stage_results",
        "reconciliation_mismatches",
        "reconciliation_runs",
        "research_bundle_items",
        "research_bundles",
        "risk_debates",
        "risk_rejection_feedback",
        "reflection_corrections",
        "reflection_records",
        "reflection_sources",
        "schema_metadata",
        "schema_migrations",
        "source_objects",
        "source_records",
    }
)

# The exact function inventory of the public schema after migrations 0001-0013,
# each entry being the function name plus its identity argument types rendered by
# array_to_string(proargtypes::regtype[], ',').  This includes the 36 pgcrypto
# extension functions installed by migration 0009.  Any extra function, overload
# or removal is drift, so a rogue SECURITY DEFINER function can never hide here.
_AUTHORITATIVE_FUNCTIONS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("acquire_job_lease", "text,text,interval"),
        ("advance_analysis_stage", "uuid,text,text,text,text"),
        ("advance_proposal_stage", "uuid,text,text,text,text"),
        ("armor", "bytea"),
        ("armor", "bytea,text[],text[]"),
        ("audit_event_payload_is_valid", "text,jsonb"),
        ("audit_payload_contains_secret", "jsonb"),
        ("broker_order_status_transition_is_valid", "text,text"),
        ("create_analysis_run", "uuid,uuid,text,text"),
        ("create_proposal_run", "uuid,uuid,uuid,text"),
        ("crypt", "text,text"),
        ("dearmor", "text"),
        ("decrypt", "bytea,bytea,text"),
        ("decrypt_iv", "bytea,bytea,bytea,text"),
        ("digest", "bytea,text"),
        ("digest", "text,text"),
        ("domain_event_payload_is_valid", "text,jsonb"),
        ("encrypt", "bytea,bytea,text"),
        ("encrypt_iv", "bytea,bytea,bytea,text"),
        ("enforce_domain_event_sequence_and_timestamp", ""),
        ("gen_random_bytes", "integer"),
        ("gen_random_uuid", ""),
        ("gen_salt", "text"),
        ("gen_salt", "text,integer"),
        ("guard_account_baseline_revision_write", ""),
        ("guard_account_baseline_write", ""),
        ("guard_broker_order_insert", ""),
        ("guard_broker_order_write", ""),
        ("guard_broker_updated_at", ""),
        ("guard_control_state_write", ""),
        ("guard_job_instance_status_write", ""),
        ("guard_order_intent_write", ""),
        ("guard_proposal_run_write", ""),
        ("guard_proposal_stage_result_write", ""),
        ("hmac", "bytea,bytea,text"),
        ("hmac", "text,text,text"),
        ("order_status_transition_is_valid", "text,text"),
        ("p3d_canonical_json", "json"),
        ("p3d_derive_run_id", "text,text[]"),
        ("p3d_text_is_safe", "text"),
        (
            "claim_model_call_attempt",
            "uuid,uuid,uuid,uuid,text,text,integer,text,text,text,text,integer,text,text,text",
        ),
        ("guard_model_call_claim_write", ""),
        (
            "register_model_call_attempt",
            "uuid,uuid,uuid,uuid,text,text,integer,text,text,text,text,integer,text,text,"
            "text,text,text,boolean,integer,integer,integer,timestamp with time zone,"
            "timestamp with time zone,text,text,text,text,text",
        ),
        (
            "register_reflection_record",
            "text,text,text,timestamp with time zone,timestamp with time zone,"
            "timestamp with time zone,timestamp with time zone,text,text,text,text,text,bytea,"
            "text,text,text,text,text,text[],text[],text[],timestamp with time zone[],text,text",
        ),
        (
            "register_memory_candidate",
            "text,text,timestamp with time zone,timestamp with time zone,text,text,text,bytea,"
            "integer,integer,integer,text,text,text,text[]",
        ),
        (
            "register_memory_curation_audit",
            "uuid,text,text,text,text,text,text,text,text,text,integer,integer,text,text,text,"
            "integer,integer,integer,text",
        ),
        ("validate_memory_artifact", "text,text,text,text"),
        ("promote_memory_artifact", "text,text,timestamp with time zone"),
        ("current_memory_artifact", "timestamp with time zone"),
        ("current_memory_pointer_artifact", ""),
        ("p3f_text_is_safe", "text,integer"),
        ("p3f_instruction_text_is_safe", "text"),
        ("p3f_fact_text_is_closed", "text,text[],json,text[]"),
        ("pgp_armor_headers", "text"),
        ("pgp_key_id", "bytea"),
        ("pgp_pub_decrypt", "bytea,bytea"),
        ("pgp_pub_decrypt", "bytea,bytea,text"),
        ("pgp_pub_decrypt", "bytea,bytea,text,text"),
        ("pgp_pub_decrypt_bytea", "bytea,bytea"),
        ("pgp_pub_decrypt_bytea", "bytea,bytea,text"),
        ("pgp_pub_decrypt_bytea", "bytea,bytea,text,text"),
        ("pgp_pub_encrypt", "text,bytea"),
        ("pgp_pub_encrypt", "text,bytea,text"),
        ("pgp_pub_encrypt_bytea", "bytea,bytea"),
        ("pgp_pub_encrypt_bytea", "bytea,bytea,text"),
        ("pgp_sym_decrypt", "bytea,text"),
        ("pgp_sym_decrypt", "bytea,text,text"),
        ("pgp_sym_decrypt_bytea", "bytea,text"),
        ("pgp_sym_decrypt_bytea", "bytea,text,text"),
        ("pgp_sym_encrypt", "text,text"),
        ("pgp_sym_encrypt", "text,text,text"),
        ("pgp_sym_encrypt_bytea", "bytea,text"),
        ("pgp_sym_encrypt_bytea", "bytea,text,text"),
        ("prevent_append_only_mutation", ""),
        ("publish_source_object", "text"),
        ("register_evidence_packet", "uuid,text,timestamp with time zone,text,text,text"),
        (
            "register_proposal_context",
            "uuid,uuid,integer,text,uuid,uuid,text,uuid,text,text,text",
        ),
        (
            "register_research_bundle",
            "uuid,uuid,text,timestamp with time zone,text,timestamp with time zone,text,text,"
            "jsonb,text,text",
        ),
        ("register_risk_feedback", "uuid,uuid,text,text"),
        ("register_source_object", "text,integer"),
        (
            "register_source_record",
            "text,text,text,text,text,timestamp with time zone,text,boolean,boolean",
        ),
        ("release_job_lease", "text,text,bigint,text"),
        ("renew_job_lease", "text,text,bigint,interval"),
        ("transition_job_status", "text,text,bigint,text"),
        ("validate_and_stamp_audit_event", ""),
    }
)

_P3_PUBLIC_TABLE_PRIVILEGES: Final = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)

# The single P3 API allowlist shared by the runtime-role check and the PUBLIC check.
_P3_FUNCTION_SIGNATURES: Final = (
    ("public.register_source_object(text,integer)", True),
    ("public.publish_source_object(text)", False),
    (
        "public.register_source_record(text,text,text,text,text,timestamp with time zone,"
        "text,boolean,boolean)",
        True,
    ),
    (
        "public.register_evidence_packet(uuid,text,timestamp with time zone,text,text,text)",
        True,
    ),
    ("public.create_analysis_run(uuid,uuid,text,text)", True),
    ("public.advance_analysis_stage(uuid,text,text,text,text)", True),
    (
        "public.register_research_bundle(uuid,uuid,text,timestamp with time zone,text,"
        "timestamp with time zone,text,text,jsonb,text,text)",
        True,
    ),
    ("public.register_risk_feedback(uuid,uuid,text,text)", True),
    (
        "public.register_proposal_context(uuid,uuid,integer,text,uuid,uuid,text,uuid,text,text,text)",
        True,
    ),
    ("public.create_proposal_run(uuid,uuid,uuid,text)", True),
    ("public.advance_proposal_stage(uuid,text,text,text,text)", True),
    ("public.guard_proposal_run_write()", False),
    ("public.guard_proposal_stage_result_write()", False),
    ("public.p3d_canonical_json(json)", False),
    ("public.p3d_derive_run_id(text,text[])", False),
    ("public.p3d_text_is_safe(text)", False),
    (
        "public.claim_model_call_attempt(uuid,uuid,uuid,uuid,text,text,integer,text,text,"
        "text,text,integer,text,text,text)",
        True,
    ),
    ("public.guard_model_call_claim_write()", False),
    (
        "public.register_model_call_attempt(uuid,uuid,uuid,uuid,text,text,integer,text,text,"
        "text,text,integer,text,text,text,text,text,boolean,integer,integer,integer,"
        "timestamp with time zone,timestamp with time zone,text,text,text,text,text)",
        True,
    ),
    (
        "public.register_reflection_record(text,text,text,timestamp with time zone,"
        "timestamp with time zone,timestamp with time zone,timestamp with time zone,"
        "text,text,text,text,text,bytea,text,text,text,text,text,text[],text[],text[],"
        "timestamp with time zone[],text,text)",
        True,
    ),
    (
        "public.register_memory_candidate(text,text,timestamp with time zone,"
        "timestamp with time zone,text,text,text,bytea,integer,integer,integer,text,text,"
        "text,text[])",
        False,
    ),
    (
        "public.register_memory_curation_audit(uuid,text,text,text,text,text,text,text,text,"
        "text,integer,integer,text,text,text,integer,integer,integer,text)",
        False,
    ),
    ("public.validate_memory_artifact(text,text,text,text)", False),
    ("public.promote_memory_artifact(text,text,timestamp with time zone)", False),
    ("public.current_memory_artifact(timestamp with time zone)", True),
    ("public.current_memory_pointer_artifact()", False),
    ("public.p3f_text_is_safe(text,integer)", False),
    ("public.p3f_instruction_text_is_safe(text)", False),
    ("public.p3f_fact_text_is_closed(text,text[],json,text[])", False),
)

_MEMORY_FUNCTION_SIGNATURES: Final[frozenset[str]] = frozenset(
    signature
    for signature, _ in _P3_FUNCTION_SIGNATURES
    if signature.startswith("public.register_reflection_record(")
    or signature.startswith("public.register_memory_candidate(")
    or signature.startswith("public.register_memory_curation_audit(")
    or signature.startswith("public.validate_memory_artifact(")
    or signature.startswith("public.promote_memory_artifact(")
    or signature.startswith("public.current_memory_artifact(")
    or signature.startswith("public.current_memory_pointer_artifact(")
    or signature.startswith("public.p3f_text_is_safe(")
    or signature.startswith("public.p3f_instruction_text_is_safe(")
    or signature.startswith("public.p3f_fact_text_is_closed(")
)

_CURATOR_EXECUTE_SIGNATURES: Final[frozenset[str]] = frozenset(
    {
        "public.register_memory_candidate(text,text,timestamp with time zone,"
        "timestamp with time zone,text,text,text,bytea,integer,integer,integer,text,text,"
        "text,text[])",
        "public.register_memory_curation_audit(uuid,text,text,text,text,text,text,text,text,"
        "text,integer,integer,text,text,text,integer,integer,integer,text)",
        "public.validate_memory_artifact(text,text,text,text)",
        "public.promote_memory_artifact(text,text,timestamp with time zone)",
        "public.current_memory_artifact(timestamp with time zone)",
        "public.current_memory_pointer_artifact()",
    }
)

_RUNTIME_EXECUTE_SIGNATURES: Final[frozenset[str]] = frozenset(
    {
        "public.acquire_job_lease(text,text,interval)",
        "public.renew_job_lease(text,text,bigint,interval)",
        "public.release_job_lease(text,text,bigint,text)",
        "public.transition_job_status(text,text,bigint,text)",
        "public.domain_event_payload_is_valid(text,jsonb)",
        "public.audit_event_payload_is_valid(text,jsonb)",
        *(signature for signature, expected in _P3_FUNCTION_SIGNATURES if expected),
    }
)


class PostgresRoleError(RuntimeError):
    """Raised when runtime-role provisioning cannot prove the trust boundary."""


@dataclass(frozen=True, slots=True)
class RuntimeRoleEvidence:
    owner_role: str
    runtime_role: str
    database_name: str


@dataclass(frozen=True, slots=True)
class MemoryCuratorRoleEvidence:
    owner_role: str
    curator_role: str
    database_name: str


def provision_runtime_role(owner_dsn: str, runtime_role: str) -> RuntimeRoleEvidence:
    """Grant only the privileges used by the current application repositories.

    The login role must already exist.  Role creation and its credential remain an
    operator concern so neither the application nor this repository handles a runtime
    password.  The supplied connection must be the owner of every authoritative object.
    """

    _validate_dsn(owner_dsn)
    _validate_role_name(runtime_role)
    with psycopg.connect(owner_dsn, autocommit=False) as connection, connection.cursor() as cursor:
        owner_role, database_name = _current_identity(cursor)
        if owner_role == runtime_role:
            raise PostgresRoleError("runtime role must differ from the migration owner role")
        _assert_runtime_role_flags(cursor, runtime_role)
        _assert_not_owner_member(cursor, runtime_role, owner_role)
        _assert_authoritative_object_owner(cursor, owner_role)
        _assert_p3_function_security(cursor)

        role = sql.Identifier(runtime_role)
        database = sql.Identifier(database_name)
        cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role))
        cursor.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM {}").format(database, role))
        cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(role))
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        cursor.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role))
        cursor.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(role))
        cursor.execute(sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {}").format(role))
        cursor.execute(
            sql.SQL(
                "GRANT SELECT ON TABLE public.schema_metadata, public.schema_migrations, "
                "public.domain_events, public.audit_events, public.job_instances, "
                "public.order_intents, public.broker_orders, public.fills, "
                "public.reconciliation_runs, public.reconciliation_mismatches, "
                "public.control_commands, public.control_state, "
                "public.account_baselines, public.account_baseline_revisions, "
                "public.source_objects, public.source_records, public.evidence_packets, "
                "public.analysis_runs, "
                "public.analysis_stage_results, "
                "public.research_bundles, public.research_bundle_items, "
                "public.risk_rejection_feedback, public.proposal_contexts, "
                "public.proposal_runs, public.risk_debates, "
                "public.portfolio_proposals, public.proposal_stage_results, "
                "public.model_call_claims, public.model_call_audits TO {}"
            ).format(role)
        )
        cursor.execute(
            sql.SQL(
                "GRANT SELECT ON TABLE public.approved_reflection_records, "
                "public.approved_reflection_sources TO {}"
            ).format(role)
        )
        cursor.execute(
            sql.SQL(
                "GRANT INSERT ON TABLE public.domain_events, public.audit_events, "
                "public.job_instances, public.order_intents, public.broker_orders, "
                "public.fills, public.reconciliation_runs, "
                "public.reconciliation_mismatches, public.control_commands TO {}"
            ).format(role)
        )
        cursor.execute(
            sql.SQL(
                "GRANT UPDATE ON TABLE public.order_intents, public.broker_orders, "
                "public.control_state TO {}"
            ).format(role)
        )
        for signature in (
            "public.acquire_job_lease(TEXT, TEXT, INTERVAL)",
            "public.renew_job_lease(TEXT, TEXT, BIGINT, INTERVAL)",
            "public.release_job_lease(TEXT, TEXT, BIGINT, TEXT)",
            "public.transition_job_status(TEXT, TEXT, BIGINT, TEXT)",
            "public.domain_event_payload_is_valid(TEXT, JSONB)",
            "public.audit_event_payload_is_valid(TEXT, JSONB)",
            "public.register_source_object(TEXT, INTEGER)",
            "public.register_source_record(TEXT, TEXT, TEXT, TEXT, TEXT, "
            "TIMESTAMPTZ, TEXT, BOOLEAN, BOOLEAN)",
            "public.register_evidence_packet(UUID, TEXT, TIMESTAMPTZ, TEXT, TEXT, TEXT)",
            "public.create_analysis_run(UUID, UUID, TEXT, TEXT)",
            "public.advance_analysis_stage(UUID, TEXT, TEXT, TEXT, TEXT)",
            "public.register_research_bundle(UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, "
            "TIMESTAMPTZ, TEXT, TEXT, JSONB, TEXT, TEXT)",
            "public.register_risk_feedback(UUID, UUID, TEXT, TEXT)",
            "public.register_proposal_context(UUID, UUID, INTEGER, TEXT, UUID, UUID, TEXT, "
            "UUID, TEXT, TEXT, TEXT)",
            "public.create_proposal_run(UUID, UUID, UUID, TEXT)",
            "public.advance_proposal_stage(UUID, TEXT, TEXT, TEXT, TEXT)",
            "public.register_model_call_attempt(UUID, UUID, UUID, UUID, TEXT, TEXT, INTEGER, "
            "TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN, INTEGER, "
            "INTEGER, INTEGER, TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TEXT, TEXT, TEXT, TEXT)",
            "public.claim_model_call_attempt(UUID, UUID, UUID, UUID, TEXT, TEXT, INTEGER, "
            "TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, TEXT)",
            "public.register_reflection_record(TEXT, TEXT, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, "
            "TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TEXT, TEXT, TEXT, TEXT, BYTEA, TEXT, TEXT, "
            "TEXT, TEXT, TEXT, TEXT[], TEXT[], TEXT[], TIMESTAMPTZ[], TEXT, TEXT)",
            "public.current_memory_artifact(TIMESTAMPTZ)",
        ):
            cursor.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                    sql.SQL(signature),
                    role,
                )
            )
        _assert_runtime_privileges(cursor, runtime_role, database_name)
        _assert_p3_runtime_privileges(cursor, runtime_role)
        _assert_runtime_function_privileges(cursor, runtime_role)
        _assert_no_public_privileges(cursor)
        _assert_public_schema_inventory(cursor)
        connection.commit()
    return RuntimeRoleEvidence(owner_role, runtime_role, database_name)


def verify_runtime_role(owner_dsn: str, runtime_role: str) -> RuntimeRoleEvidence:
    """Fail closed unless the configured runtime role remains non-owner and restricted."""

    _validate_dsn(owner_dsn)
    _validate_role_name(runtime_role)
    with psycopg.connect(owner_dsn, autocommit=True) as connection, connection.cursor() as cursor:
        owner_role, database_name = _current_identity(cursor)
        if owner_role == runtime_role:
            raise PostgresRoleError("runtime role must differ from the migration owner role")
        _assert_runtime_role_flags(cursor, runtime_role)
        _assert_not_owner_member(cursor, runtime_role, owner_role)
        _assert_authoritative_object_owner(cursor, owner_role)
        _assert_runtime_is_not_object_owner(cursor, runtime_role)
        _assert_runtime_privileges(cursor, runtime_role, database_name)
        _assert_p3_runtime_privileges(cursor, runtime_role)
        _assert_runtime_function_privileges(cursor, runtime_role)
        _assert_p3_function_security(cursor)
        _assert_no_public_privileges(cursor)
        _assert_public_schema_inventory(cursor)
    return RuntimeRoleEvidence(owner_role, runtime_role, database_name)


def provision_memory_curator_role(
    owner_dsn: str, curator_role: str
) -> MemoryCuratorRoleEvidence:
    """Provision the independent login that may curate but never publish source/trades."""

    _validate_dsn(owner_dsn)
    _validate_role_name(curator_role)
    with psycopg.connect(owner_dsn, autocommit=False) as connection, connection.cursor() as cursor:
        owner_role, database_name = _current_identity(cursor)
        if owner_role == curator_role:
            raise PostgresRoleError("memory curator must differ from the migration owner")
        _assert_curator_role_flags(cursor, curator_role)
        _assert_not_owner_member(cursor, curator_role, owner_role)
        _assert_no_role_memberships(cursor, curator_role)
        _assert_authoritative_object_owner(cursor, owner_role)
        _assert_p3_function_security(cursor)

        role = sql.Identifier(curator_role)
        database = sql.Identifier(database_name)
        cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role))
        cursor.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM {}").format(database, role))
        cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(role))
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        cursor.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role))
        cursor.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(role))
        cursor.execute(sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {}").format(role))
        cursor.execute(
            sql.SQL(
                "GRANT SELECT ON TABLE public.approved_reflection_records, "
                "public.approved_reflection_sources TO {}"
            ).format(role)
        )
        for signature in _CURATOR_EXECUTE_SIGNATURES:
            cursor.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                    sql.SQL(signature), role
                )
            )
        _assert_curator_privileges(cursor, curator_role, database_name)
        _assert_no_public_privileges(cursor)
        _assert_public_schema_inventory(cursor)
        connection.commit()
    return MemoryCuratorRoleEvidence(owner_role, curator_role, database_name)


def verify_memory_curator_role(
    owner_dsn: str, curator_role: str
) -> MemoryCuratorRoleEvidence:
    """Fail closed on any curator flag, ownership, object, or privilege drift."""

    _validate_dsn(owner_dsn)
    _validate_role_name(curator_role)
    with psycopg.connect(owner_dsn, autocommit=True) as connection, connection.cursor() as cursor:
        owner_role, database_name = _current_identity(cursor)
        if owner_role == curator_role:
            raise PostgresRoleError("memory curator must differ from the migration owner")
        _assert_curator_role_flags(cursor, curator_role)
        _assert_not_owner_member(cursor, curator_role, owner_role)
        _assert_no_role_memberships(cursor, curator_role)
        _assert_authoritative_object_owner(cursor, owner_role)
        _assert_runtime_is_not_object_owner(cursor, curator_role)
        _assert_curator_privileges(cursor, curator_role, database_name)
        _assert_p3_function_security(cursor)
        _assert_no_public_privileges(cursor)
        _assert_public_schema_inventory(cursor)
    return MemoryCuratorRoleEvidence(owner_role, curator_role, database_name)


def _validate_dsn(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise PostgresRoleError("owner DSN must be non-empty text")
    return value


def _validate_role_name(value: object) -> str:
    if type(value) is not str or _ROLE_PATTERN.fullmatch(value) is None:
        raise PostgresRoleError("runtime role must use the canonical bounded role format")
    return value


def _current_identity(cursor: psycopg.Cursor[object]) -> tuple[str, str]:
    cursor.execute("SELECT current_user, current_database()")
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None or len(row) != 2 or type(row[0]) is not str or type(row[1]) is not str:
        raise PostgresRoleError("could not determine PostgreSQL owner identity")
    return row[0], row[1]


def _assert_runtime_role_flags(cursor: psycopg.Cursor[object], runtime_role: str) -> None:
    cursor.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin "
        "FROM pg_catalog.pg_roles WHERE rolname = %s",
        (runtime_role,),
    )
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None or row != (False, False, False, True):
        raise PostgresRoleError("runtime role must be LOGIN and have no elevated role flags")


def _assert_curator_role_flags(cursor: psycopg.Cursor[object], curator_role: str) -> None:
    cursor.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin "
        "FROM pg_catalog.pg_roles WHERE rolname = %s",
        (curator_role,),
    )
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None or row != (False, False, False, True):
        raise PostgresRoleError("memory curator must be LOGIN without elevated role flags")


def _assert_no_role_memberships(cursor: psycopg.Cursor[object], role_name: str) -> None:
    cursor.execute(
        """
        SELECT pg_catalog.count(*)
        FROM pg_catalog.pg_auth_members AS membership
        JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
        WHERE member.rolname = %s
        """,
        (role_name,),
    )
    if cursor.fetchone() != (0,):
        raise PostgresRoleError("memory curator must not inherit any other role")


def _assert_not_owner_member(
    cursor: psycopg.Cursor[object],
    runtime_role: str,
    owner_role: str,
) -> None:
    cursor.execute(
        "SELECT pg_catalog.pg_has_role(%s, %s, 'MEMBER')",
        (runtime_role, owner_role),
    )
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None or row[0] is not False:
        raise PostgresRoleError("runtime role must not inherit migration-owner authority")


def _assert_authoritative_object_owner(cursor: psycopg.Cursor[object], owner_role: str) -> None:
    cursor.execute(
        """
        SELECT DISTINCT owner_name
        FROM (
            SELECT pg_catalog.pg_get_userbyid(c.relowner) AS owner_name
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname IN (
                  'schema_metadata', 'schema_migrations', 'domain_events',
                  'audit_events', 'job_instances', 'job_leases',
                  'order_intents', 'broker_orders', 'fills',
                  'reconciliation_runs', 'reconciliation_mismatches',
                  'control_commands', 'control_state', 'account_baselines',
                  'account_baseline_revisions', 'source_objects',
                  'source_records', 'evidence_packets', 'analysis_runs',
                  'analysis_stage_results', 'research_bundles',
                  'research_bundle_items', 'risk_rejection_feedback',
                  'proposal_contexts', 'proposal_runs', 'risk_debates',
                  'portfolio_proposals', 'proposal_stage_results',
                  'model_call_claims', 'model_call_audits',
                  'reflection_records', 'reflection_sources',
                  'reflection_corrections', 'approved_reflection_records',
                  'approved_reflection_sources', 'memory_artifacts',
                  'memory_artifact_sources', 'memory_artifact_state_events',
                  'memory_promotion_history', 'memory_current_pointer',
                  'memory_curation_audits'
              )
            UNION ALL
            SELECT pg_catalog.pg_get_userbyid(p.proowner) AS owner_name
            FROM pg_catalog.pg_proc AS p
            JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
              AND p.proname IN (
                  'acquire_job_lease', 'renew_job_lease', 'release_job_lease',
                  'transition_job_status', 'guard_job_instance_status_write',
                  'order_status_transition_is_valid',
                  'broker_order_status_transition_is_valid',
                  'guard_order_intent_write', 'guard_broker_order_write',
                  'guard_control_state_write', 'guard_account_baseline_write',
                  'guard_account_baseline_revision_write',
                  'register_source_object', 'publish_source_object',
                  'register_source_record', 'register_evidence_packet',
                  'create_analysis_run', 'advance_analysis_stage',
                  'register_research_bundle', 'register_risk_feedback',
                  'register_proposal_context', 'create_proposal_run',
                  'advance_proposal_stage', 'guard_proposal_run_write',
                  'guard_proposal_stage_result_write', 'p3d_canonical_json',
                  'p3d_derive_run_id', 'p3d_text_is_safe',
                  'guard_model_call_claim_write', 'claim_model_call_attempt',
                  'register_model_call_attempt', 'register_reflection_record',
                  'register_memory_candidate', 'register_memory_curation_audit',
                  'validate_memory_artifact',
                  'promote_memory_artifact', 'current_memory_artifact',
                  'current_memory_pointer_artifact',
                  'p3f_text_is_safe', 'p3f_instruction_text_is_safe',
                  'p3f_fact_text_is_closed'
              )
        ) AS owners
        """
    )
    owners = {str(cast(tuple[object, ...], row)[0]) for row in cursor.fetchall()}
    if owners != {owner_role}:
        raise PostgresRoleError("migration connection does not own every authoritative object")


def _assert_runtime_is_not_object_owner(cursor: psycopg.Cursor[object], runtime_role: str) -> None:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND pg_catalog.pg_get_userbyid(c.relowner) = %s
            UNION ALL
            SELECT 1
            FROM pg_catalog.pg_proc AS p
            JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND pg_catalog.pg_get_userbyid(p.proowner) = %s
        )
        """,
        (runtime_role, runtime_role),
    )
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None or row[0] is not False:
        raise PostgresRoleError("runtime role must not own objects in the authoritative schema")


def _assert_runtime_privileges(
    cursor: psycopg.Cursor[object], runtime_role: str, database_name: str
) -> None:
    cursor.execute(
        """
        SELECT
            has_database_privilege(%s, %s, 'TEMPORARY'),
            has_schema_privilege(%s, 'public', 'CREATE'),
            has_table_privilege(%s, 'public.job_leases', 'INSERT,UPDATE,DELETE'),
            has_table_privilege(%s, 'public.job_instances', 'UPDATE,DELETE'),
            has_function_privilege(
                %s, 'public.acquire_job_lease(text,text,interval)', 'EXECUTE'
            ),
            has_function_privilege(
                %s, 'public.transition_job_status(text,text,bigint,text)', 'EXECUTE'
            ),
            has_table_privilege(%s, 'public.order_intents', 'INSERT,UPDATE'),
            has_table_privilege(%s, 'public.order_intents', 'DELETE'),
            has_table_privilege(%s, 'public.broker_orders', 'INSERT,UPDATE'),
            has_table_privilege(%s, 'public.fills', 'INSERT'),
            has_table_privilege(%s, 'public.fills', 'UPDATE,DELETE'),
            has_table_privilege(%s, 'public.reconciliation_runs', 'INSERT'),
            has_table_privilege(%s, 'public.reconciliation_runs', 'UPDATE,DELETE'),
            has_table_privilege(%s, 'public.reconciliation_mismatches', 'INSERT'),
            has_table_privilege(%s, 'public.reconciliation_mismatches', 'UPDATE,DELETE'),
            has_table_privilege(%s, 'public.control_commands', 'INSERT'),
            has_table_privilege(%s, 'public.control_commands', 'UPDATE,DELETE'),
            has_table_privilege(%s, 'public.control_state', 'UPDATE'),
            has_table_privilege(%s, 'public.control_state', 'INSERT,DELETE'),
            has_table_privilege(%s, 'public.account_baselines', 'INSERT'),
            has_table_privilege(%s, 'public.account_baselines', 'UPDATE,DELETE'),
            has_table_privilege(%s, 'public.account_baselines', 'SELECT'),
            has_table_privilege(%s, 'public.account_baseline_revisions', 'INSERT'),
            has_table_privilege(%s, 'public.account_baseline_revisions', 'UPDATE,DELETE'),
            has_table_privilege(%s, 'public.account_baseline_revisions', 'SELECT')
        """,
        (
            runtime_role,
            database_name,
            *([runtime_role] * 24),
        ),
    )
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row != (
        False,
        False,
        False,
        False,
        True,
        True,
        True,
        False,
        True,
        True,
        False,
        True,
        False,
        True,
        False,
        True,
        False,
        True,
        False,
        False,
        False,
        True,
        False,
        False,
        True,
    ):
        raise PostgresRoleError(
            "runtime role privileges do not match the approved least-privilege set"
        )


def _assert_p3_runtime_privileges(cursor: psycopg.Cursor[object], runtime_role: str) -> None:
    tables = (
        "source_objects",
        "source_records",
        "evidence_packets",
        "analysis_runs",
        "analysis_stage_results",
        "research_bundles",
        "research_bundle_items",
        "risk_rejection_feedback",
        "proposal_contexts",
        "proposal_runs",
        "risk_debates",
        "portfolio_proposals",
        "proposal_stage_results",
        "model_call_audits",
        "model_call_claims",
    )
    for table in tables:
        cursor.execute(
            "SELECT "
            "has_table_privilege(%s, %s, 'SELECT'), "
            "has_table_privilege(%s, %s, 'INSERT'), "
            "has_table_privilege(%s, %s, 'UPDATE'), "
            "has_table_privilege(%s, %s, 'DELETE'), "
            "has_table_privilege(%s, %s, 'TRUNCATE'), "
            "has_table_privilege(%s, %s, 'REFERENCES'), "
            "has_table_privilege(%s, %s, 'TRIGGER')",
            tuple(value for _ in range(7) for value in (runtime_role, f"public.{table}")),
        )
        if cursor.fetchone() != (True, False, False, False, False, False, False):
            raise PostgresRoleError(
                "runtime role P3 table privileges do not match the approved set"
            )

    for table in (
        "reflection_records",
        "reflection_sources",
        "reflection_corrections",
        "memory_artifacts",
        "memory_artifact_sources",
        "memory_artifact_state_events",
        "memory_promotion_history",
        "memory_current_pointer",
        "memory_curation_audits",
    ):
        cursor.execute(
            "SELECT "
            "has_table_privilege(%s, %s, 'SELECT'), "
            "has_table_privilege(%s, %s, 'INSERT'), "
            "has_table_privilege(%s, %s, 'UPDATE'), "
            "has_table_privilege(%s, %s, 'DELETE'), "
            "has_table_privilege(%s, %s, 'TRUNCATE'), "
            "has_table_privilege(%s, %s, 'REFERENCES'), "
            "has_table_privilege(%s, %s, 'TRIGGER')",
            tuple(value for _ in range(7) for value in (runtime_role, f"public.{table}")),
        )
        if cursor.fetchone() != (False, False, False, False, False, False, False):
            raise PostgresRoleError(
                "runtime role memory table privileges do not match the approved set"
            )
    for view in ("approved_reflection_records", "approved_reflection_sources"):
        cursor.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT'), "
            "has_table_privilege(%s, %s, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
            (runtime_role, f"public.{view}", runtime_role, f"public.{view}"),
        )
        if cursor.fetchone() != (True, False):
            raise PostgresRoleError(
                "runtime role reflection-view privileges do not match the approved set"
            )

    functions = _P3_FUNCTION_SIGNATURES
    for signature, expected in functions:
        cursor.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            (runtime_role, signature),
        )
        if cursor.fetchone() != (expected,):
            raise PostgresRoleError(
                "runtime role P3 function privileges do not match the approved set"
            )


def _assert_p3_function_security(cursor: psycopg.Cursor[object]) -> None:
    """Every P3 authority function must retain definer rights and a fixed path."""

    for signature, _ in _P3_FUNCTION_SIGNATURES:
        cursor.execute(
            """
            SELECT p.prosecdef, p.proconfig
            FROM pg_catalog.pg_proc AS p
            WHERE p.oid = pg_catalog.to_regprocedure(%s)
            """,
            (signature,),
        )
        row = cast(tuple[object, ...] | None, cursor.fetchone())
        expected_path = (
            ["search_path=pg_catalog, public"]
            if signature in _MEMORY_FUNCTION_SIGNATURES
            else ["search_path=pg_catalog, public, pg_temp"]
        )
        if row != (True, expected_path):
            raise PostgresRoleError(
                "P3 function security configuration does not match the approved set"
            )


def _assert_curator_privileges(
    cursor: psycopg.Cursor[object], curator_role: str, database_name: str
) -> None:
    cursor.execute(
        "SELECT has_database_privilege(%s, %s, 'CONNECT'), "
        "has_database_privilege(%s, %s, 'TEMPORARY'), "
        "has_schema_privilege(%s, 'public', 'USAGE'), "
        "has_schema_privilege(%s, 'public', 'CREATE')",
        (curator_role, database_name, curator_role, database_name, curator_role, curator_role),
    )
    if cursor.fetchone() != (True, False, True, False):
        raise PostgresRoleError("memory curator database/schema privileges drifted")

    cursor.execute(
        """
        SELECT c.relname,
               has_table_privilege(%s, c.oid, 'SELECT'),
               has_table_privilege(%s, c.oid, 'INSERT'),
               has_table_privilege(%s, c.oid, 'UPDATE'),
               has_table_privilege(%s, c.oid, 'DELETE'),
               has_table_privilege(%s, c.oid, 'TRUNCATE'),
               has_table_privilege(%s, c.oid, 'REFERENCES'),
               has_table_privilege(%s, c.oid, 'TRIGGER')
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
        ORDER BY c.relname
        """,
        (curator_role,) * 7,
    )
    for row in cast("list[tuple[object, ...]]", cursor.fetchall()):
        expected = (
            (True, False, False, False, False, False, False)
            if str(row[0]) in _AUTHORITATIVE_VIEW_NAMES
            else (False, False, False, False, False, False, False)
        )
        if row[1:] != expected:
            raise PostgresRoleError("memory curator table/view privileges exceed the approved set")

    cursor.execute(
        """
        SELECT 'public.' || p.proname || '(' ||
                   array_to_string(p.proargtypes::regtype[], ',') || ')',
               has_function_privilege(%s, p.oid, 'EXECUTE')
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
        """,
        (curator_role,),
    )
    observed = {
        str(row[0]): bool(row[1])
        for row in cast("list[tuple[object, ...]]", cursor.fetchall())
    }
    expected_functions = {
        signature: signature in _CURATOR_EXECUTE_SIGNATURES for signature in observed
    }
    if observed != expected_functions or not observed.keys() >= _CURATOR_EXECUTE_SIGNATURES:
        raise PostgresRoleError("memory curator function privileges exceed the approved set")


def _assert_runtime_function_privileges(cursor: psycopg.Cursor[object], runtime_role: str) -> None:
    """Runtime may execute exactly the centrally approved public-schema functions."""

    cursor.execute(
        """
        SELECT 'public.' || p.proname || '(' ||
                   array_to_string(p.proargtypes::regtype[], ',') || ')',
               has_function_privilege(%s, p.oid, 'EXECUTE')
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
        """,
        (runtime_role,),
    )
    observed = {
        str(row[0]): bool(row[1]) for row in cast("list[tuple[object, ...]]", cursor.fetchall())
    }
    expected = {signature: signature in _RUNTIME_EXECUTE_SIGNATURES for signature in observed}
    if observed != expected or not observed.keys() >= _RUNTIME_EXECUTE_SIGNATURES:
        raise PostgresRoleError(
            "runtime role function privileges do not match the approved inventory"
        )


def _assert_no_public_privileges(cursor: psycopg.Cursor[object]) -> None:
    """PUBLIC must hold nothing on authoritative tables or public-schema functions."""

    cursor.execute(
        """
        SELECT c.relname,
               has_table_privilege('public', c.oid, 'SELECT'),
               has_table_privilege('public', c.oid, 'INSERT'),
               has_table_privilege('public', c.oid, 'UPDATE'),
               has_table_privilege('public', c.oid, 'DELETE'),
               has_table_privilege('public', c.oid, 'TRUNCATE'),
               has_table_privilege('public', c.oid, 'REFERENCES'),
               has_table_privilege('public', c.oid, 'TRIGGER')
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
        """
    )
    for row in cast("list[tuple[object, ...]]", cursor.fetchall()):
        if row[1:] != (False, False, False, False, False, False, False):
            raise PostgresRoleError("P3 table privileges granted to PUBLIC exceed the approved set")
    cursor.execute(
        """
        SELECT has_function_privilege('public', p.oid, 'EXECUTE')
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
        """
    )
    for row in cast("list[tuple[object, ...]]", cursor.fetchall()):
        if row[0] is not False:
            raise PostgresRoleError("function privileges granted to PUBLIC exceed the approved set")


def _assert_public_schema_inventory(cursor: psycopg.Cursor[object]) -> None:
    """The public schema must contain exactly the authoritative tables and functions."""

    cursor.execute(
        """
        SELECT c.relname
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
        """
    )
    tables = {str(row[0]) for row in cast("list[tuple[object, ...]]", cursor.fetchall())}
    if tables != _AUTHORITATIVE_TABLE_NAMES:
        raise PostgresRoleError("public schema tables do not match the authoritative inventory")
    cursor.execute(
        """
        SELECT c.relname
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm')
        """
    )
    views = {str(row[0]) for row in cast("list[tuple[object, ...]]", cursor.fetchall())}
    if views != _AUTHORITATIVE_VIEW_NAMES:
        raise PostgresRoleError("public schema views do not match the authoritative inventory")
    cursor.execute(
        """
        SELECT p.proname, array_to_string(p.proargtypes::regtype[], ',')
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
        """
    )
    functions = {
        (str(row[0]), str(row[1])) for row in cast("list[tuple[object, ...]]", cursor.fetchall())
    }
    if functions != _AUTHORITATIVE_FUNCTIONS:
        raise PostgresRoleError("public schema functions do not match the authoritative inventory")
