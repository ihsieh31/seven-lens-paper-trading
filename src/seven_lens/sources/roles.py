"""Closed P4 source roles, per-family manifests, and the immutable registry."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Final

from seven_lens.config.errors import ConfigurationError
from seven_lens.domain.value_objects import SchemaVersion
from seven_lens.security.secret_values import SecretKind
from seven_lens.sources.contracts import RightsStatus

_HASH_DOMAIN: Final = b"seven-lens.p4.source-registry.v1\x00"
P4_CANONICAL_REGISTRY_HASH: Final = (
    "d6f7bc89d5452709d78fb39ea6e55b96c738122b0b4327d08289e23e38360cc9"
)
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_HOST_TEXT: Final = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_QUERY_NAME: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
_HEADER_NAME: Final = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_MEDIA_TYPE: Final = re.compile(r"^[a-z]+/[a-z0-9.+-]+$")
_PLACEHOLDER: Final = re.compile(r"^\{([a-z_][a-z0-9_]*)\}$")
_PATH_SEGMENT: Final = re.compile(r"^[A-Za-z0-9._{}-]+$")
_PRODUCER_VERSION: Final = "p4a.sources.v1"

_MAX_REQUEST_BYTES: Final = 65_536
_MAX_RESPONSE_BYTES: Final = 4_000_000
_MAX_DECOMPRESSED_BYTES: Final = 16_000_000
_MAX_TIMEOUT_SECONDS: Final = 60
_MAX_WINDOW_SECONDS: Final = 3_600
_MAX_PAGINATION_PAGES: Final = 1_000


class SourceRole(StrEnum):
    """The closed set of evidentiary authorities a source family may hold."""

    AUTHORITY = "AUTHORITY"
    CONFIRMATION = "CONFIRMATION"
    DISCOVERY = "DISCOVERY"
    RESEARCH_SUPPLEMENT = "RESEARCH_SUPPLEMENT"


class CoverageLabel(StrEnum):
    """Independent market-coverage dimension; never merges into the role axis."""

    FULL = "FULL"
    LIMITED_MARKET_COVERAGE = "LIMITED_MARKET_COVERAGE"


class NonExecutableReason(StrEnum):
    """Why a registered family must not issue network requests in P4-A."""

    NON_GET_UPSTREAM = "NON_GET_UPSTREAM"
    RIGHTS_UNVERIFIED = "RIGHTS_UNVERIFIED"
    CREDENTIAL_QUERY_NOT_PERMITTED = "CREDENTIAL_QUERY_NOT_PERMITTED"
    NO_PINNED_EXACT_HOST = "NO_PINNED_EXACT_HOST"


class StoragePolicy(StrEnum):
    """What raw payload retention the family's terms permit."""

    RETAIN_RAW_EVIDENCE = "RETAIN_RAW_EVIDENCE"
    RETAIN_METADATA_ONLY = "RETAIN_METADATA_ONLY"


class P4SourceFamily(StrEnum):
    """The closed P4-A source families; each maps to exactly one manifest."""

    ALPACA_ASSETS = "ALPACA_ASSETS"
    ALPACA_HISTORICAL_BARS = "ALPACA_HISTORICAL_BARS"
    ALPACA_IEX_QUOTES = "ALPACA_IEX_QUOTES"
    ALPACA_CORPORATE_ACTIONS = "ALPACA_CORPORATE_ACTIONS"
    SEC_EDGAR = "SEC_EDGAR"
    ISSUER_IR = "ISSUER_IR"
    EXCHANGE_OFFICIAL = "EXCHANGE_OFFICIAL"
    FRED_ALFRED = "FRED_ALFRED"
    TREASURY = "TREASURY"
    BLS = "BLS"
    BEA = "BEA"
    EIA = "EIA"
    TAVILY = "TAVILY"
    GDELT = "GDELT"
    YFINANCE = "YFINANCE"


