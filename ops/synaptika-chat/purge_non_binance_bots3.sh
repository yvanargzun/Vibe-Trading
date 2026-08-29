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
)

for u in "${UNITS[@]}"; do
  systemctl stop "$u" 2>/dev/null || true
  systemctl disable "$u" 2>/dev/null || true
  systemctl mask "$u" 2>/dev/null || true
  systemctl reset-failed "$u" 2>/dev/null || true
done
systemctl daemon-reload

echo "=== kill leftovers ==="
pgrep -af 'alpaca_scalp15|alpaca_smart_fast|vibe_autotrade_loop|ft_telegram_loop|telegram_control_bot|config_alpaca|SynaptikaEmaRsiAlpaca|vibe-trading serve' || echo 'no leftovers listed'
pkill -TERM -f 'alpaca_scalp15.py' 2>/dev/null || true
pkill -TERM -f 'alpaca_smart_fast' 2>/dev/null || true
pkill -TERM -f 'vibe_autotrade_loop.py' 2>/dev/null || true
pkill -TERM -f 'ft_telegram_loop.py' 2>/dev/null || true
pkill -TERM -f 'telegram_control_bot.py' 2>/dev/null || true
pkill -TERM -f 'config_alpaca_paper' 2>/dev/null || true
pkill -TERM -f 'config_alpaca_stocks' 2>/dev/null || true
pkill -TERM -f 'SynaptikaEmaRsiAlpaca' 2>/dev/null || true
pkill -TERM -f 'vibe-trading serve' 2>/dev/null || true
sleep 2
pkill -9 -f 'alpaca_smart_fast' 2>/dev/null || true
pkill -9 -f 'ft_telegram_loop.py' 2>/dev/null || true
pkill -9 -f 'alpaca_scalp15.py' 2>/dev/null || true
pkill -9 -f 'vibe_autotrade_loop.py' 2>/dev/null || true

# Kill orphan freqtrade python '-' parent only if not binance child tree
# safer: leave alone if binance is healthy

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
    for k in ("webhook", "discord", "slack"):
        if isinstance(d.get(k), dict):
            d[k]["enabled"] = False
    p.write_text(json.dumps(d, indent=2) + "\n")
    print(name, "telegram", tg.get("enabled"))
PY

RENDER=/opt/hermes-tools/bin/ft-futures-render-config
if [ -f "$RENDER" ] && ! grep -q 'SYNAPTIKA_DISABLE_TELEGRAM' "$RENDER"; then
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
  echo "patched render"
fi

systemctl unmask freqtrade-binance-futures.service 2>/dev/null || true
systemctl enable --now freqtrade-binance-futures.service
sleep 3
# re-disable telegram after ExecStartPre render
python3 <<'PY'
import json
from pathlib import Path
p = Path("/opt/hermes-tools/freqtrade/user_data/config_binance_futures.runtime.json")
d = json.loads(p.read_text())
tg = d.get("telegram") if isinstance(d.get("telegram"), dict) else {}
changed = False
if tg.get("enabled"):
    tg["enabled"] = False
    d["telegram"] = tg
    changed = True
if changed:
    p.write_text(json.dumps(d, indent=2) + "\n")
    print("had to re-disable after start")
else:
    print("telegram stays disabled")
print("enabled=", tg.get("enabled"))
PY

echo "=== RUNNING ==="
systemctl list-units --type=service --state=running --no-pager | grep -iE 'freq|alpaca|vibe|telegram|scalp' || echo '(none)'
echo "=== ENABLED/MASKED ==="
for u in "${UNITS[@]}" freqtrade-binance-futures.service; do
  echo "$u active=$(systemctl is-active $u 2>/dev/null) enabled=$(systemctl is-enabled $u 2>/dev/null)"
done
echo "=== PROCS ==="
ps -eo rss,cmd --sort=-rss | grep -iE 'freqtrade|alpaca|vibe_auto|telegram|scalp|smart_fast|vibe-trading' | grep -v grep || echo '(none)'
free -h
