#!/bin/bash
set -e
echo '=== env ==='
docker exec synaptika-chat-webui printenv ENABLE_WEB_SEARCH WEB_SEARCH_ENGINE BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL ENABLE_PERSISTENT_CONFIG ENABLE_SEARCH_QUERY_GENERATION

echo '=== ddgs ==='
docker exec -i synaptika-chat-webui python - <<'PY'
try:
    from ddgs import DDGS
except Exception:
    from duckduckgo_search import DDGS
q='noticias internacionales hoy'
try:
    d=DDGS()
    rows=list(d.text(q, max_results=3))
    print('DDGS_OK', len(rows))
    for r in rows[:3]:
        print('-', (r.get('title') or r.get('href') or '')[:100])
except Exception as e:
    print('DDGS_FAIL', type(e).__name__, e)
PY

echo '=== auth + web search config ==='
rm -f /tmp/j.jar
curl -s -c /tmp/j.jar -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin >/tmp/signin.json
TOKEN=$(python3 -c 'import json;print(json.load(open("/tmp/signin.json")).get("token",""))')
echo token_len=${#TOKEN}
curl -s -H "Authorization: Bearer $TOKEN" https://synaptika-chat.duckdns.org/api/v1/configs | python3 -c 'import sys,json
d=json.load(sys.stdin)
# print search related recursively
import re
s=json.dumps(d)
for m in re.findall(r".{0,40}(web.?search|duckduckgo|enable_web).{0,40}", s, flags=re.I):
    print(m)
print("top_keys", list(d)[:30] if isinstance(d, dict) else type(d))
' || true

curl -s -H "Authorization: Bearer $TOKEN" https://synaptika-chat.duckdns.org/api/v1/retrieval/config | head -c 800; echo
