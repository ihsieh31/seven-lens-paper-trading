"""Config-driven generic analysis provider composition.

The stack is composed exactly once per process from one immutable
:class:`AnalysisProviderConfig` snapshot, the one generic analysis-provider
Keychain reference, and the supplied typed audit/executor/clock capabilities.
There is no second route, no per-caller endpoint or model override, and no
fallback secret source.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from seven_lens.application.model_invoker import AuditedModelInvoker
from seven_lens.application.ports.model_audit import ModelCallAuditPort
from seven_lens.application.ports.secrets import SecretProvider
from seven_lens.application.secret_service import ScopedSecretProvider
from seven_lens.config.analysis_provider import AnalysisProviderConfig
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.analysis_providers import (
    ConfiguredAnalysisProvider,
    ConfiguredProposalProvider,
)
from seven_lens.infrastructure.chat_completions_transport import (
    ChatCompletionsHttpExecutor,
    ChatCompletionsModelTransport,
    StdlibChatCompletionsHttpExecutor,
)
from seven_lens.security.secret_values import SecretKind, SecretRef


@dataclass(frozen=True, slots=True)
class AnalysisProviderStack:
    """Public typed providers plus the exact immutable route snapshot in use."""

    analysis_provider: ConfiguredAnalysisProvider
    proposal_provider: ConfiguredProposalProvider
    config: AnalysisProviderConfig

    def __post_init__(self) -> None:
        if (
            type(self.analysis_provider) is not ConfiguredAnalysisProvider
            or type(self.proposal_provider) is not ConfiguredProposalProvider
            or type(self.config) is not AnalysisProviderConfig
        ):
            raise ValueError("analysis provider stack is invalid")


def analysis_provider_secret_refs() -> frozenset[SecretRef]:
    """The research provider scope can request only the generic credential."""

    return frozenset({SecretRef.primary(SecretKind.ANALYSIS_PROVIDER_API_KEY)})


def default_operator_config_root() -> Path:
    """The production operator config directory (never a secret location)."""

    import os

    from seven_lens.config.analysis_provider import validate_production_root

    override = os.environ.get("SEVEN_LENS_ANALYSIS_PROVIDER_CONFIG_ROOT")
    if override is not None:
        root = Path(override)
        if not root.is_absolute():
            raise ValueError("analysis provider config root is invalid")
        return validate_production_root(root)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    if not base.is_absolute():
        raise ValueError("analysis provider config root is invalid")
    return validate_production_root(base / "seven-lens")


def build_analysis_provider_stack(
    *,
    secret_provider: SecretProvider,
    audit: ModelCallAuditPort,
    executor: ChatCompletionsHttpExecutor | None = None,
    clock: Callable[[], UtcTimestamp] | None = None,
    config: AnalysisProviderConfig | None = None,
    config_root: Path | None = None,
) -> AnalysisProviderStack:
    """Compose one configured-route provider stack; the config is load-once."""

    if not callable(getattr(secret_provider, "get_secret", None)):
        raise ValueError("analysis provider stack secret capability is invalid")
    if not all(callable(getattr(audit, method, None)) for method in ("load", "claim", "persist")):
        raise ValueError("analysis provider stack audit capability is invalid")
    if executor is not None and not callable(getattr(executor, "execute", None)):
        raise ValueError("analysis provider stack transport capability is invalid")
    if clock is not None and not callable(clock):
        raise ValueError("analysis provider stack clock capability is invalid")
    if config is None:
        from seven_lens.config.analysis_provider import load_analysis_provider_config

        config = load_analysis_provider_config(config_root or default_operator_config_root())
    if type(config) is not AnalysisProviderConfig:
        raise ValueError("analysis provider stack route is invalid")
    selected_clock = clock or _utc_now
    selected_executor = executor if executor is not None else StdlibChatCompletionsHttpExecutor()
    scoped_secrets = ScopedSecretProvider(secret_provider, analysis_provider_secret_refs())
    api_key = scoped_secrets.get_secret(SecretRef.primary(SecretKind.ANALYSIS_PROVIDER_API_KEY))
    transport = ChatCompletionsModelTransport(
        config=config,
        api_key=api_key,
        executor=selected_executor,
        clock=selected_clock,
    )
    invoker = AuditedModelInvoker(
        config=config,
        transport=transport,
        audit=audit,
        clock=lambda: _validated_now(selected_clock).value,
    )
    return AnalysisProviderStack(
        analysis_provider=ConfiguredAnalysisProvider(invoker, config),
        proposal_provider=ConfiguredProposalProvider(invoker, config),
        config=config,
    )


def _utc_now() -> UtcTimestamp:
    return UtcTimestamp(datetime.now(UTC))


def _validated_now(clock: Callable[[], UtcTimestamp]) -> UtcTimestamp:
    try:
        value = clock()
    except Exception:
        raise ValueError("analysis provider clock failed") from None
    if type(value) is not UtcTimestamp:
        raise ValueError("analysis provider clock is invalid")
    return value
