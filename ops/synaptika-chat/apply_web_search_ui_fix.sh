#!/bin/bash
set -e
cd /root/synaptika-chat
docker compose --env-file secrets.env up -d chat-webui
sleep 5
for i in 1 2 3 4 5 6 7 8 9 10 12; do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait=$i code=$code
  [ "$code" = "200" ] && break
  sleep 4
done

docker exec synaptika-chat-webui python /srv/fix_web_search_ui.py
docker restart synaptika-chat-webui

for i in 1 2 3 4 5 6 7 8 9 10 12; do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait2=$i code=$code
  [ "$code" = "200" ] && break
  sleep 4
done

TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# Force model refresh
curl -s -o /dev/null -w 'models_refresh=%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
  'https://synaptika-chat.duckdns.org/api/models?refresh=true' || true

curl -s -H "Authorization: Bearer $TOKEN" https://synaptika-chat.duckdns.org/api/models > /tmp/models.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/models.json'))
for m in d.get('data') or []:
    if m.get('id')=='synaptika-chat-auto':
        info=m.get('info') or {}
        meta=info.get('meta') if isinstance(info, dict) else {}
        print('FOUND synaptika-chat-auto')
        print('has_info', bool(info))
        print('capabilities', (meta or {}).get('capabilities'))
        print('defaultFeatureIds', (meta or {}).get('defaultFeatureIds'))
        break
else:
    print('MODEL_NOT_FOUND')
PY

curl -s -H "Authorization: Bearer $TOKEN" https://synaptika-chat.duckdns.org/api/config > /tmp/cfg.json
python3 - <<'PY'
import json
f=json.load(open('/tmp/cfg.json')).get('features',{})
print('enable_web_search', f.get('enable_web_search'))
PY
