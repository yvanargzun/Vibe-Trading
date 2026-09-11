#!/bin/bash
set -u

echo "=== status before ==="
for u in \
  vibe-autotrade vibe-trading vibe-telegram \
  alpaca-paper-autotrade alpaca-paper-scalp15 alpaca-paper-telegram \
  freqtrade-alpaca-paper freqtrade-alpaca-stocks freqtrade-binance-futures freqtrade-telegram
do
  act=$(systemctl is-active "$u" 2>/dev/null || echo missing)
  en=$(systemctl is-enabled "$u" 2>/dev/null || echo missing)
  echo "$u active=$act enabled=$en"
done
echo
free -h
BEFORE_USED=$(free -b | awk '/Mem:/{print $3}')
BEFORE_AVAIL=$(free -b | awk '/Mem:/{print $7}')
BEFORE_SWAP=$(free -b | awk '/Swap:/{print $3}')

echo
echo "=== STOP + DISABLE systemd trading ==="
# Keep vibe-telegram-control (Synaptika Hermes/Sales/FB)
UNITS=(
  vibe-autotrade.service
  vibe-trading.service
  vibe-telegram.service
  alpaca-paper-autotrade.service
  alpaca-paper-scalp15.service
  alpaca-paper-telegram.service
  freqtrade-alpaca-paper.service
  freqtrade-alpaca-stocks.service
  freqtrade-telegram.service
)
for u in "${UNITS[@]}"; do
  if systemctl cat "$u" >/dev/null 2>&1; then
    systemctl stop "$u" 2>/dev/null || true
    systemctl disable "$u" 2>/dev/null || true
    echo "stopped+disabled $u"
  else
    echo "skip missing $u"
  fi
done
# already masked
systemctl stop freqtrade-binance-futures.service 2>/dev/null || true

echo
echo "=== STOP docker trading helpers (keep caddy) ==="
for c in synaptika-trade-ops-1 synaptika-trade-llm-proxy-1; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$c"; then
    docker update --restart=no "$c" 2>/dev/null || true
    docker stop "$c" 2>/dev/null || true
    echo "stopped $c"
  fi
done

echo
echo "=== STOP OpenBB MCP (Hermes trading tools) if running ==="
if pgrep -f 'openbb-mcp' >/dev/null 2>&1; then
  pkill -f 'openbb-mcp' 2>/dev/null || true
  sleep 1
  echo "killed openbb-mcp"
else
  echo "openbb-mcp not running"
fi
# disable any openbb unit if exists
systemctl list-unit-files --no-pager 2>/dev/null | grep -i openbb | awk '{print $1}' | while read -r u; do
  systemctl stop "$u" 2>/dev/null || true
  systemctl disable "$u" 2>/dev/null || true
  echo "stopped+disabled $u"
done

sleep 3
echo
echo "=== AFTER ==="
free -h
AFTER_USED=$(free -b | awk '/Mem:/{print $3}')
AFTER_AVAIL=$(free -b | awk '/Mem:/{print $7}')
AFTER_SWAP=$(free -b | awk '/Swap:/{print $3}')

echo
echo "=== kept (Synaptika) ==="
systemctl is-active vibe-telegram-control.service docker.service
docker ps --format '{{.Names}} {{.Status}}'

echo
python3 - <<PY
bu,ba,bs=int("$BEFORE_USED"),int("$BEFORE_AVAIL"),int("$BEFORE_SWAP")
au,aa,ass=int("$AFTER_USED"),int("$AFTER_AVAIL"),int("$AFTER_SWAP")
def mb(x): return x/1024/1024
print(f"RAM used:      {mb(bu):.0f} -> {mb(au):.0f} MB  ({mb(bu-au):+.0f})")
print(f"RAM available: {mb(ba):.0f} -> {mb(aa):.0f} MB  ({mb(aa-ba):+.0f})")
print(f"Swap used:     {mb(bs):.0f} -> {mb(ass):.0f} MB  ({mb(bs-ass):+.0f})")
PY

echo
echo "=== trading units final ==="
for u in vibe-autotrade vibe-trading vibe-telegram alpaca-paper-autotrade alpaca-paper-scalp15 vibe-telegram-control; do
  echo "$u: $(systemctl is-active $u 2>/dev/null) enabled=$(systemctl is-enabled $u 2>/dev/null)"
done
