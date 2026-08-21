from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
ANALYSIS = ROOT / "src" / "seven_lens" / "analysis"

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "alpaca",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
        "psycopg",
        "pydantic",
        "langgraph",
        "openai",
        "tavily",
    }
)
FORBIDDEN_IMPORT_TARGETS = frozenset({"seven_lens.execution", "seven_lens.infrastructure"})
FORBIDDEN_TEXT_MARKERS = (
    "paper-api.alpaca.markets",
    "api.alpaca.markets",
    "OrderIntent",
    "seven_lens.execution",
    "seven_lens.infrastructure",
)


def collect_import_targets(tree: ast.Module) -> set[str]:
    """Resolve every import to its concrete target, including relative and alias forms."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            prefix = "seven_lens." if node.level > 0 else ""
            if node.module:
                root = f"{prefix}{node.module}"
                imported.add(root)
                imported.add(root.split(".")[0])
                for alias in node.names:
                    imported.add(f"{root}.{alias.name}")
            else:
                imported.update(f"{prefix}{alias.name}" for alias in node.names)
    return imported


def test_analysis_source_has_no_execution_broker_provider_network_or_secret_capability() -> None:
    for path in ANALYSIS.rglob("*.py"):
        source = path.read_text()
        tree = ast.parse(source)
        targets = collect_import_targets(tree)
        assert not (targets & FORBIDDEN_IMPORT_ROOTS), path
        assert not (targets & FORBIDDEN_IMPORT_TARGETS), path
        lowered = source.lower()
        for marker in FORBIDDEN_TEXT_MARKERS:
            assert marker.lower() not in lowered, (path, marker)


def test_import_scan_catches_relative_and_alias_execution_imports() -> None:
    evasive_snippets = (
        "from .execution import helper\n",
        "from seven_lens import execution\n",
        "from . import infrastructure\n",
        "import seven_lens.infrastructure as infra\n",
    )
    for snippet in evasive_snippets:
        targets = collect_import_targets(ast.parse(snippet))
        assert targets & FORBIDDEN_IMPORT_TARGETS, snippet


def test_upstream_manifest_is_exact_inventory_not_runtime_code() -> None:
    manifest_path = ROOT / "third_party" / "tradingagents" / "SOURCE_MANIFEST.json"
    raw = json.loads(manifest_path.read_text())
    assert raw["commit"] == "a33fd4c0f134485a43553a2c23a63cb14adbd88f"
    assert raw["license"]["id"] == "Apache-2.0"
    assert (
        raw["license"]["sha256"]
        == "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
    )
    assert raw["notice_present"] is False
    assert raw["runtime_code_vendored"] is False
    assert len(raw["planned_source_paths"]) == len(set(raw["planned_source_paths"]))
    assert not list((ROOT / "third_party" / "tradingagents").rglob("*.py"))


def test_upstream_license_copy_has_exact_hash() -> None:
    license_bytes = (ROOT / "third_party" / "tradingagents" / "LICENSE").read_bytes()
    assert (
        hashlib.sha256(license_bytes).hexdigest()
        == "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
    )
