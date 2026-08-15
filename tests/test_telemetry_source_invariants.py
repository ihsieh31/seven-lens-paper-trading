"""Source guards for the dependency-neutral, closed P1-C2 telemetry boundary."""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).parents[1] / "src" / "seven_lens"
APPLICATION_AND_DOMAIN = (
    *(SRC_ROOT / "application").rglob("*.py"),
    *(SRC_ROOT / "domain").rglob("*.py"),
)
TELEMETRY_MODULES = (
    SRC_ROOT / "application" / "ports" / "telemetry.py",
    SRC_ROOT / "observability" / "context.py",
    SRC_ROOT / "observability" / "instruments.py",
    SRC_ROOT / "observability" / "failsafe.py",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_application_and_domain_import_no_backend_sdk_network_logging_or_postgres() -> None:
    forbidden_roots = {
        "logging",
        "opentelemetry",
        "prometheus_client",
        "psycopg",
        "requests",
        "sentry_sdk",
        "sqlalchemy",
        "urllib",
    }
    for path in APPLICATION_AND_DOMAIN:
        roots = {name.split(".")[0] for name in _imports(_tree(path))}
        assert roots.isdisjoint(forbidden_roots), path


def test_telemetry_modules_import_no_exporter_backend_sdk_or_network_client() -> None:
    forbidden_roots = {
        "httpx",
        "opentelemetry",
        "prometheus_client",
        "psycopg",
        "requests",
        "sentry_sdk",
        "sqlalchemy",
        "urllib",
    }
    for path in TELEMETRY_MODULES:
        roots = {name.split(".")[0] for name in _imports(_tree(path))}
        assert roots.isdisjoint(forbidden_roots), path


def test_native_keychain_bridge_has_no_telemetry_import() -> None:
    path = SRC_ROOT / "infrastructure" / "macos_keychain.py"
    assert all("telemetry" not in name for name in _imports(_tree(path)))


def test_recorder_ports_expose_no_arbitrary_name_or_attribute_methods() -> None:
    tree = _tree(SRC_ROOT / "application" / "ports" / "telemetry.py")
    classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in {"MetricRecorder", "TraceRecorder"}
    }
    assert [
        node.name for node in classes["MetricRecorder"].body if isinstance(node, ast.FunctionDef)
    ] == ["record"]
    assert [
        node.name for node in classes["TraceRecorder"].body if isinstance(node, ast.FunctionDef)
    ] == ["start_span", "end_span"]
