"""Unit tests for the typed composition root and its secret boundaries."""

# mypy: ignore-errors

from __future__ import annotations

import pytest

from fakes.control import FakeControlRepository
from fakes.secrets import FakeSecretProvider
from seven_lens.application.composition import (
    CompositionError,
    ExecutionStackConfig,
    RuntimeDatabaseConfig,
    build_execution_stack,
    execution_secret_refs,
    resolve_alpaca_credentials,
)
from seven_lens.application.control_service import ControlPlane
from seven_lens.application.execution_service import ExecutionEngine
from seven_lens.application.ports.secrets import SecretProviderError
from seven_lens.application.reconciliation_service import Reconciler
from seven_lens.config.errors import ConfigurationError
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.postgres import compose_runtime_dsn
from seven_lens.security.secret_values import SecretKind, SecretRef, SecretValue

_BASE_TIME_TEXT = "2026-08-17T13:35:00.000000Z"


class _FixedClock:
    def __call__(self) -> object:
        from seven_lens.domain.value_objects import UtcTimestamp

        return UtcTimestamp.from_isoformat(_BASE_TIME_TEXT)


def _database_mapping() -> dict[str, object]:
    return {
        "host": "localhost",
        "port": 5432,
        "dbname": "seven_lens",
        "user": "seven_lens_runtime",
        "sslmode": "require",
        "password_account": "primary",
    }


def _account_mapping() -> dict[str, object]:
    return {
        "expected_account_id": "fake-paper-primary",
        "cash_tolerance_cents": 100,
        "nav_tolerance_cents": 100,
    }


def _stack_mapping() -> dict[str, object]:
    return {
        "paper": {"environment": "PAPER", "base_url": "https://paper-api.alpaca.markets"},
        "database": _database_mapping(),
        "account": _account_mapping(),
        "alpaca_key_account": "primary",
        "alpaca_secret_account": "primary",
    }


class _DummyPriceProvider:
    def current_price(self, symbol):  # type: ignore[no-untyped-def]
        from seven_lens.execution.orders import Price

        return Price.from_cents(10_000)


def _provider() -> FakeSecretProvider:
    return FakeSecretProvider(
        values={
            ref: SecretValue(f"fake-{ref.kind.value}".encode()) for ref in execution_secret_refs()
        }
    )


class TestExecutionStackConfig:
    def test_exact_schema_parses_into_typed_values(self) -> None:
        config = ExecutionStackConfig.from_mapping(_stack_mapping())
        assert config.paper.base_url == "https://paper-api.alpaca.markets"
        assert config.database.host == "localhost"
        assert config.database.password_ref.kind is SecretKind.POSTGRES_RUNTIME_PASSWORD

    @pytest.mark.parametrize(
        "mutation",
        [
            {"extra": True},
            {"paper": {"environment": "PAPER"}},
            {"database": {"host": "localhost"}},
        ],
    )
    def test_unknown_or_missing_fields_fail_closed(self, mutation: dict[str, object]) -> None:
        values = _stack_mapping()
        values.update(mutation)
        if set(mutation) & {"paper", "database"} and len(mutation) == 1:
            key = next(iter(mutation))
            values[key] = mutation[key]
            if key == "database":
                values["database"] = {"host": "localhost"}
        with pytest.raises((CompositionError, ConfigurationError)):
            ExecutionStackConfig.from_mapping(values)

    def test_live_or_unknown_broker_endpoints_are_rejected(self) -> None:
        values = _stack_mapping()
        assert isinstance(values["paper"], dict)
        values["paper"] = {"environment": "PAPER", "base_url": "https://api.alpaca.markets"}
        with pytest.raises(ConfigurationError):
            ExecutionStackConfig.from_mapping(values)


class TestRuntimeDsn:
    def test_dsn_composition_redacts_and_reveals_exactly_once(self) -> None:
        database = RuntimeDatabaseConfig.from_mapping(_database_mapping())
        provider = _provider()

        dsn = compose_runtime_dsn(database, provider)

        assert "fake-POSTGRES_RUNTIME_PASSWORD" in dsn.conninfo()
        assert str(dsn) == "postgresql://<redacted>"
        assert repr(dsn) == "RuntimeDsn(<redacted>)"

    def test_dsn_requires_the_scoped_password_secret(self) -> None:
        database = RuntimeDatabaseConfig.from_mapping(_database_mapping())
        password_ref = database.password_ref
        provider = FakeSecretProvider(failures={password_ref: SecretProviderError("missing")})
        with pytest.raises(SecretProviderError):
            compose_runtime_dsn(database, provider)

    def test_foreign_password_reference_is_rejected(self) -> None:
        with pytest.raises(CompositionError, match="postgres runtime reference"):
            RuntimeDatabaseConfig(
                host="localhost",
                port=5432,
                dbname="seven_lens",
                user="seven_lens_runtime",
                sslmode="require",
                password_ref=SecretRef.primary(SecretKind.OPENAI_API_KEY),
            )


