"""Paper-only broker configuration and startup validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from seven_lens.config.errors import ConfigurationError

PAPER_API_BASE_URL: Final = "https://paper-api.alpaca.markets"
PAPER_ENDPOINT_ALLOWLIST: Final[frozenset[str]] = frozenset({PAPER_API_BASE_URL})


class BrokerEnvironment(StrEnum):
    """The only broker environment represented by the application domain."""

    PAPER = "PAPER"


@dataclass(frozen=True, slots=True)
class PaperBrokerConfig:
    """Broker startup inputs with no live-trading switch or fallback."""

    environment: BrokerEnvironment
    base_url: str

    def __post_init__(self) -> None:
        if self.environment is not BrokerEnvironment.PAPER:
            raise ConfigurationError("broker environment must be PAPER")
        _validate_paper_endpoint(self.base_url)

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> PaperBrokerConfig:
        """Parse an exact configuration schema, rejecting missing or extra fields."""
        expected_fields = {"environment", "base_url"}
        actual_fields = set(values)
        if actual_fields != expected_fields:
            missing = sorted(expected_fields - actual_fields)
            extra = sorted(actual_fields - expected_fields)
            raise ConfigurationError(
                f"invalid broker configuration fields: missing={missing}, extra={extra}"
            )

        raw_environment = values["environment"]
        if not isinstance(raw_environment, str):
            raise ConfigurationError("broker environment must be PAPER")
        try:
            environment = BrokerEnvironment(raw_environment)
        except ValueError as error:
            raise ConfigurationError("broker environment must be PAPER") from error

        raw_base_url = values["base_url"]
        if not isinstance(raw_base_url, str):
            raise ConfigurationError("broker base URL must be a string")
        return cls(environment=environment, base_url=raw_base_url)


def _validate_paper_endpoint(base_url: object) -> None:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ConfigurationError("broker base URL must not be empty")
    if base_url not in PAPER_ENDPOINT_ALLOWLIST:
        raise ConfigurationError("broker base URL is not in the Paper endpoint allowlist")


def validate_paper_startup(config: PaperBrokerConfig) -> PaperBrokerConfig:
    """Reassert Paper-only invariants immediately before process startup."""
    if not isinstance(config, PaperBrokerConfig):
        raise ConfigurationError("startup requires a validated PaperBrokerConfig")
    if config.environment is not BrokerEnvironment.PAPER:
        raise ConfigurationError("broker environment must be PAPER")
    _validate_paper_endpoint(config.base_url)
    return config
