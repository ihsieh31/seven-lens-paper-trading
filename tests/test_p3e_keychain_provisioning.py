"""Static safety contracts for interactive analysis-provider provisioning."""

from pathlib import Path


def test_agnes_provisioning_never_accepts_secret_argv_env_or_file() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "provision_agnes_keychain.sh").read_text(
        encoding="utf-8"
    )

    assert "exec /usr/bin/security add-generic-password" in script
    assert "-a primary" in script
    assert "-s seven-lens.paper-trading.agnes.api-key" in script
    assert script.rstrip().endswith("-w")
    assert "$1" not in script
    assert "read " not in script
    assert "API_KEY=" not in script
    assert ">" not in script.replace(">&2", "")


def test_analysis_provider_provisioning_trusts_only_locked_python_runtime() -> None:
    script = (
        Path(__file__).parents[1] / "scripts" / "provision_analysis_provider_keychain.sh"
    ).read_text(encoding="utf-8")

    assert "uv run --locked python" in script
    assert 'python_executable="$(' in script
    assert 'python_app_executable="$(' in script
    assert "exec /usr/bin/security add-generic-password" in script
    assert "-a primary" in script
    assert "-s seven-lens.paper-trading.analysis-provider.api-key" in script
    assert '-T "$python_executable"' in script
    assert '-T "$python_app_executable"' in script
    assert script.rstrip().endswith("-w")
    assert "-A" not in script
    assert "$1" not in script
    assert "read " not in script
    assert "API_KEY=" not in script
    redirects_removed = script.replace(">&2", "").replace(">/dev/null", "").replace("2>&1", "")
    assert ">" not in redirects_removed
