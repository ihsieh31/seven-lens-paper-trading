"""Paper-only broker endpoint and startup validation tests."""

import pytest

from seven_lens.config.broker import (
    BrokerEnvironment,
    PaperBrokerConfig,
    validate_paper_startup,
)

PAPER_ENDPOINT = "https://paper-api.alpaca.markets"


def test_paper_endpoint_is_valid_and_mapping_round_trip_is_explicit() -> None:
    config = PaperBrokerConfig(
        environment=BrokerEnvironment.PAPER,
        base_url=PAPER_ENDPOINT,
    )

    assert validate_paper_startup(config) is config

    from_mapping = PaperBrokerConfig.from_mapping(
        {"environment": "PAPER", "base_url": PAPER_ENDPOINT}
    )
    assert from_mapping == config


def test_broker_environment_has_no_live_path() -> None:
    assert tuple(BrokerEnvironment) == (BrokerEnvironment.PAPER,)
    assert BrokerEnvironment.PAPER.value == "PAPER"


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "   ",
        "https://api.alpaca.markets",  # Alpaca live endpoint
        "http://paper-api.alpaca.markets",  # wrong transport
        "https://paper-api.alpaca.markets.evil.example",  # look-alike host
        "https://paper-api.alpaca.markets/v2",  # path mutation
        "https://example.invalid",
        None,
    ],
)
def test_startup_rejects_empty_live_unknown_and_mutated_endpoints(base_url: object) -> None:
    with pytest.raises(ValueError):
        config = PaperBrokerConfig(
            environment=BrokerEnvironment.PAPER,
            base_url=base_url,  # type: ignore[arg-type]
        )
        validate_paper_startup(config)


@pytest.mark.parametrize(
    "environment",
    ["live", "LIVE", "production", "paper-live", None, 1],
)
def test_from_mapping_rejects_unknown_or_live_environments(environment: object) -> None:
    with pytest.raises(ValueError):
        PaperBrokerConfig.from_mapping({"environment": environment, "base_url": PAPER_ENDPOINT})


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {"environment": "PAPER"},
        {"base_url": PAPER_ENDPOINT},
        {"environment": "PAPER", "base_url": ""},
        {"environment": "PAPER", "base_url": "https://api.alpaca.markets"},
        {"environment": "PAPER", "base_url": "https://unknown.invalid"},
        {"environment": "PAPER", "base_url": PAPER_ENDPOINT, "unexpected": "field"},
        {"environment": "PAPER", "base_url": PAPER_ENDPOINT, "allow_live": False},
    ],
)
def test_from_mapping_fails_closed_for_missing_or_unsafe_values(mapping: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        config = PaperBrokerConfig.from_mapping(mapping)
        validate_paper_startup(config)


def test_constructor_cannot_select_live_environment_even_with_paper_url() -> None:
    with pytest.raises((ValueError, TypeError)):
        PaperBrokerConfig(
            environment="live",  # type: ignore[arg-type]
            base_url=PAPER_ENDPOINT,
        )
