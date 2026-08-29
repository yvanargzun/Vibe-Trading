#!/bin/bash
set -e
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

echo '=== auth config ==='
curl -s -H "Authorization: Bearer $TOKEN" https://synaptika-chat.duckdns.org/api/config > /tmp/c.json
wc -c /tmp/c.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/c.json'))
print(json.dumps(d, indent=2)[:2500])
PY

echo '=== permissions ==='
curl -s -H "Authorization: Bearer $TOKEN" https://synaptika-chat.duckdns.org/api/v1/users/permissions > /tmp/p.json || true
head -c 1500 /tmp/p.json; echo

echo '=== model detail synaptika ==='
curl -s -H "Authorization: Bearer $TOKEN" https://synaptika-chat.duckdns.org/api/v1/models > /tmp/m.json || true
python3 - <<'PY'
import json
try:
  d=json.load(open('/tmp/m.json'))
except Exception as e:
  print('fail', e, open('/tmp/m.json').read()[:300]); raise SystemExit
items=d if isinstance(d,list) else d.get('data') or d.get('models') or []
for m in items:
  mid=m.get('id') if isinstance(m,dict) else None
  if mid and 'synaptika' in mid:
    print(json.dumps(m, indent=2)[:2000])
PY

# Inspect running app config object via docker
docker exec -i synaptika-chat-webui python - <<'PY'
import os
print('ENV ENABLE_WEB_SEARCH', os.environ.get('ENABLE_WEB_SEARCH'))
# try import config
try:
  from open_webui.env import ENABLE_WEB_SEARCH
  print('env.ENABLE_WEB_SEARCH', ENABLE_WEB_SEARCH)
except Exception as e:
  print('import env fail', e)
try:
  from open_webui.config import ENABLE_WEB_SEARCH as C
  print('config.ENABLE_WEB_SEARCH', getattr(C, 'value', C))
except Exception as e:
  print('import config fail', e)
try:
  from open_webui import config as cfg
  names=[n for n in dir(cfg) if 'WEB_SEARCH' in n or 'SEARCH' in n]
  print('config names', names)
  for n in names:
    o=getattr(cfg,n)
    print(n, getattr(o,'value', o))
except Exception as e:
  print('cfg dir fail', e)
PY
