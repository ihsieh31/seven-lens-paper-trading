#!/bin/sh
# Provision a rotated Agnes key without placing it in argv, environment, or a file.
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "Agnes credential provisioning requires macOS Keychain." >&2
    exit 1
fi

echo "Enter the rotated Agnes API key at the protected Keychain prompt." >&2
exec /usr/bin/security add-generic-password \
    -U \
    -a primary \
    -s seven-lens.paper-trading.agnes.api-key \
    -w
