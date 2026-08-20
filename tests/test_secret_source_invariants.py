# mypy: ignore-errors
"""Scoped source guards for the P1-C1 no-fallback/read-only secret boundary."""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).parents[1] / "src" / "seven_lens"
SECRET_MODULES = (
    SRC_ROOT / "security" / "secret_values.py",
    SRC_ROOT / "application" / "ports" / "secrets.py",
    SRC_ROOT / "application" / "secret_service.py",
    SRC_ROOT / "infrastructure" / "macos_keychain.py",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".")[0])
    return roots


def test_secret_modules_do_not_read_environment_argv_database_or_subprocess() -> None:
    forbidden_imports = {"argparse", "os", "subprocess"}
    forbidden_attributes = {"argv", "environ", "getenv"}
    for path in SECRET_MODULES:
        tree = _tree(path)
        assert _import_roots(tree).isdisjoint(forbidden_imports), path
        assert not any(
            isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
            for node in ast.walk(tree)
        ), path
        assert not any(
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and "persistence" in node.module.split(".")
            for node in ast.walk(tree)
        ), path


def test_native_adapter_contains_no_write_export_or_security_cli_calls() -> None:
    path = SRC_ROOT / "infrastructure" / "macos_keychain.py"
    tree = _tree(path)
    forbidden_native_names = {
        "SecItemAdd",
        "SecItemUpdate",
        "SecItemDelete",
        "SecKeychainItemExport",
    }
    referenced_names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }

    assert referenced_names.isdisjoint(forbidden_native_names)
    assert "/usr/bin/security" not in path.read_text(encoding="utf-8")


def test_secret_provider_protocol_exposes_only_exact_lookup() -> None:
    tree = _tree(SRC_ROOT / "application" / "ports" / "secrets.py")
    provider = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SecretProvider"
    )
    methods = [node.name for node in provider.body if isinstance(node, ast.FunctionDef)]

    assert methods == ["get_secret"]


def test_production_secret_modules_do_not_import_test_fakes_or_logging_serializers() -> None:
    for path in SECRET_MODULES:
        tree = _tree(path)
        imports = _import_roots(tree)
        assert "tests" not in imports, path
        assert "logging" not in imports, path
        assert "json" not in imports, path


def test_env_example_contains_only_non_secret_configuration_names() -> None:
    env_example = Path(__file__).parents[1] / ".env.example"
    names = {
        line.split("=", 1)[0]
        for line in env_example.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }

    assert names == {
        "ALPACA_PAPER_API_BASE_URL",
        "TAVILY_COMPLIANCE_MODE",
        "TAVILY_ACCOUNT_ID",
    }
