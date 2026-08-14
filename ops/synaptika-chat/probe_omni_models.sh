#!/bin/bash
set -e
curl -s http://127.0.0.1:20128/v1/models > /tmp/omni_models.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/omni_models.json'))
ids=[m.get('id') for m in d.get('data') or []]
for i in ids:
  if any(x in i.lower() for x in ('openrouter','gemini','google','auto/','or/','free','openai-compatible')):
    print(i)
print('TOTAL', len(ids))
print('SAMPLE', ids[:15])
PY

MODEL=$(python3 - <<'PY'
import json
ids=[m["id"] for m in json.load(open('/tmp/omni_models.json')).get('data') or []]
prefs=[]
for i in ids:
  low=i.lower()
  if 'openrouter' in low or low.startswith('or/') or ':free' in low:
    prefs.append(i)
print(prefs[0] if prefs else (ids[0] if ids else ''))
PY
)
echo "MODEL=$MODEL"
curl -s -m 120 http://127.0.0.1:20128/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Di solo: omniroute-ok\"}],\"max_tokens\":40}" \
  > /tmp/omni_chat.json
head -c 1200 /tmp/omni_chat.json; echo

for i in 1 2 3 4 5 6 7 8; do
  c=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo chat_wait=$i code=$c
  [ "$c" = "200" ] && break
  sleep 4
done

docker exec synaptika-chat-webui wget -qO- http://omniroute:20128/v1/models 2>&1 | head -c 400; echo
curl -s http://127.0.0.1:20128/api/monitoring/health; echo
