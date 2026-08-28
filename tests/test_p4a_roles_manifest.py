# mypy: ignore-errors
"""P4-A closed source-role registry and per-family manifest tests."""

from __future__ import annotations

import dataclasses
from enum import StrEnum

import pytest

from seven_lens.config.errors import ConfigurationError
from seven_lens.domain.value_objects import SchemaVersion
from seven_lens.security.secret_values import SecretKind
from seven_lens.sources.contracts import RightsStatus
from seven_lens.sources.roles import (
    CoverageLabel,
    NonExecutableReason,
    P4SourceFamily,
    SourceManifestRegistry,
    SourceRole,
    StoragePolicy,
    p4_manifest_registry,
)

_EXPECTED_FAMILY_ROLES = {
    "ALPACA_ASSETS": SourceRole.AUTHORITY,
    "ALPACA_HISTORICAL_BARS": SourceRole.AUTHORITY,
    "ALPACA_IEX_QUOTES": SourceRole.AUTHORITY,
    "ALPACA_CORPORATE_ACTIONS": SourceRole.CONFIRMATION,
    "SEC_EDGAR": SourceRole.AUTHORITY,
    "ISSUER_IR": SourceRole.CONFIRMATION,
    "EXCHANGE_OFFICIAL": SourceRole.AUTHORITY,
    "FRED_ALFRED": SourceRole.AUTHORITY,
    "TREASURY": SourceRole.AUTHORITY,
    "BLS": SourceRole.AUTHORITY,
    "BEA": SourceRole.AUTHORITY,
    "EIA": SourceRole.AUTHORITY,
    "TAVILY": SourceRole.DISCOVERY,
    "GDELT": SourceRole.DISCOVERY,
    "YFINANCE": SourceRole.RESEARCH_SUPPLEMENT,
}


def test_source_role_enum_is_exactly_the_four_approved_roles() -> None:
    assert issubclass(SourceRole, StrEnum)
    assert {role.value for role in SourceRole} == {
        "AUTHORITY",
        "CONFIRMATION",
        "DISCOVERY",
        "RESEARCH_SUPPLEMENT",
    }
    assert len(SourceRole) == 4


def test_registry_contains_every_approved_family_exactly_once() -> None:
    registry = p4_manifest_registry()

    assert {family.value for family in P4SourceFamily} == set(_EXPECTED_FAMILY_ROLES)
    assert len(P4SourceFamily) == 15
    for family in P4SourceFamily:
        assert registry.policy(family).family is family
    with pytest.raises(KeyError):
        registry.policy("NOT_A_FAMILY")  # type: ignore[arg-type]


def test_every_family_carries_its_pinned_role() -> None:
    registry = p4_manifest_registry()

    for family_name, role in _EXPECTED_FAMILY_ROLES.items():
        policy = registry.policy(P4SourceFamily(family_name))
        assert policy.role is role, family_name


def test_iex_is_authority_with_mandatory_limited_coverage_flag() -> None:
    registry = p4_manifest_registry()

    iex = registry.policy(P4SourceFamily.ALPACA_IEX_QUOTES)
    assert iex.role is SourceRole.AUTHORITY
    assert iex.coverage is CoverageLabel.LIMITED_MARKET_COVERAGE
    bars = registry.policy(P4SourceFamily.ALPACA_HISTORICAL_BARS)
    assert bars.coverage is CoverageLabel.FULL


def test_registry_hash_is_deterministic_and_content_bound() -> None:
    registry = p4_manifest_registry()

    assert registry.registry_hash == p4_manifest_registry().registry_hash
    assert len(registry.registry_hash) == 64


def test_tavily_is_discovery_but_not_network_executable_for_non_get_upstream() -> None:
    registry = p4_manifest_registry()

    tavily = registry.policy(P4SourceFamily.TAVILY)
    assert tavily.role is SourceRole.DISCOVERY
    assert tavily.executable is False
    assert tavily.non_executable_reason is NonExecutableReason.NON_GET_UPSTREAM


def test_yfinance_is_supplement_with_unverified_rights_and_no_execution() -> None:
    registry = p4_manifest_registry()

    yfinance = registry.policy(P4SourceFamily.YFINANCE)
    assert yfinance.role is SourceRole.RESEARCH_SUPPLEMENT
    assert yfinance.rights is RightsStatus.UNKNOWN
    assert yfinance.executable is False
    assert yfinance.non_executable_reason is NonExecutableReason.RIGHTS_UNVERIFIED


def test_executable_families_have_verified_rights() -> None:
    registry = p4_manifest_registry()

    for family in P4SourceFamily:
        policy = registry.policy(family)
        if policy.executable:
            assert policy.rights is not RightsStatus.UNKNOWN, family
            assert policy.non_executable_reason is None, family
        else:
            assert policy.non_executable_reason is not None, family


