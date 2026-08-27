#!/usr/bin/env bash
set -euo pipefail

script_path="${BASH_SOURCE[0]}"
if [[ "$script_path" != /* ]]; then
    script_path="$PWD/$script_path"
fi
script_dir="${script_path%/*}"
repo_root="$(cd "$script_dir/.." && pwd -P)"
cd "$repo_root"

if [[ "${SEVEN_LENS_P3E_LIVE:-}" != "1" ]]; then
    echo "ERROR: set SEVEN_LENS_P3E_LIVE=1 only for the approved Agnes live gate." >&2
    exit 1
fi
if [[ "${SEVEN_LENS_P3E_KEY_ROTATED:-}" != "1" ]]; then
    echo "ERROR: confirm rotation with SEVEN_LENS_P3E_KEY_ROTATED=1 before any POST." >&2
    exit 1
fi
if [[ "${SEVEN_LENS_P3E_REQUEST_LIMIT:-}" != "6" ]]; then
    echo "ERROR: P3-E live acceptance requires SEVEN_LENS_P3E_REQUEST_LIMIT=6." >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker is required for disposable PostgreSQL live acceptance." >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is required for P3-E live acceptance." >&2
    exit 1
fi

postgres_image="postgres:16.15-alpine@sha256:ab5c955e9e57ae9879d4411ab49a912be9d162455676f7bf56e951b11ac73785"
postgres_user="seven_lens_p3e_owner"
# Test-only password; callers may replace it with another disposable value.
postgres_password="${SEVEN_LENS_TEST_POSTGRES_PASSWORD:-seven-lens-disposable-test-only}"
postgres_database="seven_lens_p3e"
container_name="seven-lens-p3e-postgres-$$-${RANDOM}"
owner_token="${container_name}-owner"
owner_label="seven-lens.p3e.owner"
container_id=""

cleanup() {
    local original_status=$?
    local candidate_id="${container_id:-}"
    local actual_id=""
    local actual_label=""
    local actual_name=""

    trap - EXIT INT TERM
    if [[ -z "$candidate_id" ]]; then
        candidate_id="$(docker inspect --format '{{.Id}}' "$container_name" 2>/dev/null || true)"
    fi
    if [[ "$candidate_id" =~ ^[0-9a-f]{64}$ ]]; then
        actual_id="$(docker inspect --format '{{.Id}}' "$candidate_id" 2>/dev/null || true)"
        actual_name="$(docker inspect --format '{{.Name}}' "$candidate_id" 2>/dev/null || true)"
        actual_label="$(docker inspect \
            --format '{{ index .Config.Labels "seven-lens.p3e.owner" }}' \
            "$candidate_id" 2>/dev/null || true)"
        if [[ "$actual_id" == "$candidate_id" \
            && "$actual_name" == "/$container_name" \
            && "$actual_label" == "$owner_token" ]]; then
            if ! docker rm --force --volumes "$candidate_id" >/dev/null 2>&1; then
                echo "ERROR: disposable PostgreSQL cleanup failed." >&2
                if (( original_status == 0 )); then
                    original_status=1
                fi
            fi
        elif (( original_status == 0 )); then
            echo "ERROR: disposable PostgreSQL identity check failed." >&2
            original_status=1
        fi
    elif [[ -n "$candidate_id" ]] && (( original_status == 0 )); then
        echo "ERROR: disposable PostgreSQL identity is invalid." >&2
        original_status=1
    fi
    exit "$original_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

container_id="$(docker run --detach \
    --name "$container_name" \
    --label "$owner_label=$owner_token" \
    --env "POSTGRES_USER=$postgres_user" \
    --env "POSTGRES_PASSWORD=$postgres_password" \
    --env "POSTGRES_DB=$postgres_database" \
    --publish 127.0.0.1::5432 \
    --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=512m \
    "$postgres_image")"

if [[ ! "$container_id" =~ ^[0-9a-f]{64}$ ]]; then
    echo "ERROR: Docker returned an invalid disposable container identity." >&2
    exit 1
fi

port_binding="$(docker port "$container_id" 5432/tcp)"
if [[ ! "$port_binding" =~ ^127\.0\.0\.1:([0-9]{1,5})$ ]]; then
    echo "ERROR: Docker returned an invalid localhost PostgreSQL port." >&2
    exit 1
fi
host_port="${BASH_REMATCH[1]}"
if (( 10#$host_port < 1 || 10#$host_port > 65535 )); then
    echo "ERROR: Docker returned an invalid localhost PostgreSQL port." >&2
    exit 1
fi

ready=false
for (( attempt = 1; attempt <= 60; attempt++ )); do
    if docker exec "$container_id" \
        pg_isready -U "$postgres_user" -d "$postgres_database" >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    echo "ERROR: disposable PostgreSQL did not become ready within 60 seconds." >&2
    exit 1
fi

server_version_num="$(docker exec "$container_id" \
    psql -U "$postgres_user" -d "$postgres_database" -Atqc 'SHOW server_version_num')"
if [[ ! "$server_version_num" =~ ^[0-9]+$ ]] \
    || (( 10#$server_version_num / 10000 != 16 )); then
    echo "ERROR: P3-E live acceptance requires PostgreSQL major version 16." >&2
    exit 1
fi

TEST_DATABASE_URL="postgresql://${postgres_user}:${postgres_password}@127.0.0.1:${host_port}/${postgres_database}" \
REQUIRE_POSTGRES_INTEGRATION=1 \
SEVEN_LENS_P3E_LIVE=1 \
SEVEN_LENS_P3E_KEY_ROTATED=1 \
SEVEN_LENS_P3E_REQUEST_LIMIT=6 \
    uv run --locked python scripts/run_p3e_live_pytest.py
