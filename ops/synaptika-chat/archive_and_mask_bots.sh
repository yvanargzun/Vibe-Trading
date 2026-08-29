#!/bin/bash
set -euo pipefail
ARCHIVE=/root/disabled-systemd-units
mkdir -p "$ARCHIVE"

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
  if [ -f "/etc/systemd/system/$u" ] && [ ! -L "/etc/systemd/system/$u" ]; then
    mv "/etc/systemd/system/$u" "$ARCHIVE/$u"
    echo "archived $u"
  fi
  # also archive drop-ins
  if [ -d "/etc/systemd/system/${u}.d" ]; then
    mv "/etc/systemd/system/${u}.d" "$ARCHIVE/${u}.d" 2>/dev/null || true
  fi
  ln -sfn /dev/null "/etc/systemd/system/$u"
  echo "masked $u"
done

systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true

systemctl enable --now freqtrade-binance-futures.service
sleep 2

echo "=== status ==="
for u in "${UNITS[@]}"; do
  echo "$u enabled=$(systemctl is-enabled "$u" 2>&1) active=$(systemctl is-active "$u" 2>&1)"
done
echo "binance enabled=$(systemctl is-enabled freqtrade-binance-futures.service) active=$(systemctl is-active freqtrade-binance-futures.service)"
echo "=== running match ==="
systemctl list-units --type=service --state=running --no-pager | grep -iE 'freq|alpaca|vibe|telegram|scalp' || true
ps -eo rss,cmd --sort=-rss | grep -iE 'freqtrade|alpaca|vibe_auto|telegram|smart_fast' | grep -v grep || echo '(no extra procs)'
free -h | head -2
ls "$ARCHIVE"
