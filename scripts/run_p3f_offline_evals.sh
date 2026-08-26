#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
fixture_dir="$repo_dir/tests/fixtures/p3f_evals_v12"

cd "$repo_dir"
exec uv run python -m seven_lens.evals offline \
  --fixtures "$fixture_dir" \
  --frozen-report "$fixture_dir/reports/offline-scripted-v12.json"
