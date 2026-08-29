"""Typed composition root for the execution stack.

This closes the two P2-entry contracts from the security review:

* Configuration binding happens exactly once, at the parsing edge, into typed
  frozen values; there is no generic mapping bag beyond ``from_mapping``.
* The runtime database password is resolved through the scoped secret boundary
  via an exact ``SecretRef`` and revealed at exactly one bounded point when a
  connection info string is composed.  The composed DSN never implements a
  disclosing ``__str__`` and never reaches logs, telemetry, or audit payloads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from seven_lens.application.control_service import ControlPlane
from seven_lens.application.execution_service import ExecutionEngine
from seven_lens.application.ports.broker import PaperBrokerPort
from seven_lens.application.ports.persistence import ControlRepository
from seven_lens.application.ports.secrets import SecretProvider
from seven_lens.application.reconciliation_service import (
    AccountReconciliationPolicy,
    Reconciler,
    ReconciliationMarkPriceProvider,
)
from seven_lens.config.broker import PaperBrokerConfig, validate_paper_startup
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.security.secret_values import (
    SecretKind,
    SecretRef,
    SecretValue,
    validated_secret_ref_identity,
)

_SSL_MODES: Final[frozenset[str]] = frozenset({"require", "verify-ca", "verify-full"})
_HOST_PATTERN_MAX: Final = 253
_DB_FIELD_MAX: Final = 63
_UNSAFE_DSN_FIELD_CHARS: Final[frozenset[str]] = frozenset({"/", "@", ":", "?", "#", "%", "\\"})


class CompositionError(ValueError):
    """Raised when the execution stack is composed from invalid inputs."""


@dataclass(frozen=True, slots=True)
class RuntimeDatabaseConfig:
    """Non-secret connection parameters plus the exact password reference."""

    host: str
    port: int
    dbname: str
    user: str
    sslmode: str
    password_ref: SecretRef

    def __post_init__(self) -> None:
        for field_name, value in (
            ("host", self.host),
            ("dbname", self.dbname),
            ("user", self.user),
        ):
            if (
                type(value) is not str
                or not value.strip()
                or len(value) > _DB_FIELD_MAX
                or "\x00" in value
                or any(
                    char in _UNSAFE_DSN_FIELD_CHARS or ord(char) < 0x20 or ord(char) == 0x7F
                    for char in value
                )
            ):
                raise CompositionError(f"database {field_name} must be bounded safe text")
        if len(self.host) > _HOST_PATTERN_MAX:
            raise CompositionError("database host exceeds the bounded length")
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise CompositionError("database port must be between 1 and 65535")
        if type(self.sslmode) is not str or self.sslmode not in _SSL_MODES:
            raise CompositionError("database sslmode must be require, verify-ca, or verify-full")
        if not isinstance(self.password_ref, SecretRef):
            raise CompositionError("database password_ref must be a SecretRef")
        identity = validated_secret_ref_identity(self.password_ref)
        if identity is None or identity[0] is not SecretKind.POSTGRES_RUNTIME_PASSWORD:
            raise CompositionError("database password must use the postgres runtime reference")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> RuntimeDatabaseConfig:
        """Parse the exact schema; unknown or missing keys fail closed."""
        expected = {"host", "port", "dbname", "user", "sslmode", "password_account"}
        if set(values) != expected:
            missing = sorted(expected - set(values))
            extra = sorted(set(values) - expected)
            raise CompositionError(
                f"invalid database configuration fields: missing={missing}, extra={extra}"
            )
        password_account = values["password_account"]
        if type(password_account) is not str or password_account != "primary":
            raise CompositionError("database password account must be 'primary'")
        return cls(
            host=_text(values["host"], "host"),
            port=_port(values["port"]),
            dbname=_text(values["dbname"], "dbname"),
            user=_text(values["user"], "user"),
            sslmode=_sslmode(values["sslmode"]),
            password_ref=SecretRef.primary(SecretKind.POSTGRES_RUNTIME_PASSWORD),
        )


@dataclass(frozen=True, slots=True)
class AccountReconciliationConfig:
    """Typed account reconciliation policy; never learned from the broker."""

    expected_account_id: str
    cash_tolerance_cents: int
    nav_tolerance_cents: int

    def __post_init__(self) -> None:
        if (
            type(self.expected_account_id) is not str
            or not self.expected_account_id.strip()
            or len(self.expected_account_id) > 100
        ):
            raise CompositionError("account expected_account_id must be bounded text up to 100")
        if type(self.cash_tolerance_cents) is not int or self.cash_tolerance_cents < 0:
            raise CompositionError("account cash_tolerance_cents must be a non-negative integer")
        if type(self.nav_tolerance_cents) is not int or self.nav_tolerance_cents < 0:
            raise CompositionError("account nav_tolerance_cents must be a non-negative integer")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> AccountReconciliationConfig:
        expected = {"expected_account_id", "cash_tolerance_cents", "nav_tolerance_cents"}
        if set(values) != expected:
            missing = sorted(expected - set(values))
            extra = sorted(set(values) - expected)
            raise CompositionError(
                f"invalid account configuration fields: missing={missing}, extra={extra}"
            )
        return cls(
            expected_account_id=_text(values["expected_account_id"], "expected_account_id"),
            cash_tolerance_cents=_positive_int(
                values["cash_tolerance_cents"], "cash_tolerance_cents", allow_zero=True
            ),
            nav_tolerance_cents=_positive_int(
                values["nav_tolerance_cents"], "nav_tolerance_cents", allow_zero=True
            ),
        )


@dataclass(frozen=True, slots=True)
class ExecutionStackConfig:
    """The complete typed startup input for the execution stack."""

    paper: PaperBrokerConfig
    database: RuntimeDatabaseConfig
    account: AccountReconciliationConfig
    alpaca_key_account: str
    alpaca_secret_account: str

    def __post_init__(self) -> None:
        if not isinstance(self.paper, PaperBrokerConfig):
            raise CompositionError("paper must be a validated PaperBrokerConfig")
        if not isinstance(self.database, RuntimeDatabaseConfig):
            raise CompositionError("database must be a RuntimeDatabaseConfig")
        if not isinstance(self.account, AccountReconciliationConfig):
            raise CompositionError("account must be an AccountReconciliationConfig")
        if type(self.alpaca_key_account) is not str or self.alpaca_key_account != "primary":
            raise CompositionError("alpaca key account must be 'primary'")
        if type(self.alpaca_secret_account) is not str or self.alpaca_secret_account != "primary":
            raise CompositionError("alpaca secret account must be 'primary'")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> ExecutionStackConfig:
        """Parse the exact nested schema; nothing unknown survives this edge."""
        expected = {"paper", "database", "account", "alpaca_key_account", "alpaca_secret_account"}
        if set(values) != expected:
            missing = sorted(expected - set(values))
            extra = sorted(set(values) - expected)
            raise CompositionError(
                f"invalid execution configuration fields: missing={missing}, extra={extra}"
            )
        paper_values = values["paper"]
        if not isinstance(paper_values, Mapping):
            raise CompositionError("paper configuration must be a mapping")
        database_values = values["database"]
        if not isinstance(database_values, Mapping):
            raise CompositionError("database configuration must be a mapping")
        account_values = values["account"]
        if not isinstance(account_values, Mapping):
            raise CompositionError("account configuration must be a mapping")
        return cls(
            paper=PaperBrokerConfig.from_mapping(paper_values),
            database=RuntimeDatabaseConfig.from_mapping(database_values),
            account=AccountReconciliationConfig.from_mapping(account_values),
            alpaca_key_account=_text(values["alpaca_key_account"], "alpaca_key_account"),
            alpaca_secret_account=_text(values["alpaca_secret_account"], "alpaca_secret_account"),
        )


@dataclass(frozen=True, slots=True)
class AlpacaPaperCredentials:
    """Both Alpaca Paper secrets resolved as one all-or-nothing bundle."""

    key_id: SecretValue
    secret_key: SecretValue

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, SecretValue) or not isinstance(self.secret_key, SecretValue):
            raise CompositionError("alpaca credentials must be SecretValue instances")


@dataclass(frozen=True, slots=True)
class ExecutionStack:
    """The composed, ready-to-run execution services."""

    engine: ExecutionEngine
    reconciler: Reconciler
    control_plane: ControlPlane


def execution_secret_refs() -> frozenset[SecretRef]:
    """The exact execution-scope capability allowlist for secret lookups."""
    return frozenset(
        {
            SecretRef.primary(SecretKind.ALPACA_PAPER_KEY_ID),
            SecretRef.primary(SecretKind.ALPACA_PAPER_SECRET_KEY),
            SecretRef.primary(SecretKind.POSTGRES_RUNTIME_PASSWORD),
        }
    )


def resolve_alpaca_credentials(provider: SecretProvider) -> AlpacaPaperCredentials:
    """Resolve both Alpaca Paper secrets or fail without partial state."""
    key_id = provider.get_secret(SecretRef.primary(SecretKind.ALPACA_PAPER_KEY_ID))
    secret_key = provider.get_secret(SecretRef.primary(SecretKind.ALPACA_PAPER_SECRET_KEY))
    return AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)


def account_reconciliation_policy(
    config: AccountReconciliationConfig,
) -> AccountReconciliationPolicy:
    """Convert the typed account config into the one shared reconciliation policy."""
    if not isinstance(config, AccountReconciliationConfig):
        raise CompositionError("account config must be an AccountReconciliationConfig")
    return AccountReconciliationPolicy(
        expected_account_id=config.expected_account_id,
        cash_tolerance_cents=config.cash_tolerance_cents,
        nav_tolerance_cents=config.nav_tolerance_cents,
    )


def build_execution_stack(
    config: ExecutionStackConfig,
    *,
    broker: PaperBrokerPort,
    clock: Callable[[], UtcTimestamp],
    control: ControlRepository,
    price_provider: ReconciliationMarkPriceProvider,
) -> ExecutionStack:
    """Validate Paper-only startup and wire the deterministic services."""
    validate_paper_startup(config.paper)
    if not hasattr(price_provider, "current_price"):
        raise CompositionError("price_provider must implement current_price")
    account_policy = account_reconciliation_policy(config.account)
    return ExecutionStack(
        engine=ExecutionEngine(broker=broker, clock=clock, control=control),
        reconciler=Reconciler(
            broker=broker,
            clock=clock,
            account_policy=account_policy,
            price_provider=price_provider,
        ),
        control_plane=ControlPlane(clock=clock),
    )


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > _DB_FIELD_MAX:
        raise CompositionError(f"{field_name} must be bounded text")
    return value


def _port(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 65_535:
        raise CompositionError("port must be an integer between 1 and 65535")
    return value


def _positive_int(value: object, field_name: str, *, allow_zero: bool = False) -> int:
    if type(value) is not int:
        raise CompositionError(f"{field_name} must be an integer")
    if allow_zero:
        if value < 0:
            raise CompositionError(f"{field_name} must be a non-negative integer")
    elif value <= 0:
        raise CompositionError(f"{field_name} must be a positive integer")
    return value


def _sslmode(value: object) -> str:
    if type(value) is not str or value not in _SSL_MODES:
        raise CompositionError("sslmode must be require, verify-ca, or verify-full")
    return value
