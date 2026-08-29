#!/usr/bin/env bash
set -euo pipefail
echo '=== pre ==='
free -h | sed -n '2,3p'
df -h / | tail -1

cp -a /root/synaptika-chat/docker-compose.yml "/root/synaptika-chat/docker-compose.yml.bak.$(date +%Y%m%d%H%M%S)"
cp /tmp/synaptika-chat-docker-compose.yml /root/synaptika-chat/docker-compose.yml
grep -q '^OMNIROUTE_REQUIRE_API_KEY=true' /root/synaptika-chat/secrets.env || echo 'OMNIROUTE_REQUIRE_API_KEY=true' >> /root/synaptika-chat/secrets.env
grep -q '^OMNIROUTE_GATEWAY_API_KEY=' /root/synaptika-chat/secrets.env || { echo 'MISSING GATEWAY KEY'; exit 1; }

if [[ -f /tmp/Caddyfile.new ]]; then
  cp -a /root/synaptika-trade/Caddyfile "/root/synaptika-trade/Caddyfile.bak.$(date +%Y%m%d%H%M%S)"
  cp /tmp/Caddyfile.new /root/synaptika-trade/Caddyfile
fi

echo '=== pull chat images ==='
cd /root/synaptika-chat
docker compose --env-file secrets.env pull omniroute omniroute-redis chat-webui || docker compose --env-file secrets.env pull

echo '=== recreate chat ==='
docker compose --env-file secrets.env up -d --force-recreate omniroute-redis omniroute chat-webui

echo '=== pull/rebuild trade ==='
cd /root/synaptika-trade
docker compose --env-file secrets.env pull caddy open-webui || true
docker compose --env-file secrets.env build --pull ops llm-proxy || docker compose --env-file secrets.env build ops llm-proxy
docker compose --env-file secrets.env up -d

echo '=== cleanup disk ==='
rm -rf /root/.agent-browser/browsers || true
docker image prune -f
docker builder prune -f --filter until=168h || true
journalctl --vacuum-size=50M || true

echo '=== wait omni ==='
code=000
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/api/monitoring/health || true)
  if [[ "$code" == "200" ]]; then break; fi
  sleep 3
done
KEY=$(tr -d '\r\n' </root/synaptika-chat/OMNIROUTE_GATEWAY_API_KEY.txt)
echo "health=$code"
echo -n 'models_noauth='; curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/v1/models; echo
echo -n 'models_auth='; curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${KEY}" http://127.0.0.1:20128/v1/models; echo
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
df -h / | tail -1
free -h | sed -n '2,3p'
echo DONE
