#!/bin/bash
# Deploy Synaptika Trade portal (Ops + Open WebUI + Caddy) on Hetzner
set -eu
DEST=/root/synaptika-trade
mkdir -p "$DEST/brand" "$DEST/ops_panel/static" "$DEST/chat_history"
touch "$DEST/ops_audit.jsonl"
touch "$DEST/chat_history/.gitkeep"
chmod 644 "$DEST/ops_audit.jsonl"
chmod 777 "$DEST/chat_history" || true

# Install Docker if missing
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y docker-compose-plugin
fi

# Copy stack from /tmp/synaptika-trade if present
if [ -d /tmp/synaptika-trade ]; then
  cp -a /tmp/synaptika-trade/. "$DEST/"
fi

# Secrets (do not overwrite existing)
if [ ! -f "$DEST/secrets.env" ]; then
  cat > "$DEST/secrets.env" <<'EOF'
DUCKDNS_DOMAIN=synaptika-trade
DUCKDNS_TOKEN=REPLACE_ME
DUCKDNS_IP=46.225.50.87
LETSENCRYPT_EMAIL=yvan@synaptika.local
OPS_PASSWORD=SynaptikaTrade2026!
OPS_API_KEY=REPLACE_WITH_RANDOM_HEX
OPENAI_API_KEY=
OPENAI_API_BASE_URL=https://api.openai.com/v1
EOF
  chmod 600 "$DEST/secrets.env"
fi

# Ensure OPS_API_KEY exists on older secrets files
if ! grep -q '^OPS_API_KEY=' "$DEST/secrets.env" 2>/dev/null; then
  echo "OPS_API_KEY=$(openssl rand -hex 24)" >> "$DEST/secrets.env"
fi
if grep -q '^OPS_API_KEY=REPLACE_WITH_RANDOM_HEX$' "$DEST/secrets.env" 2>/dev/null; then
  sed -i "s/^OPS_API_KEY=REPLACE_WITH_RANDOM_HEX$/OPS_API_KEY=$(openssl rand -hex 24)/" "$DEST/secrets.env"
fi

# LF fix scripts
sed -i 's/\r$//' "$DEST/duckdns-update.sh" "$DEST/deploy.sh" 2>/dev/null || true
chmod +x "$DEST/duckdns-update.sh" "$DEST/deploy.sh" 2>/dev/null || true

# systemd duckdns timer
cat >/etc/systemd/system/duckdns-synaptika.service <<'EOF'
[Unit]
Description=Update DuckDNS synaptika-trade
After=network-online.target
[Service]
Type=oneshot
EnvironmentFile=/root/synaptika-trade/secrets.env
ExecStart=/root/synaptika-trade/duckdns-update.sh
EOF
cat >/etc/systemd/system/duckdns-synaptika.timer <<'EOF'
[Unit]
Description=Refresh DuckDNS every 5 minutes
[Timer]
OnBootSec=30
OnUnitActiveSec=5min
Unit=duckdns-synaptika.service
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now duckdns-synaptika.timer || true

# Export compose env
set -a
# shellcheck disable=SC1091
source "$DEST/secrets.env"
set +a
export LETSENCRYPT_EMAIL OPS_PASSWORD OPS_API_KEY OPENAI_API_KEY OPENAI_API_BASE_URL
export OLLAMA_API_KEY OLLAMA_CLOUD_BASE_URL OLLAMA_DEFAULT_MODEL

cd "$DEST"
ln -sfn secrets.env .env
docker compose --env-file secrets.env pull || true
docker compose --env-file secrets.env up -d --build

sleep 8
# Force Open WebUI DB: free models + Ollama Cloud + copiloto
docker compose --env-file secrets.env exec -T \
  -e OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  -e OPENAI_API_BASE_URL="${OPENAI_API_BASE_URL:-https://openrouter.ai/api/v1}" \
  -e OLLAMA_API_KEY="${OLLAMA_API_KEY:-}" \
  -e OLLAMA_CLOUD_BASE_URL="${OLLAMA_CLOUD_BASE_URL:-https://ollama.com/v1}" \
  open-webui python3 /srv/webui/apply_free_models.py || true
docker compose --env-file secrets.env exec -T \
  -e OPS_API_KEY="${OPS_API_KEY:-}" \
  -e OLLAMA_API_KEY="${OLLAMA_API_KEY:-}" \
  -e OLLAMA_CLOUD_BASE_URL="${OLLAMA_CLOUD_BASE_URL:-https://ollama.com/v1}" \
  -e OLLAMA_DEFAULT_MODEL="${OLLAMA_DEFAULT_MODEL:-deepseek-v4-flash}" \
  -e CHAT_HISTORY_DIR=/data/chat_history \
  open-webui python3 /srv/webui/apply_copilot.py || true
docker compose --env-file secrets.env exec -T \
  -e CHAT_HISTORY_DIR=/data/chat_history \
  open-webui python3 /srv/webui/export_chat_history.py || true
# Restart so TOOL_SERVERS cache picks up OpenAPI write tools + model toolIds
docker compose --env-file secrets.env restart open-webui || true
sleep 5
# Smoke: OpenAPI must expose write ops for copiloto control
if curl -fsS -H "X-Ops-Key: ${OPS_API_KEY}" http://127.0.0.1:8787/ops/api/openapi.json \
  | grep -q set_strategy_mode; then
  echo "OK: OpenAPI write tools (set_strategy_mode) present"
else
  echo "WARN: OpenAPI missing set_strategy_mode — check ops container mounts" >&2
fi

docker compose ps
echo "==== health ===="
curl -fsS http://127.0.0.1:8787/healthz || true
echo
echo "Portal: https://synaptika-trade.duckdns.org"
echo "Chat:   https://synaptika-trade.duckdns.org/ops/chat  (Ops login only)"
echo "Hist:   https://synaptika-trade.duckdns.org/ops/historial"
echo "Local:  http://127.0.0.1:8787"
