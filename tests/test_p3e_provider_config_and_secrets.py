# mypy: ignore-errors
"""P3-E exact Agnes route and sealed research-secret boundary tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from fakes.secrets import FakeSecretProvider
from fakes.telemetry import (
    FakeDiagnosticSink,
    FakeMetricRecorder,
    FakeTraceRecorder,
    FixedMonotonicClock,
    FixedSpanIdFactory,
    fixed_context,
)
from seven_lens.application.p3e_composition import (
    build_agnes_provider_stack,
    research_provider_secret_refs,
)
from seven_lens.application.secret_service import InstrumentedSecretProvider
from seven_lens.config.errors import ConfigurationError
from seven_lens.config.provider import (
    ApiFlavor,
    ProviderKind,
    ProviderLogicalRole,
    ReasoningEffective,
    ReasoningRequested,
    agnes_25_flash_config,
)
from seven_lens.observability.failsafe import FailSafeTelemetry
from seven_lens.security.secret_values import SecretKind, SecretRef, SecretValue
from test_p3e_model_invoker import FakeAuditPort

FAKE_AGNES_SECRET = SecretValue.from_bytes(b"fake-agnes-api-key-for-p3e-tests")


def _valid_mapping() -> dict[str, object]:
    return {
        "provider_kind": "AGNES",
        "api_flavor": "CHAT_COMPLETIONS",
        "scheme": "https",
        "host": "apihub.agnes-ai.com",
        "path": "/v1/chat/completions",
        "model_id": "agnes-2.5-flash",
        "connect_timeout_ms": 2_000,
        "read_timeout_ms": 30_000,
        "total_timeout_ms": 45_000,
        "request_byte_cap": 131_072,
        "response_byte_cap": 131_072,
        "max_output_tokens": 8_192,
        "temperature": 0.0,
        "reasoning_requested": "MAX",
        "reasoning_effective": "UNKNOWN",
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
        "policy_id": "p3e-agnes-2.5-flash-only-v1",
    }


def test_agnes_config_is_one_exact_frozen_route_for_every_logical_role() -> None:
    config = agnes_25_flash_config(_valid_mapping())

    assert config.provider_kind is ProviderKind.AGNES
    assert config.api_flavor is ApiFlavor.CHAT_COMPLETIONS
    assert config.endpoint == "https://apihub.agnes-ai.com/v1/chat/completions"
    assert config.model_id == "agnes-2.5-flash"
    assert config.temperature == 0.0
    assert config.reasoning_requested is ReasoningRequested.MAX
    assert config.reasoning_effective is ReasoningEffective.UNKNOWN
    assert config.roles == tuple(ProviderLogicalRole)
    assert config.fallback_model_id is None
    assert config.fallback_attempts == 0
    assert all(
        not flag
        for flag in (
            config.stream,
            config.tools,
            config.state,
            config.files,
            config.follow_redirects,
            config.trust_env,
            config.proxy,
            config.automatic_retry,
        )
    )
    with pytest.raises(FrozenInstanceError):
        config.model_id = "attacker-model"  # type: ignore[misc]


@pytest.mark.parametrize("field", tuple(_valid_mapping()))
def test_agnes_config_rejects_every_missing_field(field: str) -> None:
    values = _valid_mapping()
    del values[field]

    with pytest.raises(ConfigurationError, match="provider configuration fields"):
        agnes_25_flash_config(values)


def test_agnes_config_rejects_unknown_field() -> None:
    values = _valid_mapping()
    values["base_url_override"] = "https://attacker.invalid"

    with pytest.raises(ConfigurationError, match="provider configuration fields"):
        agnes_25_flash_config(values)


def test_package_owned_policy_constants_cannot_be_mutated_into_an_override() -> None:
    from seven_lens.config import provider

    with pytest.raises(TypeError):
        provider._EXPECTED_VALUES["host"] = "attacker.invalid"  # type: ignore[index]
    assert agnes_25_flash_config().host == "apihub.agnes-ai.com"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_kind", "OPENAI"),
        ("api_flavor", "RESPONSES"),
        ("scheme", "http"),
        ("host", "apihub.agnes-ai.com:443"),
        ("host", "user@apihub.agnes-ai.com"),
        ("host", "attacker.invalid"),
        ("path", "/v1/../chat/completions"),
        ("path", "/v1/chat/completions?debug=1"),
        ("path", "/v1/chat/completions#fragment"),
        ("model_id", "agnes-2.0-flash"),
        ("connect_timeout_ms", True),
        ("read_timeout_ms", 30_001),
        ("total_timeout_ms", 0),
        ("request_byte_cap", False),
        ("response_byte_cap", 1),
        ("max_output_tokens", True),
        ("temperature", 0),
        ("temperature", 0.1),
        ("reasoning_requested", "UNKNOWN"),
        ("reasoning_effective", "MAX"),
        ("stream", True),
        ("tools", True),
        ("state", True),
        ("files", True),
        ("follow_redirects", True),
        ("trust_env", True),
        ("proxy", True),
        ("automatic_retry", True),
        ("fallback_model_id", "agnes-2.0-flash"),
        ("fallback_attempts", 1),
        ("policy_id", "caller-selected-policy"),
    ],
)
def test_agnes_config_rejects_route_model_capability_and_numeric_overrides(
    field: str, value: object
) -> None:
    values = _valid_mapping()
    values[field] = value

    with pytest.raises(ConfigurationError):
        agnes_25_flash_config(values)


def test_agnes_ref_has_one_exact_primary_keychain_identity() -> None:
    ref = SecretRef.primary(SecretKind.AGNES_API_KEY)

    assert ref.kind is SecretKind.AGNES_API_KEY
    assert ref.account_id == "primary"
    assert ref.keychain_service == "seven-lens.paper-trading.agnes.api-key"
    assert ref.keychain_account == "primary"
    assert "seven-lens.paper-trading.agnes.api-key" not in repr(ref)


def test_research_scope_contains_only_agnes_and_denies_all_foreign_secrets() -> None:
    agnes_ref = SecretRef.primary(SecretKind.AGNES_API_KEY)
    refs = research_provider_secret_refs()
    assert refs == frozenset({agnes_ref})

    foreign_refs = (
        SecretRef.primary(SecretKind.ALPACA_PAPER_KEY_ID),
        SecretRef.primary(SecretKind.ALPACA_PAPER_SECRET_KEY),
        SecretRef.primary(SecretKind.POSTGRES_RUNTIME_PASSWORD),
        SecretRef.primary(SecretKind.OPENAI_API_KEY),
        SecretRef.tavily("acct-01"),
    )
    backend = FakeSecretProvider(
        {agnes_ref: FAKE_AGNES_SECRET, **{ref: FAKE_AGNES_SECRET for ref in foreign_refs}}
    )
    stack = build_agnes_provider_stack(secret_provider=backend, audit=FakeAuditPort())

    assert backend.calls == [agnes_ref]
    for capability in ("config", "transport", "invoker", "api_key", "secret_provider"):
        assert not hasattr(stack, capability)


def test_composition_exports_no_raw_credential_resolution_capability() -> None:
    from seven_lens.application import p3e_composition

    assert not hasattr(p3e_composition, "AgnesCredentials")
    assert not hasattr(p3e_composition, "resolve_agnes_credentials")
    assert not hasattr(p3e_composition, "build_research_secret_provider")


def test_composition_constructs_the_exact_ref_and_accepts_no_forged_ref_argument() -> None:
    genuine = SecretRef.primary(SecretKind.AGNES_API_KEY)
    forged = SecretRef.primary(SecretKind.AGNES_API_KEY)
    object.__setattr__(forged, "_account_id", "attacker")
    backend = FakeSecretProvider({genuine: FAKE_AGNES_SECRET})

    build_agnes_provider_stack(secret_provider=backend, audit=FakeAuditPort())

    assert backend.calls == [genuine]


def test_agnes_lookup_telemetry_records_only_bounded_kind_and_outcome() -> None:
    ref = SecretRef.primary(SecretKind.AGNES_API_KEY)
    metrics = FakeMetricRecorder()
    traces = FakeTraceRecorder()
    telemetry = FailSafeTelemetry(
        metrics,
        traces,
        FakeDiagnosticSink(),
        monotonic_clock=FixedMonotonicClock((5.0, 5.01)),
        span_id_factory=FixedSpanIdFactory(),
    )
    provider = InstrumentedSecretProvider(
        FakeSecretProvider({ref: FAKE_AGNES_SECRET}), telemetry, fixed_context()
    )

    assert provider.get_secret(ref) is FAKE_AGNES_SECRET
    attributes = {
        attribute.key.value: attribute.value
        for point in metrics.points
        for attribute in point.attributes
    }
    assert attributes == {"secret_kind": "agnes_api_key", "outcome": "success"}
    evidence = repr((metrics.points, traces.starts, traces.ends))
    assert "fake-agnes-api-key" not in evidence
    assert "seven-lens.paper-trading.agnes.api-key" not in evidence
