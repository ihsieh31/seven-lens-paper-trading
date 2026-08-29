"""Subprocess black-box tests for the two operator CLI commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ENDPOINT = "https://integrate.api.nvidia.com/v1"
MODEL = "openai/gpt-oss-120b"
SET_ENDPOINT = ("set-endpoint", ENDPOINT)
SET_MODEL = ("set-model", MODEL)
CLI_MODULE = "seven_lens.cli.analysis_provider"
_ROOT_ENV = "SEVEN_LENS_ANALYSIS_PROVIDER_CONFIG_ROOT"
_FILE = "analysis-provider.json"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env[_ROOT_ENV] = str(root)
    return subprocess.run(
        [sys.executable, "-m", CLI_MODULE, *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _root(tmp_path: Path) -> Path:
    directory = tmp_path / "seven-lens"
    directory.mkdir(mode=0o700)
    return directory


def _summary(process: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert process.returncode == 0, process.stderr
    lines = process.stdout.splitlines()
    assert len(lines) == 1, process.stdout
    payload: dict[str, object] = json.loads(lines[0])
    assert process.stdout.endswith("\n")
    return payload


def test_exact_two_commands_persist_the_exact_nvidia_route(tmp_path: Path) -> None:
    root = _root(tmp_path)
    endpoint_summary = _summary(_run(root, *SET_ENDPOINT))
    assert endpoint_summary["changed"] is True
    assert endpoint_summary["config_source"] == "OPERATOR_FILE"
    assert endpoint_summary["base_url"] == ENDPOINT
    assert endpoint_summary["full_endpoint"] == f"{ENDPOINT}/chat/completions"
    assert endpoint_summary["model_id"] == "agnes-2.5-flash"
    assert endpoint_summary["restart_required"] is True
    model_summary = _summary(_run(root, *SET_MODEL))
    assert model_summary["changed"] is True
    assert model_summary["model_id"] == MODEL
    stored = json.loads((root / _FILE).read_text())
    assert stored["base_url"] == ENDPOINT
    assert stored["model_id"] == MODEL
    assert stored["schema_version"] == "seven-lens.analysis-provider-config.v1"
    assert stored["generation"] == 2
    assert len(stored["route_config_hash"]) == 64


def test_either_command_may_run_first(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _summary(_run(root, *SET_MODEL))
    _summary(_run(root, *SET_ENDPOINT))
    stored = json.loads((root / _FILE).read_text())
    assert stored["base_url"] == ENDPOINT and stored["model_id"] == MODEL


def test_same_value_does_not_bump_generation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _summary(_run(root, *SET_ENDPOINT))
    _summary(_run(root, *SET_MODEL))
    repeat = _summary(_run(root, *SET_MODEL))
    assert repeat["changed"] is False
    assert repeat["generation"] == 2
    stored = json.loads((root / _FILE).read_text())
    assert stored["generation"] == 2


def test_other_field_is_byte_stable_across_a_set(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _summary(_run(root, *SET_ENDPOINT))
    before = json.loads((root / _FILE).read_text())
    _summary(_run(root, *SET_MODEL))
    after = json.loads((root / _FILE).read_text())
    assert before["base_url"] == after["base_url"]
    assert before["route_config_hash"] != after["route_config_hash"]


def test_invalid_endpoint_fails_with_fixed_output_and_keeps_old_bytes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _summary(_run(root, *SET_ENDPOINT))
    original = (root / _FILE).read_bytes()
    process = _run(root, "set-endpoint", "http://evil.example/v1")
    assert process.returncode != 0
    assert "evil.example" not in process.stdout + process.stderr
    assert (root / _FILE).read_bytes() == original


def test_invalid_model_fails_without_writing(tmp_path: Path) -> None:
    root = _root(tmp_path)
    process = _run(root, "set-model", "../traversal")
    assert process.returncode != 0
    assert not (root / _FILE).exists()
    assert "traversal" not in process.stdout + process.stderr


def test_help_writes_nothing_and_show_does_not_create_files(tmp_path: Path) -> None:
    root = _root(tmp_path)
    help_process = _run(root, "--help")
    assert help_process.returncode == 0
    assert not (root / _FILE).exists()
    show = _summary(_run(root, "show"))
    assert show["config_source"] == "PACKAGE_DEFAULT"
    assert show["generation"] == 0
    assert not (root / _FILE).exists()


def test_usage_error_uses_a_fixed_nonzero_exit(tmp_path: Path) -> None:
    root = _root(tmp_path)
    missing_value = _run(root, "set-endpoint")
    assert missing_value.returncode != 0
    unknown = _run(root, "revoke-all")
    assert unknown.returncode != 0
    assert not (root / _FILE).exists()


def test_corrupt_existing_file_is_never_overwritten_silently(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / _FILE).write_bytes(b"{corrupt")
    process = _run(root, *SET_MODEL)
    assert process.returncode != 0
    assert (root / _FILE).read_bytes() == b"{corrupt"


def test_success_stdout_never_contains_home_or_temp_paths(tmp_path: Path) -> None:
    root = _root(tmp_path)
    process = _run(root, *SET_ENDPOINT)
    summary = _summary(process)
    assert str(tmp_path) not in process.stdout
    assert os.path.expanduser("~") not in process.stdout
    assert set(summary) == {
        "base_url",
        "changed",
        "config_source",
        "full_endpoint",
        "generation",
        "model_id",
        "restart_required",
        "route_config_hash",
    }


def test_lost_update_race_is_serialized_by_the_lock(tmp_path: Path) -> None:
    root = _root(tmp_path)
    env = dict(os.environ)
    env[_ROOT_ENV] = str(root)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, os, sys, time\n"
                "path = sys.argv[1]\n"
                "fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                "sys.stdout.write('locked\\n')\n"
                "sys.stdout.flush()\n"
                "time.sleep(20)\n"
            ),
            str(root / (_FILE + ".lock")),
        ],
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline() == "locked\n"
        writer = subprocess.Popen(
            [sys.executable, "-m", CLI_MODULE, *SET_ENDPOINT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert writer.poll() is None  # blocked on the exclusive lock, not writing
        holder.kill()
        holder.wait()
        out, err = writer.communicate(timeout=60)
        assert writer.returncode == 0, err
        payload = json.loads(out.strip())
        assert payload["changed"] is True
        stored = json.loads((root / _FILE).read_text())
        assert stored["base_url"] == ENDPOINT
        assert stored["generation"] == 1
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()


def test_concurrent_writers_both_succeed_without_lost_update(tmp_path: Path) -> None:
    root = _root(tmp_path)
    env = dict(os.environ)
    env[_ROOT_ENV] = str(root)
    barrier = root / "barrier"
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import os, subprocess, sys\n"
                    "barrier = sys.argv[1]\n"
                    "fd = os.open(barrier, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)\n"
                    "os.write(fd, b'x')\n"
                    "os.close(fd)\n"
                    "while os.stat(barrier).st_size < 2:\n"
                    "    pass\n"
                    "raise SystemExit(subprocess.call([sys.executable, '-m', '"
                    + CLI_MODULE
                    + "', 'set-model', '"
                    + MODEL
                    + "']))\n"
                ),
                str(barrier),
            ],
            env=env,
        )
        for _ in range(2)
    ]
    codes = [process.wait(timeout=120) for process in processes]
    assert codes == [0, 0]
    stored = json.loads((root / _FILE).read_text())
    assert stored["model_id"] == MODEL
    assert stored["generation"] == 1
    assert len(stored["route_config_hash"]) == 64


def test_cli_never_opens_a_network_socket(tmp_path: Path) -> None:
    root = _root(tmp_path)
    env = dict(os.environ)
    env[_ROOT_ENV] = str(root)
    guard = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import socket\n"
                "def _explode(*args, **kwargs):\n"
                "    raise AssertionError('network socket opened by CLI')\n"
                "socket.socket = _explode\n"
                "socket.create_connection = _explode\n"
                "from seven_lens.cli.analysis_provider import main\n"
                "raise SystemExit(main(['set-endpoint', '" + ENDPOINT + "']))\n"
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert guard.returncode == 0, guard.stderr
