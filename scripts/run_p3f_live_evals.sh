#!/usr/bin/env bash
set -euo pipefail

script_path="${BASH_SOURCE[0]}"
if [[ "$script_path" != /* ]]; then
    script_path="$PWD/$script_path"
fi
script_dir="${script_path%/*}"
repo_root="$(cd "$script_dir/.." && pwd -P)"
cd "$repo_root"

authorization_file="${SEVEN_LENS_P3F_AUTHORIZATION_FILE:-}"
trusted_config_hash="${SEVEN_LENS_P3F_TRUSTED_CONFIG_HASH:-}"
if [[ -z "$authorization_file" || -z "$trusted_config_hash" ]]; then
    echo "ERROR: P3-F authorization file and trusted config hash are required." >&2
    exit 1
fi

fixtures="${SEVEN_LENS_P3F_FIXTURES:-tests/fixtures/p3f_evals_v14}"
if [[ "${SEVEN_LENS_P3F_LIVE:-0}" != "1" ]]; then
    exec uv run --locked python -m seven_lens.evals live-plan \
        --authorization-file "$authorization_file" \
        --trusted-config-hash "$trusted_config_hash" \
        --fixtures "$fixtures"
fi

trusted_grant_sha256="${SEVEN_LENS_P3F_TRUSTED_GRANT_SHA256:-}"
grant_file="${SEVEN_LENS_P3F_GRANT_FILE:-}"
evidence_filename="${SEVEN_LENS_P3F_EVIDENCE_FILENAME:-}"
if [[ -z "$trusted_grant_sha256" || -z "$grant_file" || -z "$evidence_filename" ]]; then
    echo "ERROR: live mode requires trusted grant hash, private grant file, and evidence filename." >&2
    exit 1
fi

uv run --locked python -m seven_lens.evals live-run \
    --authorization-file "$authorization_file" \
    --trusted-config-hash "$trusted_config_hash" \
    --trusted-grant-sha256 "$trusted_grant_sha256" \
    --grant-file "$grant_file" \
    --fixtures "$fixtures" \
    --evidence-filename "$evidence_filename" \
    --execute-live

# Retention: archive the (non-secret) authorization next to the evidence so a
# fresh acceptance can independently re-verify the grant/expiry/case-ID binding.
evidence_dir=".seven-lens-local/p3f-live-evidence"
if [[ -f "$evidence_dir/$evidence_filename" ]]; then
    archive_name="${evidence_filename%.json}"
    cp "$authorization_file" "$evidence_dir/${archive_name}.authorization.json"
    echo "Archived authorization to $evidence_dir/${archive_name}.authorization.json" >&2
fi
