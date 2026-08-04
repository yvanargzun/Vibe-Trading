#!/bin/bash
# Update DuckDNS A record for synaptika-trade
set -eu
SECRETS=/root/synaptika-trade/secrets.env
# shellcheck disable=SC1090
source "$SECRETS"
DOMAIN="${DUCKDNS_DOMAIN:-synaptika-trade}"
TOKEN="${DUCKDNS_TOKEN:?missing DUCKDNS_TOKEN}"
IP="${DUCKDNS_IP:-$(curl -4 -fsS ifconfig.me)}"
RESP=$(curl -fsS "https://www.duckdns.org/update?domains=${DOMAIN}&token=${TOKEN}&ip=${IP}&verbose=true" || true)
echo "$(date -Is) ip=${IP} resp=${RESP}"
echo "$RESP" | grep -qi '^OK' || echo "$RESP" | grep -q OK || {
  echo "DUCKDNS_FAILED — crea el dominio en https://www.duckdns.org y pega el token correcto en secrets.env"
  exit 1
}
