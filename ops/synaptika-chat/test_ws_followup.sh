#!/bin/bash
set -e
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

echo "=== follow-up WITH web_search ==="
timeout 100 curl -s -N -m 95 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"auto/best-free",
    "stream":true,
    "messages":[
      {"role":"user","content":"hola"},
      {"role":"assistant","content":"hola"},
      {"role":"user","content":"que hora es en CDMX?"}
    ],
    "features":{"web_search":true},
    "params":{"function_calling":"legacy"}
  }' \
  https://synaptika-chat.duckdns.org/api/chat/completions > /tmp/ws2.json || echo exit=$?
python3 - <<'PY'
raw=open('/tmp/ws2.json',encoding='utf-8',errors='replace').read()
import json
content=[]; reason=[]; done=False; err=None
for line in raw.splitlines():
  if not line.startswith('data:'): continue
  p=line[5:].strip()
  if p=='[DONE]': done=True; continue
  try: d=json.loads(p)
  except: continue
  if d.get('error'): err=d['error']
  for ch in d.get('choices') or []:
    delta=ch.get('delta') or {}
    if delta.get('content'): content.append(delta['content'])
    if delta.get('reasoning_content'): reason.append('r')
print('done', done, 'err', err, 'content', ''.join(content)[:300] or '(empty)', 'reason_chunks', len(reason), 'bytes', len(raw))
PY
docker logs synaptika-chat-webui --tail 30 2>&1 | grep -iE 'cancel|error|ddgs|web_search|exception' | tail -15