class TestStackBuilding:
    def test_stack_wires_engine_reconciler_and_control_plane(self) -> None:
        from seven_lens.execution.fake_broker import FakePaperBroker

        config = ExecutionStackConfig.from_mapping(_stack_mapping())
        broker = FakePaperBroker(clock=_FixedClock())  # type: ignore[arg-type]
        control = FakeControlRepository(UtcTimestamp.from_isoformat(_BASE_TIME_TEXT))
        stack = build_execution_stack(
            config,
            broker=broker,
            clock=_FixedClock(),  # type: ignore[arg-type]
            control=control,
            price_provider=_DummyPriceProvider(),
        )

        assert isinstance(stack.engine, ExecutionEngine)
        assert isinstance(stack.reconciler, Reconciler)
        assert isinstance(stack.control_plane, ControlPlane)
        # Account policy must be wired from config, not silently None
        assert stack.reconciler._account_policy is not None  # type: ignore[attr-defined]
        assert stack.reconciler._account_policy.expected_account_id == "fake-paper-primary"  # type: ignore[attr-defined]
        assert stack.reconciler._price_provider is not None  # type: ignore[attr-defined]

    def test_stack_requires_account_config(self) -> None:

        values = _stack_mapping()
        del values["account"]
        with pytest.raises(CompositionError):
            ExecutionStackConfig.from_mapping(values)

    def test_stack_wired_reconciler_detects_wrong_account(self) -> None:
        from seven_lens.execution.fake_broker import FakePaperBroker
        from seven_lens.execution.orders import UsdAmount

        config = ExecutionStackConfig.from_mapping(_stack_mapping())
        # Broker reports a different account id than expected
        broker = FakePaperBroker(
            clock=_FixedClock(),  # type: ignore[arg-type]
            account_id="other-id",
            cash=UsdAmount.from_cents(10_000_000),
            equity=UsdAmount.from_cents(10_000_000),
            buying_power=UsdAmount.from_cents(5_000_000),
        )
        control = FakeControlRepository(UtcTimestamp.from_isoformat(_BASE_TIME_TEXT))
        stack = build_execution_stack(
            config,
            broker=broker,
            clock=_FixedClock(),  # type: ignore[arg-type]
            control=control,
            price_provider=_DummyPriceProvider(),
        )
        # Need a baseline so cash check can run; create via direct DB or fake
        # Use the stack's reconciler directly with a fake UoW
        from fakes.control import FakeReconciliationRepository
        from fakes.orders import FakeOrderRepository
        from seven_lens.domain.value_objects import UtcTimestamp as UT
        from seven_lens.infrastructure.postgres import AccountBaseline

        baseline = AccountBaseline(
            account_id="other-id",
            opening_cash_cents=10_000_000,
            effective_at=UT.from_isoformat(_BASE_TIME_TEXT),
            created_at=UT.from_isoformat(_BASE_TIME_TEXT),
            updated_at=UT.from_isoformat(_BASE_TIME_TEXT),
        )

        class _BaselineRepo:
            def get_baseline(self, account_id: str):  # type: ignore[no-untyped-def]
                return baseline if account_id == "other-id" else None

        class _UoW:  # type: ignore[no-untyped-def]
            def __init__(self):
                self.orders = FakeOrderRepository()
                self.reconciliations = FakeReconciliationRepository()
                self.control = control
                self.account_baselines = _BaselineRepo()

            def commit(self):
                pass

        from seven_lens.domain.value_objects import TradingDate

        result = stack.reconciler.run(_UoW(), TradingDate.from_isoformat("2026-08-17"))
        from seven_lens.execution.reconciliation import MismatchKind

        assert any(m.kind == MismatchKind.ACCOUNT_ID_MISMATCH for m in result.mismatches)

    def test_execution_secret_allowlist_is_exact(self) -> None:
        refs = execution_secret_refs()
        assert len(refs) == 3
        kinds = {ref.kind for ref in refs}
        assert kinds == {
            SecretKind.ALPACA_PAPER_KEY_ID,
            SecretKind.ALPACA_PAPER_SECRET_KEY,
            SecretKind.POSTGRES_RUNTIME_PASSWORD,
        }

    def test_alpaca_credentials_resolve_all_or_nothing(self) -> None:
        credentials = resolve_alpaca_credentials(_provider())
        assert isinstance(credentials.key_id, SecretValue)
        assert isinstance(credentials.secret_key, SecretValue)
        key_ref = SecretRef.primary(SecretKind.ALPACA_PAPER_KEY_ID)
        failing = FakeSecretProvider(failures={key_ref: SecretProviderError("denied")})
        with pytest.raises(SecretProviderError):
            resolve_alpaca_credentials(failing)
