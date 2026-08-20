# mypy: ignore-errors
"""Source-level guardrails that keep the first release Paper-only."""

import re
from pathlib import Path

from seven_lens.config.broker import BrokerEnvironment

SRC_ROOT = Path(__file__).parents[1] / "src" / "seven_lens"


def _source_files() -> list[Path]:
    return sorted(path for path in SRC_ROOT.rglob("*.py") if path.is_file())


def test_source_contains_no_alpaca_live_endpoint_or_live_adapter_module() -> None:
    source_files = _source_files()

    assert source_files
    assert not any(
        re.search(r"(?:^|[_-])live(?:[_-]|$)", path.stem, re.IGNORECASE) for path in source_files
    )
    combined_source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    assert "https://api.alpaca.markets" not in combined_source
    assert "alpaca_live" not in combined_source.lower()
    assert "live_adapter" not in combined_source.lower()


def test_source_has_no_live_switch_fields_or_production_endpoint_fields() -> None:
    forbidden_switch_fields = (
        "allow_live",
        "enable_live",
        "live_enabled",
        "use_live",
        "live_endpoint",
        "production_endpoint",
        "production_adapter",
    )
    combined_source = "\n".join(path.read_text(encoding="utf-8") for path in _source_files())
    lowered = combined_source.lower()

    assert all(field not in lowered for field in forbidden_switch_fields)


def test_broker_environment_is_exhaustively_paper() -> None:
    assert list(BrokerEnvironment) == [BrokerEnvironment.PAPER]
