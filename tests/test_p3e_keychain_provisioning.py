"""Static safety contract for interactive Agnes Keychain provisioning."""

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
