"""Typed secret reference and non-disclosing value tests."""

from __future__ import annotations

import json
import logging
import pickle
from io import StringIO

import pytest

from seven_lens.observability.structured_logging import JsonFormatter, log_event
from seven_lens.security.redaction import UNSAFE_LOG_VALUE
from seven_lens.security.secret_values import (
    SecretKind,
    SecretRef,
    SecretValue,
    SecretValueError,
)

FAKE_SECRET_TEXT = "fake-p1c1-secret-00000000"
FAKE_SECRET = FAKE_SECRET_TEXT.encode()


@pytest.mark.parametrize(
    ("kind", "service"),
    [
        (
            SecretKind.ALPACA_PAPER_KEY_ID,
            "seven-lens.paper-trading.alpaca-paper.key-id",
        ),
        (
            SecretKind.ALPACA_PAPER_SECRET_KEY,
            "seven-lens.paper-trading.alpaca-paper.secret-key",
        ),
        (SecretKind.OPENAI_API_KEY, "seven-lens.paper-trading.openai.api-key"),
    ],
)
def test_primary_secret_references_have_fixed_mapping(kind: SecretKind, service: str) -> None:
    ref = SecretRef.primary(kind)

    assert ref.kind is kind
    assert ref.account_id == "primary"
    assert ref.keychain_service == service
    assert ref.keychain_account == "primary"


def test_tavily_reference_has_fixed_service_and_validated_account() -> None:
    ref = SecretRef.tavily("acct-01")

    assert ref.kind is SecretKind.TAVILY_API_KEY
    assert ref.keychain_service == "seven-lens.paper-trading.tavily.api-key"
    assert ref.keychain_account == "acct-01"


@pytest.mark.parametrize(
    "account_id",
    ["", " leading", "trailing ", "bad/account", "bad*account", "x" * 65, None, 1],
)
def test_tavily_reference_rejects_invalid_account_identifiers(account_id: object) -> None:
    with pytest.raises(ValueError, match="account identifier"):
        SecretRef.tavily(account_id)  # type: ignore[arg-type]


def test_callers_cannot_override_primary_account_or_tavily_service() -> None:
    with pytest.raises(ValueError):
        SecretRef(SecretKind.OPENAI_API_KEY, "attacker-selected")
    with pytest.raises(TypeError):
        SecretRef.tavily("acct-01", service="attacker")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        SecretRef.primary(SecretKind.TAVILY_API_KEY)


def test_secret_ref_is_runtime_sealed_against_property_overrides() -> None:
    with pytest.raises(TypeError, match="SecretRef cannot be subclassed"):
        type(
            "ForgedSecretRef",
            (SecretRef,),
            {
                "keychain_service": property(lambda self: "attacker.service"),
                "keychain_account": property(lambda self: "attacker-account"),
            },
        )


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    [
        ("_account_id", "attacker-account"),
        ("_kind", SecretKind.TAVILY_API_KEY),
        ("unexpected", "attacker-value"),
    ],
)
def test_secret_ref_rejects_all_attribute_assignment_after_creation(
    attribute: str,
    replacement: object,
) -> None:
    ref = SecretRef.primary(SecretKind.OPENAI_API_KEY)

    with pytest.raises(AttributeError, match="SecretRef is immutable"):
        setattr(ref, attribute, replacement)

    assert ref.kind is SecretKind.OPENAI_API_KEY
    assert ref.account_id == "primary"


@pytest.mark.parametrize("size", [1, 4_096])
def test_secret_value_accepts_exact_byte_boundaries(size: int) -> None:
    value = SecretValue.from_bytes(b"x" * size)

    assert value.reveal_text() == "x" * size


@pytest.mark.parametrize(
    "untrusted",
    [
        b"",
        b" ",
        b"  fake",
        b"fake  ",
        b"x" * 4_097,
        b"\xff",
        b"fake\x00secret",
        b"fake\rsecret",
        b"fake\nsecret",
        bytearray(b"fake"),
    ],
)
def test_secret_value_rejects_malformed_input_without_echoing_it(untrusted: object) -> None:
    with pytest.raises(SecretValueError) as captured:
        SecretValue.from_bytes(untrusted)  # type: ignore[arg-type]

    assert FAKE_SECRET_TEXT not in str(captured.value)
    assert "fake" not in str(captured.value)


def test_secret_value_string_repr_and_pickle_never_disclose_plaintext() -> None:
    value = SecretValue.from_bytes(FAKE_SECRET)

    assert str(value) == "[REDACTED_SECRET_VALUE]"
    assert repr(value) == "[REDACTED_SECRET_VALUE]"
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(value)


def test_nested_secret_value_is_unsafe_not_stringified_by_structured_logging() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("seven_lens.tests.secret_value")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        value = SecretValue.from_bytes(FAKE_SECRET)
        log_event(logger, "secret_boundary_test", nested={"value": value})
        serialized = stream.getvalue()
        event = json.loads(serialized)

        assert FAKE_SECRET_TEXT not in serialized
        assert event["fields"]["nested"]["value"] == UNSAFE_LOG_VALUE
    finally:
        logger.removeHandler(handler)
        handler.close()
