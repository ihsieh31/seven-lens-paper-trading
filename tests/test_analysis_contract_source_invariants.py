from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
ANALYSIS = ROOT / "src" / "seven_lens" / "analysis"


def test_analysis_source_has_no_execution_broker_provider_network_or_secret_capability() -> None:
    forbidden_import_roots = {
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
    forbidden_text = (
        "paper-api.alpaca.markets",
        "api.alpaca.markets",
        "OrderIntent",
        "seven_lens.execution",
        "seven_lens.infrastructure",
    )
    for path in ANALYSIS.rglob("*.py"):
        source = path.read_text()
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not (imported & forbidden_import_roots), path
        lowered = source.lower()
        for marker in forbidden_text:
            assert marker.lower() not in lowered, (path, marker)


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
