#!/bin/bash
set -e
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

echo "token_ok ${#TOKEN}"
curl -s -m 180 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"synaptika-chat-auto",
    "stream":false,
    "messages":[{"role":"user","content":"Que noticias internacionales hay hoy? Dame 3 titulares concretos con fuente."}],
    "features":{"web_search":true},
    "params":{"function_calling":"legacy","stream_response":false}
  }' \
  https://synaptika-chat.duckdns.org/api/chat/completions > /tmp/news.json

python3 - <<'PY'
import json
raw=open('/tmp/news.json',encoding='utf-8',errors='replace').read()
print('bytes', len(raw))
print('raw_head', raw[:500])
try:
  d=json.loads(raw)
except Exception as e:
  print('not_json', e)
  raise SystemExit(1)
msg=(d.get('choices') or [{}])[0].get('message') or {}
print('content', (msg.get('content') or '')[:1200])
print('sources', str(d.get('sources') or d.get('citations'))[:800])
PY

echo '=== logs ==='
docker logs synaptika-chat-webui --tail 40 2>&1 | grep -iE 'web_search|ddgs|process_web|chat/completions|error|exception' | tail -20