def test_rights_unknown_executable_policy_is_rejected() -> None:
    registry = p4_manifest_registry()
    base = registry.policy(P4SourceFamily.GDELT)

    with pytest.raises(ConfigurationError):
        dataclasses.replace(
            base,
            rights=RightsStatus.UNKNOWN,
            executable=True,
            non_executable_reason=None,
        )


def test_role_escalation_from_discovery_to_authority_is_rejected() -> None:
    registry = p4_manifest_registry()
    base = registry.policy(P4SourceFamily.TAVILY)

    with pytest.raises(ConfigurationError):
        dataclasses.replace(base, role=SourceRole.AUTHORITY)


def test_supplement_escalation_to_authority_is_rejected() -> None:
    registry = p4_manifest_registry()
    base = registry.policy(P4SourceFamily.YFINANCE)

    with pytest.raises(ConfigurationError):
        dataclasses.replace(base, role=SourceRole.AUTHORITY)


def test_duplicate_family_registration_is_rejected() -> None:
    registry = p4_manifest_registry()

    duplicated = (*registry.policies, registry.policy(P4SourceFamily.SEC_EDGAR))
    with pytest.raises(ConfigurationError):
        SourceManifestRegistry(duplicated)


def test_incomplete_registry_is_rejected() -> None:
    registry = p4_manifest_registry()

    with pytest.raises(ConfigurationError):
        SourceManifestRegistry(registry.policies[:-1])


@pytest.mark.parametrize(
    ("family_name", "field", "value"),
    [
        ("SEC_EDGAR", "timeout_seconds", 0),
        ("SEC_EDGAR", "timeout_seconds", 61),
        ("SEC_EDGAR", "max_response_bytes", 0),
        ("SEC_EDGAR", "max_response_bytes", 4_000_001),
        ("SEC_EDGAR", "max_request_bytes", 0),
        ("SEC_EDGAR", "max_decompressed_bytes", 0),
        ("SEC_EDGAR", "requests_per_window", 0),
        ("SEC_EDGAR", "requests_per_window", 6),
        ("SEC_EDGAR", "window_seconds", 0),
        ("SEC_EDGAR", "burst_limit", 0),
        ("SEC_EDGAR", "pagination_max_pages", 0),
        ("SEC_EDGAR", "pagination_max_pages", 1001),
    ],
)
def test_resource_budgets_are_nonzero_and_within_caps(
    family_name: str, field: str, value: int
) -> None:
    registry = p4_manifest_registry()
    base = registry.policy(P4SourceFamily(family_name))

    with pytest.raises(ConfigurationError):
        dataclasses.replace(base, **{field: value})


def test_registry_rate_budget_enforces_sec_five_requests_per_second() -> None:
    registry = p4_manifest_registry()

    sec = registry.policy(P4SourceFamily.SEC_EDGAR)
    assert sec.requests_per_window == 5
    assert sec.window_seconds == 1


@pytest.mark.parametrize(
    ("family_name", "host", "scheme"),
    [
        ("ALPACA_ASSETS", "paper-api.alpaca.markets", "https"),
        ("ALPACA_HISTORICAL_BARS", "data.alpaca.markets", "https"),
        ("ALPACA_IEX_QUOTES", "data.alpaca.markets", "https"),
        ("ALPACA_CORPORATE_ACTIONS", "data.alpaca.markets", "https"),
        ("SEC_EDGAR", "data.sec.gov", "https"),
        ("FRED_ALFRED", "api.stlouisfed.org", "https"),
        ("TREASURY", "fiscaldata.treasury.gov", "https"),
        ("BLS", "api.bls.gov", "https"),
        ("BEA", "api.bea.gov", "https"),
        ("EIA", "api.eia.gov", "https"),
        ("TAVILY", "api.tavily.com", "https"),
        ("GDELT", "api.gdeltproject.org", "https"),
        ("YFINANCE", "query1.finance.yahoo.com", "https"),
    ],
)
def test_exact_hosts_are_pinned_per_family(family_name: str, host: str, scheme: str) -> None:
    registry = p4_manifest_registry()

    policy = registry.policy(P4SourceFamily(family_name))
    assert policy.host == host
    assert policy.scheme == scheme
    assert policy.path_template.startswith("/")


def test_issuer_and_exchange_policies_exist_with_registered_hosts() -> None:
    registry = p4_manifest_registry()

    issuer = registry.policy(P4SourceFamily.ISSUER_IR)
    exchange = registry.policy(P4SourceFamily.EXCHANGE_OFFICIAL)
    assert issuer.role is SourceRole.CONFIRMATION
    assert exchange.role is SourceRole.AUTHORITY
    assert "www.nyse.com" in {host for host in exchange.registered_hosts}
    assert "www.nasdaq.com" in {host for host in exchange.registered_hosts}
    assert issuer.registered_hosts == ()
    assert exchange.host == ""


