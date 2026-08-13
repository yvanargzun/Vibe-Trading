#!/bin/bash
set -e
cd /root/synaptika-chat
docker compose --env-file secrets.env build --no-cache chat-llm-proxy
docker compose --env-file secrets.env up -d chat-llm-proxy

TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

echo "token_ok=${#TOKEN}"

# Try common web-search endpoints
for path in \
  '/api/v1/retrieval/process/web' \
  '/api/v1/retrieval/process/web/search' \
  '/api/v1/tools/web_search'
 do
  echo "TRY $path"
  curl -s -o /tmp/ws.json -w 'code=%{http_code}\n' \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"query":"noticias internacionales hoy","collection_name":"web"}' \
    "https://synaptika-chat.duckdns.org$path" || true
  head -c 300 /tmp/ws.json; echo
done

curl -fsS http://127.0.0.1:4001/healthz; echo
