#!/bin/bash
set -e
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

test_model() {
  local model="$1"
  echo "=== $model ==="
  timeout 90 curl -s -N -m 85 \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$model\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Di solo: hola\"},{\"role\":\"assistant\",\"content\":\"hola\"},{\"role\":\"user\",\"content\":\"Di solo: adios\"}],\"features\":{\"web_search\":false}}" \
    https://synaptika-chat.duckdns.org/api/chat/completions > /tmp/t.json || echo exit=$?
  python3 - <<'PY'
import json
raw=open('/tmp/t.json',encoding='utf-8',errors='replace').read()
content=[]; reason=[]; done=False; model=None; fr=None
for line in raw.splitlines():
  if not line.startswith('data:'): continue
  p=line[5:].strip()
  if p=='[DONE]': done=True; continue
  try: d=json.loads(p)
  except: continue
  model=d.get('model') or model
  for ch in d.get('choices') or []:
    fr=ch.get('finish_reason') or fr
    delta=ch.get('delta') or {}
    if delta.get('content'): content.append(delta['content'])
    if delta.get('reasoning_content'): reason.append(delta['reasoning_content'])
print('done', done, 'upstream', model, 'finish', fr)
print('content', ''.join(content)[:300] or '(empty)')
print('reason_len', len(''.join(reason)), 'bytes', len(raw))
PY
}

test_model "auto/best-free"
test_model "auto/fast"
test_model "auto/chat"
test_model "synaptika-chat-auto"
