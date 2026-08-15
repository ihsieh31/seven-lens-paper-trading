#!/usr/bin/env bash
set -euo pipefail

run_postgres=false
if (( $# == 1 )) && [[ "$1" == "--postgres" ]]; then
    run_postgres=true
elif (( $# != 0 )); then
    echo "ERROR: expected no arguments or exactly --postgres." >&2
    exit 2
fi

script_path="${BASH_SOURCE[0]}"
if [[ "$script_path" != /* ]]; then
    script_path="$PWD/$script_path"
fi
script_dir="${script_path%/*}"
repo_root="$(cd "$script_dir/.." && pwd -P)"
cd "$repo_root"

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is required; install it from the official uv documentation and retry." >&2
    exit 1
fi

uv sync --python 3.13 --locked
uv lock --check --offline
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest -m "not integration" -ra --tb=short

if [[ "$run_postgres" == true ]]; then
    "$repo_root/scripts/run_postgres_integration.sh"
fi
