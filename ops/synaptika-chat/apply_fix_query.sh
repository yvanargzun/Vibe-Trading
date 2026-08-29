#!/bin/bash
set -e
cd /root/synaptika-chat

# Ensure compose has query gen off
grep -q 'ENABLE_SEARCH_QUERY_GENERATION=false' docker-compose.yml || \
  sed -i 's/ENABLE_SEARCH_QUERY_GENERATION=true/ENABLE_SEARCH_QUERY_GENERATION=false/' docker-compose.yml

docker compose --env-file secrets.env up -d chat-webui
sleep 2
docker cp /root/synaptika-chat/fix_query_gen.py synaptika-chat-webui:/tmp/fix_query_gen.py
# wait healthy
for i in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait=$i code=$code
  [ "$code" = "200" ] && break
  sleep 3
done

docker exec synaptika-chat-webui python3 /tmp/fix_query_gen.py
docker restart synaptika-chat-webui

for i in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait2=$i code=$code
  [ "$code" = "200" ] && break
  sleep 3
done

TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

IP=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' synaptika-chat-webui)

echo "=== chat ==="
timeout 100 curl -s -N -m 95 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"synaptika-chat-auto","stream":true,"messages":[{"role":"user","content":"Que noticias internacionales hay hoy? Dame 3 titulares concretos con fuente."}],"features":{"web_search":true},"params":{"function_calling":"legacy"}}' \
  "http://$IP:8080/api/chat/completions" > /tmp/news4.json || echo curl_exit=$?

python3 - <<'PY'
raw=open('/tmp/news4.json',encoding='utf-8',errors='replace').read()
print('bytes', len(raw))
parts=[]; sources=False; done=False
import json
for line in raw.splitlines():
    if not line.startswith('data:'): continue
    p=line[5:].strip()
    if p=='[DONE]':
        done=True; continue
    try: d=json.loads(p)
    except: continue
    if d.get('sources'): sources=True
    for ch in d.get('choices') or []:
        c=(ch.get('delta') or {}).get('content') or (ch.get('message') or {}).get('content')
        if c: parts.append(c)
print('done', done, 'sources', sources)
print('ANSWER:')
print(''.join(parts)[:2000])
PY

echo "=== search queries in logs ==="
docker logs synaptika-chat-webui --tail 80 2>&1 | grep -iE 'ddgs|search=|query=' | tail -25