_PINNED_ROLES: Final[Mapping[P4SourceFamily, SourceRole]] = MappingProxyType(
    {
        P4SourceFamily.ALPACA_ASSETS: SourceRole.AUTHORITY,
        P4SourceFamily.ALPACA_HISTORICAL_BARS: SourceRole.AUTHORITY,
        P4SourceFamily.ALPACA_IEX_QUOTES: SourceRole.AUTHORITY,
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS: SourceRole.CONFIRMATION,
        P4SourceFamily.SEC_EDGAR: SourceRole.AUTHORITY,
        P4SourceFamily.ISSUER_IR: SourceRole.CONFIRMATION,
        P4SourceFamily.EXCHANGE_OFFICIAL: SourceRole.AUTHORITY,
        P4SourceFamily.FRED_ALFRED: SourceRole.AUTHORITY,
        P4SourceFamily.TREASURY: SourceRole.AUTHORITY,
        P4SourceFamily.BLS: SourceRole.AUTHORITY,
        P4SourceFamily.BEA: SourceRole.AUTHORITY,
        P4SourceFamily.EIA: SourceRole.AUTHORITY,
        P4SourceFamily.TAVILY: SourceRole.DISCOVERY,
        P4SourceFamily.GDELT: SourceRole.DISCOVERY,
        P4SourceFamily.YFINANCE: SourceRole.RESEARCH_SUPPLEMENT,
    }
)
_PINNED_COVERAGE: Final[Mapping[P4SourceFamily, CoverageLabel]] = MappingProxyType(
    {
        P4SourceFamily.ALPACA_IEX_QUOTES: CoverageLabel.LIMITED_MARKET_COVERAGE,
        P4SourceFamily.ALPACA_ASSETS: CoverageLabel.FULL,
        P4SourceFamily.ALPACA_HISTORICAL_BARS: CoverageLabel.FULL,
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS: CoverageLabel.FULL,
        P4SourceFamily.SEC_EDGAR: CoverageLabel.FULL,
        P4SourceFamily.ISSUER_IR: CoverageLabel.FULL,
        P4SourceFamily.EXCHANGE_OFFICIAL: CoverageLabel.FULL,
        P4SourceFamily.FRED_ALFRED: CoverageLabel.FULL,
        P4SourceFamily.TREASURY: CoverageLabel.FULL,
        P4SourceFamily.BLS: CoverageLabel.FULL,
        P4SourceFamily.BEA: CoverageLabel.FULL,
        P4SourceFamily.EIA: CoverageLabel.FULL,
        P4SourceFamily.TAVILY: CoverageLabel.FULL,
        P4SourceFamily.GDELT: CoverageLabel.FULL,
        P4SourceFamily.YFINANCE: CoverageLabel.FULL,
    }
)
_PINNED_RATES: Final[Mapping[P4SourceFamily, tuple[int, int, int]]] = MappingProxyType(
    {
        P4SourceFamily.ALPACA_ASSETS: (200, 60, 10),
        P4SourceFamily.ALPACA_HISTORICAL_BARS: (200, 60, 10),
        P4SourceFamily.ALPACA_IEX_QUOTES: (200, 60, 10),
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS: (200, 60, 10),
        P4SourceFamily.SEC_EDGAR: (5, 1, 5),
        P4SourceFamily.ISSUER_IR: (10, 60, 5),
        P4SourceFamily.EXCHANGE_OFFICIAL: (10, 60, 5),
        P4SourceFamily.FRED_ALFRED: (120, 60, 10),
        P4SourceFamily.TREASURY: (30, 60, 5),
        P4SourceFamily.BLS: (10, 60, 5),
        P4SourceFamily.BEA: (30, 60, 5),
        P4SourceFamily.EIA: (30, 60, 5),
        P4SourceFamily.TAVILY: (10, 60, 5),
        P4SourceFamily.GDELT: (10, 60, 5),
        P4SourceFamily.YFINANCE: (10, 60, 5),
    }
)
_PINNED_AUTH: Final[Mapping[P4SourceFamily, tuple[SecretKind, ...]]] = MappingProxyType(
    {
        P4SourceFamily.ALPACA_ASSETS: (
            SecretKind.ALPACA_PAPER_KEY_ID,
            SecretKind.ALPACA_PAPER_SECRET_KEY,
        ),
        P4SourceFamily.ALPACA_HISTORICAL_BARS: (
            SecretKind.ALPACA_PAPER_KEY_ID,
            SecretKind.ALPACA_PAPER_SECRET_KEY,
        ),
        P4SourceFamily.ALPACA_IEX_QUOTES: (
            SecretKind.ALPACA_PAPER_KEY_ID,
            SecretKind.ALPACA_PAPER_SECRET_KEY,
        ),
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS: (
            SecretKind.ALPACA_PAPER_KEY_ID,
            SecretKind.ALPACA_PAPER_SECRET_KEY,
        ),
        P4SourceFamily.SEC_EDGAR: (),
        P4SourceFamily.ISSUER_IR: (),
        P4SourceFamily.EXCHANGE_OFFICIAL: (),
        P4SourceFamily.FRED_ALFRED: (SecretKind.FRED_API_KEY,),
        P4SourceFamily.TREASURY: (),
        P4SourceFamily.BLS: (SecretKind.BLS_API_KEY,),
        P4SourceFamily.BEA: (SecretKind.BEA_API_KEY,),
        P4SourceFamily.EIA: (SecretKind.EIA_API_KEY,),
        P4SourceFamily.TAVILY: (SecretKind.TAVILY_API_KEY,),
        P4SourceFamily.GDELT: (),
        P4SourceFamily.YFINANCE: (),
    }
)
_REGISTERED_HOST_FAMILIES: Final[frozenset[P4SourceFamily]] = frozenset(
    {P4SourceFamily.ISSUER_IR, P4SourceFamily.EXCHANGE_OFFICIAL}
)


