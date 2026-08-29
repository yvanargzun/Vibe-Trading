#!/bin/bash
set -e
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

echo "token=${#TOKEN}"

# Confirm model features again
curl -s -H "Authorization: Bearer $TOKEN" 'https://synaptika-chat.duckdns.org/api/models?refresh=true' > /tmp/models.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/models.json'))
for m in d.get('data') or []:
  if m.get('id')=='synaptika-chat-auto':
    meta=(m.get('info') or {}).get('meta') or {}
    print('caps', meta.get('capabilities'))
    print('defaults', meta.get('defaultFeatureIds'))
PY

# Simulate chat completion WITH web_search feature like frontend
echo '=== chat with features.web_search ==='
curl -s -N -m 90 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"synaptika-chat-auto",
    "stream":true,
    "messages":[{"role":"user","content":"Que noticias internacionales hay hoy? Resume 3 titulares concretos."}],
    "features":{"web_search":true},
    "tool_ids":[],
    "params":{"function_calling":"native"}
  }' \
  https://synaptika-chat.duckdns.org/api/chat/completions 2>/dev/null | tee /tmp/chat_out.txt | head -c 2500
echo
echo '--- tail ---'
tail -c 800 /tmp/chat_out.txt; echo

echo '=== recent webui logs ==='
docker logs synaptika-chat-webui --tail 80 2>&1 | grep -iE 'search|web|tool|error|FAIL|ddgs|duck|retrieval' | tail -40

echo '=== proxy logs ==='
docker logs synaptika-chat-llm-proxy --tail 30 2>&1
