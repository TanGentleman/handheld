#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE=".env"

# --- Preflight ---

if ! command -v uv &>/dev/null; then
  echo "error: uv is not installed"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

echo ":: syncing dependencies"
uv sync --project "$REPO_ROOT"

# --- Modal auth ---

if ! uv run modal profile current &>/dev/null; then
  echo "error: not logged into modal — run 'modal setup' first"
  exit 1
fi

# --- Secrets ---

secret_exists() {
  uv run modal secret list 2>/dev/null | grep -q "$1"
}

# Determine the API token:
#   1. RODNEY_PASSWORD env var (user-chosen, easy to type on phone)
#   2. Existing .env file from a previous deploy
#   3. Auto-generate a random token
if [ -n "${RODNEY_PASSWORD:-}" ]; then
  api_token="$RODNEY_PASSWORD"
  echo ":: using RODNEY_PASSWORD as API token"
elif [ -f "$ENV_FILE" ] && grep -q '^RODNEY_API_TOKEN=' "$ENV_FILE"; then
  api_token=$(grep '^RODNEY_API_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
  echo ":: reusing token from .env"
else
  api_token=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  echo ":: generated new API token"
fi

cookie_secret=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Always (re)create the secret so password changes take effect
uv run modal secret create rodney-auth \
  "RODNEY_API_TOKENS=${api_token}" \
  "RODNEY_COOKIE_SECRET=${cookie_secret}" \
  --force

# Persist token locally for recovery
echo "RODNEY_API_TOKEN=${api_token}" > "$ENV_FILE"

# --- Deploy ---

echo ":: deploying rodney"
deploy_output=$(uv run modal deploy deploy.py 2>&1)
echo "$deploy_output"

# Extract the public URL from deploy output
public_url=$(echo "$deploy_output" | grep -oE 'https://[^ ]+modal\.run' | head -1)

echo ""
echo "========================================="
echo "  rodney is live"
echo "========================================="
echo ""
echo "  url:        ${public_url:-<check modal dashboard>}"
echo "  login:      ${public_url:-<url>}/login"
echo "  docs:       ${public_url:-<url>}/docs"
echo ""
echo "  token:      ${api_token}"
echo "  saved to:   ${ENV_FILE}"
echo ""
echo "  logs:       modal app logs rodney"
echo "  dashboard:  https://modal.com/apps/deployed/rodney"
echo "========================================="

# Print QR code if qrencode is available
if command -v qrencode &>/dev/null && [ -n "$public_url" ]; then
  echo ""
  echo "  scan to open on your phone:"
  qrencode -t ANSIUTF8 "${public_url}/login"
fi
