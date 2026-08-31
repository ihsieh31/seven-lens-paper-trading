# mypy: ignore-errors
"""P1-C3 workflow, PostgreSQL gate, and shell-script acceptance tests."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_p1.sh"
POSTGRES_SCRIPT = REPO_ROOT / "scripts" / "run_postgres_integration.sh"
CONFTEST = REPO_ROOT / "tests" / "conftest.py"

FAKE_PSYCOPG_16 = """
class Result:
    def fetchone(self):
        return ("160015",)

class Connection:
    def execute(self, query):
        return Result()

    def close(self):
        return None

def connect(database_url, connect_timeout=5):
    return Connection()
"""


def _run_gate_project(
    tmp_path: Path,
    *,
    environment: dict[str, str],
    test_source: str = "def test_placeholder():\n    assert True\n",
    psycopg_source: str | None = None,
) -> subprocess.CompletedProcess[str]:
    project = tmp_path / "gate-project"
    project.mkdir()
    shutil.copy2(CONFTEST, project / "conftest.py")
    (project / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    integration: synthetic integration test\n",
        encoding="utf-8",
    )
    (project / "test_gate.py").write_text(test_source, encoding="utf-8")
    if psycopg_source is not None:
        (project / "psycopg.py").write_text(psycopg_source, encoding="utf-8")

    process_environment = os.environ.copy()
    process_environment.pop("REQUIRE_POSTGRES_INTEGRATION", None)
    process_environment.pop("TEST_DATABASE_URL", None)
    process_environment.update(environment)
    process_environment["PYTHONPATH"] = str(project)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_gate.py"],
        cwd=project,
        env=process_environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _run_bash(
    script: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_environment = os.environ.copy()
    if environment is not None:
        process_environment.update(environment)
    return subprocess.run(
        ["/bin/bash", str(script), *arguments],
        cwd=REPO_ROOT.parent,
        env=process_environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_integration_marker_and_psycopg_dependency_are_static() -> None:
    with (REPO_ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)

    markers = project["tool"]["pytest"]["ini_options"]["markers"]
    dependencies = project["project"]["dependencies"]
    assert any(marker.startswith("integration:") for marker in markers)
    assert any(dependency.startswith("psycopg[binary]") for dependency in dependencies)
    for integration_module in (REPO_ROOT / "tests" / "integration").glob("test_*.py"):
        assert "pytest.importorskip" not in integration_module.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("environment", "expected_message"),
    [
        (
            {"REQUIRE_POSTGRES_INTEGRATION": "1"},
            "PostgreSQL integration gate requires TEST_DATABASE_URL.",
        ),
        (
            {
                "REQUIRE_POSTGRES_INTEGRATION": "1",
                "TEST_DATABASE_URL": "sqlite:///synthetic.db",
            },
            "PostgreSQL integration gate requires a PostgreSQL URL.",
        ),
    ],
)
def test_required_gate_rejects_missing_or_non_postgres_url(
    tmp_path: Path,
    environment: dict[str, str],
    expected_message: str,
) -> None:
    result = _run_gate_project(tmp_path, environment=environment)

    assert result.returncode != 0
    assert expected_message in result.stderr
    assert "sqlite:///synthetic.db" not in result.stderr


def test_required_gate_rejects_missing_psycopg_with_bounded_error(tmp_path: Path) -> None:
    result = _run_gate_project(
        tmp_path,
        environment={
            "REQUIRE_POSTGRES_INTEGRATION": "1",
            "TEST_DATABASE_URL": "postgresql://fake:fake@127.0.0.1:1/fake",
        },
        psycopg_source='raise ModuleNotFoundError("synthetic missing driver detail")\n',
    )

    assert result.returncode != 0
    assert "PostgreSQL integration gate requires the psycopg dependency." in result.stderr
    assert "synthetic missing driver detail" not in result.stderr


def test_required_gate_rejects_connection_failure_without_dsn(tmp_path: Path) -> None:
    result = _run_gate_project(
        tmp_path,
        environment={
            "REQUIRE_POSTGRES_INTEGRATION": "1",
            "TEST_DATABASE_URL": "postgresql://fake:fake-password@127.0.0.1:1/fake",
        },
        psycopg_source='def connect(*args, **kwargs):\n    raise RuntimeError("unsafe detail")\n',
    )

    assert result.returncode != 0
    assert "PostgreSQL integration gate could not connect to PostgreSQL." in result.stderr
    assert "fake-password" not in result.stderr
    assert "unsafe detail" not in result.stderr


def test_required_gate_rejects_non_16_server(tmp_path: Path) -> None:
    result = _run_gate_project(
        tmp_path,
        environment={
            "REQUIRE_POSTGRES_INTEGRATION": "1",
            "TEST_DATABASE_URL": "postgresql://fake:fake@127.0.0.1:1/fake",
        },
        psycopg_source=FAKE_PSYCOPG_16.replace("160015", "170011"),
    )

    assert result.returncode != 0
    assert "PostgreSQL integration gate requires PostgreSQL major version 16." in result.stderr


def test_required_gate_turns_integration_skip_into_failure(tmp_path: Path) -> None:
    result = _run_gate_project(
        tmp_path,
        environment={
            "REQUIRE_POSTGRES_INTEGRATION": "1",
            "TEST_DATABASE_URL": "postgresql://fake:fake@127.0.0.1:1/fake",
        },
        test_source=(
            "import pytest\n\n"
            "@pytest.mark.integration\n"
            "@pytest.mark.skip(reason='synthetic skip')\n"
            "def test_skipped_integration():\n"
            "    pass\n"
        ),
        psycopg_source=FAKE_PSYCOPG_16,
    )

    assert result.returncode != 0
    assert "PostgreSQL integration gate forbids skipped integration tests." in result.stdout


def test_ordinary_non_integration_run_does_not_require_database(tmp_path: Path) -> None:
    result = _run_gate_project(tmp_path, environment={})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_shell_scripts_exist_and_are_executable() -> None:
    for script in (VERIFY_SCRIPT, POSTGRES_SCRIPT):
        mode = script.stat().st_mode
        assert stat.S_ISREG(mode)
        assert mode & stat.S_IXUSR


def test_verify_script_rejects_unknown_argument_before_bootstrap() -> None:
    result = _run_bash(VERIFY_SCRIPT, "--unknown", environment={"PATH": ""})

    assert result.returncode == 2
    assert result.stderr == "ERROR: expected no arguments or exactly --postgres.\n"


def test_missing_uv_and_docker_have_bounded_prerequisite_errors() -> None:
    missing_uv = _run_bash(VERIFY_SCRIPT, environment={"PATH": ""})
    missing_docker = _run_bash(POSTGRES_SCRIPT, environment={"PATH": ""})

    assert missing_uv.returncode != 0
    assert missing_uv.stderr == (
        "ERROR: uv is required; install it from the official uv documentation and retry.\n"
    )
    assert missing_docker.returncode != 0
    assert missing_docker.stderr == (
        "ERROR: Docker is required for disposable PostgreSQL integration testing.\n"
    )


def test_failure_cleanup_uses_only_verified_exact_container_identity(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_state = tmp_path / "state"
    fake_bin.mkdir()
    fake_state.mkdir()
    docker_log = fake_state / "docker.log"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
container_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
command_name="${1:-}"
shift || true
case "$command_name" in
    info)
        exit 0
        ;;
    run)
        while (( $# )); do
            case "$1" in
                --name)
                    printf '%s' "$2" > "$FAKE_DOCKER_STATE/name"
                    shift 2
                    ;;
                --label)
                    printf '%s' "${2#*=}" > "$FAKE_DOCKER_STATE/label"
                    shift 2
                    ;;
                *)
                    shift
                    ;;
            esac
        done
        printf '%s\\n' "$container_id"
        ;;
    port)
        printf '%s\\n' 'unsafe-port-output'
        ;;
    inspect)
        all_args="$*"
        if [[ "$all_args" == *'.Id'* ]]; then
            printf '%s\\n' "$container_id"
        elif [[ "$all_args" == *'.Name'* ]]; then
            printf '/%s\\n' "$(<"$FAKE_DOCKER_STATE/name")"
        else
            printf '%s\\n' "$(<"$FAKE_DOCKER_STATE/label")"
        fi
        ;;
    rm)
        exit 0
        ;;
    *)
        exit 1
        ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/bash\n"
        'if [[ "$*" == *"python -c"* ]]; then\n'
        "    printf '%s\\n' '55432'\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    result = _run_bash(
        POSTGRES_SCRIPT,
        environment={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "FAKE_DOCKER_LOG": str(docker_log),
            "FAKE_DOCKER_STATE": str(fake_state),
        },
    )

    assert result.returncode != 0
    log_lines = docker_log.read_text(encoding="utf-8").splitlines()
    expected_id = "a" * 64
    assert f"rm --force --volumes {expected_id}" in log_lines
    removal_lines = [line for line in log_lines if line.startswith("rm ")]
    assert removal_lines == [f"rm --force --volumes {expected_id}"]


def test_scripts_exclude_unsafe_sources_and_destructive_commands() -> None:
    combined = "\n".join(
        script.read_text(encoding="utf-8").lower() for script in (VERIFY_SCRIPT, POSTGRES_SCRIPT)
    )
    for forbidden in (
        "keychain",
        ".env",
        "api key",
        "broker",
        "docker prune",
        "curl",
        "wget",
    ):
        assert forbidden not in combined
    assert '--publish "127.0.0.1:${host_port}:5432"' in combined
    assert "--mount type=volume,destination=/var/lib/postgresql/data" in combined
    assert 'docker rm --force --volumes "$candidate_id"' in combined


def test_workflow_is_two_job_read_only_zero_secret_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    jobs_section = workflow.split("\njobs:\n", maxsplit=1)[1]
    job_names = re.findall(r"^  ([a-z][a-z0-9-]+):$", jobs_section, flags=re.MULTILINE)

    assert job_names == ["quality-unit", "postgres-integration"]
    assert workflow.count("runs-on: ubuntu-24.04") == 2
    assert "macos-" not in workflow.lower()
    assert "permissions:\n  contents: read" in workflow
    assert ": write" not in workflow
    assert "secrets." not in workflow
    assert "pull_request_target" not in workflow
    assert "persist-credentials: false" in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow


def test_workflow_actions_and_postgres_image_are_immutable() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    action_references = re.findall(r"^\s*uses: ([^@\s]+)@([^\s]+)$", workflow, re.MULTILINE)

    assert action_references
    assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for _, reference in action_references)
    assert "actions/checkout v7.0.1" in workflow
    assert "actions/setup-python v7.0.0" in workflow
    assert "astral-sh/setup-uv v10.0.1" in workflow
    assert 'version: "0.12.5"' in workflow
    assert (
        "postgres:16.15-alpine@sha256:"
        "ab5c955e9e57ae9879d4411ab49a912be9d162455676f7bf56e951b11ac73785"
    ) in workflow


def test_workflow_commands_match_p1_c3_contract() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    required_commands = (
        "uv sync --python 3.13 --locked",
        "uv lock --check",
        "uv run --locked ruff format --check .",
        "uv run --locked ruff check .",
        "uv run --locked mypy",
        'uv run --locked pytest -m "not integration" -ra --tb=short',
        'uv run --locked pytest tests/integration -m "integration and not live" -ra --tb=short',
    )

    assert all(command in workflow for command in required_commands)
    assert 'REQUIRE_POSTGRES_INTEGRATION: "1"' in workflow
    assert "pg_isready" in workflow
    assert "SHOW server_version_num" in workflow
    assert "enable-cache: true" in workflow
    assert ".venv" not in workflow


def test_postgres_integration_job_excludes_live_marker() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'pytest tests/integration -m "integration and not live" -ra --tb=short' in workflow
    assert "SEVEN_LENS_P2E_LIVE" not in workflow