def test_issuer_and_exchange_are_parse_only_with_no_pinned_exact_host() -> None:
    registry = p4_manifest_registry()

    for family in (P4SourceFamily.ISSUER_IR, P4SourceFamily.EXCHANGE_OFFICIAL):
        policy = registry.policy(family)
        assert policy.executable is False, family
        assert policy.non_executable_reason is NonExecutableReason.NO_PINNED_EXACT_HOST, family


@pytest.mark.parametrize(
    ("family_name", "expected"),
    [
        ("ALPACA_ASSETS", (SecretKind.ALPACA_PAPER_KEY_ID, SecretKind.ALPACA_PAPER_SECRET_KEY)),
        (
            "ALPACA_HISTORICAL_BARS",
            (SecretKind.ALPACA_PAPER_KEY_ID, SecretKind.ALPACA_PAPER_SECRET_KEY),
        ),
        ("ALPACA_IEX_QUOTES", (SecretKind.ALPACA_PAPER_KEY_ID, SecretKind.ALPACA_PAPER_SECRET_KEY)),
        (
            "ALPACA_CORPORATE_ACTIONS",
            (SecretKind.ALPACA_PAPER_KEY_ID, SecretKind.ALPACA_PAPER_SECRET_KEY),
        ),
        ("SEC_EDGAR", ()),
        ("ISSUER_IR", ()),
        ("EXCHANGE_OFFICIAL", ()),
        ("FRED_ALFRED", (SecretKind.FRED_API_KEY,)),
        ("TREASURY", ()),
        ("BLS", (SecretKind.BLS_API_KEY,)),
        ("BEA", (SecretKind.BEA_API_KEY,)),
        ("EIA", (SecretKind.EIA_API_KEY,)),
        ("TAVILY", (SecretKind.TAVILY_API_KEY,)),
        ("GDELT", ()),
        ("YFINANCE", ()),
    ],
)
def test_auth_secrets_use_exact_typed_kinds(family_name: str, expected: tuple) -> None:
    registry = p4_manifest_registry()

    assert registry.policy(P4SourceFamily(family_name)).auth_secrets == expected


def test_header_allowlists_name_headers_only_and_never_values() -> None:
    registry = p4_manifest_registry()

    for family in P4SourceFamily:
        policy = registry.policy(family)
        for header in policy.header_allowlist:
            assert header == header.strip()
            assert ":" not in header
            assert len(header) <= 64


def test_query_allowlists_are_bounded_and_required_queries_present() -> None:
    registry = p4_manifest_registry()

    fred = registry.policy(P4SourceFamily.FRED_ALFRED)
    assert "realtime_start" in fred.query_allowlist
    assert "realtime_end" in fred.query_allowlist
    assert {"realtime_start", "realtime_end"} <= set(fred.required_query)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", "Data.Sec.Gov"),
        ("host", "192.0.2.1"),
        ("host", "data.sec.gov:443"),
        ("host", "xn--data-ned.sec.gov"),
        ("host", "data.sec..gov"),
        ("host", "da ta.sec.gov"),
    ],
)
def test_invalid_hosts_are_rejected_at_policy_construction(field: str, value: str) -> None:
    registry = p4_manifest_registry()
    base = registry.policy(P4SourceFamily.SEC_EDGAR)

    with pytest.raises(ConfigurationError):
        dataclasses.replace(base, **{field: value})


@pytest.mark.parametrize(
    "template",
    ["submissions/CIK000000.json", "/a//b", "/a/../b", "/a/{cik", "/a/?x=1", "/#frag", ""],
)
def test_invalid_path_templates_are_rejected(template: str) -> None:
    registry = p4_manifest_registry()
    base = registry.policy(P4SourceFamily.SEC_EDGAR)

    with pytest.raises(ConfigurationError):
        dataclasses.replace(base, path_template=template)


def test_policy_schema_versions_are_strict() -> None:
    registry = p4_manifest_registry()

    for family in P4SourceFamily:
        policy = registry.policy(family)
        assert type(policy.schema_version) is SchemaVersion
    base = registry.policy(P4SourceFamily.BEA)
    with pytest.raises(ValueError):
        dataclasses.replace(base, schema_version=SchemaVersion("not-a-version"))


def test_storage_policy_and_producer_version_present_per_family() -> None:
    registry = p4_manifest_registry()

    for family in P4SourceFamily:
        policy = registry.policy(family)
        assert policy.storage in StoragePolicy
        assert policy.producer_version and len(policy.producer_version) <= 64
        assert policy.allowed_media_types


def test_registry_is_frozen_and_immutable() -> None:
    registry = p4_manifest_registry()

    with pytest.raises(dataclasses.FrozenInstanceError):
        registry.registry_hash = "0" * 64  # type: ignore[misc]
    assert type(registry.policies) is tuple
    with pytest.raises(dataclasses.FrozenInstanceError):
        registry.policies[0].family = P4SourceFamily.BEA  # type: ignore[misc]
