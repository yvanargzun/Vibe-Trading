#!/bin/bash
set -e
source /root/synaptika-chat/secrets.env
echo "DuckDNS token len=${#DUCKDNS_TOKEN}"
curl -sS "https://www.duckdns.org/update?domains=synaptika-omni&token=${DUCKDNS_TOKEN}&ip=46.225.50.87"; echo
curl -sS "https://www.duckdns.org/update?domains=synaptika-chat&token=${DUCKDNS_TOKEN}&ip=46.225.50.87"; echo

if ! grep -q 'synaptika-omni.duckdns.org' /root/synaptika-trade/Caddyfile; then
  cat >> /root/synaptika-trade/Caddyfile <<'EOF'

# OmniRoute dashboard + OpenAI-compatible /v1
synaptika-omni.duckdns.org {
	encode gzip
	reverse_proxy synaptika-chat-omniroute:20128 {
		flush_interval -1
	}
}
EOF
  echo "patched Caddyfile"
fi

cd /root/synaptika-trade
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>&1 | tail -8 || docker compose restart caddy

echo "=== mem ==="
free -h
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}' | head -20

echo "=== owui -> omniroute ==="
docker exec synaptika-chat-webui python3 - <<'PY'
import urllib.request
try:
    r = urllib.request.urlopen("http://omniroute:20128/v1/models", timeout=20)
    data = r.read()
    print("models_bytes", len(data), "head", data[:200])
except Exception as e:
    print("ERR", e)
PY

echo "=== chat test ==="
curl -s -m 90 http://127.0.0.1:20128/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"openrouter/free","messages":[{"role":"user","content":"Di solo: omniroute-ok"}],"max_tokens":40}' \
  | head -c 800
echo

echo "=== public HTTPS probe ==="
curl -s -o /dev/null -w 'omni=%{http_code}\n' --connect-timeout 10 https://synaptika-omni.duckdns.org/ || true
curl -s -o /dev/null -w 'chat=%{http_code}\n' --connect-timeout 10 https://synaptika-chat.duckdns.org/ || true

echo "OMNIROUTE_INITIAL_PASSWORD=$OMNIROUTE_INITIAL_PASSWORD"
