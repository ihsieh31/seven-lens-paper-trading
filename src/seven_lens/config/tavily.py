"""Tavily compliance and quota state without API clients or secrets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final

from seven_lens.config.errors import ConfigurationError
from seven_lens.domain.value_objects import UtcTimestamp

_ACCOUNT_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_EVIDENCE_RECORD_ID_PATTERN: Final = re.compile(r"^tavily-authz-[0-9a-f]{32}$")
_SOURCE_RECORD_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{7,127}$")
_PLACEHOLDER_PATTERN: Final = re.compile(
    r"(?:fake|placeholder|example|test|todo|tbd|unknown)", re.IGNORECASE
)
_PER_ACCOUNT_MONTHLY_HARD_CAP: Final = 1_000
_UNVERIFIED_GLOBAL_MONTHLY_HARD_CAP: Final = 1_000
_AUTHORIZED_GLOBAL_MONTHLY_HARD_CAP: Final = 7_000
_MAX_AUTHORIZED_ACCOUNTS: Final = 7
_EXTERNAL_AUTHORIZATION_VERIFIER_AVAILABLE: Final = False


class TavilyComplianceMode(StrEnum):
    """Legal authority under which Tavily accounts may be enabled."""

    SINGLE_ACCOUNT_UNVERIFIED = "SINGLE_ACCOUNT_UNVERIFIED"
    AUTHORIZED_ACCOUNT_POOL = "AUTHORIZED_ACCOUNT_POOL"


class TavilyAuthorizationEvidenceSource(StrEnum):
    """Permitted kinds of independently retained authorization evidence."""

    SUPPORT_CONFIRMATION = "SUPPORT_CONFIRMATION"
    ORDER_FORM = "ORDER_FORM"
    ADMIN_CONSOLE = "ADMIN_CONSOLE"


class TavilyAuthorizationStatus(StrEnum):
    """Review status recorded for an immutable evidence record."""

    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class TavilyAuthorizationEvidenceRecord:
    """Audit metadata; the record is not itself proof of external verification."""

    record_id: str
    source: TavilyAuthorizationEvidenceSource
    source_record_id: str
    authorized_account_ids: tuple[str, ...]
    verified_at: UtcTimestamp | None
    status: TavilyAuthorizationStatus

    def __post_init__(self) -> None:
        if (
            not isinstance(self.record_id, str)
            or _EVIDENCE_RECORD_ID_PATTERN.fullmatch(self.record_id) is None
        ):
            raise ConfigurationError("invalid Tavily authorization evidence record_id")
        if not isinstance(self.source, TavilyAuthorizationEvidenceSource):
            raise ConfigurationError("invalid Tavily authorization evidence source")
        if (
            not isinstance(self.source_record_id, str)
            or _SOURCE_RECORD_ID_PATTERN.fullmatch(self.source_record_id) is None
            or _PLACEHOLDER_PATTERN.search(self.source_record_id) is not None
        ):
            raise ConfigurationError("invalid or placeholder Tavily source record identifier")
        if not isinstance(self.authorized_account_ids, tuple) or not (
            1 <= len(self.authorized_account_ids) <= _MAX_AUTHORIZED_ACCOUNTS
        ):
            raise ConfigurationError("authorization evidence must bind 1 to 7 accounts")
        if any(
            not isinstance(account_id, str) or _ACCOUNT_ID_PATTERN.fullmatch(account_id) is None
            for account_id in self.authorized_account_ids
        ):
            raise ConfigurationError("authorization evidence contains an invalid account_id")
        if len(self.authorized_account_ids) != len(set(self.authorized_account_ids)):
            raise ConfigurationError("authorization evidence account_id values must be unique")
        if not isinstance(self.status, TavilyAuthorizationStatus):
            raise ConfigurationError("invalid Tavily authorization evidence status")
        if self.status is TavilyAuthorizationStatus.UNVERIFIED:
            if self.verified_at is not None:
                raise ConfigurationError("unverified evidence cannot have verified_at")
        elif not isinstance(self.verified_at, UtcTimestamp):
            raise ConfigurationError("reviewed authorization evidence requires verified_at in UTC")


@dataclass(frozen=True, slots=True)
class TavilyAccountUsage:
    """Non-secret per-account usage, reset, enablement, and cooldown state."""

    account_id: str
    enabled: bool
    monthly_usage_credits: int
    monthly_hard_cap_credits: int
    reset_at: UtcTimestamp
    cooldown_until: UtcTimestamp | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.account_id, str)
            or _ACCOUNT_ID_PATTERN.fullmatch(self.account_id) is None
        ):
            raise ConfigurationError("Tavily account_id must be a non-secret stable identifier")
        if not isinstance(self.enabled, bool):
            raise ConfigurationError("Tavily enabled must be a boolean")
        _require_credit_count(self.monthly_usage_credits, "monthly_usage_credits")
        _require_credit_count(self.monthly_hard_cap_credits, "monthly_hard_cap_credits")
        if self.monthly_hard_cap_credits != _PER_ACCOUNT_MONTHLY_HARD_CAP:
            raise ConfigurationError("each Tavily account hard cap must be 1,000 credits")
        if self.monthly_usage_credits > self.monthly_hard_cap_credits:
            raise ConfigurationError("Tavily account usage exceeds its monthly hard cap")
        if not isinstance(self.reset_at, UtcTimestamp):
            raise ConfigurationError("Tavily reset_at must have UTC storage semantics")
        if self.cooldown_until is not None and not isinstance(self.cooldown_until, UtcTimestamp):
            raise ConfigurationError("Tavily cooldown_until must have UTC storage semantics")


@dataclass(frozen=True, slots=True)
class TavilyComplianceConfig:
    """Validated account-pool authority and global quota state."""

    mode: TavilyComplianceMode
    accounts: tuple[TavilyAccountUsage, ...]
    global_monthly_usage_credits: int
    authorization_evidence: TavilyAuthorizationEvidenceRecord | None = None

    cross_account_concurrency_allowed: ClassVar[bool] = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, TavilyComplianceMode):
            raise ConfigurationError("unknown Tavily compliance mode")
        if not isinstance(self.accounts, tuple) or not self.accounts:
            raise ConfigurationError("Tavily accounts must be a non-empty tuple")
        if any(not isinstance(account, TavilyAccountUsage) for account in self.accounts):
            raise ConfigurationError("Tavily accounts contain an invalid schema")
        if self.authorization_evidence is not None and not isinstance(
            self.authorization_evidence, TavilyAuthorizationEvidenceRecord
        ):
            raise ConfigurationError("Tavily authorization evidence must be an audit record")

        account_ids = [account.account_id for account in self.accounts]
        if len(account_ids) != len(set(account_ids)):
            raise ConfigurationError("Tavily account_id values must be unique")

        _require_credit_count(self.global_monthly_usage_credits, "global_monthly_usage_credits")
        recorded_account_usage = sum(account.monthly_usage_credits for account in self.accounts)
        if self.global_monthly_usage_credits < recorded_account_usage:
            raise ConfigurationError("Tavily global usage cannot undercount account ledgers")
        if self.global_monthly_usage_credits > self.global_monthly_hard_cap_credits:
            raise ConfigurationError("Tavily global usage exceeds its monthly hard cap")

        enabled_count = sum(account.enabled for account in self.accounts)
        if self.mode is TavilyComplianceMode.SINGLE_ACCOUNT_UNVERIFIED:
            if len(self.accounts) > _MAX_AUTHORIZED_ACCOUNTS:
                raise ConfigurationError(
                    "Tavily account registry cannot contain more than 7 accounts"
                )
            if enabled_count != 1:
                raise ConfigurationError(
                    "SINGLE_ACCOUNT_UNVERIFIED requires exactly one enabled account"
                )
            return

        if len(self.accounts) > _MAX_AUTHORIZED_ACCOUNTS:
            raise ConfigurationError("AUTHORIZED_ACCOUNT_POOL allows at most 7 accounts")
        if enabled_count < 1:
            raise ConfigurationError("AUTHORIZED_ACCOUNT_POOL requires an enabled account")
        self._validate_authorized_pool_evidence(account_ids)

    @property
    def global_monthly_hard_cap_credits(self) -> int:
        if self.mode is TavilyComplianceMode.SINGLE_ACCOUNT_UNVERIFIED:
            return _UNVERIFIED_GLOBAL_MONTHLY_HARD_CAP
        return _AUTHORIZED_GLOBAL_MONTHLY_HARD_CAP

    def _validate_authorized_pool_evidence(self, account_ids: list[str]) -> None:
        evidence = self.authorization_evidence
        if evidence is None:
            raise ConfigurationError(
                "AUTHORIZED_ACCOUNT_POOL requires a verified authorization evidence record"
            )
        if evidence.status is not TavilyAuthorizationStatus.VERIFIED:
            raise ConfigurationError("Tavily authorization evidence is not currently verified")
        if set(evidence.authorized_account_ids) != set(account_ids):
            raise ConfigurationError(
                "Tavily authorization evidence does not match the current account set"
            )
        if not _EXTERNAL_AUTHORIZATION_VERIFIER_AVAILABLE:
            raise ConfigurationError(
                "AUTHORIZED_ACCOUNT_POOL is unavailable until external verification exists"
            )


def _require_credit_count(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConfigurationError(f"{field_name} must be a non-negative integer")
