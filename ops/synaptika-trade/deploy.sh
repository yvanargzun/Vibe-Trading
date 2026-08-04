#!/bin/bash
# Deploy Synaptika Trade portal (Ops + Open WebUI + Caddy) on Hetzner
set -eu
DEST=/root/synaptika-trade
mkdir -p "$DEST/brand" "$DEST/ops_panel/static"
touch "$DEST/ops_audit.jsonl"
chmod 644 "$DEST/ops_audit.jsonl"

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
OPENAI_API_KEY=
OPENAI_API_BASE_URL=https://api.openai.com/v1
EOF
  chmod 600 "$DEST/secrets.env"
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
export LETSENCRYPT_EMAIL OPS_PASSWORD OPENAI_API_KEY OPENAI_API_BASE_URL

cd "$DEST"
ln -sfn secrets.env .env
docker compose --env-file secrets.env pull || true
docker compose --env-file secrets.env up -d --build

sleep 5
docker compose ps
echo "==== health ===="
curl -fsS http://127.0.0.1:8787/healthz || true
echo
echo "Portal: https://synaptika-trade.duckdns.org  (need DuckDNS OK + ports 80/443)"
echo "Chat:   https://synaptika-trade.duckdns.org/chat/"
echo "Local:  http://127.0.0.1:8787"
