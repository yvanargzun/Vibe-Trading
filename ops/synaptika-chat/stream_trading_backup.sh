#!/bin/bash
# Stream a compressed backup of trading stack to stdout (pipe to local file).
# Usage on VPS: bash /tmp/stream_trading_backup.sh | ... 
set -euo pipefail
# Prefer zstd ultra if available for better ratio/speed balance
if command -v zstd >/dev/null; then
  COMP="zstd -T0 -19"
  echo "COMPRESSOR=zstd-19" >&2
else
  COMP="xz -T0 -9"
  echo "COMPRESSOR=xz-9" >&2
fi

# Collect unit files that exist
UNITS=()
for u in \
  /etc/systemd/system/vibe-autotrade.service \
  /etc/systemd/system/vibe-trading.service \
  /etc/systemd/system/vibe-telegram.service \
  /etc/systemd/system/alpaca-paper-autotrade.service \
  /etc/systemd/system/alpaca-paper-scalp15.service \
  /etc/systemd/system/alpaca-paper-telegram.service \
  /etc/systemd/system/freqtrade-alpaca-paper.service \
  /etc/systemd/system/freqtrade-alpaca-stocks.service \
  /etc/systemd/system/freqtrade-binance-futures.service \
  /etc/systemd/system/freqtrade-telegram.service
do
  [[ -e "$u" ]] && UNITS+=("${u#/}")
done

echo "Starting tar…" >&2
tar -C / -cf - \
  --ignore-failed-read \
  opt/vibe-trade \
  opt/hermes-tools \
  root/.alpaca-paper \
  root/.alpaca-scalp15 \
  "${UNITS[@]}" \
  2>/tmp/trading_backup_tar.err | eval "$COMP"
