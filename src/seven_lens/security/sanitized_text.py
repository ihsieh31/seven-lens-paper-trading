"""Pure text guard shared by provider inputs and authoritative model outputs."""

from __future__ import annotations

import re
import unicodedata

_URI = re.compile(
    r"(?i)(?:"
    r"\b[a-z][a-z0-9+.-]{1,31}://|"
    r"\b(?:data|file|javascript|mailto|postgres(?:ql)?|ssh|tel|urn):|"
    r"(?<!:)//[^\s/]+"
    r")"
)
_BARE_HOST = re.compile(
    r"(?i)(?:\b[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\.)+"
    r"(?:ai|app|cn|co|com|dev|gov|info|invalid|io|net|org)(?::[0-9]{1,5})?(?:/[^\s]*)?"
)
_EMAIL = re.compile(r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/[A-Za-z0-9._-]+/|[A-Za-z]:\\)")
_RELATIVE_PATH = re.compile(r"(?:^|[\s(\[{'\"])(?:\.\.?/)[^\s)\]}'\"]+")
_IP_HOST_PATH = re.compile(
    r"(?i)(?:"
    r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?::[0-9]{1,5})?/[^\s]*|"
    r"\[[0-9a-f:.]+\](?::[0-9]{1,5})?/[^\s]*"
    r")"
)
_SENSITIVE_MARKER = re.compile(
    r"(?ix)\b(?:"
    r"api\s+key|secret(?:\s+key|\s+ref)?|credential|password|token|dsn|bearer|"
    r"authorization(?:\s+header)?|(?:request\s+)?header|"
    r"account\s+(?:id|number|name)|"
    r"broker\s+order\s+id|client\s+order\s+id|"
    r"(?:customer|client|user|portfolio\s+owner)\s+(?:id|name)|"
    r"email|phone|address"
    r")\b"
)


def validate_sanitized_text(
    value: object,
    field: str,
    *,
    maximum: int,
    empty: bool = False,
) -> str:
    """Return one bounded string or reject identity, secret, URI, and path material."""

    if type(value) is not str:
        raise ValueError(f"{field} must be an exact string")
    if "\x00" in value:
        raise ValueError(f"{field} must not contain NUL")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{field} must contain valid Unicode") from error
    if len(encoded) > maximum or (not empty and not encoded):
        raise ValueError(f"{field} is outside its bounded length")
    normalized = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(character) == "Cf" for character in normalized):
        raise ValueError(f"{field} contains prohibited invisible format controls")
    folded = normalized.casefold()
    label_view = re.sub(r"[_\-]+", " ", folded)
    if (
        _URI.search(folded)
        or _BARE_HOST.search(folded)
        or _EMAIL.search(folded)
        or _ABSOLUTE_PATH.search(normalized)
        or _RELATIVE_PATH.search(normalized)
        or _IP_HOST_PATH.search(folded)
        or _SENSITIVE_MARKER.search(label_view)
    ):
        raise ValueError(f"{field} contains prohibited identity, secret, or capability material")
    return value
