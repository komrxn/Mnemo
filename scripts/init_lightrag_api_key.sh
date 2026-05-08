#!/usr/bin/env bash
# Generates a random API key for lightrag-api and stores it in secrets/.
# Idempotent: skips if key already exists.

set -euo pipefail

KEY_FILE="secrets/lightrag_api_key.txt"

mkdir -p secrets

if [ -f "$KEY_FILE" ]; then
    echo "API key already exists at $KEY_FILE"
    exit 0
fi

python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$KEY_FILE"
chmod 600 "$KEY_FILE"
echo "Generated new API key at $KEY_FILE"