def _validate_host_text(host: str, field_name: str) -> None:
    if type(host) is not str or _HOST_TEXT.fullmatch(host) is None:
        raise ConfigurationError(f"{field_name} must be a canonical lowercase DNS host")
    labels = host.split(".")
    if any(label.startswith("xn--") for label in labels):
        raise ConfigurationError(f"{field_name} must not use punycode encoding")
    if host.split(".")[-1].isdigit():
        raise ConfigurationError(f"{field_name} must not be an IP literal")


def _validate_path_template(template: str) -> None:
    if type(template) is not str or not template.startswith("/"):
        raise ConfigurationError("path_template must start with a slash")
    if any(char in template for char in ("?", "#", " ")):
        raise ConfigurationError("path_template must not carry query, fragment, or space text")
    segments = template.split("/")[1:]
    if not segments or any(not segment for segment in segments):
        raise ConfigurationError("path_template must use non-empty path segments")
    placeholders: list[str] = []
    complete_placeholder = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
    for segment in segments:
        if segment == "..":
            raise ConfigurationError("path_template must not contain traversal segments")
        if _PATH_SEGMENT.fullmatch(segment) is None:
            raise ConfigurationError("path_template contains a non-canonical segment")
        residue = complete_placeholder.sub("", segment)
        if "{" in residue or "}" in residue:
            raise ConfigurationError("path_template contains a malformed placeholder")
        placeholders.extend(complete_placeholder.findall(segment))


@dataclass(frozen=True, slots=True)
class EndpointSpec:
    """One concrete endpoint: sanitized id, canonical template, anchored pattern."""

    endpoint_id: str
    path_template: str
    path_pattern: str


