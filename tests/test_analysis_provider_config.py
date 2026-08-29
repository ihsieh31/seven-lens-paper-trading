"""Strict schema/hash/store tests for the generic analysis provider config."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from seven_lens.config.analysis_provider import (
    LEGACY_ROUTE_CONFIG_HASH,
    PACKAGE_DEFAULT_BASE_URL,
    PACKAGE_DEFAULT_MODEL_ID,
    AnalysisProviderConfig,
    ConfigSource,
    canonical_base_url,
    canonical_model_id,
    endpoint_policy_id_for,
    load_analysis_provider_config,
    package_default_analysis_provider_config,
    route_config_hash_for,
    validate_production_root,
)
from seven_lens.config.errors import ConfigurationError

_FILE_NAME = "analysis-provider.json"


def _write_operator(root: Path, *, base_url: str, model_id: str, generation: int = 1) -> bytes:
    payload = {
        "base_url": base_url,
        "generation": generation,
        "model_id": model_id,
        "route_config_hash": route_config_hash_for(base_url, model_id),
        "schema_version": "seven-lens.analysis-provider-config.v1",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    root.mkdir(mode=0o700, exist_ok=True)
    (root / _FILE_NAME).write_bytes(encoded)
    return encoded


def _root(tmp_path: Path) -> Path:
    directory = tmp_path / "seven-lens"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def test_package_default_is_the_legacy_agnes_route_at_generation_zero() -> None:
    config = package_default_analysis_provider_config()
    assert type(config) is AnalysisProviderConfig
    assert config.config_source is ConfigSource.PACKAGE_DEFAULT
    assert config.generation == 0
    assert config.base_url == PACKAGE_DEFAULT_BASE_URL
    assert config.model_id == PACKAGE_DEFAULT_MODEL_ID
    assert config.api_flavor == "CHAT_COMPLETIONS"
    assert config.provider_kind == "OPENAI_COMPATIBLE"
    assert config.scheme == "https"
    assert config.host == "apihub.agnes-ai.com"
    assert config.base_path == "/v1"
    assert config.full_endpoint == "https://apihub.agnes-ai.com/v1/chat/completions"
    assert config.route_config_hash == route_config_hash_for(
        PACKAGE_DEFAULT_BASE_URL, PACKAGE_DEFAULT_MODEL_ID
    )
    assert config.endpoint_policy_id == f"analysis-route-v1:{config.route_config_hash}"


def test_legacy_hash_is_deterministic_over_canonical_agnes_material() -> None:
    material = {
        "api_flavor": "CHAT_COMPLETIONS",
        "automatic_retry": False,
        "base_url": PACKAGE_DEFAULT_BASE_URL,
        "connect_timeout_ms": 2_000,
        "fallback_attempts": 0,
        "fallback_model_id": None,
        "files": False,
        "follow_redirects": False,
        "max_output_tokens": 8_192,
        "model_id": PACKAGE_DEFAULT_MODEL_ID,
        "policy_schema": "seven-lens.analysis-route-policy.v1",
        "provider_kind": "OPENAI_COMPATIBLE",
        "proxy": False,
        "read_timeout_ms": 180_000,
        "request_byte_cap": 131_072,
        "response_byte_cap": 131_072,
        "state": False,
        "stream": False,
        "temperature": 0.0,
        "tools": False,
        "total_timeout_ms": 180_000,
        "trust_env": False,
    }
    encoded = json.dumps(
        material, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == LEGACY_ROUTE_CONFIG_HASH
    assert LEGACY_ROUTE_CONFIG_HASH == LEGACY_ROUTE_CONFIG_HASH  # constant across imports


def test_route_hash_excludes_generation_path_and_time() -> None:
    first = route_config_hash_for("https://integrate.api.nvidia.com/v1", "openai/gpt-oss-120b")
    second = route_config_hash_for("https://integrate.api.nvidia.com/v1", "openai/gpt-oss-120b")
    assert first == second
    assert len(first) == 64
    assert first == first.lower()
    assert all(character in "0123456789abcdef" for character in first)


def test_endpoint_policy_id_binds_the_exact_hash() -> None:
    digest = route_config_hash_for("https://integrate.api.nvidia.com/v1", "openai/gpt-oss-120b")
    assert endpoint_policy_id_for(digest) == f"analysis-route-v1:{digest}"


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("https://integrate.api.nvidia.com/v1", "https://integrate.api.nvidia.com/v1"),
        ("https://INTEGRATE.API.NVIDIA.COM/v1", "https://integrate.api.nvidia.com/v1"),
        ("https://integrate.api.nvidia.com:443/v1", "https://integrate.api.nvidia.com/v1"),
        ("https://integrate.api.nvidia.com/v1/", "https://integrate.api.nvidia.com/v1"),
        ("https://integrate.api.nvidia.com", "https://integrate.api.nvidia.com"),
    ],
)
def test_base_url_canonicalization(raw: str, canonical: str) -> None:
    assert canonical_base_url(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "http://integrate.api.nvidia.com/v1",
        "https://user:pass@integrate.api.nvidia.com/v1",
        "https://integrate.api.nvidia.com/v1?x=1",
        "https://integrate.api.nvidia.com/v1#frag",
        "https:///v1",
        "https://integrate.api.nvidia.com./v1",
        "https://integrate.api.nvidia.com:8443/v1",
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://10.0.0.1/v1",
        "https://localhost/v1",
        "https://box.local/v1",
        "https://integrate.api.nvidia.com/v1%2Fchat",
        "https://integrate.api.nvidia.com/v1/..",
        "https://integrate.api.nvidia.com/v1//chat",
        "https://integrate.api.nvidia.com/v1/chat/completions",
        "https://integrate.api.nvidia.com/v1/Chat/Completions",
        "https://integrate.api.nvidia.com/v1 chat",
        "https://integrate.api.nvidia.com/v1\t",
        " https://integrate.api.nvidia.com/v1",
        "https://integrate.api.nvidia.com/v\u200b1",
        "",
        "ftp://integrate.api.nvidia.com/v1",
        "https://integrate.api.nvidia.com/" + "a" * 300,
        "https://" + "a" * 64 + ".com/v1",
    ],
)
def test_base_url_rejections(raw: str) -> None:
    with pytest.raises(ConfigurationError):
        canonical_base_url(raw)


def test_model_id_accepts_bounded_ids_including_one_slash() -> None:
    assert canonical_model_id("openai/gpt-oss-120b") == "openai/gpt-oss-120b"
    assert canonical_model_id("vendor/model-x") == "vendor/model-x"
    assert canonical_model_id("a" * 128) == "a" * 128


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " openai/gpt-oss-120b",
        "openai/gpt-oss-120b ",
        "model with spaces",
        "model\nwith-control",
        "deep\\seek",
        "deep%20seek",
        "deep?seek",
        "deep#seek",
        "deep@seek",
        "a//b",
        "/leading",
        "trailing/",
        "./",
        "../model",
        "model/..",
        ".",
        "..",
        "a" * 129,
        "models/openai/gpt-oss-120b",
        "deeп-seek",
        "deep\x00seek",
    ],
)
def test_model_id_rejections(raw: str) -> None:
    with pytest.raises(ConfigurationError):
        canonical_model_id(raw)


def test_operator_file_loads_as_an_exact_generic_config(tmp_path: Path) -> None:
    root = _root(tmp_path)
    encoded = _write_operator(
        root, base_url="https://integrate.api.nvidia.com/v1", model_id="openai/gpt-oss-120b"
    )
    config = load_analysis_provider_config(root)
    assert config.config_source is ConfigSource.OPERATOR_FILE
    assert config.generation == 1
    assert config.base_url == "https://integrate.api.nvidia.com/v1"
    assert config.full_endpoint == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert config.model_id == "openai/gpt-oss-120b"
    assert config.endpoint_policy_id == f"analysis-route-v1:{config.route_config_hash}"
    assert (root / _FILE_NAME).read_bytes() == encoded


def test_missing_operator_file_falls_back_to_package_default(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = load_analysis_provider_config(root)
    assert config.config_source is ConfigSource.PACKAGE_DEFAULT
    assert config.generation == 0


def test_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write_operator(
        root, base_url="https://integrate.api.nvidia.com/v1", model_id="openai/gpt-oss-120b"
    )
    path = root / _FILE_NAME
    payload = json.loads(path.read_bytes())
    payload["model_id"] = "other-model"
    path.write_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(root)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update({"extra": 1}),
        lambda p: p.pop("route_config_hash"),
        lambda p: p.pop("base_url"),
        lambda p: p.update({"schema_version": "seven-lens.analysis-provider-config.v2"}),
        lambda p: p.update({"generation": True}),
        lambda p: p.update({"generation": 0}),
        lambda p: p.update({"generation": 2**63}),
        lambda p: p.update({"generation": "1"}),
        lambda p: p.update({"generation": 1.0}),
        lambda p: p.update({"base_url": "http://integrate.api.nvidia.com/v1"}),
        lambda p: p.update({"model_id": "bad model"}),
        lambda p: p.update({"route_config_hash": "z" * 64}),
    ],
)
def test_operator_field_violations_fail_closed(
    tmp_path: Path, mutate: Callable[[dict[str, object]], None]
) -> None:
    root = _root(tmp_path)
    _write_operator(
        root, base_url="https://integrate.api.nvidia.com/v1", model_id="openai/gpt-oss-120b"
    )
    path = root / _FILE_NAME
    payload = json.loads(path.read_bytes())
    mutate(payload)
    path.write_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(root)


@pytest.mark.parametrize(
    "encoded",
    [
        b'{"base_url": "https://integrate.api.nvidia.com/v1", '
        b'"base_url": "https://integrate.api.nvidia.com/v1", '
        b'"generation": 1, "model_id": "openai/gpt-oss-120b", '
        b'"route_config_hash": "'
        + b"0" * 64
        + b'", "schema_version": "seven-lens.analysis-provider-config.v1"}',
        b'{"base_url": "https://integrate.api.nvidia.com/v1", "generation": NaN, "model_id": "m", '
        b'"route_config_hash": "' + b"0" * 64 + b'", "schema_version": "x"}',
    ],
)
def test_duplicate_keys_and_nan_are_rejected(tmp_path: Path, encoded: bytes) -> None:
    root = _root(tmp_path)
    (root / _FILE_NAME).write_bytes(encoded)
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(root)


def test_bom_and_non_utf8_are_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    path = root / _FILE_NAME
    valid = json.dumps(
        {
            "base_url": "https://integrate.api.nvidia.com/v1",
            "generation": 1,
            "model_id": "openai/gpt-oss-120b",
            "route_config_hash": route_config_hash_for(
                "https://integrate.api.nvidia.com/v1", "openai/gpt-oss-120b"
            ),
            "schema_version": "seven-lens.analysis-provider-config.v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(b"\xef\xbb\xbf" + valid)
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(root)
    path.write_bytes(valid[:-1] + b"\xff")
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(root)


def test_oversize_operator_file_is_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    path = root / _FILE_NAME
    path.write_bytes(b'{"pad": "' + b"x" * 200_000 + b'"}')
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(root)


def test_group_writable_file_and_directory_are_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    directory = root
    os.chmod(directory, 0o775)
    path = directory / _FILE_NAME
    path.write_bytes(b"{}")
    os.chmod(path, 0o666)
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(root)
    os.chmod(path, 0o600)
    os.chmod(directory, 0o777)
    os.chmod(directory, 0o700)
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(root)


def test_symlinked_file_or_root_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real-root"
    real.mkdir(mode=0o700)
    link = tmp_path / "xdg-link"
    link.symlink_to(real)
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(link)
    _write_operator(
        real, base_url="https://integrate.api.nvidia.com/v1", model_id="openai/gpt-oss-120b"
    )
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(link)


def test_fifo_is_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    os.mkfifo(root / _FILE_NAME)
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(root)


def test_corrupt_operator_file_never_falls_back_to_package_default(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / _FILE_NAME).write_bytes(b"{not json")
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(root)


def test_relative_root_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        load_analysis_provider_config(Path("relative-root"))


def test_config_post_init_revalidates_and_rejects_drift() -> None:
    config = package_default_analysis_provider_config()
    assert (
        AnalysisProviderConfig(
            config_source=config.config_source,
            generation=config.generation,
            base_url=config.base_url,
            model_id=config.model_id,
        )
        == config
    )
    tampered = package_default_analysis_provider_config()
    object.__setattr__(tampered, "route_config_hash", "0" * 64)
    with pytest.raises(ConfigurationError):
        tampered.__post_init__()


def test_runtime_config_is_frozen_and_slotted() -> None:
    from dataclasses import FrozenInstanceError

    config = package_default_analysis_provider_config()
    with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
        config.model_id = "changed"  # type: ignore[misc]
    assert not hasattr(config, "__dict__")


def test_config_module_pulls_in_no_network_or_keychain_surface() -> None:
    import seven_lens.config.analysis_provider as module

    bound = set(module.__dict__)
    assert "socket" not in bound
    assert "ssl" not in bound
    assert "http" not in bound
    assert "hashlib" in bound  # route hash needs stdlib hashing only


def test_stat_regular_gate_uses_lstat_not_follow(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = tmp_path / "elsewhere"
    target.mkdir(mode=0o700)
    _write_operator(
        target, base_url="https://integrate.api.nvidia.com/v1", model_id="openai/gpt-oss-120b"
    )
    os.replace(target / _FILE_NAME, root / _FILE_NAME)
    mode = (root / _FILE_NAME).stat().st_mode
    assert stat.S_ISREG(mode)
    config = load_analysis_provider_config(root)
    assert config.config_source is ConfigSource.OPERATOR_FILE


def test_missing_root_directory_yields_package_default(tmp_path: Path) -> None:
    config = load_analysis_provider_config(tmp_path / "does-not-exist")
    assert config.config_source is ConfigSource.PACKAGE_DEFAULT


def test_validate_production_root_rejects_symlink_components(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ConfigurationError, match="root is invalid"):
        validate_production_root(link)
    with pytest.raises(ConfigurationError, match="root is invalid"):
        validate_production_root(link / "seven-lens")
    clean = real / "seven-lens"
    assert validate_production_root(clean) == clean
    assert validate_production_root(clean / "not-yet-created") == clean / "not-yet-created"
