# mypy: ignore-errors
"""Fail-closed tests for Tavily account compliance configuration.

The fixtures contain audit metadata only. No API keys, environment variables,
external authorization checks, or network calls are involved.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

import pytest

from seven_lens.config.errors import ConfigurationError
from seven_lens.config.tavily import (
    TavilyAccountUsage,
    TavilyAuthorizationEvidenceRecord,
    TavilyAuthorizationEvidenceSource,
    TavilyAuthorizationStatus,
    TavilyComplianceConfig,
    TavilyComplianceMode,
)
from seven_lens.domain.value_objects import UtcTimestamp

RESET_AT = datetime(2026, 9, 1, tzinfo=UTC)
VERIFIED_AT = UtcTimestamp(datetime(2026, 8, 14, 6, 30, tzinfo=UTC))


def account(
    number: int,
    *,
    enabled: bool = True,
    usage: int = 0,
    cap: int = 1_000,
    cooldown_until: datetime | None = None,
) -> TavilyAccountUsage:
    """Create a deterministic, credential-free usage ledger fixture."""
    return TavilyAccountUsage(
        account_id=f"acct-{number:02d}",
        enabled=enabled,
        monthly_usage_credits=usage,
        monthly_hard_cap_credits=cap,
        reset_at=UtcTimestamp(RESET_AT),
        cooldown_until=(UtcTimestamp(cooldown_until) if cooldown_until is not None else None),
    )


def evidence_record(
    account_numbers: Iterable[int] = range(1, 8),
    *,
    status: TavilyAuthorizationStatus = TavilyAuthorizationStatus.VERIFIED,
    source_record_id: str = "CASE-86753090",
) -> TavilyAuthorizationEvidenceRecord:
    return TavilyAuthorizationEvidenceRecord(
        record_id=f"tavily-authz-{'a' * 32}",
        source=TavilyAuthorizationEvidenceSource.SUPPORT_CONFIRMATION,
        source_record_id=source_record_id,
        authorized_account_ids=tuple(f"acct-{number:02d}" for number in account_numbers),
        verified_at=(None if status is TavilyAuthorizationStatus.UNVERIFIED else VERIFIED_AT),
        status=status,
    )


def compliance(
    mode: TavilyComplianceMode,
    accounts: tuple[TavilyAccountUsage, ...],
    *,
    global_usage: int = 0,
    evidence: TavilyAuthorizationEvidenceRecord | None = None,
) -> TavilyComplianceConfig:
    return TavilyComplianceConfig(
        mode=mode,
        accounts=accounts,
        global_monthly_usage_credits=global_usage,
        authorization_evidence=evidence,
    )


def test_single_account_unverified_allows_one_enabled_account() -> None:
    config = compliance(
        TavilyComplianceMode.SINGLE_ACCOUNT_UNVERIFIED,
        (account(1), account(2, enabled=False), account(3, enabled=False)),
    )

    assert config.mode is TavilyComplianceMode.SINGLE_ACCOUNT_UNVERIFIED
    assert config.global_monthly_hard_cap_credits == 1_000
    assert sum(item.enabled for item in config.accounts) == 1


def test_single_account_unverified_allows_exact_global_and_per_account_caps() -> None:
    config = compliance(
        TavilyComplianceMode.SINGLE_ACCOUNT_UNVERIFIED,
        (account(1, usage=1_000),),
        global_usage=1_000,
    )

    assert config.accounts[0].monthly_usage_credits == 1_000
    assert config.global_monthly_usage_credits == 1_000


def test_global_usage_cannot_undercount_account_ledgers() -> None:
    with pytest.raises(ValueError):
        compliance(
            TavilyComplianceMode.SINGLE_ACCOUNT_UNVERIFIED,
            (account(1, usage=600),),
            global_usage=599,
        )


def test_authorized_pool_fails_closed_even_with_claimed_verified_record() -> None:
    accounts = tuple(account(number) for number in range(1, 8))
    evidence = evidence_record()

    with pytest.raises(ConfigurationError, match="external verification"):
        compliance(
            TavilyComplianceMode.AUTHORIZED_ACCOUNT_POOL,
            accounts,
            global_usage=7_000,
            evidence=evidence,
        )

    assert evidence.authorized_account_ids == tuple(item.account_id for item in accounts)
    assert evidence.verified_at == VERIFIED_AT
    assert evidence.status is TavilyAuthorizationStatus.VERIFIED


def test_single_mode_preserves_reset_and_cooldown_metadata() -> None:
    cooldown = RESET_AT + timedelta(hours=1)

    config = compliance(
        TavilyComplianceMode.SINGLE_ACCOUNT_UNVERIFIED,
        (account(1, cooldown_until=cooldown),),
    )

    assert config.accounts[0].reset_at == UtcTimestamp(RESET_AT)
    assert config.accounts[0].cooldown_until == UtcTimestamp(cooldown)


@pytest.mark.parametrize(
    "config_factory",
    [
        lambda: compliance(
            TavilyComplianceMode.SINGLE_ACCOUNT_UNVERIFIED,
            (account(1), account(2)),
        ),
        lambda: compliance(
            TavilyComplianceMode.SINGLE_ACCOUNT_UNVERIFIED,
            (account(1, usage=1_001),),
        ),
        lambda: compliance(
            TavilyComplianceMode.SINGLE_ACCOUNT_UNVERIFIED,
            (account(1),),
            global_usage=1_001,
        ),
        lambda: compliance(
            TavilyComplianceMode.AUTHORIZED_ACCOUNT_POOL,
            tuple(account(number) for number in range(1, 8)),
            evidence=None,
        ),
        lambda: compliance(
            TavilyComplianceMode.AUTHORIZED_ACCOUNT_POOL,
            tuple(account(number) for number in range(1, 9)),
            evidence=evidence_record(),
        ),
        lambda: compliance(
            TavilyComplianceMode.AUTHORIZED_ACCOUNT_POOL,
            (account(1, usage=1_001),),
            evidence=evidence_record((1,)),
        ),
        lambda: compliance(
            TavilyComplianceMode.AUTHORIZED_ACCOUNT_POOL,
            (account(1),),
            global_usage=7_001,
            evidence=evidence_record((1,)),
        ),
    ],
)
def test_tavily_configuration_rejects_unsafe_or_over_cap_states(
    config_factory: Callable[[], TavilyComplianceConfig],
) -> None:
    with pytest.raises(ValueError):
        config_factory()


@pytest.mark.parametrize(
    "untrusted_evidence",
    [
        "x:abc",
        "file:///etc/passwd",
        "tavily-support:TICKET-123",
        "FAKE-TICKET-999",
        "placeholder",
        "",
        None,
    ],
)
def test_authorized_pool_rejects_user_supplied_evidence_strings(
    untrusted_evidence: object,
) -> None:
    with pytest.raises(ConfigurationError):
        TavilyComplianceConfig(
            mode=TavilyComplianceMode.AUTHORIZED_ACCOUNT_POOL,
            accounts=(account(1),),
            global_monthly_usage_credits=0,
            authorization_evidence=untrusted_evidence,  # type: ignore[arg-type]
        )


def test_authorization_evidence_must_match_current_account_set() -> None:
    with pytest.raises(ConfigurationError, match="current account set"):
        compliance(
            TavilyComplianceMode.AUTHORIZED_ACCOUNT_POOL,
            (account(1), account(2)),
            evidence=evidence_record((1, 3)),
        )


@pytest.mark.parametrize(
    "status",
    [
        TavilyAuthorizationStatus.UNVERIFIED,
        TavilyAuthorizationStatus.REVOKED,
        TavilyAuthorizationStatus.EXPIRED,
    ],
)
def test_authorized_pool_rejects_non_current_evidence_status(
    status: TavilyAuthorizationStatus,
) -> None:
    with pytest.raises(ConfigurationError, match="not currently verified"):
        compliance(
            TavilyComplianceMode.AUTHORIZED_ACCOUNT_POOL,
            (account(1),),
            evidence=evidence_record((1,), status=status),
        )


@pytest.mark.parametrize("source_record_id", ["placeholder", "FAKE-TICKET-999", "test-case"])
def test_evidence_record_rejects_placeholder_source_identifiers(
    source_record_id: str,
) -> None:
    with pytest.raises(ConfigurationError):
        evidence_record(source_record_id=source_record_id)


def test_unverified_evidence_cannot_claim_a_verification_time() -> None:
    with pytest.raises(ConfigurationError):
        TavilyAuthorizationEvidenceRecord(
            record_id=f"tavily-authz-{'b' * 32}",
            source=TavilyAuthorizationEvidenceSource.ORDER_FORM,
            source_record_id="ORDER-86753090",
            authorized_account_ids=("acct-01",),
            verified_at=VERIFIED_AT,
            status=TavilyAuthorizationStatus.UNVERIFIED,
        )


@pytest.mark.parametrize("bad_usage", [-1, -100, 1_001])
def test_account_usage_rejects_negative_or_over_cap_values(bad_usage: int) -> None:
    with pytest.raises(ValueError):
        account(1, usage=bad_usage)


def test_tavily_modes_are_explicit_and_do_not_include_live_or_paygo_modes() -> None:
    assert {mode.name for mode in TavilyComplianceMode} == {
        "SINGLE_ACCOUNT_UNVERIFIED",
        "AUTHORIZED_ACCOUNT_POOL",
    }
    assert TavilyComplianceConfig.cross_account_concurrency_allowed is False
