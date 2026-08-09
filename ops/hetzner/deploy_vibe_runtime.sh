#!/bin/bash
# Deploy Vibe runtime scripts ONLY to /root/.vibe-trading (not Alpaca).
# ETH scalper service is stopped/disabled — v6 owns the full Spot book.
set -eu
DEST=/root/.vibe-trading
mkdir -p "$DEST"
FILES="telegram_control_bot.py telegram_notify_prefs.py telegram_dynamic_monitor.py telegram_monitor_loop.py vibe_autotrade_loop.py equity_chart.py dynamic_goals.py market_orchestrator.py trade_events.py binance_wallets.py v6_config.py v6_trace.py strategy_feedback.py adaptive_tuner.py PROMPT_V6.md"
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
  "$DEST/v6_config.py" \
  "$DEST/v6_trace.py" \
  "$DEST/market_orchestrator.py" \
  "$DEST/binance_wallets.py" \
  "$DEST/dynamic_goals.py" \
  "$DEST/strategy_feedback.py" \
  "$DEST/adaptive_tuner.py" \
  "$DEST/trade_events.py"

# Remove ETH scalper permanently — v6 owns the full Spot book
systemctl stop vibe-eth-scalp.service 2>/dev/null || true
systemctl disable vibe-eth-scalp.service 2>/dev/null || true
systemctl mask vibe-eth-scalp.service 2>/dev/null || true
rm -f /etc/systemd/system/vibe-eth-scalp.service
systemctl daemon-reload || true

# Archive leftover scalper runtime (keep history json for charts if any)
python3 - <<'PY'
import json
import shutil
from pathlib import Path
home = Path("/root/.vibe-trading")
arch = home / "legacy_eth_scalp"
arch.mkdir(exist_ok=True)
for name in (
    "vibe_eth_scalp_loop.py",
    "eth_scalp_state.json",
    "eth_scalp_loop.log",
):
    p = home / name
    if p.exists():
        shutil.move(str(p), str(arch / name))
        print("MOVED", name)
# tombstone state so old digests know it's gone
(arch / "REMOVED.txt").write_text("ETH scalper removed; do not restart.\n", encoding="utf-8")
print("ETH_SCALP_REMOVED")
PY

systemctl restart vibe-telegram-control.service vibe-telegram.service vibe-autotrade.service
sleep 3
systemctl is-active vibe-telegram-control vibe-telegram vibe-autotrade
systemctl is-active vibe-eth-scalp 2>&1 || true
journalctl -u vibe-autotrade -n 20 --no-pager
