#!/bin/bash
# Deploy Vibe runtime scripts ONLY to /root/.vibe-trading (not Alpaca).
# ETH scalper service is stopped/disabled — v6 owns the full Spot book.
set -eu
DEST=/root/.vibe-trading
mkdir -p "$DEST"
FILES="telegram_control_bot.py telegram_notify_prefs.py telegram_dynamic_monitor.py telegram_monitor_loop.py vibe_autotrade_loop.py equity_chart.py dynamic_goals.py market_orchestrator.py trade_events.py binance_wallets.py v6_config.py v6_trace.py PROMPT_V6.md"
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
  "$DEST/dynamic_goals.py"

# Retire ETH scalper so deploys never revive it
systemctl stop vibe-eth-scalp.service 2>/dev/null || true
systemctl disable vibe-eth-scalp.service 2>/dev/null || true

# Clear stale scalper sleeve flags (reserve / orphan position memory)
python3 - <<'PY'
import json
from pathlib import Path
p = Path("/root/.vibe-trading/eth_scalp_state.json")
if p.exists():
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        st = {}
    st["position"] = None
    st["active_float"] = False
    st["reserved_usdt"] = 0
    st["last_regime"] = "dead"
    st["retired"] = True
    p.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
    print("SCALP_STATE_CLEARED")
else:
    print("SCALP_STATE_ABSENT")
PY

systemctl restart vibe-telegram-control.service vibe-telegram.service vibe-autotrade.service
sleep 3
systemctl is-active vibe-telegram-control vibe-telegram vibe-autotrade
systemctl is-active vibe-eth-scalp || true
journalctl -u vibe-autotrade -n 20 --no-pager
