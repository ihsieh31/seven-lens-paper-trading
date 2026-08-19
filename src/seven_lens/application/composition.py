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
from seven_lens.application.reconciliation_service import Reconciler
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
                or "/" in value
                or "@" in value
                or ":" in value
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
class ExecutionStackConfig:
    """The complete typed startup input for the execution stack."""

    paper: PaperBrokerConfig
    database: RuntimeDatabaseConfig
    alpaca_key_account: str
    alpaca_secret_account: str

    def __post_init__(self) -> None:
        if not isinstance(self.paper, PaperBrokerConfig):
            raise CompositionError("paper must be a validated PaperBrokerConfig")
        if not isinstance(self.database, RuntimeDatabaseConfig):
            raise CompositionError("database must be a RuntimeDatabaseConfig")
        if type(self.alpaca_key_account) is not str or self.alpaca_key_account != "primary":
            raise CompositionError("alpaca key account must be 'primary'")
        if type(self.alpaca_secret_account) is not str or self.alpaca_secret_account != "primary":
            raise CompositionError("alpaca secret account must be 'primary'")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> ExecutionStackConfig:
        """Parse the exact nested schema; nothing unknown survives this edge."""
        expected = {"paper", "database", "alpaca_key_account", "alpaca_secret_account"}
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
        return cls(
            paper=PaperBrokerConfig.from_mapping(paper_values),
            database=RuntimeDatabaseConfig.from_mapping(database_values),
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


def build_execution_stack(
    config: ExecutionStackConfig,
    *,
    broker: PaperBrokerPort,
    clock: Callable[[], UtcTimestamp],
    control: ControlRepository,
) -> ExecutionStack:
    """Validate Paper-only startup and wire the deterministic services."""
    validate_paper_startup(config.paper)
    return ExecutionStack(
        engine=ExecutionEngine(broker=broker, clock=clock, control=control),
        reconciler=Reconciler(broker=broker, clock=clock),
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


def _sslmode(value: object) -> str:
    if type(value) is not str or value not in _SSL_MODES:
        raise CompositionError("sslmode must be require, verify-ca, or verify-full")
    return value
