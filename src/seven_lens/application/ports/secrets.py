"""Persistence- and platform-neutral secret lookup contract."""

from __future__ import annotations

from typing import Protocol

from seven_lens.security.secret_values import SecretRef, SecretValue


class SecretProviderError(RuntimeError):
    """Base class for bounded secret lookup failures."""


class SecretNotFound(SecretProviderError):
    def __init__(self) -> None:
        super().__init__("required secret was not found")


class SecretAmbiguous(SecretProviderError):
    def __init__(self) -> None:
        super().__init__("secret lookup was ambiguous")


class SecretAccessDenied(SecretProviderError):
    def __init__(self) -> None:
        super().__init__("secret access was denied")


class KeychainLocked(SecretProviderError):
    def __init__(self) -> None:
        super().__init__("Keychain is locked or requires interaction")


class SecretLookupTimeout(SecretProviderError):
    def __init__(self) -> None:
        super().__init__("secret lookup timed out")


class MalformedSecret(SecretProviderError):
    def __init__(self) -> None:
        super().__init__("secret value is malformed")


class SecretBackendUnavailable(SecretProviderError):
    def __init__(self) -> None:
        super().__init__("secret backend is unavailable")


class SecretCapabilityDenied(SecretProviderError):
    def __init__(self) -> None:
        super().__init__("secret capability is not available")


class SecretProvider(Protocol):
    """Read one exact typed secret reference."""

    def get_secret(self, ref: SecretRef) -> SecretValue:
        """Return one validated secret or raise a typed fail-closed error."""
        ...
