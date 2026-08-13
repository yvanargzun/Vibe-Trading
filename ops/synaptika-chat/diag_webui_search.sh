#!/bin/bash
set -e
echo '=== env ==='
docker exec synaptika-chat-webui printenv | grep -iE 'WEB_SEARCH|ENABLE_WEB|PERSISTENT|SEARCH_QUERY|BYPASS_WEB' | sort

echo '=== api/config ==='
curl -s https://synaptika-chat.duckdns.org/api/config > /tmp/owui_config.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/owui_config.json'))
print('features', json.dumps(d.get('features'), indent=2))
print('name', d.get('name'), 'version', d.get('version'))
print('top', sorted(d.keys())[:40])
PY

TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
echo "token_ok=${#TOKEN}"

curl -s -H "Authorization: Bearer $TOKEN" https://synaptika-chat.duckdns.org/api/v1/auths/ > /tmp/owui_user.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/owui_user.json'))
print('user', {k:d.get(k) for k in ['email','role','id']})
s=d.get('settings') or {}
ui=(s.get('ui') if isinstance(s, dict) else {}) or {}
print('ui_flags', {k:ui.get(k) for k in sorted(ui) if any(x in k.lower() for x in ['web','search','stream','tool'])})
PY

curl -s -H "Authorization: Bearer $TOKEN" https://synaptika-chat.duckdns.org/api/models > /tmp/owui_models.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/owui_models.json'))
for m in (d.get('data') or [])[:10]:
    caps=(m.get('meta') or {}).get('capabilities') if isinstance(m.get('meta'), dict) else None
    info=m.get('info') or {}
    print(m.get('id'), 'caps', caps, 'info_caps', (info.get('meta') or {}).get('capabilities') if isinstance(info, dict) else None)
PY

docker exec -i synaptika-chat-webui python - <<'PY'
import sqlite3, json
c=sqlite3.connect('/app/backend/data/webui.db')
print('DB models:')
for row in c.execute('select id,name,is_active,meta,params from model'):
    print(row[0], row[1], row[2], str(row[3])[:160], str(row[4])[:120])
print('DB config web/search:')
for k,v in c.execute("select key, value from config where lower(key) like '%web%' or lower(key) like '%search%'"):
    print(k, '=', v[:120])
PY

# permissions endpoint if any
for p in /api/v1/users/permissions /api/v1/configs/default /api/config; do
  code=$(curl -s -o /tmp/p.json -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "https://synaptika-chat.duckdns.org$p")
  echo "GET $p -> $code"
  head -c 250 /tmp/p.json; echo
done
