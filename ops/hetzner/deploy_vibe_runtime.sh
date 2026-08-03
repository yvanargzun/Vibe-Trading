#!/bin/bash
# Deploy Vibe runtime scripts ONLY to /root/.vibe-trading (not Alpaca).
set -eu
DEST=/root/.vibe-trading
mkdir -p "$DEST"
FILES="telegram_control_bot.py telegram_notify_prefs.py telegram_dynamic_monitor.py telegram_monitor_loop.py vibe_autotrade_loop.py vibe_eth_scalp_loop.py equity_chart.py dynamic_goals.py market_orchestrator.py trade_events.py"
for f in $FILES; do
  if [ -f "/tmp/$f" ]; then
    sed -i 's/\r$//' "/tmp/$f" || true
    cp "/tmp/$f" "$DEST/"
  fi
done
python3 -m py_compile \
  "$DEST/telegram_control_bot.py" \
  "$DEST/telegram_notify_prefs.py" \
  "$DEST/telegram_dynamic_monitor.py" \
  "$DEST/vibe_autotrade_loop.py" \
  "$DEST/vibe_eth_scalp_loop.py"
systemctl restart vibe-telegram-control.service vibe-telegram.service vibe-autotrade.service vibe-eth-scalp.service
sleep 3
systemctl is-active vibe-telegram-control vibe-telegram vibe-autotrade vibe-eth-scalp
journalctl -u vibe-telegram-control -n 15 --no-pager