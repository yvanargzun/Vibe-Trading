#!/bin/bash
set -e
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

echo "=== msg1 ==="
timeout 90 curl -s -N -m 85 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"auto/best-free","stream":true,"messages":[{"role":"user","content":"Di solo: uno"}],"features":{"web_search":false}}' \
  http://127.0.0.1:20128/v1/chat/completions > /tmp/m1.json || echo m1_exit=$?
python3 - <<'PY'
raw=open('/tmp/m1.json',encoding='utf-8',errors='replace').read()
parts=[]
import json
for line in raw.splitlines():
  if not line.startswith('data:'): continue
  p=line[5:].strip()
  if p=='[DONE]':
    print('m1 DONE'); continue
  try: d=json.loads(p)
  except: continue
  for ch in d.get('choices') or []:
    c=(ch.get('delta') or {}).get('content') or (ch.get('message') or {}).get('content')
    if c: parts.append(c)
print('m1', ''.join(parts)[:200], 'bytes', len(raw))
PY

echo "=== msg2 follow-up via OWUI ==="
# Use OWUI chat completions with history (how UI sends follow-ups)
timeout 120 curl -s -N -m 110 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"auto/best-free",
    "stream":true,
    "messages":[
      {"role":"user","content":"Di solo: uno"},
      {"role":"assistant","content":"uno"},
      {"role":"user","content":"Di solo: dos"}
    ],
    "features":{"web_search":false},
    "params":{"function_calling":"legacy","stream_response":true}
  }' \
  https://synaptika-chat.duckdns.org/api/chat/completions > /tmp/m2.json || echo m2_exit=$?

python3 - <<'PY'
raw=open('/tmp/m2.json',encoding='utf-8',errors='replace').read()
print('m2_bytes', len(raw), 'head', repr(raw[:200]))
parts=[]; done=False
import json
for line in raw.splitlines():
  if not line.startswith('data:'): continue
  p=line[5:].strip()
  if p=='[DONE]':
    done=True; continue
  try: d=json.loads(p)
  except: continue
  if d.get('error'): print('ERR', d['error'])
  for ch in d.get('choices') or []:
    delta=ch.get('delta') or {}
    c=delta.get('content') or (ch.get('message') or {}).get('content')
    if c: parts.append(c)
    if delta.get('reasoning_content'): parts.append('[r]')
print('done', done, 'content', ''.join(parts)[:400])
PY

echo "=== recent errors ==="
docker logs synaptika-chat-webui --tail 40 2>&1 | grep -iE 'error|exception|timeout|fail|omni|stream' | tail -20
docker logs synaptika-chat-omniroute --tail 40 2>&1 | grep -iE 'error|exception|timeout|fail|abort' | tail -20
