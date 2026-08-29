#!/bin/bash
# Deploy Synaptika Chat (general Open WebUI) on the Synaptika Trade VPS
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  if grep -q $'\r' "${BASH_SOURCE[0]}" 2>/dev/null; then
    tmp="$(mktemp)"
    tr -d '\r' < "${BASH_SOURCE[0]}" > "$tmp"
    exec bash "$tmp" "$@"
  fi
fi
set -eu
DEST=/root/synaptika-chat
TRADE=/root/synaptika-trade

mkdir -p "$DEST/llm_proxy"

if [ -d /tmp/synaptika-chat ]; then
  find /tmp/synaptika-chat -type f -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
  cp -a /tmp/synaptika-chat/. "$DEST/"
  find "$DEST" -type f -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
fi

# Secrets: seed from trade if missing
if [ ! -f "$DEST/secrets.env" ]; then
  umask 077
  {
    echo "DUCKDNS_DOMAIN=synaptika-chat"
    echo "DUCKDNS_IP=46.225.50.87"
    echo "LLM_PROXY_MODEL=synaptika-chat-auto"
    echo "ALLOW_PAID_OPENROUTER=0"
    echo "ENABLE_SIGNUP=true"
    echo "WEBUI_SECRET_KEY=$(openssl rand -hex 24)"
    echo "GROQ_API_KEY="
    echo "GROQ_MODEL=llama-3.3-70b-versatile"
    if [ -f "$TRADE/secrets.env" ]; then
      # shellcheck disable=SC1090
      set -a
      # shellcheck source=/dev/null
      source "$TRADE/secrets.env"
      set +a
      echo "DUCKDNS_TOKEN=${DUCKDNS_TOKEN:-}"
      echo "LETSENCRYPT_EMAIL=${LETSENCRYPT_EMAIL:-admin@localhost}"
      echo "OPENAI_API_KEY=${OPENAI_API_KEY:-}"
      echo "OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-${OPENAI_API_KEY:-}}"
      echo "OPENAI_API_BASE_URL=https://openrouter.ai/api/v1"
      echo "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1"
      echo "GEMINI_API_KEY=${GEMINI_API_KEY:-}"
      echo "GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.0-flash}"
      echo "OPENROUTER_FAILOVER_MODELS=${OPENROUTER_FAILOVER_MODELS:-google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free,nvidia/nemotron-3-nano-30b-a3b:free,openai/gpt-oss-20b:free,google/gemma-3-27b-it:free,meta-llama/llama-3.3-70b-instruct:free,qwen/qwen-2.5-72b-instruct:free}"
    fi
  } > "$DEST/secrets.env"
  chmod 600 "$DEST/secrets.env"
fi

ln -sfn secrets.env "$DEST/.env"

# Ensure GROQ line exists
if ! grep -q '^GROQ_API_KEY=' "$DEST/secrets.env"; then
  echo "GROQ_API_KEY=" >> "$DEST/secrets.env"
fi

# Point DuckDNS synaptika-chat → this VPS
set -a
# shellcheck source=/dev/null
source "$DEST/secrets.env"
set +a
if [ -n "${DUCKDNS_TOKEN:-}" ]; then
  echo "Updating DuckDNS synaptika-chat → 46.225.50.87"
  curl -fsS "https://www.duckdns.org/update?domains=synaptika-chat&token=${DUCKDNS_TOKEN}&ip=46.225.50.87" || true
  echo
fi

# Patch trade Caddyfile for synaptika-chat host (idempotent)
CADDY="$TRADE/Caddyfile"
MARKER="# synaptika-chat block"
if [ -f "$CADDY" ] && ! grep -q 'synaptika-chat.duckdns.org' "$CADDY"; then
  cat >> "$CADDY" <<'EOF'

# synaptika-chat block — general Open WebUI
synaptika-chat.duckdns.org {
	encode gzip
	reverse_proxy synaptika-chat-webui:8080
}
EOF
  echo "Appended synaptika-chat site to $CADDY"
fi

cd "$DEST"
docker compose --env-file secrets.env build chat-llm-proxy
docker compose --env-file secrets.env up -d

# Reload trade Caddy so new host is live
if docker ps --format '{{.Names}}' | grep -q '^synaptika-trade-caddy-1$'; then
  docker exec synaptika-trade-caddy-1 caddy reload --config /etc/caddy/Caddyfile || \
    docker restart synaptika-trade-caddy-1
fi

echo "Waiting for chat-webui..."
for i in $(seq 1 40); do
  if docker exec synaptika-chat-webui curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1 \
    || docker exec synaptika-chat-webui curl -fsS http://127.0.0.1:8080/ >/dev/null 2>&1; then
    echo "chat-webui is up"
    break
  fi
  sleep 3
done

echo "Proxy health:"
curl -fsS http://127.0.0.1:4001/healthz || true
echo
echo "Done. URL: https://synaptika-chat.duckdns.org"
echo "First visit: create admin account, then set ENABLE_SIGNUP=false and redeploy."
echo "If Groq is empty, add GROQ_API_KEY to $DEST/secrets.env and: docker compose --env-file secrets.env up -d"
