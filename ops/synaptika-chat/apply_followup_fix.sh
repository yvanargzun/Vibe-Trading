#!/bin/bash
set -e
cd /root/synaptika-chat
sed -i 's/^DEFAULT_MODELS=.*/DEFAULT_MODELS=auto\/chat/' secrets.env || echo 'DEFAULT_MODELS=auto/chat' >> secrets.env
grep -q '^DEFAULT_MODELS=' secrets.env || echo 'DEFAULT_MODELS=auto/chat' >> secrets.env

docker compose --env-file secrets.env up -d chat-webui
for i in $(seq 1 25); do
  c=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait=$i code=$c
  [ "$c" = "200" ] && break
  sleep 3
done

docker cp /root/synaptika-chat/fix_followup_hang.py synaptika-chat-webui:/tmp/fix_followup_hang.py
docker exec synaptika-chat-webui python3 /tmp/fix_followup_hang.py
docker restart synaptika-chat-webui

for i in $(seq 1 25); do
  c=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait2=$i code=$c
  [ "$c" = "200" ] && break
  sleep 3
done

TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

echo "=== 3-turn chat auto/chat ==="
timeout 120 curl -s -N -m 110 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"auto/chat",
    "stream":true,
    "messages":[
      {"role":"user","content":"Di solo: uno"},
      {"role":"assistant","content":"uno"},
      {"role":"user","content":"Di solo: dos"},
      {"role":"assistant","content":"dos"},
      {"role":"user","content":"Di solo: tres"}
    ],
    "features":{"web_search":false}
  }' \
  https://synaptika-chat.duckdns.org/api/chat/completions > /tmp/t3.json || echo exit=$?

python3 - <<'PY'
import json
raw=open('/tmp/t3.json',encoding='utf-8',errors='replace').read()
content=[]; done=False; model=None
for line in raw.splitlines():
  if not line.startswith('data:'): continue
  p=line[5:].strip()
  if p=='[DONE]': done=True; continue
  try: d=json.loads(p)
  except: continue
  model=d.get('model') or model
  for ch in d.get('choices') or []:
    c=((ch.get('delta') or {}).get('content'))
    if c: content.append(c)
print('done', done, 'model', model, 'content', ''.join(content)[:200], 'bytes', len(raw))
PY

docker exec synaptika-chat-webui printenv DEFAULT_MODELS OPENAI_API_BASE_URL
