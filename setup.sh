#!/usr/bin/env bash
# One-command setup: creates required directories, generates API key, validates .env.
# Idempotent — safe to run multiple times.

set -euo pipefail

if [ ! -f .env ]; then
    if [ ! -f .env.example ]; then
        echo "ERROR: .env.example not found. Are you in the project root?"
        exit 1
    fi
    cp .env.example .env
    echo "Created .env from .env.example — edit it before running 'docker compose up -d'."
fi

mkdir -p secrets data/vault data/lightrag data/redis

./scripts/init_lightrag_api_key.sh

if [ ! -f secrets/vault_ssh_key ]; then
    echo ""
    echo "No vault SSH key found. To enable git-sync of your vault, run:"
    echo "  ssh-keygen -t ed25519 -C 'mnemo-vault' -f secrets/vault_ssh_key -N ''"
    echo "Then add secrets/vault_ssh_key.pub to your GitHub repo as a deploy key."
    echo "(Skip this if you don't need vault git-sync.)"
fi

echo ""
echo "Setup complete. Next steps:"
echo "  1. Edit .env  (TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, ALLOWED_USER_IDS)"
echo "  2. docker compose up -d"
echo "  3. docker compose logs -f bot"
