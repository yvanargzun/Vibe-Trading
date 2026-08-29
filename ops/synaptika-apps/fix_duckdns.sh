#!/usr/bin/env bash
set -euo pipefail
set -a
# shellcheck disable=SC1091
source /root/synaptika-trade/secrets.env
set +a
IP="${DUCKDNS_IP:-$(curl -4 -fsS ifconfig.me)}"
echo "IP=$IP"
for d in synaptika-demos synaptika-messenger; do
  RESP=$(curl -fsS "https://www.duckdns.org/update?domains=${d}&token=${DUCKDNS_TOKEN}&ip=${IP}&verbose=true" || true)
  echo "${d}: ${RESP}"
done
echo '--- resolve ---'
for d in synaptika-demos synaptika-messenger synaptika-chat; do
  getent hosts "${d}.duckdns.org" || echo "${d} NXDOMAIN"
done
