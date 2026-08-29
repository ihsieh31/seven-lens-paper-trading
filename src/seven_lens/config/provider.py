"""Legacy package-default Agnes route plus the generic provider kind enum.

The active analysis route is built by
:mod:`seven_lens.config.analysis_provider`; the types here remain only as the
historical package default identity and shared enums for claims and audits.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from seven_lens.config.errors import ConfigurationError


class ProviderKind(StrEnum):
    """Provider identities: the historical default and the generic active kind."""

    AGNES = "AGNES"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"


class ApiFlavor(StrEnum):
    """Exact provider wire protocol selected by policy."""

    CHAT_COMPLETIONS = "CHAT_COMPLETIONS"


class ReasoningRequested(StrEnum):
    """Policy request; this does not claim the provider made it effective."""

    MAX = "MAX"


class ReasoningEffective(StrEnum):
    """Pre-live truth state; provider effectiveness has not been observed."""

    UNKNOWN = "UNKNOWN"


class ProviderLogicalRole(StrEnum):
    """Every logical P3-C/P3-D model role covered by the one fixed route."""

    TECHNICAL_ANALYST = "TECHNICAL_ANALYST"
    FUNDAMENTALS_ANALYST = "FUNDAMENTALS_ANALYST"
    NEWS_ANALYST = "NEWS_ANALYST"
    SENTIMENT_ANALYST = "SENTIMENT_ANALYST"
    BULL_RESEARCHER = "BULL_RESEARCHER"
    BEAR_RESEARCHER = "BEAR_RESEARCHER"
    RESEARCH_MANAGER = "RESEARCH_MANAGER"
    TRADER = "TRADER"
    AGGRESSIVE_RISK = "AGGRESSIVE_RISK"
    CONSERVATIVE_RISK = "CONSERVATIVE_RISK"
    NEUTRAL_RISK = "NEUTRAL_RISK"
    PORTFOLIO_MANAGER = "PORTFOLIO_MANAGER"
    PORTFOLIO_MANAGER_RETRY = "PORTFOLIO_MANAGER_RETRY"


_EXPECTED_FIELDS: Final = frozenset(
    {
        "provider_kind",
        "api_flavor",
        "scheme",
        "host",
        "path",
        "model_id",
        "connect_timeout_ms",
        "read_timeout_ms",
        "total_timeout_ms",
        "request_byte_cap",
        "response_byte_cap",
        "max_output_tokens",
        "temperature",
        "reasoning_requested",
        "reasoning_effective",
        "stream",
        "tools",
        "state",
        "files",
        "follow_redirects",
        "trust_env",
        "proxy",
        "automatic_retry",
        "fallback_model_id",
        "fallback_attempts",
        "policy_id",
    }
)
_EXPECTED_VALUES: Final[Mapping[str, object]] = MappingProxyType(
    {
        "provider_kind": "AGNES",
        "api_flavor": "CHAT_COMPLETIONS",
        "scheme": "https",
        "host": "apihub.agnes-ai.com",
        "path": "/v1/chat/completions",
        "model_id": "agnes-2.5-flash",
        "connect_timeout_ms": 2_000,
        "read_timeout_ms": 180_000,
        "total_timeout_ms": 180_000,
        "request_byte_cap": 131_072,
        "response_byte_cap": 131_072,
        "max_output_tokens": 8_192,
        "temperature": 0.0,
        "reasoning_requested": "MAX",
        "reasoning_effective": "UNKNOWN",
        "stream": False,
        "tools": False,
        "state": False,
        "files": False,
        "follow_redirects": False,
        "trust_env": False,
        "proxy": False,
        "automatic_retry": False,
        "fallback_model_id": None,
        "fallback_attempts": 0,
        "policy_id": "p3e-agnes-2.5-flash-only-v1",
    }
)


@dataclass(frozen=True, slots=True)
class AgnesProviderConfig:
    """Frozen exact route selected by the user for all P3-E logical roles."""

    provider_kind: ProviderKind
    api_flavor: ApiFlavor
    scheme: str
    host: str
    path: str
    model_id: str
    connect_timeout_ms: int
    read_timeout_ms: int
    total_timeout_ms: int
    request_byte_cap: int
    response_byte_cap: int
    max_output_tokens: int
    temperature: float
    reasoning_requested: ReasoningRequested
    reasoning_effective: ReasoningEffective
    stream: bool
    tools: bool
    state: bool
    files: bool
    follow_redirects: bool
    trust_env: bool
    proxy: bool
    automatic_retry: bool
    fallback_model_id: None
    fallback_attempts: int
    policy_id: str

    def __post_init__(self) -> None:
        actual: dict[str, object] = {
            "provider_kind": (
                self.provider_kind.value
                if type(self.provider_kind) is ProviderKind
                else self.provider_kind
            ),
            "api_flavor": (
                self.api_flavor.value if type(self.api_flavor) is ApiFlavor else self.api_flavor
            ),
            "scheme": self.scheme,
            "host": self.host,
            "path": self.path,
            "model_id": self.model_id,
            "connect_timeout_ms": self.connect_timeout_ms,
            "read_timeout_ms": self.read_timeout_ms,
            "total_timeout_ms": self.total_timeout_ms,
            "request_byte_cap": self.request_byte_cap,
            "response_byte_cap": self.response_byte_cap,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "reasoning_requested": (
                self.reasoning_requested.value
                if type(self.reasoning_requested) is ReasoningRequested
                else self.reasoning_requested
            ),
            "reasoning_effective": (
                self.reasoning_effective.value
                if type(self.reasoning_effective) is ReasoningEffective
                else self.reasoning_effective
            ),
            "stream": self.stream,
            "tools": self.tools,
            "state": self.state,
            "files": self.files,
            "follow_redirects": self.follow_redirects,
            "trust_env": self.trust_env,
            "proxy": self.proxy,
            "automatic_retry": self.automatic_retry,
            "fallback_model_id": self.fallback_model_id,
            "fallback_attempts": self.fallback_attempts,
            "policy_id": self.policy_id,
        }
        if any(
            type(actual[name]) is not type(expected) or actual[name] != expected
            for name, expected in _EXPECTED_VALUES.items()
        ):
            raise ConfigurationError("provider configuration does not match approved policy")

    @property
    def endpoint(self) -> str:
        return f"{self.scheme}://{self.host}{self.path}"

    @property
    def roles(self) -> tuple[ProviderLogicalRole, ...]:
        return tuple(ProviderLogicalRole)


def agnes_25_flash_config(values: Mapping[str, object] | None = None) -> AgnesProviderConfig:
    """Bind an exact schema or construct the package-owned approved route."""

    supplied = dict(_EXPECTED_VALUES) if values is None else dict(values)
    if set(supplied) != _EXPECTED_FIELDS:
        missing = sorted(_EXPECTED_FIELDS - set(supplied))
        extra = sorted(set(supplied) - _EXPECTED_FIELDS)
        raise ConfigurationError(
            f"invalid provider configuration fields: missing={missing}, extra={extra}"
        )
    return AgnesProviderConfig(
        provider_kind=_exact_enum(supplied["provider_kind"], ProviderKind),
        api_flavor=_exact_enum(supplied["api_flavor"], ApiFlavor),
        scheme=supplied["scheme"],  # type: ignore[arg-type]
        host=supplied["host"],  # type: ignore[arg-type]
        path=supplied["path"],  # type: ignore[arg-type]
        model_id=supplied["model_id"],  # type: ignore[arg-type]
        connect_timeout_ms=supplied["connect_timeout_ms"],  # type: ignore[arg-type]
        read_timeout_ms=supplied["read_timeout_ms"],  # type: ignore[arg-type]
        total_timeout_ms=supplied["total_timeout_ms"],  # type: ignore[arg-type]
        request_byte_cap=supplied["request_byte_cap"],  # type: ignore[arg-type]
        response_byte_cap=supplied["response_byte_cap"],  # type: ignore[arg-type]
        max_output_tokens=supplied["max_output_tokens"],  # type: ignore[arg-type]
        temperature=supplied["temperature"],  # type: ignore[arg-type]
        reasoning_requested=_exact_enum(supplied["reasoning_requested"], ReasoningRequested),
        reasoning_effective=_exact_enum(supplied["reasoning_effective"], ReasoningEffective),
        stream=supplied["stream"],  # type: ignore[arg-type]
        tools=supplied["tools"],  # type: ignore[arg-type]
        state=supplied["state"],  # type: ignore[arg-type]
        files=supplied["files"],  # type: ignore[arg-type]
        follow_redirects=supplied["follow_redirects"],  # type: ignore[arg-type]
        trust_env=supplied["trust_env"],  # type: ignore[arg-type]
        proxy=supplied["proxy"],  # type: ignore[arg-type]
        automatic_retry=supplied["automatic_retry"],  # type: ignore[arg-type]
        fallback_model_id=supplied["fallback_model_id"],  # type: ignore[arg-type]
        fallback_attempts=supplied["fallback_attempts"],  # type: ignore[arg-type]
        policy_id=supplied["policy_id"],  # type: ignore[arg-type]
    )


def _exact_enum[EnumT: StrEnum](value: object, enum_type: type[EnumT]) -> EnumT:
    if type(value) is not str:
        raise ConfigurationError("provider configuration does not match approved policy")
    try:
        return enum_type(value)
    except ValueError:
        raise ConfigurationError("provider configuration does not match approved policy") from None
