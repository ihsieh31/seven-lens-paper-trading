# mypy: ignore-errors
"""P4-A immutable policy config: canonical profile, wire, hash, and mutation tests."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from seven_lens.config.errors import ConfigurationError
from seven_lens.config.p4 import P4PolicyConfig
from seven_lens.domain.value_objects import SchemaVersion

_APPROVED_DECIMALS = {
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

_APPROVED_INTS = {
    "max_long_positions": 15,
    "quote_max_age_seconds": 5,
    "max_spread_bps": 30,
    "price_collar_bps": 25,
}

_APPROVED_BOOLS = {
    "single_account": True,
    "short_enabled": False,
    "submit_enabled": False,
    "zero_cost_only": True,
    "whole_shares_only": True,
    "iex_coverage_warning_mandatory": True,
}


def _all_field_names() -> list[str]:
    return [
        *_APPROVED_DECIMALS,
        *_APPROVED_INTS,
        *_APPROVED_BOOLS,
    ]


def test_canonical_profile_pins_every_approved_value() -> None:
    config = P4PolicyConfig.canonical()

    assert config.strategy_id == "seven_lens_long"
    assert config.schema_version == SchemaVersion("1.0.0")
    for name, expected in _APPROVED_DECIMALS.items():
        assert getattr(config, name) == Decimal(expected), name
    for name, expected in _APPROVED_INTS.items():
        assert getattr(config, name) == expected, name
    for name, expected in _APPROVED_BOOLS.items():
        assert getattr(config, name) is expected, name
    assert config.policy_hash == config.compute_hash()
    assert config.verify_integrity() is None or config.verify_integrity() is True


def test_canonical_profile_is_deterministic_across_calls() -> None:
    assert P4PolicyConfig.canonical() == P4PolicyConfig.canonical()
    assert P4PolicyConfig.canonical().policy_hash == P4PolicyConfig.canonical().policy_hash


def test_policy_hash_is_domain_separated_from_content() -> None:
    import hashlib
    import json

    config = P4PolicyConfig.canonical()
    wire = config.to_mapping()
    content = {key: value for key, value in wire.items() if key != "policy_hash"}
    raw_payload = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    bare_hash = hashlib.sha256(raw_payload).hexdigest()

    assert config.policy_hash != bare_hash
    assert len(config.policy_hash) == 64
    assert (
        config.policy_hash
        == hashlib.sha256(b"seven-lens.p4.policy-config.v1\x00" + raw_payload).hexdigest()
    )


def test_wire_round_trip_is_canonical_and_byte_identical() -> None:
    config = P4PolicyConfig.canonical()

    first = config.to_mapping()
    second = config.to_mapping()
    assert first == second

    rebuilt = P4PolicyConfig.from_mapping(first)
    assert rebuilt == config
    assert rebuilt.policy_hash == config.policy_hash


def test_decimal_wire_values_use_exactly_the_approved_scale() -> None:
    wire = P4PolicyConfig.canonical().to_mapping()

    assert wire["long_gross_limit"] == "0.9000"
    assert wire["minimum_adjustment_usd"] == "100.00"
    assert wire["adv_participation_limit"] == "0.0010"


@pytest.mark.parametrize("name", list(_APPROVED_DECIMALS))
def test_any_decimal_field_shifted_by_one_unit_is_rejected(name: str) -> None:
    scale = 2 if name == "minimum_adjustment_usd" else 4
    step = Decimal(1).scaleb(-scale)

    for direction in (step, -step):
        wire = P4PolicyConfig.canonical().to_mapping()
        shifted = Decimal(wire[name]) + direction
        wire[name] = format(shifted, f".{scale}f")
        with pytest.raises(ConfigurationError):
            P4PolicyConfig.from_mapping(wire)


@pytest.mark.parametrize("name", list(_APPROVED_DECIMALS))
@pytest.mark.parametrize("variant", ["0.9", "0.90000", "9E-1", " 0.9000", "+0.9000"])
def test_noncanonical_decimal_wire_text_is_rejected(name: str, variant: str) -> None:
    wire = P4PolicyConfig.canonical().to_mapping()
    wire[name] = variant

    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)


@pytest.mark.parametrize("name", list(_APPROVED_INTS))
@pytest.mark.parametrize("offset", [-1, 1])
def test_any_integer_field_shifted_by_one_is_rejected(name: str, offset: int) -> None:
    wire = P4PolicyConfig.canonical().to_mapping()
    wire[name] = _APPROVED_INTS[name] + offset

    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)


@pytest.mark.parametrize("name", list(_APPROVED_BOOLS))
def test_any_bool_field_flipped_is_rejected(name: str) -> None:
    wire = P4PolicyConfig.canonical().to_mapping()
    wire[name] = not _APPROVED_BOOLS[name]

    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)


def test_bool_as_int_and_int_as_bool_are_rejected() -> None:
    wire = P4PolicyConfig.canonical().to_mapping()
    wire["max_long_positions"] = True
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)

    wire = P4PolicyConfig.canonical().to_mapping()
    wire["short_enabled"] = 0
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)

    wire = P4PolicyConfig.canonical().to_mapping()
    wire["single_account"] = 1
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)


def test_strategy_id_is_pinned() -> None:
    wire = P4PolicyConfig.canonical().to_mapping()
    wire["strategy_id"] = "seven_lens_short"
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)

    wire = P4PolicyConfig.canonical().to_mapping()
    wire["schema_version"] = "2.0.0"
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)


def test_unknown_and_missing_fields_are_rejected() -> None:
    wire = P4PolicyConfig.canonical().to_mapping()
    wire["emergency_override"] = True
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)

    wire = P4PolicyConfig.canonical().to_mapping()
    del wire["drawdown_freeze"]
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)


def test_nan_infinity_and_negative_zero_are_rejected() -> None:
    for variant in ("NaN", "Infinity", "-Infinity"):
        wire = P4PolicyConfig.canonical().to_mapping()
        wire["name_limit"] = variant
        with pytest.raises(ConfigurationError):
            P4PolicyConfig.from_mapping(wire)

    wire = P4PolicyConfig.canonical().to_mapping()
    wire["name_limit"] = "-0.0500"
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)

    wire = P4PolicyConfig.canonical().to_mapping()
    wire["short_gross_limit"] = "-0.0000"
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)


def test_subclass_instances_are_rejected() -> None:
    class EvilDecimal(Decimal):
        pass

    class EvilInt(int):
        pass

    class EvilStr(str):
        pass

    class EvilBool(int):
        pass

    kwargs: dict[str, object] = {}
    for name, text in _APPROVED_DECIMALS.items():
        kwargs[name] = EvilDecimal(text) if name == "long_gross_limit" else Decimal(text)
    for name, value in _APPROVED_INTS.items():
        kwargs[name] = EvilInt(value) if name == "max_long_positions" else value
    for name, value in _APPROVED_BOOLS.items():
        kwargs[name] = EvilBool(value) if name == "short_enabled" else value
    kwargs["strategy_id"] = EvilStr("seven_lens_long")
    kwargs["schema_version"] = SchemaVersion("1.0.0")

    with pytest.raises(ValueError):
        P4PolicyConfig(**kwargs)  # type: ignore[arg-type]


def test_frozen_instance_cannot_be_mutated() -> None:
    config = P4PolicyConfig.canonical()

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.name_limit = Decimal("0.9900")  # type: ignore[misc]


def test_tampered_hash_or_content_is_detected_by_verify_integrity() -> None:
    config = P4PolicyConfig.canonical()

    tampered_hash = object.__new__(P4PolicyConfig)
    for field in dataclasses.fields(P4PolicyConfig):
        object.__setattr__(tampered_hash, field.name, getattr(config, field.name))
    object.__setattr__(tampered_hash, "policy_hash", "0" * 64)
    with pytest.raises(ValueError, match="policy_hash"):
        tampered_hash.verify_integrity()

    tampered_content = object.__new__(P4PolicyConfig)
    for field in dataclasses.fields(P4PolicyConfig):
        object.__setattr__(tampered_content, field.name, getattr(config, field.name))
    object.__setattr__(tampered_content, "sector_limit", Decimal("0.9900"))
    with pytest.raises(ValueError, match="policy_hash"):
        tampered_content.verify_integrity()


def test_wrong_policy_hash_on_wire_is_rejected() -> None:
    wire = P4PolicyConfig.canonical().to_mapping()
    wire["policy_hash"] = "0" * 64
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)


def test_from_mapping_rejects_non_mapping_and_non_string_values() -> None:
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(["not", "a", "mapping"])  # type: ignore[arg-type]

    wire = P4PolicyConfig.canonical().to_mapping()
    wire["name_limit"] = 0.05
    with pytest.raises(ConfigurationError):
        P4PolicyConfig.from_mapping(wire)
