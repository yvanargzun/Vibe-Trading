#!/bin/bash
set -u
echo "=== openbb ==="
du -sh /opt/hermes-tools/venvs/openbb 2>/dev/null
ls /opt/hermes-tools/venvs 2>/dev/null
echo
echo "=== sizes to backup ==="
du -sh /opt/vibe-trade /opt/hermes-tools /root/.alpaca-paper /root/.alpaca-scalp15 /root/.vibe-trading 2>/dev/null
echo
echo "=== free ==="
df -h /
echo
echo "=== openbb how started ==="
systemctl list-units --all --no-pager 2>/dev/null | grep -i openbb || true
ls /etc/systemd/system/*openbb* 2>/dev/null || true
pgrep -af openbb || true
grep -r openbb /root/.hermes/config.yaml /etc/systemd/system/ 2>/dev/null | head -20 || true
