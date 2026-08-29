#!/usr/bin/env bash
# Deploy Synaptika demos + Messenger onto the Trade VPS (off Render).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO_URL="${SYNAPTIKA_REPO_URL:-https://github.com/yvanargzun/Synaptika.git}"
BRANCH="${SYNAPTIKA_BRANCH:-main}"
SECRETS="$ROOT/secrets.env"
TRADE_SECRETS=/root/synaptika-trade/secrets.env
CHAT_SECRETS=/root/synaptika-chat/secrets.env
CHAT_KEY=/root/synaptika-chat/OMNIROUTE_GATEWAY_API_KEY.txt

cd "$ROOT"

if [[ ! -d repo/.git ]]; then
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" repo
else
  git -C repo fetch --depth 1 origin "$BRANCH"
  git -C repo checkout -B "$BRANCH" "origin/$BRANCH"
fi

# Ensure build ignores match
cp -f "$ROOT/.dockerignore" repo/.dockerignore
cp -f "$ROOT/Dockerfile" repo/Dockerfile

# Seed secrets.env if missing (never overwrite existing)
if [[ ! -f "$SECRETS" ]]; then
  echo "Creating $SECRETS — fill from PC .env if empty"
  touch "$SECRETS"
  chmod 600 "$SECRETS"
fi

upsert() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$SECRETS"; then
    grep -v "^${key}=" "$SECRETS" > "${SECRETS}.tmp"
    mv "${SECRETS}.tmp" "$SECRETS"
  fi
  printf '%s=%s\n' "$key" "$val" >> "$SECRETS"
}

# OmniRoute gateway key from chat stack
if [[ -f "$CHAT_KEY" ]]; then
  upsert OMNIROUTE_API_KEY "$(tr -d '\r\n' < "$CHAT_KEY")"
fi
upsert AI_PROVIDER omniroute
upsert OMNIROUTE_BASE_URL "http://synaptika-chat-omniroute:20128/v1"
upsert OMNIROUTE_MODEL auto/chat
upsert BOOKING_BASE_URL "https://synaptika-messenger.duckdns.org"
upsert DEMOS_BASE_URL "https://synaptika-demos.duckdns.org"
upsert MESSENGER_URL "https://synaptika-messenger.duckdns.org"
upsert NODE_ENV production
upsert TRUST_PROXY 1

if ! grep -q '^DEMO_LEADS_SECRET=.\+' "$SECRETS"; then
  upsert DEMO_LEADS_SECRET "$(openssl rand -hex 24)"
fi
if ! grep -q '^TELEGRAM_WEBHOOK_SECRET=.\+' "$SECRETS"; then
  upsert TELEGRAM_WEBHOOK_SECRET "$(openssl rand -hex 24)"
fi

# DuckDNS: point demos + messenger hosts at this VPS
if [[ -f "$TRADE_SECRETS" ]]; then
  # shellcheck disable=SC1090
  set -a
  # shellcheck disable=SC1091
  source "$TRADE_SECRETS"
  set +a
  TOKEN="${DUCKDNS_TOKEN:?missing DUCKDNS_TOKEN in trade secrets}"
  IP="${DUCKDNS_IP:-$(curl -4 -fsS ifconfig.me)}"
  for d in synaptika-demos synaptika-messenger; do
    RESP=$(curl -fsS "https://www.duckdns.org/update?domains=${d}&token=${TOKEN}&ip=${IP}&verbose=true" || true)
    echo "duckdns ${d} ip=${IP} resp=${RESP}"
  done
fi

echo "=== build ==="
docker compose build --pull

echo "=== up ==="
docker compose up -d --force-recreate

echo "=== wait ==="
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8788/health || true)
  [[ "$code" == "200" ]] && break
  sleep 2
done
echo "messenger_health=$code"
curl -s http://127.0.0.1:8788/health || true
echo
curl -s -o /dev/null -w "demos_local=%{http_code}\n" http://127.0.0.1:4173/ || true
docker ps --filter name=synaptika- --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
df -h / | tail -1
free -h | sed -n '2,3p'
echo DONE
echo "Public URLs:"
echo "  https://synaptika-demos.duckdns.org/"
echo "  https://synaptika-messenger.duckdns.org/health"
echo "Update Meta webhook to: https://synaptika-messenger.duckdns.org/webhook"
