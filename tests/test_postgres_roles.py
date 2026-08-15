"""Unit contracts for PostgreSQL runtime-role provisioning inputs."""

from __future__ import annotations

import pytest

from seven_lens.infrastructure.postgres_roles import (
    PostgresRoleError,
    provision_runtime_role,
    verify_runtime_role,
)


@pytest.mark.parametrize("dsn", ["", "   ", None, 7])
def test_runtime_role_operations_reject_invalid_owner_dsn_before_connect(dsn: object) -> None:
    with pytest.raises(PostgresRoleError, match="DSN"):
        provision_runtime_role(dsn, "seven_lens_runtime")  # type: ignore[arg-type]
    with pytest.raises(PostgresRoleError, match="DSN"):
        verify_runtime_role(dsn, "seven_lens_runtime")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "role",
    ["", "SevenLens", "7runtime", "runtime-role", "runtime role", "x" * 64],
)
def test_runtime_role_operations_reject_unbounded_or_unsafe_role_names(role: str) -> None:
    with pytest.raises(PostgresRoleError, match="role format"):
        provision_runtime_role("postgresql://127.0.0.1/example", role)
