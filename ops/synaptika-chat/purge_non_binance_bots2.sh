#!/bin/bash
set -euo pipefail

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
  ft-research-all.service
)

echo "=== force stop/disable/mask ==="
for u in "${UNITS[@]}"; do
  systemctl stop "$u" 2>/dev/null || true
  systemctl disable "$u" 2>/dev/null || true
  systemctl mask "$u" 2>/dev/null || true
  echo "done $u -> $(systemctl is-active "$u" 2>/dev/null || true) / $(systemctl is-enabled "$u" 2>/dev/null || true)"
done

echo "=== kill leftover bot/notify processes (keep binance) ==="
# Kill by cmdline, excluding binance futures
ps -eo pid,cmd | while read -r pid cmd; do
  case "$cmd" in
    *config_binance_futures*|*SynaptikaEmaRsiFutures*|PID*|*grep*) continue ;;
  esac
  case "$cmd" in
    *alpaca_scalp15*|*alpaca_smart_fast*|*alpaca_paper*|*vibe_autotrade_loop*|*telegram_control_bot*|*ft_telegram_loop*|*config_alpaca*|*SynaptikaEmaRsiAlpaca*|*vibe-trading serve*|*telegram*monitor*)
      echo "kill $pid $cmd"
      kill -TERM "$pid" 2>/dev/null || true
      ;;
  esac
done
sleep 2
# force kill stubborn
ps -eo pid,cmd | while read -r pid cmd; do
  case "$cmd" in
    *config_binance_futures*|*SynaptikaEmaRsiFutures*) continue ;;
  esac
  case "$cmd" in
    *alpaca_scalp15*|*alpaca_smart_fast*|*vibe_autotrade_loop*|*ft_telegram_loop*|*config_alpaca*|*SynaptikaEmaRsiAlpaca*)
      echo "KILL -9 $pid"
      kill -9 "$pid" 2>/dev/null || true
      ;;
  esac
done

# Find parent of mysterious 'python -' under freqtrade venv if not binance
ps -eo pid,ppid,rss,cmd --sort=-rss | head -30

echo "=== find other alpaca/telegram unit files ==="
ls -la /etc/systemd/system/*alpaca* /etc/systemd/system/*telegram* /etc/systemd/system/*vibe* /etc/systemd/system/*freq* /etc/systemd/system/*scalp* 2>/dev/null || true
systemctl daemon-reload

# Ensure binance up
systemctl reset-failed freqtrade-binance-futures.service 2>/dev/null || true
systemctl restart freqtrade-binance-futures.service
sleep 3

# telegram off again after render
python3 <<'PY'
import json
from pathlib import Path
for name in ("config_binance_futures.json", "config_binance_futures.runtime.json"):
    p = Path("/opt/hermes-tools/freqtrade/user_data") / name
    if not p.exists():
        continue
    d = json.loads(p.read_text())
    tg = d.get("telegram") if isinstance(d.get("telegram"), dict) else {}
    tg["enabled"] = False
    d["telegram"] = tg
    p.write_text(json.dumps(d, indent=2) + "\n")
    print(name, "telegram.enabled", tg.get("enabled"))
PY

# Patch render script so future restarts stay muted
RENDER=/opt/hermes-tools/bin/ft-futures-render-config
if [ -f "$RENDER" ]; then
  if ! grep -q 'SYNAPTIKA_DISABLE_TELEGRAM' "$RENDER"; then
    cat >> "$RENDER" <<'EOF'

# SYNAPTIKA_DISABLE_TELEGRAM: keep Binance notifications off
python3 - <<'PY'
import json
from pathlib import Path
p = Path("/opt/hermes-tools/freqtrade/user_data/config_binance_futures.runtime.json")
if p.exists():
    d = json.loads(p.read_text())
    tg = d.get("telegram") if isinstance(d.get("telegram"), dict) else {}
    tg["enabled"] = False
    d["telegram"] = tg
    for k in ("webhook", "discord", "slack"):
        if isinstance(d.get(k), dict):
            d[k]["enabled"] = False
    p.write_text(json.dumps(d, indent=2) + "\n")
PY
EOF
    echo "patched render script to force telegram off"
  else
    echo "render script already patched"
  fi
fi

systemctl restart freqtrade-binance-futures.service
sleep 2

echo "=== FINAL running trade/telegram ==="
systemctl list-units --type=service --state=running --no-pager | grep -iE 'freq|alpaca|vibe|telegram|scalp' || echo '(none)'
echo "=== FINAL procs ==="
ps -eo rss,cmd --sort=-rss | grep -iE 'freqtrade|alpaca|vibe_auto|telegram|scalp|smart_fast' | grep -v grep || echo '(none)'
echo "=== binance ==="
systemctl is-active freqtrade-binance-futures.service
free -h
