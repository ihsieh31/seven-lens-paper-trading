"""Capability-minimal P3-E research secret composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from seven_lens.application.model_invoker import AuditedModelInvoker
from seven_lens.application.ports.model_audit import ModelCallAuditPort
from seven_lens.application.ports.secrets import SecretProvider
from seven_lens.application.secret_service import ScopedSecretProvider
from seven_lens.config.provider import agnes_25_flash_config
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.agnes_providers import (
    AgnesAnalysisProvider,
    AgnesProposalProvider,
)
from seven_lens.infrastructure.agnes_transport import (
    AgnesHttpExecutor,
    AgnesJsonModelTransport,
    StdlibAgnesHttpExecutor,
)
from seven_lens.security.secret_values import SecretKind, SecretRef


@dataclass(frozen=True, slots=True)
class AgnesProviderStack:
    """Public typed providers; raw transport and invoker capabilities are not exposed."""

    analysis_provider: AgnesAnalysisProvider
    proposal_provider: AgnesProposalProvider

    def __post_init__(self) -> None:
        if (
            type(self.analysis_provider) is not AgnesAnalysisProvider
            or type(self.proposal_provider) is not AgnesProposalProvider
        ):
            raise ValueError("Agnes provider stack is invalid")


def research_provider_secret_refs() -> frozenset[SecretRef]:
    """The P3-E research scope can request only the approved Agnes credential."""

    return frozenset({SecretRef.primary(SecretKind.AGNES_API_KEY)})


def build_agnes_provider_stack(
    *,
    secret_provider: SecretProvider,
    audit: ModelCallAuditPort,
    executor: AgnesHttpExecutor | None = None,
    clock: Callable[[], UtcTimestamp] | None = None,
) -> AgnesProviderStack:
    """Compose one Agnes-only provider stack without reading foreign secret refs."""

    if not callable(getattr(secret_provider, "get_secret", None)):
        raise ValueError("Agnes provider stack secret capability is invalid")
    if not all(callable(getattr(audit, method, None)) for method in ("load", "claim", "persist")):
        raise ValueError("Agnes provider stack audit capability is invalid")
    if executor is not None and not callable(getattr(executor, "execute", None)):
        raise ValueError("Agnes provider stack transport capability is invalid")
    if clock is not None and not callable(clock):
        raise ValueError("Agnes provider stack clock capability is invalid")
    selected_clock = clock or _utc_now
    selected_executor = executor if executor is not None else StdlibAgnesHttpExecutor()
    scoped_secrets = ScopedSecretProvider(secret_provider, research_provider_secret_refs())
    api_key = scoped_secrets.get_secret(SecretRef.primary(SecretKind.AGNES_API_KEY))
    config = agnes_25_flash_config()
    transport = AgnesJsonModelTransport(
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
    return AgnesProviderStack(
        analysis_provider=AgnesAnalysisProvider(invoker),
        proposal_provider=AgnesProposalProvider(invoker),
    )


def _utc_now() -> UtcTimestamp:
    return UtcTimestamp(datetime.now(UTC))


def _validated_now(clock: Callable[[], UtcTimestamp]) -> UtcTimestamp:
    try:
        value = clock()
    except Exception:
        raise ValueError("Agnes provider clock failed") from None
    if type(value) is not UtcTimestamp:
        raise ValueError("Agnes provider clock is invalid")
    return value
