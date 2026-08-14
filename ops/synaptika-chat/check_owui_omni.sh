#!/bin/bash
set -e
for i in $(seq 1 30); do
  c=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait=$i code=$c
  [ "$c" = "200" ] && break
  sleep 3
done

TOKEN=$(curl -s -m 30 -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin)
echo "signin_head=$(echo "$TOKEN" | head -c 120)"
TOKEN=$(echo "$TOKEN" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s -m 60 -H "Authorization: Bearer $TOKEN" \
  'https://synaptika-chat.duckdns.org/api/models?refresh=true' > /tmp/owui_models.json
python3 - <<'PY'
import json
raw=open('/tmp/owui_models.json',encoding='utf-8',errors='replace').read()
print('bytes', len(raw), 'head', raw[:120])
d=json.loads(raw)
ids=[m.get('id') for m in d.get('data') or []]
print('owui_model_count', len(ids))
for i in ids[:25]:
  print(' ', i)
print('has_auto_best_free', 'auto/best-free' in ids)
print('has_auto', any(str(i).startswith('auto/') for i in ids))
print('has_synaptika', any('synaptika' in str(i) for i in ids))
PY

# env check on webui
docker exec synaptika-chat-webui printenv OPENAI_API_BASE_URL DEFAULT_MODELS | cat
