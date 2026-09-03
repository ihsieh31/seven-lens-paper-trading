"""Subprocess black-box tests for the evals operator CLI (``python -m seven_lens.evals``)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CLI_MODULE = "seven_lens.evals"
FIXTURES = Path(__file__).parent / "fixtures" / "p3f_evals_v12"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", CLI_MODULE, *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_offline_missing_fixtures_fails_cleanly(tmp_path: Path) -> None:
    bogus = tmp_path / "no-such-corpus"
    process = _run(
        "offline", "--fixtures", str(bogus), "--frozen-report", str(tmp_path / "report.json")
    )
    assert process.returncode == 1
    assert "Traceback" not in process.stderr
    assert str(bogus) not in process.stderr + process.stdout
    assert "eval corpus root does not exist" in process.stderr


def test_offline_missing_frozen_report_fails_cleanly(tmp_path: Path) -> None:
    missing = tmp_path / "no-report.json"
    process = _run("offline", "--fixtures", str(FIXTURES), "--frozen-report", str(missing))
    assert process.returncode == 1
    assert "Traceback" not in process.stderr
    assert str(missing) not in process.stderr + process.stdout
    assert "frozen eval report is missing or unreadable" in process.stderr


def test_live_plan_missing_authorization_file_fails_cleanly(tmp_path: Path) -> None:
    missing = tmp_path / "authorization.json"
    process = _run(
        "live-plan",
        "--authorization-file",
        str(missing),
        "--trusted-config-hash",
        "0" * 64,
        "--fixtures",
        str(FIXTURES),
    )
    assert process.returncode == 1
    assert "Traceback" not in process.stderr
    assert str(missing) not in process.stderr + process.stdout
    assert process.stderr.startswith("evals: ")


def test_live_plan_rejects_invalid_authorization_bytes_fails_cleanly(tmp_path: Path) -> None:
    authorization_file = tmp_path / "authorization.json"
    authorization_file.write_bytes(b"{not strict json")
    process = _run(
        "live-plan",
        "--authorization-file",
        str(authorization_file),
        "--trusted-config-hash",
        "0" * 64,
        "--fixtures",
        str(FIXTURES),
    )
    assert process.returncode == 1
    assert "Traceback" not in process.stderr
    assert process.stderr.startswith("evals: ")
