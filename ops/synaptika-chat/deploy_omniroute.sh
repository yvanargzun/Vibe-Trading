#!/bin/bash
# Seed OmniRoute secrets + DuckDNS + Caddy host, then start stack
set -eu
DEST=/root/synaptika-chat
TRADE=/root/synaptika-trade
SECRETS="$DEST/secrets.env"

cd "$DEST"

ensure_secret() {
  local key="$1"
  local gen="$2"
  if ! grep -q "^${key}=" "$SECRETS" 2>/dev/null; then
    echo "${key}=${gen}" >> "$SECRETS"
    echo "added $key"
  elif grep -q "^${key}=$" "$SECRETS"; then
    sed -i "s|^${key}=$|${key}=${gen}|" "$SECRETS"
    echo "filled $key"
  fi
}

ensure_secret OMNIROUTE_INITIAL_PASSWORD "$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)"
ensure_secret OMNIROUTE_WS_BRIDGE_SECRET "$(openssl rand -base64 32)"
ensure_secret OMNIROUTE_API_KEY_SECRET "$(openssl rand -hex 32)"
ensure_secret OMNIROUTE_JWT_SECRET "$(openssl rand -hex 32)"
ensure_secret OMNIROUTE_STORAGE_ENCRYPTION_KEY "$(openssl rand -hex 32)"
ensure_secret OMNIROUTE_MEMORY_MB "384"
ensure_secret OMNIROUTE_REQUIRE_API_KEY "true"
ensure_secret OMNIROUTE_PUBLIC_URL "https://synaptika-omni.duckdns.org"
ensure_secret OMNIROUTE_GATEWAY_API_KEY "$(openssl rand -hex 32)"
ensure_secret OMNIROUTE_OWUI_API_KEY "$(grep '^OMNIROUTE_GATEWAY_API_KEY=' "$SECRETS" | cut -d= -f2-)"

# DuckDNS synaptika-omni → this VPS
set -a
# shellcheck disable=SC1090
source "$SECRETS"
set +a
if [ -n "${DUCKDNS_TOKEN:-}" ]; then
  echo "Updating DuckDNS synaptika-omni → 46.225.50.87"
  curl -fsS "https://www.duckdns.org/update?domains=synaptika-omni&token=${DUCKDNS_TOKEN}&ip=46.225.50.87" || true
  echo
fi

# Patch trade Caddyfile for OmniRoute dashboard
CADDY="$TRADE/Caddyfile"
if [ -f "$CADDY" ] && ! grep -q 'synaptika-omni.duckdns.org' "$CADDY"; then
  cat >> "$CADDY" <<'EOF'

# OmniRoute dashboard + OpenAI-compatible /v1
synaptika-omni.duckdns.org {
	encode gzip
	reverse_proxy synaptika-chat-omniroute:20128 {
		flush_interval -1
	}
}
EOF
  echo "Caddyfile: added synaptika-omni.duckdns.org"
  (cd "$TRADE" && docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null) \
    || (cd "$TRADE" && docker compose restart caddy) || true
fi

ln -sfn secrets.env .env
docker compose --env-file secrets.env up -d omniroute-redis omniroute

echo "Waiting for OmniRoute..."
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/api/monitoring/health 2>/dev/null || true)
  echo "health=$i code=$code"
  [ "$code" = "200" ] && break
  # some builds use /api/health
  code2=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/ 2>/dev/null || true)
  [ "$code2" = "200" ] && echo "root_ok" && break
  sleep 3
done

echo "=== models sample ==="
curl -s -m 30 http://127.0.0.1:20128/v1/models 2>/dev/null | head -c 800 || true
echo

docker compose --env-file secrets.env up -d chat-webui

echo "OmniRoute dashboard: https://synaptika-omni.duckdns.org"
echo "Password: (OMNIROUTE_INITIAL_PASSWORD in secrets.env)"
grep '^OMNIROUTE_INITIAL_PASSWORD=' "$SECRETS" || true
