"""Capability-scoped secret lookup and all-or-nothing startup resolution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from seven_lens.application.ports.secrets import (
    KeychainLocked,
    MalformedSecret,
    SecretAccessDenied,
    SecretAmbiguous,
    SecretBackendUnavailable,
    SecretCapabilityDenied,
    SecretLookupTimeout,
    SecretNotFound,
    SecretProvider,
    SecretProviderError,
)
from seven_lens.observability.context import TelemetryContext
from seven_lens.observability.failsafe import FailSafeTelemetry
from seven_lens.observability.instruments import SecretKindAttribute, SecretLookupOutcome
from seven_lens.security.secret_values import (
    SecretKind,
    SecretRef,
    SecretRefIdentity,
    SecretValue,
    validated_secret_ref_identity,
)

_SECRET_KIND_ATTRIBUTES = {
    SecretKind.ALPACA_PAPER_KEY_ID: SecretKindAttribute.ALPACA_PAPER_KEY_ID,
    SecretKind.ALPACA_PAPER_SECRET_KEY: SecretKindAttribute.ALPACA_PAPER_SECRET_KEY,
    SecretKind.AGNES_API_KEY: SecretKindAttribute.AGNES_API_KEY,
    SecretKind.OPENAI_API_KEY: SecretKindAttribute.OPENAI_API_KEY,
    SecretKind.POSTGRES_RUNTIME_PASSWORD: SecretKindAttribute.POSTGRES_RUNTIME_PASSWORD,
    SecretKind.TAVILY_API_KEY: SecretKindAttribute.TAVILY_API_KEY,
}


class ScopedSecretProvider:
    """Architecture capability wrapper; this is not an OS-level sandbox."""

    def __init__(self, backend: SecretProvider, allowed_refs: Iterable[SecretRef]) -> None:
        refs = tuple(allowed_refs)
        identities = tuple(validated_secret_ref_identity(ref) for ref in refs)
        if any(identity is None for identity in identities):
            raise ValueError("secret capability allowlist is invalid")
        self._backend = backend
        self._allowed_identities = frozenset(
            identity for identity in identities if identity is not None
        )

    def get_secret(self, ref: SecretRef) -> SecretValue:
        identity = validated_secret_ref_identity(ref)
        if identity is None or identity not in self._allowed_identities:
            raise SecretCapabilityDenied
        return self._backend.get_secret(ref)


class InstrumentedSecretProvider:
    """Application-layer decorator for one explicit processing context."""

    def __init__(
        self,
        backend: SecretProvider,
        telemetry: FailSafeTelemetry,
        telemetry_context: TelemetryContext,
    ) -> None:
        self._backend = backend
        self._telemetry = telemetry
        self._telemetry_context = telemetry_context

    def get_secret(self, ref: SecretRef) -> SecretValue:
        identity = validated_secret_ref_identity(ref)
        if identity is None:
            # Preserve the backend's existing typed/validated behavior for forged refs.
            return self._backend.get_secret(ref)
        observation = self._telemetry.start_secret_lookup(
            self._telemetry_context,
            _SECRET_KIND_ATTRIBUTES[identity[0]],
        )
        try:
            value = self._backend.get_secret(ref)
        except Exception as error:
            self._telemetry.finish_secret_lookup(observation, _secret_lookup_outcome(error))
            raise
        self._telemetry.finish_secret_lookup(observation, SecretLookupOutcome.SUCCESS)
        return value


def resolve_required_secrets(
    provider: SecretProvider,
    required_refs: Iterable[SecretRef],
) -> Mapping[SecretRef, SecretValue]:
    """Resolve a unique bundle and return nothing unless every lookup succeeds."""
    refs = tuple(required_refs)
    identities: tuple[SecretRefIdentity | None, ...] = tuple(
        validated_secret_ref_identity(ref) for ref in refs
    )
    valid_identities = tuple(identity for identity in identities if identity is not None)
    if len(valid_identities) != len(refs) or len(valid_identities) != len(set(valid_identities)):
        raise ValueError("required secret references are invalid")
    resolved: dict[SecretRef, SecretValue] = {}
    for ref in refs:
        resolved[ref] = provider.get_secret(ref)
    return MappingProxyType(resolved)


def _secret_lookup_outcome(error: Exception) -> SecretLookupOutcome:
    if isinstance(error, SecretNotFound):
        return SecretLookupOutcome.NOT_FOUND
    if isinstance(error, SecretAmbiguous):
        return SecretLookupOutcome.AMBIGUOUS
    if isinstance(error, SecretAccessDenied):
        return SecretLookupOutcome.ACCESS_DENIED
    if isinstance(error, KeychainLocked):
        return SecretLookupOutcome.KEYCHAIN_LOCKED
    if isinstance(error, SecretLookupTimeout):
        return SecretLookupOutcome.TIMEOUT
    if isinstance(error, MalformedSecret):
        return SecretLookupOutcome.MALFORMED
    if isinstance(error, SecretCapabilityDenied):
        return SecretLookupOutcome.CAPABILITY_DENIED
    if isinstance(error, (SecretBackendUnavailable, SecretProviderError)):
        return SecretLookupOutcome.BACKEND_UNAVAILABLE
    return SecretLookupOutcome.BACKEND_UNAVAILABLE
