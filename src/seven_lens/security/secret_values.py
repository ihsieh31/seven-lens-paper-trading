"""Typed secret references and values that resist accidental disclosure."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final, Never, SupportsIndex, final

_PRIMARY_ACCOUNT: Final = "primary"
_TAVILY_ACCOUNT_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_SECRET_BYTES: Final = 4_096
_REDACTED_SECRET_VALUE: Final = "[REDACTED_SECRET_VALUE]"


class SecretValueError(ValueError):
    """Raised without carrying the rejected bytes or decoded text."""

    def __init__(self) -> None:
        super().__init__("secret value is malformed")


class SecretKind(StrEnum):
    """The complete set of credential kinds represented in the P1-C1 boundary."""

    ALPACA_PAPER_KEY_ID = "ALPACA_PAPER_KEY_ID"
    ALPACA_PAPER_SECRET_KEY = "ALPACA_PAPER_SECRET_KEY"
    OPENAI_API_KEY = "OPENAI_API_KEY"
    TAVILY_API_KEY = "TAVILY_API_KEY"


_SERVICES: Final[dict[SecretKind, str]] = {
    SecretKind.ALPACA_PAPER_KEY_ID: "seven-lens.paper-trading.alpaca-paper.key-id",
    SecretKind.ALPACA_PAPER_SECRET_KEY: "seven-lens.paper-trading.alpaca-paper.secret-key",
    SecretKind.OPENAI_API_KEY: "seven-lens.paper-trading.openai.api-key",
    SecretKind.TAVILY_API_KEY: "seven-lens.paper-trading.tavily.api-key",
}
type SecretRefIdentity = tuple[SecretKind, str, str]


@final
class SecretRef:
    """An exact typed lookup; callers cannot supply a Keychain service string."""

    __slots__ = ("_account_id", "_identity", "_kind")

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("SecretRef cannot be subclassed")

    def __init__(self, kind: SecretKind, account_id: str) -> None:
        if not _is_valid_kind_account(kind, account_id):
            if type(kind) is not SecretKind:
                raise ValueError("secret kind is invalid")
            raise ValueError("secret account identifier is invalid")
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_account_id", account_id)
        object.__setattr__(self, "_identity", (kind, account_id, _SERVICES[kind]))

    def __setattr__(self, name: str, value: object) -> Never:
        del name, value
        raise AttributeError("SecretRef is immutable")

    def __delattr__(self, name: str) -> Never:
        del name
        raise AttributeError("SecretRef is immutable")

    @classmethod
    def primary(cls, kind: SecretKind) -> SecretRef:
        """Create an Alpaca Paper or OpenAI reference bound to ``primary``."""
        if kind is SecretKind.TAVILY_API_KEY:
            raise ValueError("Tavily secret references require an account identifier")
        return cls(kind, _PRIMARY_ACCOUNT)

    @classmethod
    def tavily(cls, account_id: str) -> SecretRef:
        """Create an exact Tavily reference from a validated non-secret account id."""
        return cls(SecretKind.TAVILY_API_KEY, account_id)

    @property
    def kind(self) -> SecretKind:
        return self._validated_identity()[0]

    @property
    def account_id(self) -> str:
        return self._validated_identity()[1]

    @property
    def keychain_service(self) -> str:
        return self._validated_identity()[2]

    @property
    def keychain_account(self) -> str:
        return self._validated_identity()[1]

    def _validated_identity(self) -> SecretRefIdentity:
        identity = validated_secret_ref_identity(self)
        if identity is None:
            raise ValueError("secret reference is invalid")
        return identity

    def __eq__(self, other: object) -> bool:
        if type(other) is not SecretRef:
            return NotImplemented
        left = validated_secret_ref_identity(self)
        right = validated_secret_ref_identity(other)
        return left is not None and right is not None and left == right

    def __hash__(self) -> int:
        return hash(self._validated_identity())

    def __repr__(self) -> str:
        identity = self._validated_identity()
        return f"SecretRef(kind={identity[0].value!r}, account_id={identity[1]!r})"


def _is_valid_kind_account(kind: object, account_id: object) -> bool:
    if type(kind) is not SecretKind or type(account_id) is not str or kind not in _SERVICES:
        return False
    if kind is SecretKind.TAVILY_API_KEY:
        return _TAVILY_ACCOUNT_ID_PATTERN.fullmatch(account_id) is not None
    return account_id == _PRIMARY_ACCOUNT


def validated_secret_ref_identity(value: object) -> SecretRefIdentity | None:
    """Return a fresh immutable identity only for a valid exact ``SecretRef``."""
    if type(value) is not SecretRef:
        return None
    try:
        kind = object.__getattribute__(value, "_kind")
        account_id = object.__getattribute__(value, "_account_id")
        sealed_identity = object.__getattribute__(value, "_identity")
    except AttributeError:
        return None
    if not _is_valid_kind_account(kind, account_id):
        return None
    identity = kind, account_id, _SERVICES[kind]
    if type(sealed_identity) is not tuple or sealed_identity != identity:
        return None
    return identity


class SecretValue:
    """A non-serializable wrapper that prevents accidental display, not memory access.

    The decoded value still exists as ordinary process memory.  ``reveal_text`` is an
    explicit future composition boundary, not encryption or an OS-level isolation
    mechanism.
    """

    __slots__ = ("__text",)

    def __init__(self, untrusted: bytes) -> None:
        if type(untrusted) is not bytes or not 1 <= len(untrusted) <= _MAX_SECRET_BYTES:
            raise SecretValueError
        try:
            text = untrusted.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise SecretValueError from None
        if (
            not text
            or text != text.strip()
            or not text.strip()
            or "\x00" in text
            or "\r" in text
            or "\n" in text
        ):
            raise SecretValueError
        self.__text = text

    @classmethod
    def from_bytes(cls, untrusted: bytes) -> SecretValue:
        """Validate untrusted Keychain bytes without provider-specific assumptions."""
        return cls(untrusted)

    def reveal_text(self) -> str:
        """Explicitly expose plaintext to a future client composition boundary."""
        return self.__text

    def __str__(self) -> str:
        return _REDACTED_SECRET_VALUE

    def __repr__(self) -> str:
        return _REDACTED_SECRET_VALUE

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        del protocol
        raise TypeError("SecretValue cannot be serialized")
