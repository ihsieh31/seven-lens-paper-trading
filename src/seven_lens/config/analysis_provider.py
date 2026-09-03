"""Provider-neutral analysis route configuration with a strict operator store.

The active analysis route (base URL, model, and package-owned safety policy) is
expressed by one frozen generic value object.  An operator file persisted by the
``seven_lens.cli.analysis_provider`` commands overrides the package-owned
default route; both sources pass through the exact same validation, hash, and
fail-closed load path.  This module intentionally contains no HTTP, DNS, or
Keychain capability of its own.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from seven_lens.config.errors import ConfigurationError

OPERATOR_SCHEMA_VERSION: Final = "seven-lens.analysis-provider-config.v1"
POLICY_SCHEMA: Final = "seven-lens.analysis-route-policy.v1"
ROUTE_POLICY_PREFIX: Final = "analysis-route-v1:"
PROVIDER_VERSION: Final = "openai-compatible.1"
LEGACY_ENDPOINT_POLICY_ID: Final = "p3e-agnes-2.5-flash-only-v1"
LEGACY_PROVIDER_VERSION: Final = "agnes.1"

PACKAGE_DEFAULT_BASE_URL: Final = "https://apihub.agnes-ai.com/v1"
PACKAGE_DEFAULT_MODEL_ID: Final = "agnes-2.5-flash"

_PACKAGE_DEFAULT_GENERATION: Final = 0
_OPERATOR_GENERATION_MIN: Final = 1
_GENERATION_MAX: Final = 2**63 - 1
_MAX_CONFIG_BYTES: Final = 65_536
_MAX_HOST_LENGTH: Final = 253
_MAX_PATH_LENGTH: Final = 256
_MAX_PATH_SEGMENTS: Final = 16
_FILE_NAME: Final = "analysis-provider.json"
_CHAT_COMPLETIONS_SUFFIX: Final = "/chat/completions"
_PATH_CHARACTERS: Final = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"
)
_MODEL_SEGMENT_CHARACTERS: Final = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
)

_FIXED_POLICY: Final[dict[str, object]] = {
    "api_flavor": "CHAT_COMPLETIONS",
    "provider_kind": "OPENAI_COMPATIBLE",
    "policy_schema": POLICY_SCHEMA,
    "connect_timeout_ms": 2_000,
    "read_timeout_ms": 180_000,
    "total_timeout_ms": 180_000,
    "request_byte_cap": 131_072,
    "response_byte_cap": 131_072,
    "max_output_tokens": 8_192,
    "temperature": 0.0,
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
}


def _route_material(base_url: str, model_id: str) -> dict[str, object]:
    material: dict[str, object] = {"base_url": base_url, "model_id": model_id}
    material.update(_FIXED_POLICY)
    return material


def route_config_hash_for(base_url: str, model_id: str) -> str:
    """Hash the canonical route material (never generation, path, or time)."""

    encoded = json.dumps(
        _route_material(base_url, model_id),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def endpoint_policy_id_for(route_config_hash: str) -> str:
    if not _is_route_hash(route_config_hash):
        raise ConfigurationError("analysis provider route hash is invalid")
    return ROUTE_POLICY_PREFIX + route_config_hash


def _is_route_hash(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_route_host(host: object) -> str:
    """Validate one exact lowercase route hostname (never an IP or local name)."""

    if (
        type(host) is not str
        or not host
        or host != host.lower()
        or host != host.rstrip(".")
        or any(ord(character) > 0x7E for character in host)
        or ":" in host
        or host.startswith("[")
        or host.endswith("]")
    ):
        raise ConfigurationError("analysis provider route host is invalid")
    if _is_ip_literal(host) or _is_forbidden_host(host):
        raise ConfigurationError("analysis provider route host is invalid")
    if not _valid_hostname(host) or len(host) > _MAX_HOST_LENGTH:
        raise ConfigurationError("analysis provider route host is invalid")
    return host


def canonical_base_url(raw: object) -> str:
    """Validate an operator base URL and return its exact canonical form."""

    if type(raw) is not str or not raw or raw != raw.strip() or any(map(str.isspace, raw)):
        raise ConfigurationError("analysis provider endpoint must be one non-empty https URL")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw):
        raise ConfigurationError(
            "analysis provider endpoint contains a prohibited control character"
        )
    if "\\" in raw or "%" in raw:
        raise ConfigurationError(
            "analysis provider endpoint must not contain escapes or percent encoding"
        )
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise ConfigurationError("analysis provider endpoint is not a valid URL") from None
    if parts.scheme != "https":
        raise ConfigurationError("analysis provider endpoint scheme must be https")
    if parts.username is not None or parts.password is not None:
        raise ConfigurationError("analysis provider endpoint must not include embedded credentials")
    if parts.query or parts.fragment:
        raise ConfigurationError("analysis provider endpoint must not include a query or fragment")
    host = parts.hostname
    if type(host) is not str or not host:
        raise ConfigurationError("analysis provider endpoint must include a host name")
    if host != host.lower() or host != host.rstrip("."):
        raise ConfigurationError(
            "analysis provider endpoint host must be lowercase without a trailing dot"
        )
    if any(ord(character) > 0x7E for character in host):
        raise ConfigurationError("analysis provider endpoint host is invalid")
    if ":" in host or host.startswith("[") or host.endswith("]"):
        raise ConfigurationError("analysis provider endpoint host must not be an IP literal")
    if _is_ip_literal(host) or _is_forbidden_host(host):
        raise ConfigurationError("analysis provider endpoint host must be a public DNS name")
    if not _valid_hostname(host) or len(host) > _MAX_HOST_LENGTH:
        raise ConfigurationError("analysis provider endpoint host is invalid")
    try:
        port = parts.port
    except ValueError:
        raise ConfigurationError("analysis provider endpoint port is invalid") from None
    if port is not None and port != 443:
        raise ConfigurationError(
            "analysis provider endpoint must not include a port other than 443"
        )
    path = _canonical_base_path(parts.path or "")
    return f"https://{host}{path}"


def _canonical_base_path(path: str) -> str:
    if len(path) > _MAX_PATH_LENGTH:
        raise ConfigurationError("analysis provider endpoint path is too long")
    if path in {"", "/"}:
        return ""
    if "//" in path:
        raise ConfigurationError("analysis provider endpoint path is invalid")
    if path.lower().endswith(_CHAT_COMPLETIONS_SUFFIX):
        raise ConfigurationError("analysis provider endpoint must be a base URL")
    path = path.rstrip("/")
    if not path:
        return ""
    segments = path.split("/")
    if segments[0] != "":
        raise ConfigurationError("analysis provider endpoint path must start with a slash")
    body = segments[1:]
    if any(segment == "" for segment in body):
        raise ConfigurationError("analysis provider endpoint path is invalid")
    if len(body) > _MAX_PATH_SEGMENTS:
        raise ConfigurationError("analysis provider endpoint path has too many segments")
    if any(
        segment in {".", ".."} or any(character not in _PATH_CHARACTERS for character in segment)
        for segment in body
    ):
        raise ConfigurationError("analysis provider endpoint path contains an unsafe segment")
    return "/" + "/".join(body)


def _is_ip_literal(host: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    labels = host.split(".")
    return (len(labels) == 4 and all(label.isdigit() for label in labels)) or _looks_ipv6(host)


def _looks_ipv6(host: str) -> bool:
    import socket

    try:
        socket.inet_pton(socket.AF_INET6, host)
    except OSError:
        return False
    return True


def _is_forbidden_host(host: str) -> bool:
    lowered = host.lower()
    forbidden_suffixes = (".local", ".localhost", ".arpa", ".internal", ".example", ".invalid")
    return lowered == "localhost" or lowered.endswith(forbidden_suffixes)


def _valid_hostname(host: str) -> bool:
    def valid_label(label: str) -> bool:
        return (
            bool(label)
            and len(label) <= 63
            and all(character in "0123456789abcdefghijklmnopqrstuvwxyz-" for character in label)
            and label[0] != "-"
            and label[-1] != "-"
        )

    return all(valid_label(label) for label in host.split("."))


def canonical_model_id(raw: object) -> str:
    """Validate one exact provider model identifier (never a filesystem path)."""

    if type(raw) is not str or not 1 <= len(raw) <= 128:
        raise ConfigurationError("analysis provider model id must be 1 to 128 characters")
    if raw != raw.strip() or any(map(str.isspace, raw)):
        raise ConfigurationError("analysis provider model id must not contain whitespace")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw):
        raise ConfigurationError(
            "analysis provider model id contains a prohibited control character"
        )
    segments = raw.split("/")
    if len(segments) > 2:
        raise ConfigurationError("analysis provider model id must have at most one slash")
    for segment in segments:
        if not segment or segment in {".", ".."}:
            raise ConfigurationError(
                "analysis provider model id must not contain empty or dot segments"
            )
        if any(character not in _MODEL_SEGMENT_CHARACTERS for character in segment):
            raise ConfigurationError(
                "analysis provider model id contains a character that is not allowed"
            )
    return raw


class ConfigSource(StrEnum):
    """Where the active route snapshot came from."""

    PACKAGE_DEFAULT = "PACKAGE_DEFAULT"
    OPERATOR_FILE = "OPERATOR_FILE"


@dataclass(frozen=True, slots=True)
class AnalysisProviderConfig:
    """One immutable, fully validated analysis route snapshot.

    ``base_url`` is the operator-configured base URL (canonical form) and
    ``full_endpoint`` the derived exact Chat Completions request URL.  Every
    safety policy field is a package-owned constant; only the base URL, model
    identity, source, and generation vary.  The legacy package default binds
    the historical Agnes base URL and model at generation 0.
    """

    config_source: ConfigSource
    generation: int
    base_url: str
    model_id: str

    scheme: str = "https"
    host: str = ""
    base_path: str = ""
    full_endpoint: str = ""
    route_config_hash: str = ""
    endpoint_policy_id: str = ""
    provider_version: str = PROVIDER_VERSION
    api_flavor: str = "CHAT_COMPLETIONS"
    provider_kind: str = "OPENAI_COMPATIBLE"
    policy_schema: str = POLICY_SCHEMA
    connect_timeout_ms: int = 2_000
    read_timeout_ms: int = 180_000
    total_timeout_ms: int = 180_000
    request_byte_cap: int = 131_072
    response_byte_cap: int = 131_072
    max_output_tokens: int = 8_192
    temperature: float = 0.0
    stream: bool = False
    tools: bool = False
    state: bool = False
    files: bool = False
    follow_redirects: bool = False
    trust_env: bool = False
    proxy: bool = False
    automatic_retry: bool = False
    fallback_model_id: None = None
    fallback_attempts: int = 0

    def __post_init__(self) -> None:
        if type(self.config_source) is not ConfigSource:
            raise ConfigurationError("analysis provider configuration source is invalid")
        if self.config_source is ConfigSource.PACKAGE_DEFAULT:
            if type(self.generation) is not int or self.generation != _PACKAGE_DEFAULT_GENERATION:
                raise ConfigurationError("analysis provider generation is invalid")
        elif (
            type(self.generation) is not int
            or not _OPERATOR_GENERATION_MIN <= self.generation <= _GENERATION_MAX
        ):
            raise ConfigurationError("analysis provider generation is invalid")
        if canonical_base_url(self.base_url) != self.base_url:
            raise ConfigurationError("analysis provider base url is not canonical")
        if canonical_model_id(self.model_id) != self.model_id:
            raise ConfigurationError("analysis provider model id is not canonical")
        parts = urlsplit(self.base_url)
        object.__setattr__(self, "scheme", "https")
        object.__setattr__(self, "host", parts.hostname or "")
        object.__setattr__(self, "base_path", parts.path or "")
        object.__setattr__(
            self,
            "full_endpoint",
            f"https://{self.host}{self.base_path}{_CHAT_COMPLETIONS_SUFFIX}",
        )
        expected_hash = route_config_hash_for(self.base_url, self.model_id)
        if (
            type(self.route_config_hash) is str
            and self.route_config_hash
            and not hmac.compare_digest(self.route_config_hash, expected_hash)
        ):
            raise ConfigurationError("analysis provider route hash mismatch")
        object.__setattr__(self, "route_config_hash", expected_hash)
        object.__setattr__(self, "endpoint_policy_id", endpoint_policy_id_for(expected_hash))
        if self.provider_version != PROVIDER_VERSION:
            raise ConfigurationError("analysis provider provider version drifted")
        for name, expected in _FIXED_POLICY.items():
            actual = getattr(self, name)
            if type(actual) is not type(expected) or actual != expected:
                raise ConfigurationError("analysis provider fixed policy material drifted")
        if self.fallback_model_id is not None or self.fallback_attempts != 0:
            raise ConfigurationError("analysis provider fallback policy is invalid")

    @property
    def route_provider_kind(self) -> str:
        """Provider identity for claims/audits: legacy default or generic route."""

        return (
            "AGNES" if self.config_source is ConfigSource.PACKAGE_DEFAULT else "OPENAI_COMPATIBLE"
        )

    @property
    def route_policy_id(self) -> str:
        """Endpoint policy identity: legacy constant or hash-bound generic id."""

        return (
            LEGACY_ENDPOINT_POLICY_ID
            if self.config_source is ConfigSource.PACKAGE_DEFAULT
            else self.endpoint_policy_id
        )

    @property
    def route_model_version(self) -> str:
        """Envelope-safe model version text derived from the exact model id.

        Model ids may carry one '/' separator; envelope and producer version
        fields use a stricter charset, so the separator is projected to '.'.
        The route closure itself is still enforced against the exact model id
        (claims, audits, and the wire request), never against this projection.
        """

        projected = self.model_id.replace("/", ".")
        if (
            not 1 <= len(projected) <= 64
            or projected[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
            or any(
                character
                not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._+-"
                for character in projected
            )
        ):
            raise ConfigurationError("analysis provider model version projection is invalid")
        return projected

    @property
    def route_provider_version(self) -> str:
        """Envelope provider version: legacy identity or the generic identity."""

        return (
            LEGACY_PROVIDER_VERSION
            if self.config_source is ConfigSource.PACKAGE_DEFAULT
            else self.provider_version
        )

    @property
    def route_config_hash_value(self) -> str:
        """The route hash carried by claims/audits for this snapshot."""

        return (
            LEGACY_ROUTE_CONFIG_HASH
            if self.config_source is ConfigSource.PACKAGE_DEFAULT
            else self.route_config_hash
        )


#: Deterministic route hash over the canonical legacy Agnes material.  It is
#: used to backfill historical audit rows and to validate historical claims;
#: the active composition derives route identity from the loaded snapshot only.
LEGACY_ROUTE_CONFIG_HASH: Final = route_config_hash_for(
    PACKAGE_DEFAULT_BASE_URL, PACKAGE_DEFAULT_MODEL_ID
)


def package_default_analysis_provider_config() -> AnalysisProviderConfig:
    """Build the package-owned legacy default route (generation 0)."""

    return AnalysisProviderConfig(
        config_source=ConfigSource.PACKAGE_DEFAULT,
        generation=_PACKAGE_DEFAULT_GENERATION,
        base_url=PACKAGE_DEFAULT_BASE_URL,
        model_id=PACKAGE_DEFAULT_MODEL_ID,
    )


def operator_file_path(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise ConfigurationError("analysis provider config root is invalid")
    return root / _FILE_NAME


def validate_production_root(root: Path) -> Path:
    """Reject a production root whose existing path components traverse a symlink.

    Production path resolution must not follow symlinks: a symlinked component
    (for example ``$HOME/.config``) could silently redirect the operator file
    outside the intended private directory.  Explicitly injected test roots are
    validated by the loader's own final-component checks instead.
    """

    if not isinstance(root, Path) or not root.is_absolute():
        raise ConfigurationError("analysis provider config root is invalid")
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current = current / part
        if not current.exists():
            break
        if current.is_symlink():
            raise ConfigurationError("analysis provider config root is invalid")
    return root


def load_analysis_provider_config(root: Path) -> AnalysisProviderConfig:
    """Load one validated snapshot; a missing file yields the package default.

    An existing but invalid operator file never falls back to the default.
    """

    if not isinstance(root, Path) or not root.is_absolute():
        raise ConfigurationError("analysis provider config root is invalid")
    try:
        os.lstat(root)
    except FileNotFoundError:
        return package_default_analysis_provider_config()
    except OSError:
        raise ConfigurationError("analysis provider config root is unreadable") from None
    _reject_unsafe_root(root)
    path = operator_file_path(root)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return package_default_analysis_provider_config()
    except OSError:
        raise ConfigurationError("analysis provider config file is unreadable") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size == 0
        or metadata.st_size > _MAX_CONFIG_BYTES
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ConfigurationError("analysis provider config file is unsafe")
    try:
        raw = path.read_bytes()
    except OSError:
        raise ConfigurationError("analysis provider config file is unreadable") from None
    payload = _parse_strict_operator_json(raw)
    base_url = canonical_base_url(payload["base_url"])
    model_id = canonical_model_id(payload["model_id"])
    generation = payload["generation"]
    if type(generation) is not int or not _OPERATOR_GENERATION_MIN <= generation <= _GENERATION_MAX:
        raise ConfigurationError("analysis provider generation is invalid")
    expected_hash = route_config_hash_for(base_url, model_id)
    supplied_hash = payload["route_config_hash"]
    if type(supplied_hash) is not str or not hmac.compare_digest(supplied_hash, expected_hash):
        raise ConfigurationError("analysis provider route hash mismatch")
    return AnalysisProviderConfig(
        config_source=ConfigSource.OPERATOR_FILE,
        generation=generation,
        base_url=base_url,
        model_id=model_id,
    )


def canonical_operator_bytes(config: AnalysisProviderConfig) -> bytes:
    """Serialize the exact operator file bytes for one operator-file snapshot."""

    if config.config_source is not ConfigSource.OPERATOR_FILE:
        raise ConfigurationError("analysis provider snapshot is not an operator file")
    payload = {
        "base_url": config.base_url,
        "generation": config.generation,
        "model_id": config.model_id,
        "route_config_hash": config.route_config_hash,
        "schema_version": OPERATOR_SCHEMA_VERSION,
    }
    return (
        json.dumps(
            payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )


def _reject_unsafe_root(root: Path) -> None:
    try:
        metadata = os.lstat(root)
    except FileNotFoundError:
        raise ConfigurationError("analysis provider config root does not exist") from None
    except OSError:
        raise ConfigurationError("analysis provider config root is unreadable") from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ConfigurationError("analysis provider config root is unsafe")


def _parse_strict_operator_json(raw: bytes) -> dict[str, object]:
    expected_fields = {
        "base_url",
        "generation",
        "model_id",
        "route_config_hash",
        "schema_version",
    }

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ConfigurationError("analysis provider config has duplicate keys")
            result[key] = value
        return result

    def constant(_: str) -> object:
        raise ConfigurationError("analysis provider config has non-finite numbers")

    if raw.startswith(b"\xef\xbb\xbf"):
        raise ConfigurationError("analysis provider config has a byte-order mark")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except UnicodeDecodeError:
        raise ConfigurationError("analysis provider config is not UTF-8") from None
    except json.JSONDecodeError:
        raise ConfigurationError("analysis provider config is corrupt") from None
    if type(value) is not dict or set(value) != expected_fields:
        raise ConfigurationError("analysis provider config fields are invalid")
    if value["schema_version"] != OPERATOR_SCHEMA_VERSION:
        raise ConfigurationError("analysis provider config schema version is invalid")
    return value
