#!/usr/bin/env bash
set -euo pipefail

script_path="${BASH_SOURCE[0]}"
if [[ "$script_path" != /* ]]; then
    script_path="$PWD/$script_path"
fi
script_dir="${script_path%/*}"
repo_root="$(cd "$script_dir/.." && pwd -P)"
cd "$repo_root"

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: Docker is required for disposable PostgreSQL integration testing." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker is unavailable for disposable PostgreSQL integration testing." >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is required; install it from the official uv documentation and retry." >&2
    exit 1
fi

postgres_image="postgres:16.15-alpine@sha256:ab5c955e9e57ae9879d4411ab49a912be9d162455676f7bf56e951b11ac73785"
postgres_user="seven_lens_ci"
# Test-only password; callers may replace it with another disposable value.
postgres_password="${SEVEN_LENS_TEST_POSTGRES_PASSWORD:-seven-lens-disposable-test-only}"
postgres_database="seven_lens_p1"
container_name="seven-lens-p1c3-postgres-$$-${RANDOM}"
owner_token="${container_name}-owner"
owner_label="seven-lens.p1c3.owner"
container_id=""
host_port="$(uv run --locked python -c \
    'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
if [[ ! "$host_port" =~ ^[0-9]+$ ]] || (( 10#$host_port < 1 || 10#$host_port > 65535 )); then
    echo "ERROR: could not reserve a valid localhost PostgreSQL port." >&2
    exit 1
fi

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
            --format '{{ index .Config.Labels "seven-lens.p1c3.owner" }}' \
            "$candidate_id" 2>/dev/null || true)"
        if [[ "$actual_id" == "$candidate_id" \
            && "$actual_name" == "/$container_name" \
            && "$actual_label" == "$owner_token" ]]; then
            if ! docker rm --force --volumes "$candidate_id" >/dev/null 2>&1; then
                echo "ERROR: disposable PostgreSQL container cleanup failed." >&2
                if (( original_status == 0 )); then
                    original_status=1
                fi
            fi
        elif (( original_status == 0 )); then
            echo "ERROR: disposable PostgreSQL container identity check failed." >&2
            original_status=1
        fi
    elif [[ -n "$candidate_id" ]] && (( original_status == 0 )); then
        echo "ERROR: disposable PostgreSQL container identity is invalid." >&2
        original_status=1
    fi
    exit "$original_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# WAL-heavy integration runs must not consume the Docker VM's RAM via tmpfs.
container_id="$(docker run --detach \
    --name "$container_name" \
    --label "$owner_label=$owner_token" \
    --env "POSTGRES_USER=$postgres_user" \
    --env "POSTGRES_PASSWORD=$postgres_password" \
    --env "POSTGRES_DB=$postgres_database" \
    --publish "127.0.0.1:${host_port}:5432" \
    --mount type=volume,destination=/var/lib/postgresql/data \
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
observed_host_port="${BASH_REMATCH[1]}"
if (( 10#$observed_host_port < 1 || 10#$observed_host_port > 65535 )); then
    echo "ERROR: Docker returned an invalid localhost PostgreSQL port." >&2
    exit 1
fi
if [[ "$observed_host_port" != "$host_port" ]]; then
    echo "ERROR: Docker did not retain the reserved localhost PostgreSQL port." >&2
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

server_version_num=""
for (( attempt = 1; attempt <= 30; attempt++ )); do
    server_version_num="$(docker exec "$container_id" \
        psql -U "$postgres_user" -d "$postgres_database" \
        -Atqc 'SHOW server_version_num' 2>/dev/null || true)"
    if [[ "$server_version_num" =~ ^[0-9]+$ ]]; then
        break
    fi
    sleep 1
done
if [[ ! "$server_version_num" =~ ^[0-9]+$ ]]; then
    echo "ERROR: PostgreSQL integration gate could not read the server version." >&2
    exit 1
fi
if (( 10#$server_version_num / 10000 != 16 )); then
    echo "ERROR: PostgreSQL integration gate requires PostgreSQL major version 16." >&2
    exit 1
fi

TEST_DATABASE_URL="postgresql://${postgres_user}:${postgres_password}@127.0.0.1:${host_port}/${postgres_database}" \
REQUIRE_POSTGRES_INTEGRATION=1 \
SEVEN_LENS_TEST_POSTGRES_CONTAINER_ID="$container_id" \
SEVEN_LENS_TEST_POSTGRES_CONTAINER_NAME="$container_name" \
SEVEN_LENS_TEST_POSTGRES_OWNER_TOKEN="$owner_token" \
    uv run --locked pytest tests/integration -m "integration and not live" -ra --tb=short
