#!/usr/bin/env bash
# Enable OmniRoute REQUIRE_API_KEY + shared gateway Bearer for demos/Messenger/OWUI/Hermes.
# Run on VPS: bash /root/synaptika-chat/enable_gateway_auth.sh
set -euo pipefail
cd /root/synaptika-chat

SECRETS=secrets.env
touch "$SECRETS"
chmod 600 "$SECRETS"

upsert() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$SECRETS"; then
    # portable in-place replace
    grep -v "^${key}=" "$SECRETS" > "${SECRETS}.tmp"
    mv "${SECRETS}.tmp" "$SECRETS"
  fi
  printf '%s=%s\n' "$key" "$val" >> "$SECRETS"
}

if ! grep -q '^OMNIROUTE_GATEWAY_API_KEY=.\+' "$SECRETS" 2>/dev/null; then
  KEY="$(openssl rand -hex 32)"
  upsert OMNIROUTE_GATEWAY_API_KEY "$KEY"
  echo "generated OMNIROUTE_GATEWAY_API_KEY"
else
  KEY="$(grep '^OMNIROUTE_GATEWAY_API_KEY=' "$SECRETS" | cut -d= -f2-)"
  echo "reusing existing OMNIROUTE_GATEWAY_API_KEY"
fi

upsert OMNIROUTE_REQUIRE_API_KEY "true"
upsert OMNIROUTE_OWUI_API_KEY "$KEY"

# Keep compose in sync if this file was uploaded
if [[ -f /tmp/synaptika-chat-docker-compose.yml ]]; then
  cp /tmp/synaptika-chat-docker-compose.yml /root/synaptika-chat/docker-compose.yml
fi

docker compose --env-file secrets.env up -d --force-recreate omniroute-redis omniroute chat-webui

# Hermes systemd (optional)
SVC=/etc/systemd/system/hermes.service
if [[ -f "$SVC" ]]; then
  cp -a "$SVC" "/root/hermes-omniroute-backup/hermes.service.$(date +%Y%m%d%H%M%S)" 2>/dev/null || mkdir -p /root/hermes-omniroute-backup
  # Replace OPENAI_API_KEY=... lines
  if grep -q 'OPENAI_API_KEY=' "$SVC"; then
    sed -i "s|Environment=\"OPENAI_API_KEY=.*\"|Environment=\"OPENAI_API_KEY=${KEY}\"|" "$SVC"
    sed -i "s|Environment=OPENAI_API_KEY=.*|Environment=OPENAI_API_KEY=${KEY}|" "$SVC"
  else
    sed -i "/OPENAI_BASE_URL=/a Environment=\"OPENAI_API_KEY=${KEY}\"" "$SVC" || true
  fi
  if ! grep -q 'OPENAI_BASE_URL=' "$SVC"; then
    sed -i "/\[Service\]/a Environment=\"OPENAI_BASE_URL=http://127.0.0.1:20128/v1\"" "$SVC" || true
  fi
  systemctl daemon-reload
  systemctl restart hermes.service || true
fi

echo "waiting for OmniRoute..."
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/api/monitoring/health || true)
  if [[ "$code" == "200" ]]; then break; fi
  sleep 2
done

NOAUTH=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/v1/models || true)
WITH=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${KEY}" http://127.0.0.1:20128/v1/models || true)
echo "models_noauth=${NOAUTH} models_with_key=${WITH}"

# Write key once for operator sync (mode 600); do not cat it to stdout.
printf '%s\n' "$KEY" > /root/synaptika-chat/OMNIROUTE_GATEWAY_API_KEY.txt
chmod 600 /root/synaptika-chat/OMNIROUTE_GATEWAY_API_KEY.txt
echo "key_file=/root/synaptika-chat/OMNIROUTE_GATEWAY_API_KEY.txt"
echo "done"
