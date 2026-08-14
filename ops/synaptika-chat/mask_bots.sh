#!/bin/bash
set -euo pipefail
# cleanup accidental mask from broken expansion
systemctl unmask '.service.service' 2>/dev/null || true
rm -f /etc/systemd/system/.service.service 2>/dev/null || true

UNITS=(
  alpaca-paper-autotrade.service
  alpaca-paper-scalp15.service
  alpaca-paper-telegram.service
  freqtrade-alpaca-paper.service
  freqtrade-alpaca-stocks.service
  freqtrade-telegram.service
  vibe-autotrade.service
  vibe-telegram-control.service
  vibe-telegram.service
  vibe-trading.service
)

for u in "${UNITS[@]}"; do
  systemctl stop "$u" 2>/dev/null || true
  systemctl disable "$u" 2>/dev/null || true
  systemctl mask "$u"
  echo "$u -> $(systemctl is-enabled "$u" 2>&1)"
done
systemctl daemon-reload

systemctl enable --now freqtrade-binance-futures.service
echo "binance -> $(systemctl is-active freqtrade-binance-futures.service) $(systemctl is-enabled freqtrade-binance-futures.service)"

echo "=== running ==="
systemctl list-units --type=service --state=running --no-pager | grep -iE 'freq|alpaca|vibe|telegram|scalp' || echo only_check_below
ps -eo cmd | grep -iE 'freqtrade|alpaca|telegram|vibe_auto|smart_fast' | grep -v grep
free -h | head -2
