"""Immutable P4 policy profile; approved values cannot drift through runtime inputs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Final

from seven_lens.config.errors import ConfigurationError
from seven_lens.domain.value_objects import SchemaVersion

_P4_SCHEMA_VERSION: Final = "1.0.0"
_P4_STRATEGY_ID: Final = "seven_lens_long"
_HASH_DOMAIN: Final = b"seven-lens.p4.policy-config.v1\x00"
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")

_DECIMAL_SCALE: Final[Mapping[str, int]] = {
    "long_gross_limit": 4,
    "short_gross_limit": 4,
    "total_gross_limit": 4,
    "cash_buffer_minimum": 4,
    "name_limit": 4,
    "sector_limit": 4,
    "cluster_limit": 4,
    "normal_turnover_limit": 4,
    "adv_participation_limit": 4,
    "daily_loss_stop": 4,
    "drawdown_freeze": 4,
    "minimum_adjustment_usd": 2,
    "minimum_adjustment_nav_fraction": 4,
    "rebalance_band": 4,
}
_DECIMAL_WIRE: Final[Mapping[str, str]] = {
    "long_gross_limit": "0.9000",
    "short_gross_limit": "0.0000",
    "total_gross_limit": "0.9000",
    "cash_buffer_minimum": "0.1000",
    "name_limit": "0.0500",
    "sector_limit": "0.2500",
    "cluster_limit": "0.3000",
    "normal_turnover_limit": "0.2000",
    "adv_participation_limit": "0.0010",
    "daily_loss_stop": "0.0100",
    "drawdown_freeze": "0.0800",
    "minimum_adjustment_usd": "100.00",
    "minimum_adjustment_nav_fraction": "0.0025",
    "rebalance_band": "0.0050",
}
_INT_WIRE: Final[Mapping[str, int]] = {
    "max_long_positions": 15,
    "quote_max_age_seconds": 5,
    "max_spread_bps": 30,
    "price_collar_bps": 25,
}
_INT_BOUNDS: Final[Mapping[str, tuple[int, int]]] = {
    "max_long_positions": (1, 10_000),
    "quote_max_age_seconds": (1, 86_400),
    "max_spread_bps": (1, 10_000),
    "price_collar_bps": (1, 10_000),
}
_BOOL_WIRE: Final[Mapping[str, bool]] = {
    "single_account": True,
    "short_enabled": False,
    "submit_enabled": False,
    "zero_cost_only": True,
    "whole_shares_only": True,
    "iex_coverage_warning_mandatory": True,
}
_DECIMAL_TEXT: Final = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{2,4}$")


def _content_mapping() -> dict[str, str | int | bool]:
    """Return the canonical wire content of the approved profile (no derived hash)."""
    content: dict[str, str | int | bool] = {
        "schema_version": _P4_SCHEMA_VERSION,
        "strategy_id": _P4_STRATEGY_ID,
    }
    for name in _DECIMAL_SCALE:
        content[name] = _DECIMAL_WIRE[name]
    for name in _INT_WIRE:
        content[name] = _INT_WIRE[name]
    for name in _BOOL_WIRE:
        content[name] = _BOOL_WIRE[name]
    return content


def _decimal_text(value: Decimal, scale: int) -> str:
    return format(value, f".{scale}f")


@dataclass(frozen=True, slots=True)
class P4PolicyConfig:
    """The single approved P4 profile; every field is pinned, never environment-tuned."""

    schema_version: SchemaVersion
    strategy_id: str
    long_gross_limit: Decimal
    short_gross_limit: Decimal
    total_gross_limit: Decimal
    cash_buffer_minimum: Decimal
    name_limit: Decimal
    sector_limit: Decimal
    cluster_limit: Decimal
    normal_turnover_limit: Decimal
    adv_participation_limit: Decimal
    daily_loss_stop: Decimal
    drawdown_freeze: Decimal
    minimum_adjustment_usd: Decimal
    minimum_adjustment_nav_fraction: Decimal
    rebalance_band: Decimal
    max_long_positions: int
    quote_max_age_seconds: int
    max_spread_bps: int
    price_collar_bps: int
    single_account: bool
    short_enabled: bool
    submit_enabled: bool
    zero_cost_only: bool
    whole_shares_only: bool
    iex_coverage_warning_mandatory: bool
    policy_hash: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not SchemaVersion:
            raise ConfigurationError("schema_version requires an exact SchemaVersion")
        if self.schema_version != SchemaVersion(_P4_SCHEMA_VERSION):
            raise ConfigurationError("P4 policy schema version is pinned")
        if type(self.strategy_id) is not str or self.strategy_id != _P4_STRATEGY_ID:
            raise ConfigurationError("P4 strategy_id is pinned to seven_lens_long")
        for name, scale in _DECIMAL_SCALE.items():
            self._validate_decimal(name, scale)
        for name, (minimum, maximum) in _INT_BOUNDS.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ConfigurationError(f"{name} requires an exact bounded integer")
            if value != _INT_WIRE[name]:
                raise ConfigurationError(f"{name} is pinned by the approved P4 profile")
        for name in _BOOL_WIRE:
            value = getattr(self, name)
            if type(value) is not bool or value is not _BOOL_WIRE[name]:
                raise ConfigurationError(f"{name} is pinned by the approved P4 profile")
        if self.short_gross_limit != Decimal("0.0000"):
            raise ConfigurationError("P4 short exposure is fixed at zero")
        if self.cash_buffer_minimum + self.long_gross_limit > Decimal("1.0000"):
            raise ConfigurationError("cash buffer and long gross exceed full deployment")
        if self.drawdown_freeze <= self.daily_loss_stop:
            raise ConfigurationError("drawdown freeze must exceed the daily loss stop")
        computed = self.compute_hash()
        if not self.policy_hash:
            object.__setattr__(self, "policy_hash", computed)
            return
        if type(self.policy_hash) is not str or _HASH_TEXT.fullmatch(self.policy_hash) is None:
            raise ConfigurationError("policy_hash must be a SHA-256 digest")
        if self.policy_hash != computed:
            raise ConfigurationError("policy_hash does not match frozen content")

    def _validate_decimal(self, name: str, scale: int) -> None:
        value = getattr(self, name)
        if type(value) is not Decimal:
            raise ConfigurationError(f"{name} requires an exact Decimal")
        if not value.is_finite():
            raise ConfigurationError(f"{name} must be finite")
        if value.is_zero() and value.is_signed():
            raise ConfigurationError(f"{name} must not use negative zero")
        if value.as_tuple().exponent != -scale:
            raise ConfigurationError(f"{name} must use exactly {scale} decimal places")
        if _DECIMAL_TEXT.fullmatch(_decimal_text(value, scale)) is None:
            raise ConfigurationError(f"{name} must use canonical fixed-scale text")
        if value != Decimal(_DECIMAL_WIRE[name]):
            raise ConfigurationError(f"{name} is pinned by the approved P4 profile")

    @classmethod
    def canonical(cls) -> P4PolicyConfig:
        """Return the single approved P4 profile instance."""
        content = _content_mapping()
        values: dict[str, object] = {
            "schema_version": SchemaVersion(str(content["schema_version"])),
            "strategy_id": content["strategy_id"],
        }
        for name in _DECIMAL_SCALE:
            values[name] = Decimal(str(content[name]))
        for name in _INT_WIRE:
            values[name] = content[name]
        for name in _BOOL_WIRE:
            values[name] = content[name]
        return cls(**values)  # type: ignore[arg-type]

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> P4PolicyConfig:
        """Parse the exact wire schema, rejecting any drift from the approved profile."""
        if not isinstance(values, Mapping):
            raise ConfigurationError("P4 policy wire must be a mapping")
        expected = set(_content_mapping()) | {"policy_hash"}
        if set(values) != expected:
            missing = sorted(expected - set(values))
            extra = sorted(set(values) - expected)
            raise ConfigurationError(
                f"invalid P4 policy wire fields: missing={missing}, extra={extra}"
            )
        kwargs: dict[str, object] = {
            "schema_version": _parse_schema_version(values["schema_version"]),
            "strategy_id": _parse_strategy_id(values["strategy_id"]),
        }
        for name, scale in _DECIMAL_SCALE.items():
            kwargs[name] = _parse_decimal_wire(name, values[name], scale)
        for name in _INT_WIRE:
            raw = values[name]
            if type(raw) is not int:
                raise ConfigurationError(f"{name} requires an exact integer")
            kwargs[name] = raw
        for name in _BOOL_WIRE:
            raw = values[name]
            if type(raw) is not bool:
                raise ConfigurationError(f"{name} requires an exact bool")
            kwargs[name] = raw
        raw_hash = values["policy_hash"]
        if type(raw_hash) is not str or _HASH_TEXT.fullmatch(raw_hash) is None:
            raise ConfigurationError("policy_hash must be a SHA-256 digest")
        kwargs["policy_hash"] = raw_hash
        return cls(**kwargs)  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, str | int | bool]:
        """Return the canonical wire form including the derived policy hash."""
        wire: dict[str, str | int | bool] = _content_mapping()
        for name, scale in _DECIMAL_SCALE.items():
            wire[name] = _decimal_text(getattr(self, name), scale)
        wire["policy_hash"] = self.policy_hash
        return wire

    def content_mapping(self) -> dict[str, str | int | bool]:
        """Return the canonical wire content without the derived policy hash."""
        return {key: value for key, value in self.to_mapping().items() if key != "policy_hash"}

    def compute_hash(self) -> str:
        """Return the domain-separated SHA-256 commitment over the frozen content."""
        canonical = json.dumps(
            self.content_mapping(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return sha256(_HASH_DOMAIN + canonical).hexdigest()

    def verify_integrity(self) -> bool:
        """Re-derive the hash commitment; raise when content or hash was tampered with."""
        if self.policy_hash != self.compute_hash():
            raise ValueError("policy_hash does not match frozen content")
        return True


def _parse_schema_version(raw: object) -> SchemaVersion:
    if type(raw) is not str:
        raise ConfigurationError("schema_version must be a string")
    try:
        return SchemaVersion(raw)
    except ValueError as error:
        raise ConfigurationError("schema_version must be MAJOR.MINOR.PATCH") from error


def _parse_strategy_id(raw: object) -> str:
    if type(raw) is not str:
        raise ConfigurationError("strategy_id must be a string")
    return raw


def _parse_decimal_wire(name: str, raw: object, scale: int) -> Decimal:
    if type(raw) is not str:
        raise ConfigurationError(f"{name} must be a canonical decimal string")
    digits = re.compile(rf"^(?:0|[1-9][0-9]*)\.[0-9]{{{scale}}}$")
    if digits.fullmatch(raw) is None:
        raise ConfigurationError(f"{name} must use exactly {scale} decimal places")
    parsed = Decimal(raw)
    if parsed.is_zero() and parsed.is_signed():
        raise ConfigurationError(f"{name} must not use negative zero")
    if not parsed.is_finite():
        raise ConfigurationError(f"{name} must be finite")
    return parsed
