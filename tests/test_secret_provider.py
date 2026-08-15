"""Capability scope and all-or-nothing secret resolution tests."""

from __future__ import annotations

import os

import pytest

from fakes.secrets import FakeSecretProvider
from seven_lens.application.ports.secrets import (
    KeychainLocked,
    MalformedSecret,
    SecretAccessDenied,
    SecretAmbiguous,
    SecretBackendUnavailable,
    SecretCapabilityDenied,
    SecretLookupTimeout,
    SecretNotFound,
    SecretProviderError,
)
from seven_lens.application.secret_service import ScopedSecretProvider, resolve_required_secrets
from seven_lens.security.secret_values import SecretKind, SecretRef, SecretValue

FAKE_SECRET = SecretValue.from_bytes(b"fake-provider-secret-000000")
ALPACA_KEY_ID = SecretRef.primary(SecretKind.ALPACA_PAPER_KEY_ID)
ALPACA_SECRET = SecretRef.primary(SecretKind.ALPACA_PAPER_SECRET_KEY)
OPENAI_KEY = SecretRef.primary(SecretKind.OPENAI_API_KEY)
TAVILY_KEY = SecretRef.tavily("acct-01")


class DuckTypedSecretRef:
    def __init__(self) -> None:
        self.property_reads = 0

    @property
    def keychain_service(self) -> str:
        self.property_reads += 1
        return "attacker.service"

    @property
    def keychain_account(self) -> str:
        self.property_reads += 1
        return "attacker-account"


def test_execution_scope_allows_exact_alpaca_refs_and_calls_backend_once() -> None:
    backend = FakeSecretProvider({ALPACA_KEY_ID: FAKE_SECRET})
    provider = ScopedSecretProvider(backend, (ALPACA_KEY_ID, ALPACA_SECRET))

    assert provider.get_secret(ALPACA_KEY_ID) is FAKE_SECRET
    assert backend.calls == [ALPACA_KEY_ID]


@pytest.mark.parametrize("alpaca_ref", [ALPACA_KEY_ID, ALPACA_SECRET])
def test_research_scope_denies_alpaca_before_backend_call(alpaca_ref: SecretRef) -> None:
    backend = FakeSecretProvider({alpaca_ref: FAKE_SECRET})
    provider = ScopedSecretProvider(backend, (OPENAI_KEY, TAVILY_KEY))

    with pytest.raises(SecretCapabilityDenied):
        provider.get_secret(alpaca_ref)
    assert backend.calls == []


def test_research_scope_allows_only_exact_openai_and_tavily_refs() -> None:
    backend = FakeSecretProvider({OPENAI_KEY: FAKE_SECRET, TAVILY_KEY: FAKE_SECRET})
    provider = ScopedSecretProvider(backend, (OPENAI_KEY, TAVILY_KEY))

    assert provider.get_secret(OPENAI_KEY) is FAKE_SECRET
    assert provider.get_secret(TAVILY_KEY) is FAKE_SECRET
    assert backend.calls == [OPENAI_KEY, TAVILY_KEY]


def test_required_bundle_is_immutable_and_returned_only_after_all_lookups() -> None:
    backend = FakeSecretProvider({OPENAI_KEY: FAKE_SECRET, TAVILY_KEY: FAKE_SECRET})

    bundle = resolve_required_secrets(backend, (OPENAI_KEY, TAVILY_KEY))

    assert tuple(bundle) == (OPENAI_KEY, TAVILY_KEY)
    with pytest.raises(TypeError):
        bundle[OPENAI_KEY] = FAKE_SECRET  # type: ignore[index]


def test_required_bundle_failure_never_returns_partial_success_or_uses_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-environment-secret-must-not-be-read")
    backend = FakeSecretProvider(
        {OPENAI_KEY: FAKE_SECRET},
        {TAVILY_KEY: SecretNotFound()},
    )

    with pytest.raises(SecretNotFound):
        resolve_required_secrets(backend, (OPENAI_KEY, TAVILY_KEY))

    assert backend.calls == [OPENAI_KEY, TAVILY_KEY]
    assert os.environ["OPENAI_API_KEY"] == "fake-environment-secret-must-not-be-read"


@pytest.mark.parametrize(
    "failure",
    [
        SecretNotFound(),
        SecretAmbiguous(),
        SecretAccessDenied(),
        KeychainLocked(),
        SecretLookupTimeout(),
        MalformedSecret(),
        SecretBackendUnavailable(),
        SecretCapabilityDenied(),
    ],
)
def test_failure_taxonomy_has_fixed_bounded_non_secret_messages(
    failure: SecretProviderError,
) -> None:
    text = str(failure)

    assert 1 <= len(text) <= 80
    assert "fake-provider-secret" not in text
    assert "stderr" not in text.lower()
    assert "dsn" not in text.lower()


def test_required_references_must_be_unique_and_typed() -> None:
    backend = FakeSecretProvider({OPENAI_KEY: FAKE_SECRET})

    with pytest.raises(ValueError, match="references are invalid"):
        resolve_required_secrets(backend, (OPENAI_KEY, OPENAI_KEY))
    with pytest.raises(ValueError, match="references are invalid"):
        resolve_required_secrets(backend, ("OPENAI_API_KEY",))  # type: ignore[arg-type]
    assert backend.calls == []


def test_scope_constructor_rejects_duck_ref_before_backend_or_property_access() -> None:
    backend = FakeSecretProvider()
    forged = DuckTypedSecretRef()

    with pytest.raises(ValueError, match="allowlist is invalid"):
        ScopedSecretProvider(backend, (forged,))  # type: ignore[arg-type]

    assert backend.calls == []
    assert forged.property_reads == 0


def test_scope_lookup_rejects_duck_ref_before_backend_or_property_access() -> None:
    backend = FakeSecretProvider()
    provider = ScopedSecretProvider(backend, (OPENAI_KEY,))
    forged = DuckTypedSecretRef()

    with pytest.raises(SecretCapabilityDenied):
        provider.get_secret(forged)  # type: ignore[arg-type]

    assert backend.calls == []
    assert forged.property_reads == 0


def test_required_resolution_rejects_duck_ref_before_backend_or_property_access() -> None:
    backend = FakeSecretProvider()
    forged = DuckTypedSecretRef()

    with pytest.raises(ValueError, match="references are invalid"):
        resolve_required_secrets(backend, (forged,))  # type: ignore[arg-type]

    assert backend.calls == []
    assert forged.property_reads == 0


def test_scope_revalidates_ref_and_uses_immutable_allowlist_identity() -> None:
    ref = SecretRef.primary(SecretKind.OPENAI_API_KEY)
    backend = FakeSecretProvider()
    provider = ScopedSecretProvider(backend, (ref,))
    object.__setattr__(ref, "_account_id", "attacker-account")

    with pytest.raises(SecretCapabilityDenied):
        provider.get_secret(ref)

    assert backend.calls == []


def test_required_resolution_rejects_corrupted_internal_kind_before_backend() -> None:
    ref = SecretRef.primary(SecretKind.OPENAI_API_KEY)
    backend = FakeSecretProvider()
    object.__setattr__(ref, "_kind", SecretKind.TAVILY_API_KEY)

    with pytest.raises(ValueError, match="references are invalid"):
        resolve_required_secrets(backend, (ref,))

    assert backend.calls == []
