#!/bin/bash
set -e
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"queries":["noticias internacionales hoy"]}' \
  https://synaptika-chat.duckdns.org/api/v1/retrieval/process/web/search | python3 -c 'import sys,json
raw=sys.stdin.read()
print(raw[:1200])
try:
  d=json.loads(raw)
  print("TYPE", type(d).__name__)
except Exception as e:
  print("parse_err", e)
'
curl -fsS http://127.0.0.1:4001/healthz; echo
