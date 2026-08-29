#!/bin/sh
# Provision the generic analysis-provider API key without placing it in argv,
# environment variables, or a file.  The operator is prompted by the OS.
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "Analysis provider credential provisioning requires macOS Keychain." >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to resolve the locked production Python runtime." >&2
    exit 1
fi

python_executable="$(uv run --locked python -c 'import os, sys; print(os.path.realpath(sys.executable))')"
if [ -z "$python_executable" ] || [ ! -x "$python_executable" ]; then
    echo "Could not resolve an executable production Python runtime." >&2
    exit 1
fi
python_app_executable="$(uv run --locked python -c 'import pathlib, sys; print(pathlib.Path(sys.executable).resolve().parent.parent / "Resources/Python.app/Contents/MacOS/Python")')"
if [ -z "$python_app_executable" ] || [ ! -x "$python_app_executable" ]; then
    echo "Could not resolve the executable macOS Python application runtime." >&2
    exit 1
fi

echo "Enter the analysis provider API key at the protected Keychain prompt." >&2
exec /usr/bin/security add-generic-password \
    -U \
    -a primary \
    -s seven-lens.paper-trading.analysis-provider.api-key \
    -T "$python_executable" \
    -T "$python_app_executable" \
    -w
