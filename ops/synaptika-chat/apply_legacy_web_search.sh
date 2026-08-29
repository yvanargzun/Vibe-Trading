#!/bin/bash
set -e
cd /root/synaptika-chat
docker compose --env-file secrets.env up -d chat-webui
for i in 1 2 3 4 5 6 7 8 9 10 12 14; do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait=$i code=$code
  [ "$code" = "200" ] && break
  sleep 4
done

docker exec synaptika-chat-webui python /srv/fix_web_search_legacy.py
docker restart synaptika-chat-webui

for i in 1 2 3 4 5 6 7 8 9 10 12 14; do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait2=$i code=$code
  [ "$code" = "200" ] && break
  sleep 4
done

TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s -H "Authorization: Bearer $TOKEN" 'https://synaptika-chat.duckdns.org/api/models?refresh=true' > /tmp/models.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/models.json'))
for m in d.get('data') or []:
  if m.get('id')=='synaptika-chat-auto':
    info=m.get('info') or {}
    meta=info.get('meta') or {}
    params=(info.get('params') if isinstance(info,dict) else None) or {}
    # params may be stripped from API; check separately
    print('has_info', bool(info))
    print('capabilities', meta.get('capabilities'))
    print('defaultFeatureIds', meta.get('defaultFeatureIds'))
    break
else:
  print('NO_MODEL_INFO')
PY

# Full chat with web_search - should log web_search in OWUI and return citations/news
echo '=== news chat ==='
curl -s -N -m 120 \
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
print('raw_head', raw[:400])
try:
  d=json.loads(raw)
except Exception as e:
  print('not_json', e); raise SystemExit
print('keys', list(d)[:20])
msg=(d.get('choices') or [{}])[0].get('message') or {}
print('content', (msg.get('content') or '')[:800])
print('sources', d.get('sources') or d.get('citations'))
PY

echo '=== logs ==='
docker logs synaptika-chat-webui --tail 60 2>&1 | grep -iE 'web_search|search|Skipping model|ModelModel|ddgs|process_web' | tail -30
