#!/bin/bash
set -e
echo "=== parse /tmp/news.json ==="
python3 - <<'PY'
raw=open('/tmp/news.json',encoding='utf-8',errors='replace').read()
print('bytes', len(raw))
print('starts_with', repr(raw[:40]))
contents=[]
sources=[]
for line in raw.splitlines():
    line=line.strip()
    if not line.startswith('data:'):
        continue
    payload=line[5:].strip()
    if payload=='[DONE]':
        print('DONE')
        continue
    try:
        import json
        d=json.loads(payload)
    except Exception:
        continue
    if 'sources' in d:
        sources.append(d['sources'])
    ch=d.get('choices') or []
    if ch:
        delta=ch[0].get('delta') or {}
        msg=ch[0].get('message') or {}
        if delta.get('content'):
            contents.append(delta['content'])
        if msg.get('content'):
            contents.append(msg['content'])
    if d.get('content'):
        contents.append(d['content'])
text=''.join(contents)
print('content_len', len(text))
print('content', text[:1500])
print('n_source_events', len(sources))
if sources:
    s0=sources[0]
    print('first_sources_type', type(s0), 'len', len(s0) if hasattr(s0,'__len__') else None)
    try:
        for i,src in enumerate(s0[:5]):
            name=(src.get('source') or src).get('name') if isinstance(src,dict) else src
            if isinstance(src,dict):
                docs=src.get('document') or src.get('docs') or []
                title=None
                if isinstance(src.get('source'),dict):
                    title=src['source'].get('name') or src['source'].get('url') or src['source'].get('source')
                print(i, 'keys', list(src.keys())[:10], 'title', title)
    except Exception as e:
        print('src_err', e)
PY

echo "=== DB params ==="
docker cp /root/synaptika-chat/dump_model.py synaptika-chat-webui:/tmp/dump_model.py
docker exec synaptika-chat-webui python3 /tmp/dump_model.py

echo "=== env web search ==="
docker exec synaptika-chat-webui env | grep -iE 'WEB_SEARCH|BYPASS|ENABLE_WEB' | sort

echo "=== quick chat stream parse ==="
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
IP=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' synaptika-chat-webui)
timeout 100 curl -s -N -m 95 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"synaptika-chat-auto","stream":true,"messages":[{"role":"user","content":"Busca 2 titulares de noticias internacionales de hoy con fuente."}],"features":{"web_search":true},"params":{"function_calling":"legacy"}}' \
  "http://$IP:8080/api/chat/completions" > /tmp/news3.json || echo curl_exit=$?
python3 - <<'PY'
raw=open('/tmp/news3.json',encoding='utf-8',errors='replace').read()
print('bytes', len(raw), 'head', repr(raw[:80]))
parts=[]
import json
for line in raw.splitlines():
    if not line.startswith('data:'): continue
    p=line[5:].strip()
    if p=='[DONE]': 
        print('got DONE'); continue
    try: d=json.loads(p)
    except: continue
    for ch in d.get('choices') or []:
        c=(ch.get('delta') or {}).get('content') or (ch.get('message') or {}).get('content')
        if c: parts.append(c)
print('ANSWER:', ''.join(parts)[:1500])
PY
docker logs synaptika-chat-webui --tail 30 2>&1 | grep -iE 'ddgs|web_search|error|exception|Skipping' | tail -15
