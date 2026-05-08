#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${LIGHTRAG_API_KEY_FILE:-/run/secrets/lightrag_api_key}"

if [ ! -f "$KEY_FILE" ]; then
    echo "ERROR: API key file not found at $KEY_FILE" >&2
    echo "Run ./scripts/init_lightrag_api_key.sh on the host first." >&2
    exit 1
fi

API_KEY="$(tr -d '[:space:]' < "$KEY_FILE")"

if [ -z "$API_KEY" ]; then
    echo "ERROR: API key file is empty: $KEY_FILE" >&2
    exit 1
fi

exec lightrag-server \
    --host 0.0.0.0 \
    --port 9621 \
    --working-dir /data/lightrag \
    --key "$API_KEY" \
    "$@"
