# mypy: ignore-errors
"""Unit coverage for P2-E's typed reconciliation composition; no network or DB."""

from __future__ import annotations

from seven_lens.application.composition import AccountReconciliationConfig
from seven_lens.application.reconciliation_service import AccountReconciliationPolicy
from seven_lens.cli import p2e_readonly_verify as p2e
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.postgres import RuntimeDsn


def test_p2e_record_reconciliation_requires_and_passes_account_policy(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    observed_at = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
    policy = AccountReconciliationPolicy(
        expected_account_id="operator-selected-paper-account",
        cash_tolerance_cents=100,
        nav_tolerance_cents=100,
    )
    captured: dict[str, object] = {}
    sentinel = object()

    class FakeReconciler:
        def __init__(self, **kwargs: object) -> None:
            captured["reconciler_kwargs"] = kwargs

        def run(self, unit_of_work: object, trading_date: object) -> object:
            captured["unit_of_work"] = unit_of_work
            captured["trading_date"] = trading_date
            return sentinel

    class FakeUnitOfWork:
        def __init__(self, conninfo: str) -> None:
            captured["conninfo"] = conninfo

        def __enter__(self) -> FakeUnitOfWork:
            captured["entered"] = True
            return self

        def __exit__(self, *_args: object) -> None:
            captured["exited"] = True

    monkeypatch.setattr(p2e, "Reconciler", FakeReconciler)
    monkeypatch.setattr(p2e, "PostgresUnitOfWork", FakeUnitOfWork)

    result = p2e._record_reconciliation(
        RuntimeDsn("postgresql://runtime:password@localhost/seven_lens"),
        object(),
        observed_at,
        account_policy=policy,
    )

    assert result is sentinel
    kwargs = captured["reconciler_kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["account_policy"] is policy
    assert captured["conninfo"] == "postgresql://runtime:password@localhost/seven_lens"
    assert captured["entered"] is True
    assert captured["exited"] is True


def test_p2e_account_policy_is_constructed_from_typed_account_config() -> None:
    from seven_lens.application.composition import account_reconciliation_policy

    policy = account_reconciliation_policy(
        AccountReconciliationConfig(
            expected_account_id="operator-selected-paper-account",
            cash_tolerance_cents=100,
            nav_tolerance_cents=100,
        )
    )

    assert policy.expected_account_id == "operator-selected-paper-account"
