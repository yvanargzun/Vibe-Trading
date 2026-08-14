#!/bin/bash
set -e
cd /root/synaptika-chat
# Prefer dedicated Omni host when DuckDNS works; keep path fallback on chat host
if ! grep -q 'OMNIROUTE_PUBLIC_URL=' secrets.env; then
  echo 'OMNIROUTE_PUBLIC_URL=https://synaptika-omni.duckdns.org' >> secrets.env
fi
sed -i 's|^DEFAULT_MODELS=.*||' secrets.env
echo 'DEFAULT_MODELS=auto/best-free' >> secrets.env

cp /root/synaptika-trade/Caddyfile /root/synaptika-trade/Caddyfile.bak.omni || true
# Caddyfile is uploaded separately; reload after compose
docker compose --env-file secrets.env up -d omniroute chat-webui
cd /root/synaptika-trade
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>&1 | tail -5

# Sign in OWUI and refresh models
sleep 8
TOKEN=$(curl -s -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -H "Authorization: Bearer $TOKEN" 'https://synaptika-chat.duckdns.org/api/models?refresh=true' > /tmp/owui_models.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/owui_models.json'))
ids=[m.get('id') for m in d.get('data') or []]
print('owui_model_count', len(ids))
for i in ids[:20]:
  print(' ', i)
print('has_auto_best_free', any(i=='auto/best-free' or 'auto/best-free' in i for i in ids))
print('has_omni_auto', any(str(i).startswith('auto/') for i in ids))
PY

echo "=== password ==="
grep '^OMNIROUTE_INITIAL_PASSWORD=' /root/synaptika-chat/secrets.env
