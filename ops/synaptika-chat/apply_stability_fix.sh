#!/bin/bash
set -e
cd /root/synaptika-chat
DEST=/root/synaptika-chat
TRADE=/root/synaptika-trade

# Stop the unused multi-upstream llm-proxy to free RAM (profile=fallback)
docker stop synaptika-chat-llm-proxy 2>/dev/null || true
docker rm synaptika-chat-llm-proxy 2>/dev/null || true

# Patch Caddy for stable SSE/WebSocket chat
python3 - <<'PY'
from pathlib import Path
p = Path("/root/synaptika-trade/Caddyfile")
text = p.read_text(encoding="utf-8")
old = """synaptika-chat.duckdns.org {
	reverse_proxy synaptika-chat-webui:8080 {
		flush_interval -1
	}
}"""
new = """synaptika-chat.duckdns.org {
	encode gzip
	reverse_proxy synaptika-chat-webui:8080 {
		flush_interval -1
		transport http {
			read_timeout 10m
			write_timeout 10m
			dial_timeout 10s
			keepalive 30s
		}
		header_up Host {host}
		header_up X-Real-IP {remote_host}
		header_up X-Forwarded-For {remote_host}
		header_up X-Forwarded-Proto {scheme}
	}
}"""
if old in text:
    p.write_text(text.replace(old, new), encoding="utf-8")
    print("caddy_patched")
elif "read_timeout 10m" in text and "synaptika-chat.duckdns.org" in text:
    print("caddy_already_stable")
else:
    print("caddy_manual_check")
PY

(cd "$TRADE" && docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile) || true

# Ensure secrets
grep -q '^DEFAULT_MODELS=' secrets.env && sed -i 's|^DEFAULT_MODELS=.*|DEFAULT_MODELS=auto/chat|' secrets.env || echo 'DEFAULT_MODELS=auto/chat' >> secrets.env
grep -q '^OMNIROUTE_MEMORY_MB=' secrets.env && sed -i 's|^OMNIROUTE_MEMORY_MB=.*|OMNIROUTE_MEMORY_MB=320|' secrets.env || echo 'OMNIROUTE_MEMORY_MB=320' >> secrets.env

docker compose --env-file secrets.env up -d omniroute-redis omniroute chat-webui

for i in $(seq 1 40); do
  c=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait=$i code=$c
  [ "$c" = "200" ] && break
  sleep 3
done

docker cp "$DEST/fix_stability.py" synaptika-chat-webui:/tmp/fix_stability.py
docker exec synaptika-chat-webui python3 /tmp/fix_stability.py
docker restart synaptika-chat-webui

for i in $(seq 1 40); do
  c=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait2=$i code=$c
  [ "$c" = "200" ] && break
  sleep 3
done

TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

echo "=== model count ==="
curl -s -H "Authorization: Bearer $TOKEN" 'https://synaptika-chat.duckdns.org/api/models' > /tmp/models_stable.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/models_stable.json'))
ids=[m.get('id') for m in d.get('data') or []]
print('count', len(ids))
print('sample', ids[:12])
print('has_auto_chat', 'auto/chat' in ids)
PY

echo "=== 3-turn stream ==="
timeout 120 curl -s -N -m 110 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"auto/chat",
    "stream":true,
    "messages":[
      {"role":"user","content":"Di solo: A"},
      {"role":"assistant","content":"A"},
      {"role":"user","content":"Di solo: B"},
      {"role":"assistant","content":"B"},
      {"role":"user","content":"Di solo: C"}
    ],
    "features":{"web_search":false}
  }' \
  https://synaptika-chat.duckdns.org/api/chat/completions > /tmp/stable3.json || echo exit=$?

python3 - <<'PY'
import json
raw=open('/tmp/stable3.json',encoding='utf-8',errors='replace').read()
content=[]; done=False; model=None
for line in raw.splitlines():
  if not line.startswith('data:'): continue
  p=line[5:].strip()
  if p=='[DONE]': done=True; continue
  try: d=json.loads(p)
  except: continue
  model=d.get('model') or model
  for ch in d.get('choices') or []:
    c=(ch.get('delta') or {}).get('content')
    if c: content.append(c)
print('done', done, 'model', model, 'content', ''.join(content)[:200], 'bytes', len(raw))
PY

echo "=== mem ==="
free -h | head -2
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}' | head -12
docker exec synaptika-chat-webui printenv OPENAI_API_BASE_URLS DEFAULT_MODELS ENABLE_BASE_MODELS_CACHE
