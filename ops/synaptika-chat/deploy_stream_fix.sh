#!/bin/bash
set -e
DEST=/root/synaptika-chat
cd "$DEST"

# Prefer short failover list for speed
if grep -q '^OPENROUTER_FAILOVER_MODELS=' secrets.env; then
  sed -i 's|^OPENROUTER_FAILOVER_MODELS=.*|OPENROUTER_FAILOVER_MODELS=openrouter/free|' secrets.env
else
  echo 'OPENROUTER_FAILOVER_MODELS=openrouter/free' >> secrets.env
fi

docker compose --env-file secrets.env build chat-llm-proxy
docker compose --env-file secrets.env up -d
sleep 3
docker cp /root/synaptika-chat/apply_stream_defaults.py synaptika-chat-webui:/tmp/apply_stream_defaults.py
docker exec synaptika-chat-webui python /tmp/apply_stream_defaults.py
docker restart synaptika-chat-webui

echo '=== health ==='
curl -fsS http://127.0.0.1:4001/healthz; echo

echo '=== stream probe ==='
# login
rm -f /tmp/j.jar
curl -s -c /tmp/j.jar -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin >/tmp/signin.json
TOKEN=$(python3 -c 'import json;print(json.load(open("/tmp/signin.json")).get("token",""))')
curl -s -N -m 45 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"synaptika-chat-auto","stream":true,"messages":[{"role":"user","content":"Di solo: hola"}],"max_tokens":32}' \
  https://synaptika-chat.duckdns.org/api/chat/completions 2>/dev/null | head -c 500
echo
# also hit proxy directly
curl -s -N -m 40 -H 'Content-Type: application/json' \
  -d '{"model":"synaptika-chat-auto","stream":true,"messages":[{"role":"user","content":"Di solo: ok"}],"max_tokens":24}' \
  http://127.0.0.1:4001/v1/chat/completions | head -c 400
echo
docker logs synaptika-chat-llm-proxy --tail 15
