#!/bin/bash
set -e
echo "=== file ==="
ls -la /tmp/news.json 2>/dev/null || echo no_file
wc -c /tmp/news.json 2>/dev/null || true
head -c 800 /tmp/news.json 2>/dev/null; echo

echo "=== DB ==="
docker exec synaptika-chat-webui python3 /srv/dump_model.py

echo "=== direct search API ==="
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  http://127.0.0.1:8080/api/v1/auths/signin 2>/dev/null || true)

# hit via docker network
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s -m 60 -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"noticias internacionales hoy"}' \
  https://synaptika-chat.duckdns.org/api/v1/retrieval/process/web/search | python3 -c 'import sys,json;d=json.load(sys.stdin);print(type(d), str(d)[:900])'

echo
echo "=== chat via docker exec curl to webui ==="
# find internal port
docker inspect synaptika-chat-webui --format '{{json .NetworkSettings.Networks}}' | head -c 400; echo
IP=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' synaptika-chat-webui)
echo "webui_ip=$IP"
curl -s -m 90 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"synaptika-chat-auto","stream":false,"messages":[{"role":"user","content":"Di solo: hola"}],"features":{"web_search":false}}' \
  "http://$IP:8080/api/chat/completions" | head -c 600; echo

echo "=== with web search short ==="
timeout 90 curl -s -m 85 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"synaptika-chat-auto","stream":false,"messages":[{"role":"user","content":"Busca 1 titular de noticias de hoy y citalo"}],"features":{"web_search":true},"params":{"function_calling":"legacy","stream_response":false}}' \
  "http://$IP:8080/api/chat/completions" > /tmp/news2.json || echo curl_exit=$?
wc -c /tmp/news2.json
python3 - <<'PY'
raw=open('/tmp/news2.json',encoding='utf-8',errors='replace').read()
print('head', raw[:700])
import json
try:
  d=json.loads(raw)
  msg=(d.get('choices') or [{}])[0].get('message') or {}
  print('content', (msg.get('content') or '')[:1000])
except Exception as e:
  print('err', e)
PY

echo "=== last logs ==="
docker logs synaptika-chat-webui --tail 50 2>&1 | grep -iE 'web_search|ddgs|error|exception|completions|Skipping|ModelModel' | tail -30
