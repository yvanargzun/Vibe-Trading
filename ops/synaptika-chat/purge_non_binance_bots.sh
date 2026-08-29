#!/bin/bash
set -euo pipefail

KEEP="freqtrade-binance-futures.service"

STOP_UNITS=(
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

echo "=== stop+disable non-Binance bots/notifications ==="
for u in "${STOP_UNITS[@]}"; do
  if systemctl list-unit-files --type=service --no-pager | grep -q "^${u}"; then
    systemctl stop "$u" 2>/dev/null || true
    systemctl disable "$u" 2>/dev/null || true
    # Prevent accidental re-enable on reboot via WantedBy
    systemctl mask "$u" 2>/dev/null || true
    echo "stopped/disabled/masked: $u"
  else
    echo "missing: $u"
  fi
done

# Kill leftover related processes if any
pkill -f 'alpaca_scalp15.py' 2>/dev/null || true
pkill -f 'vibe_autotrade_loop.py' 2>/dev/null || true
pkill -f 'telegram_control_bot.py' 2>/dev/null || true
pkill -f 'config_alpaca_paper' 2>/dev/null || true
pkill -f 'config_alpaca_stocks' 2>/dev/null || true
pkill -f 'SynaptikaEmaRsiAlpaca' 2>/dev/null || true
# Do NOT kill binance futures

echo "=== disable telegram inside Binance freqtrade configs ==="
python3 <<'PY'
import json
from pathlib import Path

paths = [
    Path("/opt/hermes-tools/freqtrade/user_data/config_binance_futures.json"),
    Path("/opt/hermes-tools/freqtrade/user_data/config_binance_futures.runtime.json"),
]
for p in paths:
    if not p.exists():
        print("skip", p)
        continue
    data = json.loads(p.read_text(encoding="utf-8"))
    tg = data.get("telegram")
    if isinstance(tg, dict):
        tg["enabled"] = False
        data["telegram"] = tg
        print(p, "telegram.enabled -> False")
    else:
        data["telegram"] = {"enabled": False}
        print(p, "telegram section created disabled")
    # Also disable other notification hooks if present
    for key in ("webhook", "discord", "slack"):
        if isinstance(data.get(key), dict):
            data[key]["enabled"] = False
            print(p, f"{key}.enabled -> False")
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

# If render script overwrites runtime from template, patch template sources too
for f in /opt/hermes-tools/freqtrade/user_data/config_binance_futures*.json \
         /opt/hermes-tools/bin/ft-futures-render-config; do
  [ -e "$f" ] || continue
  if [ -f "$f" ] && grep -q 'telegram' "$f" 2>/dev/null; then
    echo "note: telegram refs in $f"
  fi
done

# Patch render script / templates if telegram enabled is set true there
if [ -f /opt/hermes-tools/bin/ft-futures-render-config ]; then
  # After render, force disable again via drop-in ExecStartPost or re-run disable after restart
  :
fi

echo "=== ensure Binance stays up ==="
systemctl unmask freqtrade-binance-futures.service 2>/dev/null || true
systemctl enable freqtrade-binance-futures.service
systemctl restart freqtrade-binance-futures.service
sleep 2

# Re-apply telegram disabled on runtime after render
python3 <<'PY'
import json
from pathlib import Path
p = Path("/opt/hermes-tools/freqtrade/user_data/config_binance_futures.runtime.json")
if p.exists():
    data = json.loads(p.read_text(encoding="utf-8"))
    tg = data.get("telegram") if isinstance(data.get("telegram"), dict) else {}
    if tg.get("enabled", False):
        tg["enabled"] = False
        data["telegram"] = tg
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print("re-disabled telegram on runtime after restart render")
    else:
        print("runtime telegram already disabled:", tg.get("enabled"))
PY

# If render re-enabled telegram, restart once more with patched source
systemctl restart freqtrade-binance-futures.service
sleep 3

echo "=== status ==="
systemctl is-active freqtrade-binance-futures.service || true
systemctl is-enabled freqtrade-binance-futures.service || true

echo "=== still running trading/telegram? ==="
systemctl list-units --type=service --state=running --no-pager | grep -iE 'freq|alpaca|vibe|telegram|scalp' || echo '(none besides maybe binance)'

echo "=== processes ==="
ps -eo rss,cmd --sort=-rss | grep -iE 'freqtrade|alpaca|vibe_auto|telegram|scalp15' | grep -v grep || echo '(no matching procs)'

echo "=== memory ==="
free -h
