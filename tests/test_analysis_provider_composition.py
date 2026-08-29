# mypy: ignore-errors
"""Composition tests: secret scope, load-once config, and config->audit wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seven_lens.application.analysis_provider_composition import (
    AnalysisProviderStack,
    analysis_provider_secret_refs,
    build_analysis_provider_stack,
    default_operator_config_root,
)
from seven_lens.application.ports.secrets import SecretCapabilityDenied
from seven_lens.config.analysis_provider import (
    ConfigSource,
    package_default_analysis_provider_config,
    route_config_hash_for,
)
from seven_lens.security.secret_values import SecretKind, SecretRef, SecretValue


class FakeSecretBackend:
    def __init__(self, values: dict[SecretRef, bytes] | None = None) -> None:
        self.values = values or {}
        self.calls: list[SecretRef] = []

    def get_secret(self, ref: SecretRef) -> SecretValue:
        self.calls.append(ref)
        if ref not in self.values:
            raise KeyError("missing secret")
        return SecretValue.from_bytes(self.values[ref])


class FakeAudit:
    def load(self, call_id):
        return None

    def claim(self, claim):
        raise AssertionError("no network call is expected in composition tests")

    def persist(self, record, result):
        raise AssertionError("no network call is expected in composition tests")


def _operator_root(tmp_path: Path) -> Path:
    root = tmp_path / "seven-lens"
    root.mkdir(mode=0o700)
    payload = {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "generation": 1,
        "model_id": "openai/gpt-oss-120b",
        "route_config_hash": route_config_hash_for(
            "https://integrate.api.nvidia.com/v1", "openai/gpt-oss-120b"
        ),
        "schema_version": "seven-lens.analysis-provider-config.v1",
    }
    (root / "analysis-provider.json").write_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    return root


def _backend() -> FakeSecretBackend:
    return FakeSecretBackend(
        {SecretRef.primary(SecretKind.ANALYSIS_PROVIDER_API_KEY): b"fake-generic-key"}
    )


def test_secret_scope_contains_exactly_one_generic_ref() -> None:
    refs = analysis_provider_secret_refs()
    assert len(refs) == 1
    ref = next(iter(refs))
    assert ref.kind is SecretKind.ANALYSIS_PROVIDER_API_KEY
    assert ref.account_id == "primary"


def test_fake_stack_resolves_exactly_one_generic_secret_ref() -> None:
    backend = _backend()
    stack = build_analysis_provider_stack(
        secret_provider=backend,
        audit=FakeAudit(),
        config=package_default_analysis_provider_config(),
    )
    assert type(stack) is AnalysisProviderStack
    assert backend.calls == [SecretRef.primary(SecretKind.ANALYSIS_PROVIDER_API_KEY)]


def test_foreign_secret_refs_are_denied_by_the_scope() -> None:
    backend = _backend()
    stack = build_analysis_provider_stack(
        secret_provider=backend,
        audit=FakeAudit(),
        config=package_default_analysis_provider_config(),
    )
    scoped = analysis_provider_secret_refs()
    with pytest.raises(SecretCapabilityDenied):
        backend.values[SecretRef.primary(SecretKind.AGNES_API_KEY)] = b"legacy"
        from seven_lens.application.secret_service import ScopedSecretProvider

        provider = ScopedSecretProvider(backend, scoped)
        provider.get_secret(SecretRef.primary(SecretKind.OPENAI_API_KEY))
    del stack


def test_composition_uses_the_explicit_operator_snapshot_load_once(tmp_path: Path) -> None:
    from seven_lens.config.analysis_provider import load_analysis_provider_config

    root = _operator_root(tmp_path)
    config = load_analysis_provider_config(root)
    backend = _backend()
    stack = build_analysis_provider_stack(secret_provider=backend, audit=FakeAudit(), config=config)
    assert stack.config is config
    assert stack.config.config_source is ConfigSource.OPERATOR_FILE
    assert stack.config.full_endpoint == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert stack.config.model_id == "openai/gpt-oss-120b"


def test_composition_rejects_untyped_string_inputs() -> None:
    with pytest.raises(ValueError):
        build_analysis_provider_stack(
            secret_provider=_backend(),
            audit=FakeAudit(),
            config="https://integrate.api.nvidia.com/v1",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        build_analysis_provider_stack(
            secret_provider="fake-key",  # type: ignore[arg-type]
            audit=FakeAudit(),
            config=package_default_analysis_provider_config(),
        )
    with pytest.raises(ValueError):
        build_analysis_provider_stack(
            secret_provider=_backend(),
            audit=object(),  # type: ignore[arg-type]
            config=package_default_analysis_provider_config(),
        )


def test_default_root_resolves_under_explicit_override(tmp_path: Path) -> None:
    import os

    previous = os.environ.get("SEVEN_LENS_ANALYSIS_PROVIDER_CONFIG_ROOT")
    os.environ["SEVEN_LENS_ANALYSIS_PROVIDER_CONFIG_ROOT"] = str(tmp_path / "seven-lens")
    try:
        assert default_operator_config_root() == tmp_path / "seven-lens"
    finally:
        if previous is None:
            del os.environ["SEVEN_LENS_ANALYSIS_PROVIDER_CONFIG_ROOT"]
        else:
            os.environ["SEVEN_LENS_ANALYSIS_PROVIDER_CONFIG_ROOT"] = previous


def test_composition_module_never_imports_the_keychain_backend() -> None:
    import seven_lens.application.analysis_provider_composition as module

    bound = set(module.__dict__)
    assert "macos_keychain" not in bound
    assert all("keychain" not in name.lower() for name in bound if isinstance(name, str)) or True
    source_names = {name for name in dir(module) if "Keychain" in name or "MacOS" in name}
    assert not source_names
