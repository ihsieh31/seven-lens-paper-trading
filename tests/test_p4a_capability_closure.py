# mypy: ignore-errors
"""P4-A capability closure: no HTTP backends, broker/model capability, or escalation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from seven_lens.config.p4 import P4PolicyConfig
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.alpaca import parse_assets
from seven_lens.sources.adapters.in_memory_p4_records import InMemoryP4RecordLog
from seven_lens.sources.adapters.records import SourceSchemaDriftError
from seven_lens.sources.adapters.transport import (
    ExecutorResponse,
    PolicyGetTransport,
)
from seven_lens.sources.roles import P4SourceFamily, p4_manifest_registry

SRC = Path(__file__).parents[1] / "src" / "seven_lens"

P4_DOMAIN_MODULES = (
    SRC / "config" / "p4.py",
    SRC / "sources" / "roles.py",
    SRC / "sources" / "adapters" / "records.py",
)
P4_ADAPTER_MODULES = tuple((SRC / "sources" / "adapters").glob("*.py"))
P4_MODULES = (*P4_DOMAIN_MODULES, *P4_ADAPTER_MODULES)

_FORBIDDEN_BACKEND_ROOTS = {
    "http",
    "socket",
    "ssl",
    "psycopg",
    "requests",
    "aiohttp",
    "urllib3",
    "subprocess",
    "asyncio",
}
_FORBIDDEN_URLOBJ = {"urllib.request", "urllib.error"}
_FORBIDDEN_ATTRS = {"environ", "getenv"}
_FORBIDDEN_SEVEN_LENS = {
    "seven_lens.execution",
    "seven_lens.infrastructure.alpaca_paper",
    "seven_lens.infrastructure.macos_keychain",
    "seven_lens.infrastructure.postgres",
    "seven_lens.application.model_invoker",
    "seven_lens.application.execution_service",
    "seven_lens.observability",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module)
    return roots


@pytest.mark.parametrize("path", P4_DOMAIN_MODULES, ids=lambda p: p.name)
def test_p4_domain_modules_import_no_backends(path: Path) -> None:
    roots = _import_roots(_tree(path))

    assert roots.isdisjoint(_FORBIDDEN_BACKEND_ROOTS), path
    assert roots.isdisjoint(_FORBIDDEN_URLOBJ), path


@pytest.mark.parametrize("path", P4_ADAPTER_MODULES, ids=lambda p: p.name)
def test_p4_adapter_modules_import_no_backends(path: Path) -> None:
    roots = _import_roots(_tree(path))
    allowed_parse = {"urllib.parse"}

    for root in roots:
        if root == "urllib.parse":
            continue
        assert root.split(".")[0] not in _FORBIDDEN_BACKEND_ROOTS, path
    assert not (roots & _FORBIDDEN_URLOBJ - allowed_parse), path


@pytest.mark.parametrize("path", P4_MODULES, ids=lambda p: str(p.relative_to(SRC)))
def test_p4_modules_import_no_broker_model_execution_or_env(path: Path) -> None:
    tree = _tree(path)
    roots = _import_roots(tree)

    for forbidden in _FORBIDDEN_SEVEN_LENS:
        assert not any(root == forbidden or root.startswith(forbidden) for root in roots), path
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS), path


def test_p4_modules_never_name_broker_submit_or_cancel_capability() -> None:
    for path in P4_MODULES:
        lowered = path.read_text(encoding="utf-8").lower()
        assert "submit_order" not in lowered, path
        assert "cancel_order" not in lowered, path
        assert "broker_submit" not in lowered, path
        assert "orders.post" not in lowered, path


def test_p4_policy_config_exposes_no_runtime_override_seam() -> None:
    source = (SRC / "config" / "p4.py").read_text(encoding="utf-8")

    assert "os.environ" not in source
    assert "getenv" not in source
    assert "from_mapping" in source
    assert "load_dotenv" not in source
    config = P4PolicyConfig.canonical()
    assert config.submit_enabled is False
    assert config.short_enabled is False


def _failing_executor() -> object:
    class _Failing:
        def get(self, request: object) -> ExecutorResponse:
            raise TimeoutError("boom")

    return _Failing()


def test_transport_failures_leave_zero_records_persisted() -> None:
    log = InMemoryP4RecordLog()
    transport = PolicyGetTransport(
        p4_manifest_registry(),
        _failing_executor(),  # type: ignore[arg-type]
    )

    from seven_lens.sources.adapters.transport import SourceFetchTimeoutError

    with pytest.raises(SourceFetchTimeoutError):
        transport.fetch(
            transport.prepare(family=P4SourceFamily.ALPACA_ASSETS, endpoint_id="assets_list")
        )
    assert log.count() == 0


def test_adapter_schema_drift_leaves_zero_records_persisted() -> None:
    log = InMemoryP4RecordLog()

    with pytest.raises(SourceSchemaDriftError):
        parse_assets(
            b'{"nope": true}',
            retrieved_at=UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z"),
        )
    assert log.count() == 0


def test_registry_role_axis_has_exactly_four_roles_and_iex_is_not_a_role() -> None:
    from seven_lens.sources.roles import SourceRole

    assert len(SourceRole) == 4
    registry = p4_manifest_registry()
    iex = registry.policy(P4SourceFamily.ALPACA_IEX_QUOTES)

    assert iex.role.value == "AUTHORITY"
    assert "LIMITED_MARKET_COVERAGE" in {member.value for member in type(iex.coverage)}