@dataclass(frozen=True, slots=True)
class SourceFamilyPolicy:
    """One family's complete transport, rights, and resource manifest."""

    family: P4SourceFamily
    role: SourceRole
    coverage: CoverageLabel
    host: str
    path_template: str
    endpoints: tuple[EndpointSpec, ...]
    query_allowlist: tuple[str, ...]
    required_query: tuple[str, ...]
    header_allowlist: tuple[str, ...]
    auth_secrets: tuple[SecretKind, ...]
    max_request_bytes: int
    max_response_bytes: int
    max_decompressed_bytes: int
    timeout_seconds: int
    requests_per_window: int
    window_seconds: int
    burst_limit: int
    pagination_max_pages: int
    allowed_media_types: tuple[str, ...]
    schema_version: SchemaVersion
    rights: RightsStatus
    storage: StoragePolicy
    producer_version: str
    scheme: str = "https"
    executable: bool = True
    non_executable_reason: NonExecutableReason | None = None
    registered_hosts: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if type(self.family) is not P4SourceFamily:
            raise ConfigurationError("family requires an exact P4SourceFamily")
        if type(self.role) is not SourceRole or self.role is not _PINNED_ROLES[self.family]:
            raise ConfigurationError(f"role for {self.family.value} is pinned and cannot drift")
        if (
            type(self.coverage) is not CoverageLabel
            or self.coverage is not _PINNED_COVERAGE[self.family]
        ):
            raise ConfigurationError(f"coverage for {self.family.value} is pinned")
        if self.scheme != "https":
            raise ConfigurationError("scheme is pinned to HTTPS")
        self._validate_hosts()
        _validate_path_template(self.path_template)
        self._validate_endpoints()
        self._validate_query_headers_auth()
        self._validate_budgets()
        self._validate_media_rights_execution()

    def _validate_hosts(self) -> None:
        if self.host == "":
            if self.family not in _REGISTERED_HOST_FAMILIES:
                raise ConfigurationError(f"{self.family.value} requires an exact pinned host")
            for host in self.registered_hosts:
                _validate_host_text(host, "registered host")
            return
        if self.registered_hosts:
            raise ConfigurationError("pinned-host families must not carry registered hosts")
        _validate_host_text(self.host, "host")

    def _validate_endpoints(self) -> None:
        if type(self.endpoints) is not tuple or not self.endpoints:
            raise ConfigurationError("endpoints must be a non-empty tuple")
        if len(self.endpoints) > 16:
            raise ConfigurationError("endpoint count exceeds its bound")
        seen_ids: list[str] = []
        templates: list[str] = []
        for endpoint in self.endpoints:
            if type(endpoint) is not EndpointSpec:
                raise ConfigurationError("endpoints require exact EndpointSpec values")
            if type(endpoint.endpoint_id) is not str or not endpoint.endpoint_id:
                raise ConfigurationError("endpoint id must be sanitized text")
            if ":" in endpoint.endpoint_id or "%" in endpoint.endpoint_id:
                raise ConfigurationError("endpoint id must be sanitized text")
            seen_ids.append(endpoint.endpoint_id)
            _validate_path_template(endpoint.path_template)
            templates.append(endpoint.path_template)
            if type(endpoint.path_pattern) is not str or not endpoint.path_pattern.startswith("^/"):
                raise ConfigurationError("endpoint pattern must anchor a canonical path")
            try:
                re.compile(endpoint.path_pattern)
            except re.error as error:
                raise ConfigurationError("endpoint pattern must compile") from error
        if len(seen_ids) != len(set(seen_ids)):
            raise ConfigurationError("endpoint ids must be unique")
        if self.path_template not in templates:
            raise ConfigurationError("path_template must be one of the endpoint templates")

    def _validate_query_headers_auth(self) -> None:
        if type(self.query_allowlist) is not tuple or len(self.query_allowlist) > 32:
            raise ConfigurationError("query_allowlist must be a bounded tuple")
        if any(
            type(name) is not str or _QUERY_NAME.fullmatch(name) is None
            for name in self.query_allowlist
        ):
            raise ConfigurationError("query_allowlist contains a non-canonical name")
        if len(set(self.query_allowlist)) != len(self.query_allowlist):
            raise ConfigurationError("query_allowlist must not contain duplicates")
        if type(self.required_query) is not tuple or not set(self.required_query) <= set(
            self.query_allowlist
        ):
            raise ConfigurationError("required_query must be a subset of the allowlist")
        if len(self.required_query) != len(set(self.required_query)):
            raise ConfigurationError("required_query must not contain duplicates")
        if type(self.header_allowlist) is not tuple or len(self.header_allowlist) > 16:
            raise ConfigurationError("header_allowlist must be a bounded tuple")
        if any(
            type(name) is not str or _HEADER_NAME.fullmatch(name) is None
            for name in self.header_allowlist
        ):
            raise ConfigurationError("header_allowlist contains a non-canonical header name")
        if len(set(self.header_allowlist)) != len(self.header_allowlist):
            raise ConfigurationError("header_allowlist must not contain duplicates")
        if type(self.auth_secrets) is not tuple or len(self.auth_secrets) > 4:
            raise ConfigurationError("auth_secrets must be a bounded tuple")
        if any(type(secret) is not SecretKind for secret in self.auth_secrets):
            raise ConfigurationError("auth_secrets require exact SecretKind members")
        if len(set(self.auth_secrets)) != len(self.auth_secrets):
            raise ConfigurationError("auth_secrets must not contain duplicates")
        if self.auth_secrets != _PINNED_AUTH[self.family]:
            raise ConfigurationError(f"auth secrets for {self.family.value} are pinned")

    def _validate_budgets(self) -> None:
        for name, value, maximum in (
            ("max_request_bytes", self.max_request_bytes, _MAX_REQUEST_BYTES),
            ("max_response_bytes", self.max_response_bytes, _MAX_RESPONSE_BYTES),
            ("max_decompressed_bytes", self.max_decompressed_bytes, _MAX_DECOMPRESSED_BYTES),
        ):
            if type(value) is not int or not 0 < value <= maximum:
                raise ConfigurationError(f"{name} must be positive and within its cap")
        if self.max_decompressed_bytes < self.max_response_bytes:
            raise ConfigurationError("decompressed budget must cover the wire budget")
        for name, value, maximum in (
            ("timeout_seconds", self.timeout_seconds, _MAX_TIMEOUT_SECONDS),
            ("window_seconds", self.window_seconds, _MAX_WINDOW_SECONDS),
            ("pagination_max_pages", self.pagination_max_pages, _MAX_PAGINATION_PAGES),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ConfigurationError(f"{name} must be within its bound")
        for name in ("requests_per_window", "burst_limit"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ConfigurationError(f"{name} must be a positive integer")
        pinned_rate = _PINNED_RATES[self.family]
        if (self.requests_per_window, self.window_seconds, self.burst_limit) != pinned_rate:
            raise ConfigurationError(f"rate budget for {self.family.value} is pinned")

    def _validate_media_rights_execution(self) -> None:
        if type(self.allowed_media_types) is not tuple or not self.allowed_media_types:
            raise ConfigurationError("allowed_media_types must be a non-empty tuple")
        if len(self.allowed_media_types) > 8 or any(
            type(media) is not str or _MEDIA_TYPE.fullmatch(media) is None
            for media in self.allowed_media_types
        ):
            raise ConfigurationError("allowed_media_types contains a non-canonical type")
        if type(self.schema_version) is not SchemaVersion:
            raise ConfigurationError("schema_version requires an exact SchemaVersion")
        if type(self.rights) is not RightsStatus:
            raise ConfigurationError("rights require an exact RightsStatus")
        if type(self.storage) is not StoragePolicy:
            raise ConfigurationError("storage requires an exact StoragePolicy")
        if type(self.producer_version) is not str or not 1 <= len(self.producer_version) <= 64:
            raise ConfigurationError("producer_version must be bounded text")
        if type(self.executable) is not bool:
            raise ConfigurationError("executable requires an exact bool")
        if self.executable:
            if self.non_executable_reason is not None:
                raise ConfigurationError("executable families must not carry a disable reason")
            if self.rights is RightsStatus.UNKNOWN:
                raise ConfigurationError("rights UNKNOWN forbids production execution")
            return
        if type(self.non_executable_reason) is not NonExecutableReason:
            raise ConfigurationError("non-executable families require a typed reason")

    def wire(self) -> dict[str, object]:
        """Return the canonical manifest content used for the registry hash."""
        return {
            "family": self.family.value,
            "role": self.role.value,
            "coverage": self.coverage.value,
            "scheme": self.scheme,
            "host": self.host,
            "registered_hosts": list(self.registered_hosts),
            "path_template": self.path_template,
            "endpoints": [
                {
                    "endpoint_id": endpoint.endpoint_id,
                    "path_template": endpoint.path_template,
                    "path_pattern": endpoint.path_pattern,
                }
                for endpoint in self.endpoints
            ],
            "query_allowlist": list(self.query_allowlist),
            "required_query": list(self.required_query),
            "header_allowlist": list(self.header_allowlist),
            "auth_secret_kinds": [secret.value for secret in self.auth_secrets],
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "max_decompressed_bytes": self.max_decompressed_bytes,
            "timeout_seconds": self.timeout_seconds,
            "requests_per_window": self.requests_per_window,
            "window_seconds": self.window_seconds,
            "burst_limit": self.burst_limit,
            "pagination_max_pages": self.pagination_max_pages,
            "allowed_media_types": list(self.allowed_media_types),
            "schema_version": str(self.schema_version),
            "rights": self.rights.value,
            "storage": self.storage.value,
            "producer_version": self.producer_version,
            "executable": self.executable,
            "non_executable_reason": (
                None if self.non_executable_reason is None else self.non_executable_reason.value
            ),
        }


@dataclass(frozen=True, slots=True)
class SourceManifestRegistry:
    """Immutable startup-validated collection of every approved family manifest."""

    policies: tuple[SourceFamilyPolicy, ...]
    registry_hash: str = ""
    _index: Mapping[str, SourceFamilyPolicy] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.policies) is not tuple or any(
            type(policy) is not SourceFamilyPolicy for policy in self.policies
        ):
            raise ConfigurationError("registry policies must be exact SourceFamilyPolicy values")
        families = [policy.family for policy in self.policies]
        if len(families) != len(set(families)):
            raise ConfigurationError("each family may carry exactly one manifest")
        if len(families) != len(P4SourceFamily):
            raise ConfigurationError("the registry must cover every approved family")
        index = {policy.family.value: policy for policy in self.policies}
        object.__setattr__(self, "_index", MappingProxyType(index))
        computed = self.compute_hash()
        if not self.registry_hash:
            object.__setattr__(self, "registry_hash", computed)
            return
        if type(self.registry_hash) is not str or _HASH_TEXT.fullmatch(self.registry_hash) is None:
            raise ConfigurationError("registry_hash must be a SHA-256 digest")
        if self.registry_hash != computed:
            raise ConfigurationError("registry_hash does not match frozen manifests")

    def policy(self, family: P4SourceFamily) -> SourceFamilyPolicy:
        """Return the manifest for one exact family."""
        if type(family) is not P4SourceFamily or self._index is None:
            raise KeyError(str(family))
        policy = self._index.get(family.value)
        if policy is None:
            raise KeyError(family.value)
        return policy

    def compute_hash(self) -> str:
        """Return the domain-separated SHA-256 commitment over ordered manifests."""
        payload = [policy.wire() for policy in sorted(self.policies, key=lambda x: x.family.value)]
        canonical = json.dumps(
            payload, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return sha256(_HASH_DOMAIN + canonical).hexdigest()


_JSON: Final = ("application/json",)


def _policy(family: P4SourceFamily, **overrides: object) -> SourceFamilyPolicy:
    """Build one family's manifest from pinned inputs plus per-family overrides."""
    executable = overrides.pop("executable", True)
    reason = overrides.pop("non_executable_reason", None)
    values: dict[str, object] = {
        "family": family,
        "role": _PINNED_ROLES[family],
        "coverage": _PINNED_COVERAGE[family],
        "host": "",
        "registered_hosts": (),
        "path_template": "/",
        "endpoints": (EndpointSpec("root", "/", r"^/$"),),
        "query_allowlist": (),
        "required_query": (),
        "header_allowlist": (),
        "auth_secrets": _PINNED_AUTH[family],
        "max_request_bytes": 16_384,
        "max_response_bytes": 1_000_000,
        "max_decompressed_bytes": 4_000_000,
        "timeout_seconds": 15,
        "requests_per_window": _PINNED_RATES[family][0],
        "window_seconds": _PINNED_RATES[family][1],
        "burst_limit": _PINNED_RATES[family][2],
        "pagination_max_pages": 50,
        "allowed_media_types": _JSON,
        "schema_version": SchemaVersion("1.0.0"),
        "rights": RightsStatus.ALLOWED,
        "storage": StoragePolicy.RETAIN_METADATA_ONLY,
        "producer_version": _PRODUCER_VERSION,
        "executable": executable,
        "non_executable_reason": reason,
    }
    values.update(overrides)
    return SourceFamilyPolicy(**values)  # type: ignore[arg-type]


def _alpaca_headers() -> tuple[str, ...]:
    return ("APCA-API-KEY-ID", "APCA-API-SECRET-KEY")


def build_p4_policies() -> tuple[SourceFamilyPolicy, ...]:
    """Return every approved P4-A family manifest in closed family order."""
    alpaca_headers = _alpaca_headers()
    symbol_pattern = r"[A-Z][A-Z0-9.\-]{0,9}"
    cik_pattern = r"[0-9]{10}"
    return (
        _policy(
            P4SourceFamily.ALPACA_ASSETS,
            host="paper-api.alpaca.markets",
            path_template="/v2/assets",
            endpoints=(
                EndpointSpec("assets_list", "/v2/assets", r"^/v2/assets$"),
                EndpointSpec(
                    "asset_detail", "/v2/assets/{symbol}", rf"^/v2/assets/{symbol_pattern}$"
                ),
            ),
            query_allowlist=("status", "asset_class", "exchange"),
            header_allowlist=alpaca_headers,
            max_response_bytes=1_000_000,
            pagination_max_pages=200,
        ),
        _policy(
            P4SourceFamily.ALPACA_HISTORICAL_BARS,
            host="data.alpaca.markets",
            path_template="/v2/stocks/{symbol}/bars",
            endpoints=(
                EndpointSpec(
                    "stock_bars", "/v2/stocks/{symbol}/bars", rf"^/v2/stocks/{symbol_pattern}/bars$"
                ),
            ),
            query_allowlist=(
                "feed",
                "start",
                "end",
                "timeframe",
                "limit",
                "sort",
                "page_token",
            ),
            header_allowlist=alpaca_headers,
            max_response_bytes=2_000_000,
        ),
        _policy(
            P4SourceFamily.ALPACA_IEX_QUOTES,
            host="data.alpaca.markets",
            path_template="/v2/stocks/{symbol}/quotes/latest",
            endpoints=(
                EndpointSpec(
                    "latest_quote",
                    "/v2/stocks/{symbol}/quotes/latest",
                    rf"^/v2/stocks/{symbol_pattern}/quotes/latest$",
                ),
            ),
            query_allowlist=("feed",),
            header_allowlist=alpaca_headers,
            max_response_bytes=65_536,
            max_decompressed_bytes=262_144,
            timeout_seconds=5,
        ),
        _policy(
            P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
            host="data.alpaca.markets",
            path_template="/v1beta/corporate-actions",
            endpoints=(
                EndpointSpec(
                    "corporate_actions", "/v1beta/corporate-actions", r"^/v1beta/corporate-actions$"
                ),
            ),
            query_allowlist=(
                "types",
                "category",
                "start",
                "end",
                "limit",
                "page_token",
            ),
            header_allowlist=alpaca_headers,
            max_response_bytes=1_000_000,
        ),
        _policy(
            P4SourceFamily.SEC_EDGAR,
            host="data.sec.gov",
            path_template="/submissions/CIK{cik}.json",
            endpoints=(
                EndpointSpec(
                    "submissions",
                    "/submissions/CIK{cik}.json",
                    rf"^/submissions/CIK{cik_pattern}\.json$",
                ),
                EndpointSpec(
                    "companyfacts",
                    "/api/xbrl/companyfacts/CIK{cik}.json",
                    rf"^/api/xbrl/companyfacts/CIK{cik_pattern}\.json$",
                ),
            ),
            header_allowlist=("User-Agent",),
            max_response_bytes=4_000_000,
            max_decompressed_bytes=16_000_000,
            timeout_seconds=20,
            storage=StoragePolicy.RETAIN_RAW_EVIDENCE,
        ),
        _policy(
            P4SourceFamily.ISSUER_IR,
            path_template="/ir/{issuer_press_path}",
            endpoints=(
                EndpointSpec(
                    "issuer_press",
                    "/ir/{issuer_press_path}",
                    r"^/ir/[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$",
                ),
            ),
            registered_hosts=(),
            storage=StoragePolicy.RETAIN_METADATA_ONLY,
            executable=False,
            non_executable_reason=NonExecutableReason.NO_PINNED_EXACT_HOST,
        ),
        _policy(
            P4SourceFamily.EXCHANGE_OFFICIAL,
            registered_hosts=("www.nyse.com", "www.nasdaq.com"),
            path_template="/notice/{notice_path}",
            endpoints=(
                EndpointSpec(
                    "exchange_notice",
                    "/notice/{notice_path}",
                    r"^/notice/[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$",
                ),
            ),
            storage=StoragePolicy.RETAIN_METADATA_ONLY,
            executable=False,
            non_executable_reason=NonExecutableReason.NO_PINNED_EXACT_HOST,
        ),
        _policy(
            P4SourceFamily.FRED_ALFRED,
            host="api.stlouisfed.org",
            path_template="/fred/series/observations",
            endpoints=(
                EndpointSpec(
                    "fred_observations",
                    "/fred/series/observations",
                    r"^/fred/series/observations$",
                ),
                EndpointSpec(
                    "alfred_observations",
                    "/alfred/series/observations",
                    r"^/alfred/series/observations$",
                ),
            ),
            query_allowlist=(
                "series_id",
                "api_key",
                "file_type",
                "observation_start",
                "observation_end",
                "realtime_start",
                "realtime_end",
                "limit",
                "offset",
            ),
            required_query=("series_id", "file_type", "realtime_start", "realtime_end"),
            max_response_bytes=1_000_000,
            storage=StoragePolicy.RETAIN_RAW_EVIDENCE,
            executable=False,
            non_executable_reason=NonExecutableReason.CREDENTIAL_QUERY_NOT_PERMITTED,
        ),
        _policy(
            P4SourceFamily.TREASURY,
            host="fiscaldata.treasury.gov",
            path_template="/api/v1/{dataset}",
            endpoints=(
                EndpointSpec(
                    "fiscal_dataset", "/api/v1/{dataset}", r"^/api/v1/[a-z0-9_-]{1,80}/?$"
                ),
            ),
            query_allowlist=(
                "format",
                "limit",
                "page",
                "sort",
                "fields",
                "filter",
            ),
            required_query=("format",),
            storage=StoragePolicy.RETAIN_RAW_EVIDENCE,
        ),
        _policy(
            P4SourceFamily.BLS,
            host="api.bls.gov",
            path_template="/publicAPI/v2/timeseries/data/{series_id}",
            endpoints=(
                EndpointSpec(
                    "bls_series",
                    "/publicAPI/v2/timeseries/data/{series_id}",
                    r"^/publicAPI/v2/timeseries/data/[A-Z0-9]{1,20}$",
                ),
            ),
            query_allowlist=(
                "startyear",
                "endyear",
                "registrationkey",
                "calculations",
                "annualaverage",
            ),
            required_query=(),
            storage=StoragePolicy.RETAIN_RAW_EVIDENCE,
            executable=False,
            non_executable_reason=NonExecutableReason.CREDENTIAL_QUERY_NOT_PERMITTED,
        ),
        _policy(
            P4SourceFamily.BEA,
            host="api.bea.gov",
            path_template="/api/data",
            endpoints=(EndpointSpec("bea_data", "/api/data", r"^/api/data$"),),
            query_allowlist=(
                "UserID",
                "method",
                "datasetname",
                "TableName",
                "Frequency",
                "Year",
                "ResultFormat",
            ),
            required_query=("method", "datasetname", "ResultFormat"),
            storage=StoragePolicy.RETAIN_RAW_EVIDENCE,
            executable=False,
            non_executable_reason=NonExecutableReason.CREDENTIAL_QUERY_NOT_PERMITTED,
        ),
        _policy(
            P4SourceFamily.EIA,
            host="api.eia.gov",
            path_template="/v2/{route}",
            endpoints=(EndpointSpec("eia_route", "/v2/{route}", r"^/v2/[a-z0-9/-]{1,120}$"),),
            query_allowlist=(
                "api_key",
                "frequency",
                "data",
                "start",
                "end",
                "sort",
                "offset",
                "length",
            ),
            required_query=("frequency",),
            storage=StoragePolicy.RETAIN_RAW_EVIDENCE,
            executable=False,
            non_executable_reason=NonExecutableReason.CREDENTIAL_QUERY_NOT_PERMITTED,
        ),
        _policy(
            P4SourceFamily.TAVILY,
            host="api.tavily.com",
            path_template="/search",
            endpoints=(EndpointSpec("tavily_search", "/search", r"^/search$"),),
            header_allowlist=("Authorization", "Content-Type"),
            executable=False,
            non_executable_reason=NonExecutableReason.NON_GET_UPSTREAM,
        ),
        _policy(
            P4SourceFamily.GDELT,
            host="api.gdeltproject.org",
            path_template="/api/v2/doc/doc",
            endpoints=(EndpointSpec("gdelt_doc", "/api/v2/doc/doc", r"^/api/v2/doc/doc$"),),
            query_allowlist=("query", "mode", "format", "maxrecords", "timespan"),
            required_query=("query", "mode", "format"),
        ),
        _policy(
            P4SourceFamily.YFINANCE,
            host="query1.finance.yahoo.com",
            path_template="/v8/finance/chart/{symbol}",
            endpoints=(
                EndpointSpec(
                    "yahoo_chart",
                    "/v8/finance/chart/{symbol}",
                    rf"^/v8/finance/chart/{symbol_pattern}$",
                ),
            ),
            query_allowlist=("range", "interval"),
            required_query=("range", "interval"),
            rights=RightsStatus.UNKNOWN,
            executable=False,
            non_executable_reason=NonExecutableReason.RIGHTS_UNVERIFIED,
        ),
    )


def p4_manifest_registry() -> SourceManifestRegistry:
    """Return the canonical P4-A source manifest registry."""
    return SourceManifestRegistry(build_p4_policies(), registry_hash=P4_CANONICAL_REGISTRY_HASH)
